"""情绪引擎（P2）：回复情绪分类 -> 映射 Live2D 参数/表情 -> 注入 VTube Studio。

- classify: 用本地 LLM 对"用户消息+机器人回复"做情绪分类（后台任务，失败走规则兜底）
- express: 连接 VTS 并注入参数；VTS 未启动时静默降级，不阻塞聊天
- 情绪状态同时落库（memory.db emotions 表），供后续自训练与面板使用

VTS 对接步骤（首次）：
1. 安装 VTube Studio（Steam，免费版可用）并启动，加载 Live2D 模型
2. VTS 设置中启用 WebSocket 插件（默认 127.0.0.1:8001）
3. .env 设 VTS_ENABLED=true 后首次触发时，VTS 会弹窗要求允许插件 -> 点允许
4. 认证 token 自动保存在 data/vts_token.txt
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from core.paths import DATA_ROOT

from nonebot import get_driver
from openai import AsyncOpenAI

from ..memory.store import db
from .vts_client import VTSClient  # 2026-09-08：VTSConnectionError 导入后零使用，删

from core import llm as core_llm  # 2026-09-10 Phase1：LLM Provider 适配层

logger = logging.getLogger("emotion")

TOKEN_FILE = DATA_ROOT / "vts_token.txt"

# 六基本情绪 + 平静 + 委屈/害羞 -> VTS Live2D 参数映射（值域 -1..1，随模型可调）
# 参数名以 VTS 通用 Live2D 参数为准（ParamEyeLOpen/R、ParamMouthOpenY、ParamBrowLY/R、ParamAngleX/Z）
EMOTION_PARAMS: dict[str, dict[str, float]] = {
    "开心":   {"ParamBrowLY": 0.3, "ParamBrowRY": 0.3, "ParamMouthOpenY": 0.45, "ParamEyeLOpen": 0.15, "ParamEyeROpen": 0.15},
    "难过":   {"ParamBrowLY": -0.35, "ParamBrowRY": -0.35, "ParamMouthOpenY": -0.2, "ParamEyeLOpen": -0.25, "ParamEyeROpen": -0.25, "ParamAngleZ": 0.05},
    "生气":   {"ParamBrowLY": -0.5, "ParamBrowRY": -0.5, "ParamEyeLOpen": -0.3, "ParamEyeROpen": -0.3, "ParamMouthOpenY": 0.15},
    "惊讶":   {"ParamEyeLOpen": 0.6, "ParamEyeROpen": 0.6, "ParamMouthOpenY": 0.7, "ParamBrowLY": 0.5, "ParamBrowRY": 0.5},
    "害怕":   {"ParamEyeLOpen": 0.4, "ParamEyeROpen": 0.4, "ParamBrowLY": 0.3, "ParamBrowRY": 0.3, "ParamMouthOpenY": -0.15},
    "厌恶":   {"ParamBrowLY": -0.3, "ParamBrowRY": -0.3, "ParamMouthOpenY": -0.25, "ParamEyeLOpen": -0.2, "ParamEyeROpen": -0.2},
    "平静":   {},
    "委屈":   {"ParamBrowLY": 0.25, "ParamBrowRY": 0.25, "ParamMouthOpenY": 0.1, "ParamEyeLOpen": -0.15, "ParamEyeROpen": -0.15},
    "害羞":   {"ParamEyeLOpen": -0.2, "ParamEyeROpen": -0.2, "ParamBrowLY": 0.15, "ParamBrowRY": 0.15, "ParamAngleZ": -0.08},
}
# 情绪别名 -> 标准名（LLM 输出归一化）
EMOTION_ALIASES = {
    "高兴": "开心", "快乐": "开心", "喜悦": "开心", "兴奋": "开心", "愉快": "开心",
    "伤心": "难过", "悲伤": "难过", "失落": "难过", "沮丧": "难过", "忧郁": "难过",
    "愤怒": "生气", "不爽": "生气", "烦躁": "生气",
    "吃惊": "惊讶", "震惊": "惊讶",
    "恐惧": "害怕", "紧张": "害怕",
    "讨厌": "厌恶", "反感": "厌恶",
    "委屈": "委屈", "撒娇": "委屈",
    "害羞": "害羞", "腼腆": "害羞",
    "无": "平静", "中性": "平静", "正常": "平静", "平淡": "平静",
}

# 情绪 -> 亲密度增量（心情与好感度挂钩：正面情绪涨、负面情绪跌，小步累计）
EMOTION_INTIMACY_DELTA = {
    "开心": 0.3, "害羞": 0.4, "惊讶": 0.1,
    "委屈": -0.1, "害怕": -0.15, "难过": -0.3, "生气": -0.4, "厌恶": -0.5,
    "平静": 0.0,
}

CLASSIFY_PROMPT = """你是情绪识别模块。根据"用户消息"与"机器人回复"，判断机器人此刻应该呈现的情绪。
只输出严格 JSON（无其他文字）：{"emotion":"开心|难过|生气|惊讶|害怕|厌恶|平静|委屈|害羞","intensity":0.0到1.0}"""

# 规则兜底：简单关键词 -> 情绪
_RULE_TABLE = [
    (r"(哈哈|哈哈哈|太好啦|开心|喜欢|真棒|耶)", "开心"),
    (r"(呜呜|想哭|难过|伤心|委屈|呜呜呜)", "委屈"),
    (r"(？？|什么|不会吧|震惊|天哪)", "惊讶"),
    (r"(生气|气死|烦死|可恶|讨厌)", "生气"),
    (r"(怕|害怕|吓死)", "害怕"),
]

_client: VTSClient | None = None
_expressions_cache: list = []


def _vts_enabled() -> bool:
    return bool(getattr(get_driver().config, "vts_enabled", False))


# B11（2026-09-09 审计）：客户端单例化（照抄 plugins/memory/__init__.py 既有模式）——
# 曾每次 classify 新建 AsyncOpenAI（连接池反复重建、旧实例靠 GC 回收）；
# timeout 保险：引擎卡死时后台情绪分类快速失败走规则兜底，不拖住 express 链
_LLM_CLIENT: AsyncOpenAI | None = None


def _llm_client() -> AsyncOpenAI:
    global _LLM_CLIENT
    if _LLM_CLIENT is None:
        # 2026-09-10 Phase1：构造收拢 core.llm（默认本地配置下行为零变化）
        _LLM_CLIENT = core_llm.get_client("emotion")
    return _LLM_CLIENT


def _normalize_emotion(name: str) -> str:
    name = (name or "").strip()
    if name in EMOTION_PARAMS:
        return name
    return EMOTION_ALIASES.get(name, "平静")


def classify_rule(text: str) -> tuple[str, float]:
    """关键词规则兜底分类。"""
    for pattern, emotion in _RULE_TABLE:
        if re.search(pattern, text):
            return emotion, 0.6
    return "平静", 0.2


async def classify(user_text: str, reply_text: str) -> tuple[str, float]:
    """LLM 情绪分类；失败降级为规则。"""
    try:
        resp = await _llm_client().chat.completions.create(
            model=core_llm.resolve_model("emotion"),  # 2026-09-10 Phase1：假模型别名退役（服务端单模型，经 core.llm 解析）
            messages=[
                {"role": "system", "content": CLASSIFY_PROMPT},
                {"role": "user", "content": f"用户消息：{user_text}\n机器人回复：{reply_text}"},
            ],
            temperature=0.2,
            max_tokens=300,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},  # 分类无需思考
        )
        raw = (resp.choices[0].message.content or "").strip().strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
        emotion = _normalize_emotion(str(data.get("emotion", "平静")))
        intensity = max(0.0, min(1.0, float(data.get("intensity", 0.5))))
        return emotion, intensity
    except Exception as e:  # noqa: BLE001
        logger.debug("emotion classify LLM failed, fallback to rule: %s", e)
        return classify_rule(user_text + reply_text)


def _scaled_params(emotion: str, intensity: float) -> list[dict]:
    base = EMOTION_PARAMS.get(emotion, {})
    return [{"id": k, "value": round(v * intensity, 3)} for k, v in base.items() if v != 0]


def _get_vts_client() -> VTSClient:
    global _client
    if _client is None:
        cfg = get_driver().config
        _client = VTSClient(
            host=getattr(cfg, "vts_host", "127.0.0.1"),
            port=int(getattr(cfg, "vts_port", 8001)),
            plugin_name=getattr(cfg, "vts_plugin_name", "QQAICompanion"),
            plugin_developer="local",
        )
    return _client


def _load_token() -> str | None:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8-sig").strip() or None
    except OSError:
        return None


def _save_token(token: str):
    # 2026-09-07 P2：原子写（项目铁律；曾裸写文本——半截 token 认证失败一轮）
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    from core import atomics
    atomics.write_text_atomic(TOKEN_FILE, token)


async def _ensure_authenticated(vts: VTSClient) -> bool:
    if vts.is_authenticated:
        return True
    token = _load_token()
    if token:
        try:
            return await vts.authenticate(token)
        except Exception:  # noqa: BLE001
            token = None
    if not token:
        try:
            token = await vts.request_auth_token()
            _save_token(token)
        except Exception as e:  # noqa: BLE001
            logger.warning("VTS token 申请失败: %s", e)
            return False
    # 尝试立即认证（mock 或已允许插件场景）；失败则等用户在 VTS 弹窗允许后重试
    try:
        return await vts.authenticate(token)
    except Exception as e:  # noqa: BLE001
        logger.warning("VTS 认证未通过（请在 VTS 弹窗中允许插件 %s 后重试）: %s", vts.plugin_name, e)
        return False


async def _apply_expression(vts: VTSClient, emotion: str):
    """若模型有与情绪同名的表情，则激活它（避免重复激活同表情）。"""
    global _expressions_cache
    try:
        if not _expressions_cache:
            _expressions_cache = await vts.get_expressions()
        names = {e.get("name", ""): e.get("file", "") for e in _expressions_cache}
        now_active = set()
        for name, file_ in names.items():
            active = name.lower() == emotion.lower()
            if active or file_ in getattr(_apply_expression, "_active", set()):
                await vts.set_expression(file_, active=active)
            if active:
                now_active.add(file_)
        # 2026-09-07 P2 修复：登记当前激活集合（曾只读不写——旧表情永远停不掉，第一个非平静表情永久残留）
        _apply_expression._active = now_active
    except Exception as e:  # noqa: BLE001
        logger.debug("expression sync skipped: %s", e)


async def express(user_id: str, user_text: str, reply_text: str):
    """主入口：分类 -> 落库 -> 情绪驱动亲密度（心情↔好感度挂钩）-> （可选）驱动 VTS。"""
    try:
        emotion, intensity = await classify(user_text, reply_text)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.record_emotion(user_id, emotion, intensity, ts)
        # 心情与好感度挂钩：正面情绪涨亲密度、负面情绪跌（小步累计；催眠等临时状态不动原值）
        # 2026-09-05：好感度只对「亲密积累轨道」（白名单成员）累计——主人恒定满值、公开用户不写表
        delta = EMOTION_INTIMACY_DELTA.get(emotion, 0.0)
        if delta:
            try:
                from .. import brain as _brain

                if str(user_id) in _brain._load_intimate_wl():
                    db.update_relation(user_id, delta=delta, mood=emotion, ts=ts, cap=_brain.REL_WL_CAP)
            except Exception:  # noqa: BLE001
                pass
        logger.info("emotion=%s intensity=%.2f delta=%+.2f (user=%s)", emotion, intensity, delta, user_id)

        if not _vts_enabled():
            return
        vts = _get_vts_client()
        if not await _ensure_authenticated(vts):
            return
        params = _scaled_params(emotion, intensity)
        if params:
            await vts.inject_parameters(params)
        await _apply_expression(vts, emotion)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.debug("emotion express skipped: %s", e)


