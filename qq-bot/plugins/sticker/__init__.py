"""表情包联动（纯本地版）：AI 回复后按情绪配本地表情包，情绪化演出不限次数。

2026-09-12 T13 通用化重写（用户裁决 D10）：
- 词表**归一**：候选情绪 = 情绪分类器的 9 类（`plugins/emotion` CLASSIFY_PROMPT 逐字同名）
  开心/难过/生气/惊讶/害怕/厌恶/平静/委屈/害羞。旧 24 情绪三维体系（{人格}-{状态}-{情绪}）
  与 11 个恶堕状态段已整体删除（那是奥丁单人设时代的过时设计，通用库无法复用）。
- **目录层**（一套，规范化）：
  - 人设专属（可选）：`<卡键>/<情绪>/*.{png,jpg,jpeg,webp,gif}`（`sticker_dir` 指向该卡根）
  - 通用库（跨卡）：`qq-bot/data/stickers/_common/<情绪>/…`
- 解析顺序：人设专属(该情绪) → 通用(该情绪) → 人设专属(任意情绪) → 通用(任意情绪) → **不发图**。
  取不到一律静默不发（旧奥丁三维目录 `E:\\comfyui\\lib\\emotes` **不做兼容**，不特殊处理、不报错）。
- judge 只在 9 类内选；`emotion_hint` 存在时**沿用指定情绪**（不重选），无 hint 才让 judge 选。
- 发送：base64 直发（NapCat 对 file:/// 中文路径会下载失败 Not Found）
- 发送失败静默降级，不影响主流程
"""
import json
import random
from pathlib import Path
from core.paths import DATA_ROOT

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.log import logger as nb_logger
from openai import AsyncOpenAI

from core import atomics  # 2026-09-07 P2：状态原子写（项目铁律）
from core import llm as core_llm  # 2026-09-10 Phase1：LLM Provider 适配层

# 用 nonebot.logger（loguru）：日志真正出现在 bot.log，便于排查"不发送"类问题
logger = nb_logger.opt(colors=False)

STATE_FILE = DATA_ROOT / "sticker_state.json"
CFG_FILE = DATA_ROOT / "sticker_mode.json"
# 通用表情库根（2026-09-12 T13.2 正式接线）：data/stickers/_common/<9 类情绪>/*.{png,jpg,webp,…}
COMMON_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "stickers" / "_common"
# 旧本地差分目录（.env STICKER_DIFF_DIR）。2026-09-12：三维体系已废，本常量仅为
# debug 面板回显保留，不再进入取图链（人设专属素材改走 <卡键>/<情绪>/ 规范目录）。
DIFF_DIR = Path(getattr(get_driver().config, "sticker_diff_dir", "") or "").resolve() if getattr(get_driver().config, "sticker_diff_dir", "") else None
CLEAN_HOUR = 18           # 每日 18:00 后清空表情包缓存


def _load_cfg() -> dict:
    try:
        return json.loads(CFG_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cfg(state: dict):
    CFG_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(CFG_FILE, state)


def sticker_enabled() -> bool:
    """表情包总开关（/表情 开|关；默认取 .env STICKER_ENABLED）。"""
    return bool(_load_cfg().get("enabled", True))


def set_sticker_enabled(on: bool):
    cfg = _load_cfg()
    cfg["enabled"] = on
    _save_cfg(cfg)

# 本地表情包情绪词表（2026-09-12 T13.1 归一 24 → 9）：
# 与 plugins/emotion CLASSIFY_PROMPT 的候选集**逐字同名**——分类器产出的情绪必须都有对应目录，
# 不再有"分类器永远产不出的情绪"（24 情绪时代 15 个属此类）。
MOODS = ("开心", "难过", "生气", "惊讶", "害怕", "厌恶", "平静", "委屈", "害羞")

# 情绪化演出：情绪鲜明即发（不再宁缺毋滥）；仅纯信息/功能回复不配图
JUDGE_PROMPT = """你是表情包调度器。判断这条 AI 回复是否适合配一张表情包（图片）。
规则：
- 回复带有任何**明显的情绪色彩**就应该 send=true——情绪化演出，触发要频繁；
- 只有纯信息/纯功能回复（搜索答案、命令执行、报错、日常寒暄）才 send=false。
emotion 必须从以下候选情绪中选择一个最贴合的（与回复情绪直接相关）：{moods}
只选实际最鲜明的情绪，不要选氛围相近的次要情绪。
输出严格 JSON（无其他文字）：{{"send": true或false, "emotion": "候选情绪之一"}}"""

# 异常回复识别：报错/离线/系统提示类文本不配表情包（兜底，防"报错还发无意义图"）
ABNORMAL_REPLY_MARKS = ("[大脑离线]", "[错误]", "Traceback", "Error:", "Exception")


def _is_abnormal_reply(reply: str) -> bool:
    return any(m in reply for m in ABNORMAL_REPLY_MARKS)


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict):
    # 2026-09-07 P2：换原子写（曾裸写文本——写坏即状态清零；跨 await 竞态见调用方重读）
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(STATE_FILE, state)


