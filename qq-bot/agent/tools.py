"""agent/tools.py —— 工具集（agent 的行动能力；与记忆层单向对接，不 import brain）

Reflexion 程序记忆（Memory-to-Skill）：每个行为域（群/用户）沉淀「上次插话的反馈」，
喂给下轮感知。存储：data/agent_memory.json。
"""
from __future__ import annotations

import json
import time
from core.paths import DATA_ROOT

from core import atomics

MEMORY_FILE = DATA_ROOT / "agent_memory.json"

# 结构：{"banter:<gid>": {"last_text": str, "last_ts": float,
#                         "positive_hits": int, "negative_hits": int, "reflection": str}}


_CACHE: dict = {"mtime": -1.0, "data": {}}
_LOAD_FAILED = False  # 2026-09-07 P1：文件读坏标志（曾返回 {} 被下次 _save 持久化 → 整库静默清零）


def _load() -> dict:
    """程序记忆读取（mtime 缓存：stat 开销 ＜ json 解析）。
    2026-09-07 P1：读损坏不再返回空 dict——先伺服最后一份好缓存（fail-closed）；
    无缓存可用时返回 {} 但置 _LOAD_FAILED，_save 拒绝以空覆盖。"""
    global _LOAD_FAILED
    try:
        mtime = MEMORY_FILE.stat().st_mtime
        if mtime != _CACHE["mtime"]:
            _CACHE["data"] = json.loads(MEMORY_FILE.read_text(encoding="utf-8-sig"))
            _CACHE["mtime"] = mtime
        _LOAD_FAILED = False
        return _CACHE["data"]
    except (OSError, json.JSONDecodeError) as e:
        from loguru import logger

        logger.warning("agent_memory unreadable (serving last-good/{}): {} [{}]",
                       "empty" if not _CACHE["data"] else "stale-cache", MEMORY_FILE, type(e).__name__)
        _LOAD_FAILED = True
        return dict(_CACHE["data"] or {})


def _save(state: dict):
    global _LOAD_FAILED
    if _LOAD_FAILED and not _CACHE["data"]:
        # 2026-09-07 守卫修正：读坏且无 last-good 缓存时拒绝**一切**写入（旧条件 `not state` 恒假——
        # remember 等先构造非空单键 dict 再 _save，守卫从未生效：损坏+无缓存时单键覆写=全库清零）。
        # 等文件恢复可读（_load 成功自动解除）后写入自然恢复。
        from loguru import logger

        logger.warning("agent_memory save skipped (file unreadable, no last-good data) — 防清零保护")
        return
    _LOAD_FAILED = False
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(MEMORY_FILE, state)
    _CACHE["data"] = state
    try:
        _CACHE["mtime"] = MEMORY_FILE.stat().st_mtime
    except OSError:
        _CACHE["mtime"] = -1.0


def remember(key: str, text: str, persona: str = "", delivery: str = ""):
    """记录一次发言（每域最后一条）。2026-09-05：附带人设标签——跨卡切换时旧卡发言不再注入新卡感知（防人格串）。
    2026-09-08 活人感#2①行为账本：delivery=送达方式（"语音"/"表情"/"语音、表情"），
    recall 时回传——agent 知道自己上一条是怎么说出去的。"""
    st = _load()
    e = {**(st.get(key) or {}), "last_text": text, "last_ts": time.time(), "persona": persona}
    if delivery:
        e["delivery"] = delivery
    else:
        e.pop("delivery", None)
    st[key] = e
    _save(st)


def mark_feedback(key: str, kind: str = "negative"):
    """反馈标记：kind ∈ negative（被嫌/被吐槽）/ positive（被接话/被点赞）。"""
    st = _load()
    e = st.get(key) or {}
    f = "negative_hits" if kind == "negative" else "positive_hits"
    e[f] = int(e.get(f, 0) or 0) + 1
    e["last_ts"] = time.time()
    st[key] = e
    _save(st)


def reflect(key: str, note: str):
    """反思写回（程序记忆：下次感知读取）。"""
    st = _load()
    e = st.get(key) or {}
    e["reflection"] = note
    e["last_ts"] = time.time()
    st[key] = e
    _save(st)


# 2026-09-08 P2·C3 行为账本（真人的身体记忆——"我做过什么"是事实，不是反馈/判定）：
# 与 remember/recall 分开：act 域语义=刚做过的事（短时效），不混入"上次说的是…"的发言域模板。
ACT_TTL_SECS = 7200  # 行为记忆时效：2 小时内"你刚才做过什么"才有意义


def remember_act(key: str, text: str, persona: str = ""):
    """记录行为事实（act 域）。text 为"她刚做过什么"的短事实（如「你刚才：（掐你一下）」）。"""
    st = _load()
    e = {**(st.get(key) or {}), "last_text": text, "last_ts": time.time(), "persona": persona}
    st[key] = e
    _save(st)


def recall_act(key: str, persona: str = "", now: float | None = None) -> str:
    """行为记忆感知：ACT_TTL_SECS 内的行为事实；超时/无记录返回空（跨卡隔离同 recall）。"""
    e = _load().get(key) or {}
    if persona and e.get("persona") and e["persona"] != persona:
        return ""
    _now = now if now is not None else time.time()
    if not e.get("last_text") or _now - float(e.get("last_ts") or 0) > ACT_TTL_SECS:
        return ""
    return str(e["last_text"])[:80]


def recall(key: str, persona: str = "") -> str:
    """感知摘要（程序记忆 → 下轮感知输入）；无记录返回空。
    2026-09-05：persona 非空且记录带不同人设标签 → 返回空（旧卡发言不注入新卡，防人格串）。"""
    e = _load().get(key) or {}
    if persona and e.get("persona") and e["persona"] != persona:
        return ""
    parts = []
    if e.get("reflection"):
        parts.append(e["reflection"])
    n = int(e.get("negative_hits", 0) or 0)
    p = int(e.get("positive_hits", 0) or 0)
    if n or p:
        parts.append(f"近期反馈：被嫌弃 {n} 次 / 被接话或点赞 {p} 次")
    if e.get("last_text"):
        parts.append(f"你上次在这里说的是：{str(e['last_text'])[:60]}")
    # 2026-09-08 活人感#2①行为账本：送达方式回传（语音/表情——她知道自己上一条是怎么说出去的）
    if e.get("delivery"):
        parts.append(f"（上一条以{e['delivery']}的方式送达）")
    return "；".join(parts)
