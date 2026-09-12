"""矫正机制：对话中教她如何回答，持久化记录并在后续回复中生效。

- 触发：2026-09-06 起由 LLM 全权判定矫正意图（词表预筛已删——"你上次那样说话不太好"这类
  无关键词的表达也要能进来）；后台任务执行，不阻塞本轮回复
- 存储：data/corrections.json -> {user_id: [{id, rule, source, ts, hits}]}（每用户上限 10 条）
- 注入：memory.build_context 追加"对方纠正过的说话方式"（L4 动态约束，冲突时人格底色优先）
- 仅主人（SUPERUSERS）的矫正持久化生效；非主人消息不触发
"""
import json
import time
from pathlib import Path

from core import atomics
from core import llm as core_llm  # 2026-09-10 Phase1：LLM Provider 适配层
from nonebot.log import logger as nb_logger
from openai import AsyncOpenAI

logger = nb_logger.opt(colors=False)

# 2026-09-12 审计 I 项：本文件是累积**状态**（`atomics` 落盘，存量 2.4 KB 自 09-01）——
# 按设计（core/paths.py：记忆库/状态/日志都在 DATA_ROOT）本该在规范根，此处是历史遗留。
# audit-ok: 搬家须连存量数据一起迁（读旧写新兼容），属"状态文件搬家"→ 记 docs/遗留任务.md A9 待裁决
CORRECTION_FILE = Path(__file__).resolve().parents[2] / "data" / "corrections.json"
MAX_PER_USER = 10


def _load() -> dict:
    try:
        return json.loads(CORRECTION_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(state: dict):
    CORRECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(CORRECTION_FILE, state)


# B11（2026-09-09 审计）：客户端单例化（照抄 plugins/memory/__init__.py 既有模式）——
# 曾每次 try_extract 新建 AsyncOpenAI（连接池反复重建）；timeout=30 保险保留
_LLM_CLIENT: AsyncOpenAI | None = None


def _llm_client() -> AsyncOpenAI:
    global _LLM_CLIENT
    if _LLM_CLIENT is None:
        # 2026-09-10 Phase1：构造收拢 core.llm（timeout=30 口径在 purpose 表中保持；默认本地零变化）
        _LLM_CLIENT = core_llm.get_client("correction")
    return _LLM_CLIENT


EXTRACT_PROMPT = """你是对话矫正器。用户对 AI 角色说了一句"纠正/教导"性质的话（指出 AI 哪里说得不对、以后该怎么回答）。
规则：
- 确认这句话是否真的是矫正指令：明确指出了 AI 的某种说话/行为方式问题，并要求以后改变。
- 若是：提炼成**一条 30 字以内的具体规则**（用祈使句描述 AI 以后必须/禁止的行为，如"回复中不要用'哼，凡人'开场"、"把'妾身'换成'我'"）。
- **若用户指出 AI 重复/复读/翻来覆去说某类话：规则必须引用用户原话中的具体内容**（如"不要再反复说'留下/别走'这类话"、"不要每轮用'既然你已…便…'收尾"），禁止提炼成"避免重复"之类的空泛规则。
- 若不是（普通对话/闲聊/剧情）：corrected=false。
输出严格 JSON（无其他文字）：{"corrected": true或false, "rule": "规则"或""}"""


def list_rules(user_id: str) -> list[dict]:
    return _load().get(user_id, [])


def get_text(user_id: str) -> str:
    """返回注入文本（无规则返回空串）。命中时累计 hits。"""
    state = _load()
    rules = state.get(user_id, [])
    if not rules:
        return ""
    for r in rules:
        r["hits"] = int(r.get("hits", 0)) + 1
    _save(state)
    return "对方纠正过你的说话方式（必须遵守；与人格底色冲突时以人格为准）：\n- " + "\n- ".join(r["rule"] for r in rules)


async def try_extract(user_id: str, text: str, is_owner: bool = False) -> bool:
    """识别矫正意图并持久化（LLM 全权判定，无词表预筛——2026-09-06）。
    返回是否识别为矫正指令。仅主人有效。调用方应以后台任务方式调用（不阻塞本轮回复）。"""
    if not is_owner:
        return False
    try:
        resp = await _llm_client().chat.completions.create(
            model=core_llm.resolve_model("correction"),  # 2026-09-10 Phase1：假模型别名退役（服务端单模型，经 core.llm 解析）
            messages=[
                {"role": "system", "content": EXTRACT_PROMPT},
                {"role": "user", "content": f"用户原话：{text[:300]}"},
            ],
            temperature=0.1,
            max_tokens=150,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        raw = (resp.choices[0].message.content or "").strip().strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
        if not data.get("corrected"):
            return False
        rule = str(data.get("rule", "")).strip()
        if not rule:
            return False
    except Exception as e:  # noqa: BLE001
        logger.debug("correction extract failed: {}", e)
        return False
    state = _load()
    rules = state.setdefault(user_id, [])
    if len(rules) >= MAX_PER_USER:
        logger.info("correction full: %s (%d rules)", user_id, len(rules))
        return False
    rules.append({
        "id": int(time.time()),
        "rule": rule,
        "source": text[:200],
        "ts": time.time(),
        "hits": 0,
    })
    _save(state)
    logger.info("correction saved for %s: %s", user_id, rule)
    return True


def clear_all(user_id: str) -> bool:
    state = _load()
    if state.pop(user_id, None) is not None:
        _save(state)
        return True
    return False