def _daily_clean_if_due():
    """每日 18:00 后首次调用时清空表情包缓存（避免会话记录堆积）。"""
    from datetime import datetime as _dt

    now = _dt.now()
    state = _load_state()
    if now.hour >= CLEAN_HOUR and state.get("_clean_date") != now.date().isoformat():
        _save_state({"_clean_date": now.date().isoformat()})
        logger.info("sticker cache cleaned at %s", now.strftime("%H:%M"))


_LLM_CLIENT: AsyncOpenAI | None = None


def _llm_client() -> AsyncOpenAI:
    # 2026-09-07 P2：模块级单例（曾每次判定新建客户端不关闭——连接/FD 持续创建销毁靠 GC 兜底）
    # 2026-09-10 Phase1：构造收拢 core.llm（不传 timeout 的口径在 purpose 表中保持为 None；默认本地零变化）
    global _LLM_CLIENT
    if _LLM_CLIENT is None:
        _LLM_CLIENT = core_llm.get_client("sticker")
    return _LLM_CLIENT


def _session_key(event: MessageEvent) -> str:
    gid = getattr(event, "group_id", None)
    return f"group:{gid}" if gid else f"user:{event.get_user_id()}"


def _scan_dir(d: Path | None) -> dict[str, list[Path]]:
    """扫描一个库根 → {情绪目录名: [文件路径]}（2026-09-12 T13.2：**单层**结构）。

    期望布局 `<库根>/<情绪>/文件`；只取二级（`<情绪>`）目录下的文件，图片扩展名不限规范名。
    **目录名必须逐字是 9 类之一**（规范 T13.2）——不符合规范的布局（如旧奥丁三维
    `善-正常-开心/`）整段不入索引 → 调用方静默不发图，**绝不抛错、也绝不误发无关情绪的图**。
    """
    idx: dict[str, list[Path]] = {}
    if d is None or not d.is_dir():
        return idx
    try:
        for sub in sorted(d.iterdir()):
            if not sub.is_dir() or sub.name not in MOODS:
                continue
            files = [p for p in sorted(sub.iterdir())
                     if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif")]
            if files:
                idx[sub.name] = files
    except OSError:
        return {}
    return idx


def _pick_file(persona_index: dict[str, list[Path]], common_index: dict[str, list[Path]],
               emotion: str, last_file: str) -> tuple[Path | None, str]:
    """单层情绪匹配（2026-09-12 T13.1 替换旧三维 `_pick_file`/`_pick_persona_fallback`）。

    顺序（规范 T13.2）：人设专属(该情绪) → 通用(该情绪) → 人设专属(任意情绪) → 通用(任意情绪) → None。
    返回 (文件, 来源标记 src)——src ∈ {"人设", "通用"}，仅用于日志可观测。
    保留"尽量不与最近一张同图"行为：候选 >1 时先排除 last_file（见 `_choose`）。
    """
    for idx, src in ((persona_index, "人设"), (common_index, "通用")):
        files = idx.get(emotion) or []
        if files:
            return _choose(files, last_file), src
    for idx, src in ((persona_index, "人设"), (common_index, "通用")):
        files = [f for group in idx.values() for f in group]
        if files:
            return _choose(files, last_file), src
    return None, ""


def _choose(files: list[Path], last_file: str) -> Path:
    """同图回避：候选 >1 时优先选与上次不同的那张。"""
    if len(files) > 1:
        others = [f for f in files if f.name != last_file]
        if others:
            return random.choice(others)
    return random.choice(files)


def _mood_candidates() -> str:
    """情绪候选：9 类（2026-09-12 T13.1 归一后直接反映 MOODS）。"""
    return "、".join(MOODS)


def _norm_emotion(word: str) -> str:
    """情绪词归一：`emotion.EMOTION_ALIASES` 是项目内**唯一**别名表（逐字复用，不另建表）；
    不在表内且不在 9 类内的词（如旧 24 情绪残留）→ 空串（交 judge 在 9 类内选）。"""
    w = str(word or "").strip().strip("『』「」【】[]")
    if w in MOODS:
        return w
    try:
        from plugins import emotion as _emo

        aliases = getattr(_emo, "EMOTION_ALIASES", {}) or {}
    except Exception:  # noqa: BLE001  情绪插件不可用=只认 9 类字面词（零行为放大）
        aliases = {}
    return aliases.get(w, "")


async def try_make_sticker_segment(event: MessageEvent, reply: str, mode: int = 0, state_hint: str = "", state_label: str = "", emotion_hint: str = "", sticker_dir: str | None = None) -> MessageSegment | None:
    """主入口（生成不发送）：异常拦截 → 情绪确定（hint 沿用 / judge 9 选 1）→ 单层目录取图
    → 返回 image 段（供 brain 与文字/语音同一条消息发送）；失败返回 None。

    sticker_dir：人设专属表情库根（`<卡键>/<情绪>/`）；**为空不再掐断整链**——回落通用库
    `_common/<情绪>/`，两者皆无图才不发（2026-09-12 T13.3③）。
    emotion_hint：brain 侧标记情绪词或本条自标【基调】——存在时**沿用该情绪**（不重选）。
    mode/state_hint/state_label：保留参数形状（调用方与 smoke 契约定），不再参与目录匹配。
    """
    try:
        if not sticker_enabled():
            logger.info("sticker skipped: 表情包总开关关闭（/表情 关）")
            return None
        if _is_abnormal_reply(reply):
            logger.info("sticker skipped: abnormal reply (离线/报错)")
            return None
        _daily_clean_if_due()
        key = _session_key(event)
        state = _load_state()

        persona_index = _scan_dir(Path(sticker_dir)) if sticker_dir else {}
        common_index = _scan_dir(COMMON_ROOT)
        if not persona_index and not common_index:
            logger.info("sticker skipped: 人设库与通用库皆无素材 (sticker_dir=%s)", sticker_dir)
            return None
        # 2026-09-06 [STICKER] 协议化：状态词表/正文扫描的"强制发图"分支已删——
        # 发不发由 agent 在回复里自决标记（[STICKER] / [STICKER:情绪]）；
        # 情绪取图只由下方情绪链决定（2026-09-12：judge 仅在 9 类内选，hint 存在则沿用）。
        emotion = _norm_emotion(emotion_hint)
        if emotion:
            logger.info("sticker: 沿用指定情绪 emotion=%s", emotion)
        else:
            judge = JUDGE_PROMPT.format(moods=_mood_candidates())
            _sys = judge + ("\n\n当前机器人状态：" + state_hint if state_hint else "")
            _sys += "\n\n本次已由机器人决定发送表情：send 恒为 true，你只负责从 9 类里选 emotion。"
            resp = await _llm_client().chat.completions.create(
                model=core_llm.resolve_model("sticker"),  # 2026-09-10 Phase1：假模型别名退役（服务端单模型，经 core.llm 解析）
                messages=[
                    {"role": "system", "content": _sys},
                    {"role": "user", "content": f"AI回复：{reply[:200]}"},
                ],
                temperature=0.2,
                max_tokens=200,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},  # 决策无需思考
            )
            raw = (resp.choices[0].message.content or "").strip().strip("`")
            if raw.startswith("json"):
                raw = raw[4:].strip()
            data = json.loads(raw)
            emotion = _norm_emotion(str(data.get("emotion", "")))
        last_file = str(state.get(f"{key}:last", "") or "")
        pick, src = _pick_file(persona_index, common_index, emotion, last_file)
        if pick is None:
            logger.info("sticker skipped: no local image (emotion=%s)", emotion)
            return None
        # base64 直发（NapCat 对 file:/// 中文路径会下载失败 Not Found）
        try:
            import base64

            b64 = base64.b64encode(pick.read_bytes()).decode("ascii")
        except OSError:
            logger.warning("sticker local read failed: %s", pick)
            return None
        # 2026-09-07 P2：LLM 判定是 await——期间其他会话可能已写回；重读再更新本会话键（旧快照整体覆盖曾丢 {key}:last 防同图记录）
        state = _load_state()
        state[f"{key}:last"] = pick.name
        _save_state(state)
        logger.info(f"sticker made: src={src} emotion={emotion or '-'} mode={mode} file={pick.name}")
        return MessageSegment.image(f"base64://{b64}")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"sticker skipped: {e}")
    return None


async def maybe_send_sticker(bot: Bot, event: MessageEvent, reply: str, mode: int = 0, state_hint: str = "", state_label: str = "", emotion_hint: str = "", sticker_dir: str | None = None):
    """兼容旧接口：生成后单独发送一条表情（旧调用路径）。
    2026-09-08 P2：修参数错位——曾把 (bot, event, reply) 整体左移一位传进
    try_make_sticker_segment(event, reply, mode=...)（bot 当 event、event 当 reply、
    reply 当 mode），本接口从未走通过；唯一消费者 verify_3d_e2e.py 也因此一直在空跑。"""
    seg = await try_make_sticker_segment(
        event, reply, mode=mode, state_hint=state_hint, state_label=state_label,
        emotion_hint=emotion_hint, sticker_dir=sticker_dir,
    )
    if seg is None:
        return
    try:
        await bot.send(event, seg)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"sticker send failed: {e}")
