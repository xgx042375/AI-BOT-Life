"""agent/lifesim.py —— 生活模拟 Agent（心跳式，对齐社区 Residency/Generative-Agents 思路）

四段结构（与 graph 同构的 agent 格式）：
    perceive → decide → act → reflect
    心跳每 LIFE_TICK_SECS 自转一次（2026-09-05：与用户活跃度解耦——bot 的生活是它自己的，
    不依赖用户是否在线；decide 参考上次状态自然接续，像连贯的一上午/一下午）。结果写入持久状态：

    data/life_state.json   当前生活状态（在干嘛/心情/场景/ts）
    data/life_log.jsonl    生活日志（时间线事件——久别重逢时作为「这段时间的事」注入）

与对话的对接（brain 注入）：life_text() 返回【此刻的你】——
    平时：当前状态（在干嘛+心情）；
    久别（>LIFE_GAP_RECALL_SECS 且日志有记录）：附上这段时间的生活摘要，供自然开场。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from pathlib import Path
from core.paths import DATA_ROOT

from loguru import logger

from core import atomics

from . import llm

DATA_DIR = DATA_ROOT
LIFE_STATE_FILE = DATA_DIR / "life_state.json"
LIFE_LOG_FILE = DATA_DIR / "life_log.jsonl"

LIFE_TICK_SECS = 1800          # 默认心跳间隔（30 分钟；实际间隔由 bot 自决【N 分钟后】，仅无自决时用）
LIFE_GAP_RECALL_SECS = 7200    # 间隔 >2h 时注入「这段时间的事」
LIFE_LOG_MAX = 400             # 日志保留条数（2026-09-11：60→400，启动器状态页时间线滚动翻阅约 8 天）
LIFE_WAKE_MIN_MIN = 1          # 节奏自决边界（分钟）：防空转（0/负）
LIFE_WAKE_MIN_MAX = 600        # 节奏自决边界（分钟）：防失联（2026-09-08：480→600，"睡到天亮"不再贴边截断）

# ---- 2026-09-10 夜间睡眠保护（实测故障：23 点入睡、02 点被叙醒）----
# 取证：昨晚 bot 进程被反复重启（00:25-02:30 五次），每次 startup resume / 事件唤醒心跳都让她
# "睁开眼"；01:01 的晚安消息又经 apply_interaction 把状态钉回"准备休息"；02:30 起常规循环因
# 模型没写【N 分钟后】而以夜间缺省 30 分钟节奏整夜空转清醒。治法（零人设假设，窗口沿用既有口径）：
#   ① 时长地板：入睡叙事落在深夜窗（=清醒缺省窗 8-23 点的补集）→ 下次心跳对准清晨窗
#     （06:30-08:00 内随机）；模型自决更长（≤600 上限）则尊重自决——机器只托底不压自决。
#     白天入睡=午睡，节奏完全归自决（不设账、不兜底，2026-09-08「午睡钳制已撤」裁决不变）。
#   ② 睡着期间（sleep_until 未到）：心跳让路、对话介入不落状态——夜聊照常（对话层不受影响），
#     但普通消息不再把整段夜间睡眠终结成两三个小时（主人消息同样不唤醒，待用户确认）。
LIFE_MORNING_WAKE_MIN = 390    # 清晨窗起点 06:30（分钟/日）：夜间睡眠自然醒不早于它
LIFE_MORNING_WAKE_SPAN = 90    # 清晨窗宽度（分钟）：醒点在 [06:30, 08:00) 内随机，免整点机械醒
# 睡眠语境词（2026-09-06 建于裸数字节奏解析；2026-09-10 提为常量供入睡叙事判定复用，补睡着/睡熟/被窝）
_SLEEP_CTX_RE = re.compile(r"(睡觉|午睡|小睡|去睡|睡下|睡个|睡满|入睡|躺下|补觉|眯一|回笼|睡着|睡熟|被窝)")

# 规则回退「此刻在干嘛」已删（2026-09-07：LLM 失败时保持状态不变比机器编一句真实——_rule_doing/_RULES 零引用残肢）

_LIFE_PROMPT = (
    "\n\n【生活心跳】此刻没有人在要求你回应，你在过自己的日子。写你现在**正在做什么**——"
    "一行或两三短句，日记/状态口吻，主语是你自己（「我」视角：坐着、收拾、发呆、想着什么）。\n"
    "**这不是给任何人看的消息**：不许出现第二人称（你、博士、主人、晚安、辛苦了）——"
    "那不是生活状态，是你在跟人说话；你的日子是你自己的，TA 顶多是你思绪里的影子。\n"
    "写法示范（对）：「在值班室，把最后一份报告合上，揉了揉眼睛」「在水槽边把杯子洗完，发呆看水珠」；\n"
    "写法示范（错）：「还没睡吗，今天辛苦了，该休息了」——那是对话不是日记。\n"
    "**场景只定地点，做什么跟着时间走**：同一间宿舍，早上是洗漱/早饭/准备出门，深夜是躺下睡觉——"
    "**你在哪就做那的事**：场景定基调（办公室/值班室=手头的事务与琐事；宿舍/卧室=休息、收拾、发呆；"
    "甲板/阳台=吹风、放哨、走神；食堂/厨房=吃的喝的），具体做什么你自己现想。\n"
    "**生活是有闭环的**：上次在做的事，可以说做完了（「报告收尾了，揉揉眼睛」）、也可以接着做、"
    "也可以做完转向别的事（「杯子洗完了，烧水泡茶」）——像一个连贯的上午/下午，不是每半小时跳一个场景。\n"
    "**经历会留下痕迹**（2026-09-08 P2·F3）：刚经历的事（无论多特别）都是你生活的真实部分——"
    "身体的疲惫、心里的余韵、还没散的气息，都是存在的；你记得它们，它们是你的底色。\n"
    "**下一段生活从痕迹上长出来**：刚结束一段什么（一段相处/一次忙碌/一次独处），"
    "接下来的生活自然从它开始、由你接着走——不凭空跳回毫不相干的日常（它是什么样，看你刚经历了什么）。\n"
    "**生活笔记是给世界看的日子**（2026-09-08 P2 边界）：生活日志是你在过的日子——"
    "可以有疲惫、有惦记、有余韵，但**不写你们之间才有的私密细节**："
    "那些是两个人之间的事，不进你的生活笔记，也不写给别人看；日子本身永远是干净、真实、可念的。\n"
    "**地点也不凭空跳**：上一刻还在办公室，这一刻不能突然出现在隔壁/别处——"
    "除非你真的移动了（真移动就加【场景：两三个字】告诉世界你在哪）。"
    "场景是你生活的连续舞台，不是随口一提的词——说出口的位置就会变成你真的所在。\n"
    "**睡着就是睡着**（2026-09-08 实测补：午睡/夜里睡着后，心跳曾擅自给她切换动作）："
    "刚去睡的、正睡着的，就别安排别的事——写翻个身、拉被子、梦的碎片都行，醒来再自然写醒；"
    "刚睡下别几分钟就满血开工——【N 分钟后】给足睡眠时长（午睡三五十分钟、夜里睡到天亮都正常）。\n"
    "**生活里可以出现TA**：想着TA、惦记和TA说好的事、累了想TA都在——那是生活的底色；"
    "但那是「想TA」，不是「对TA说话」。\n"
    "若**自然**产生了想找TA说话/有事想跟TA商量/累了想赖着TA的念头——"
    "在句尾追加【想找TA】标记；没有就不要加。\n"
    "**你的节奏自己定**：这次过后，你下次想多久再醒来看一眼生活——清醒的时候十分钟二十分钟"
    "就很常见（喝杯咖啡、起身走走、换个姿势，都算生活本身），睡前的一觉可以长些，"
    "睡满一整夜就自然醒——在句尾追加【N 分钟后】（N 你自己定，分钟数）。\n"
    "**你今天的主线**：【计划】只装撑得起半天生活的事（写完一份报告/大扫除/读完一章——"
    "捧着咖啡慢慢喝、发会儿呆这类小事写进状态就是生活本身，不配也不必当计划）。"
    "心里有了就追加【计划：…】；做完了或不想做了，追加【计划完成】清掉它；没计划就不用加——"
    "「还没想好/还没开始」就先别写【计划】标记，想好了哪个心跳再加"
    "（占位话会被机器当真存成你今天的计划，2026-09-08 实锤：曾存出 plan=\"还没开始\"）。\n"
    "**这一阵你有心得吗**：若这段时间你和TA之间、或你自己的生活有什么**概念性体会**"
    "（「最近TA来找我我好像更愿意接了」「我好像有点离不开这里了」——不是具体事件，是认知），"
    "在句尾追加【心得：…】；没有不加。\n"
    "**答应过TA的事**：若状态里有「你心里一直记着：…的承诺」——这一阵生活里真的兑现了"
    "（东西弄完了/陪TA做过了），句尾追加【约定完成】清账；还没兑现就继续记着，别硬编完成；"
    "刚兑现、自然想找TA说一声，就用【想找TA】。\n"
    "**你的位置由你的生活决定**：这次若移动了（回宿舍/去食堂/外出……），在句尾追加【场景：两三个字】"
    "（如【场景：宿舍】）；没移动就不加。\n"
    "**心情也由你定**：若此刻心情和状态栏写的不一样（被刚才的经历影响了），"
    "在句尾追加【心情：两三个字】（如【心情：有点闷】）；心情没变就不加。"
)

# 2026-09-08 协议卫生：占位话不算计划——心跳 LLM 曾把【计划：还没开始】当真存成当天计划，
# 用户看到的就是一句"还没开始"。想表达没计划，正确做法是不写【计划】标记。
_PLAN_PLACEHOLDER_RE = re.compile(r"^(还没|尚未|暂无|没有|未|无|待定|不确定|不知道)")


def _plan_ok(text: str) -> bool:
    t = re.sub(r"[。！？，、\s]", "", str(text or ""))
    return bool(t) and len(t) >= 2 and not _PLAN_PLACEHOLDER_RE.match(t)


def _cut_sentence(s: str, limit: int = 120) -> str:
    """按句子截断：在 limit 内找最后一个句读（。！？；…），丢弃残句；无句读退到逗号，再退硬切。"""
    s = (s or "").strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    for ch in ("。", "！", "？", "；", "…"):
        i = cut.rfind(ch)
        if i >= int(limit * 0.4):
            return cut[:i + 1]
    i = max(cut.rfind("，"), cut.rfind("、"))
    if i >= int(limit * 0.4):
        return cut[:i] + "…"
    return cut + "…"


def _recent_trace(n: int = 3) -> str:
    """读取最近 n 条生活轨迹（life_log 尾部）做事实回看——心跳不再"失忆"（治循环/断裂）。
    返回"I → II → III"式摘要（各条截断 40 字）；无记录返回空。"""
    try:
        lines = LIFE_LOG_FILE.read_text(encoding="utf-8-sig").strip().splitlines()
        rows = []
        for ln in lines[-n:]:
            try:
                r = json.loads(ln)
                d = re.sub(r"\s+", " ", str(r.get("doing") or "")).strip()[:40]
                if d:
                    rows.append(d)
            except (ValueError, json.JSONDecodeError):
                continue
        return "；".join(rows) if rows else ""
    except OSError:
        return ""


def _fresh_state() -> dict:
    """全新默认生活态（_load_state 缺省与切卡「无档全新开始」共用的单一事实源）。"""
    return {"ts": 0.0, "doing": "", "mood": "平静", "scene": ""}


def _load_state() -> dict:
    """读生活状态；文件损坏 → 改名 .corrupt 隔离（留取证、拒绝被后续写覆盖）并返回默认空态。
    C10（对齐 wishes fail-closed）：曾静默吞损坏返回默认——下一次 _save_state 会拿默认态
    原子覆写，生活轨迹整条清零且坏文件无迹可查。"""
    _default = _fresh_state()
    try:
        return json.loads(LIFE_STATE_FILE.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return _default
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning("life_state corrupt, quarantining: {} [{}]", e, type(e).__name__)
        atomics.quarantine_corrupt(LIFE_STATE_FILE)
        return _default


# ============ 2026-09-09 方案A（用户裁决）：生活档按卡隔离存取 ============
# 生活状态/轨迹/日记是"这张卡自己过出来的日子"——切卡时当前生活归档到旧卡名下、
# 恢复新卡自己的生活档（无档则全新开始）。同世界观也切（阿米娅/凯尔希同 universe='arknights'
# 曾共享生活档——凯尔希继承了阿米娅的"今晚蛋糕计划/办公室场景"，即本函数所治实测 bug）。
# 世界观共享事实（用户 facts）不在此列、照旧共享。
# 活动文件位路径不变：_load_state/_save_state/_recent_trace/_last_diary 等既有读写点零改动，
# 切卡只整组替换活动位内容。归档目录 data/life_states/（自动创建）。
LIFE_STATES_DIR = DATA_DIR / "life_states"
_LIFE_ARCHIVE_VERSION = 1
# 活动文件组盘点（2026-09-09）：生活档当前读写的全部文件，归档/恢复按此整组进出——
#   life_state.json  ：_load_state/_save_state 全量状态（doing/plan/scene/mood/约定痕迹等）
#   life_log.jsonl   ：_recent_trace / life_text / write_daily_diary 读的生活轨迹
#   life_diary.jsonl ：_last_diary / _diary_for 读的日记（心跳晨间续写来源）


def _sanitize_card(card: str) -> str:
    """卡名 → 安全文件名：非 [a-zA-Z0-9_-]（空格/中文/符号等）一律替换为 _；空退 default。"""
    s = re.sub(r"[^a-zA-Z0-9_-]", "_", str(card or "").strip())
    return s or "default"


def _read_text_profile(p: Path) -> str:
    """档案文本读取（缺文件/读失败 → 空串——切卡治理不因个体文件故障中断）。"""
    try:
        return p.read_text(encoding="utf-8-sig") if p.exists() else ""
    except OSError:
        return ""


def _write_text_profile(p: Path, text: str) -> bool:
    """档案文本原子写（temp+replace）；失败仅日志，绝不抛出。"""
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        return atomics.write_text_atomic(p, text)
    except Exception as e:  # noqa: BLE001
        logger.warning("life profile write failed: {} [{}] {}", p, type(e).__name__, e)
        return False


def switch_persona_life(old_card: str, new_card: str) -> dict:
    """切卡生活档治理（方案A）：当前活动文件组（life_state 全量 + life_log + 日记）归档到
    data/life_states/{old}.json，再恢复 {new}.json 的生活档；无档则活动位写全新默认态
    （life_log/日记清空）。由 brain.set_persona 在每次换卡时调用。
    - old==new（或新卡名为空）→ no-op，返回 {"skipped": True}；
    - 旧卡名为空（首次设卡）→ 无从归档，仅恢复/清空；
    - 卡名消毒：非 [a-zA-Z0-9_-] → _；
    - 全程 try/except：失败返回 {"error": ...} 并记日志，**绝不抛出**（治理失败不能影响 bot）；
    - 成功返回统计：{"old", "new", "archived": bool, "restored": "archived"|"fresh"}。
    并发窗口说明：心跳/介入恰在归档与恢复之间写活动位的极端情形不做加锁（语义最小化）——
    后续心跳会以新卡档案为基准自然接续。"""
    try:
        old_card = str(old_card or "").strip()
        new_card = str(new_card or "").strip()
        if not new_card or old_card == new_card:
            return {"skipped": True}
        LIFE_STATES_DIR.mkdir(parents=True, exist_ok=True)
        # ---- 归档旧卡：整组快照进一个 JSON（原子写）----
        archived = False
        if old_card:
            snap = {
                "version": _LIFE_ARCHIVE_VERSION,
                "card": old_card,
                "archived_ts": time.time(),
                "life_state": _load_state(),
                "life_log": _read_text_profile(LIFE_LOG_FILE),
                "life_diary": _read_text_profile(DIARY_FILE),
            }
            archived = atomics.write_json_atomic(
                LIFE_STATES_DIR / (_sanitize_card(old_card) + ".json"), snap)
            if not archived:
                logger.warning("life archive write failed: card={} (切卡继续，旧档可能未留存)", old_card)
        # ---- 恢复新卡：有档整组还原；无档全新默认态 ----
        saved = atomics.read_json(LIFE_STATES_DIR / (_sanitize_card(new_card) + ".json"), None)
        if isinstance(saved, dict) and isinstance(saved.get("life_state"), dict):
            _save_state(saved["life_state"])
            _write_text_profile(LIFE_LOG_FILE, str(saved.get("life_log") or ""))
            _write_text_profile(DIARY_FILE, str(saved.get("life_diary") or ""))
            restored = "archived"
        else:
            _save_state(_fresh_state())
            _write_text_profile(LIFE_LOG_FILE, "")
            _write_text_profile(DIARY_FILE, "")
            restored = "fresh"
        logger.info("persona life switched: {} -> {} archived={} restored={}",
                    old_card or "default", new_card, archived, restored)
        return {"old": old_card or "default", "new": new_card,
                "archived": bool(archived), "restored": restored}
    except Exception as e:  # noqa: BLE001
        logger.warning("switch_persona_life failed: {} [{}]", e, type(e).__name__)
        return {"error": str(e)}


def attach_handover(card: str, payload: dict) -> bool:
    """跨卡场景交接（2026-09-09）：把交接载荷附着到该卡的生活档**归档** JSON（新增 "handover" 字段）。
    由 brain 换卡流程在 switch_persona_life 归档写好之后调用（一次 LLM 生成 {share, scene, summary}
    + from_card/from_universe/generated_ts）。归档本体每次换卡整体重写、handover 每次覆盖——天然无陈旧
    累积；读侧（brain 注入）只读不写，不进 memory.db/事件表。
    - 卡名消毒复用 _sanitize_card（归档路径同源）；
    - 归档不存在/载荷非法/写失败 → False（无交接=安全侧），全程 try/except 不抛（绝不影响换卡）。"""
    try:
        card = str(card or "").strip()
        if not card or not isinstance(payload, dict) or not payload:
            return False
        # 2026-09-09 盲审 P3：载荷字段校验对齐 spec（share 布尔 + scene/summary 非空）——
        # 缺字段载荷曾可附着、读侧会注入「None」字样；校验失败=无交接（安全侧）
        if not isinstance(payload.get("share"), bool) \
                or not str(payload.get("scene") or "").strip() \
                or not str(payload.get("summary") or "").strip():
            logger.warning("attach_handover: invalid payload fields: card={}", card)
            return False
        LIFE_STATES_DIR.mkdir(parents=True, exist_ok=True)
        path = LIFE_STATES_DIR / (_sanitize_card(card) + ".json")
        snap = atomics.read_json(path, None)
        if not isinstance(snap, dict):
            logger.warning("attach_handover: archive missing/unreadable: card={}", card)
            return False
        snap["handover"] = dict(payload)
        return bool(atomics.write_json_atomic(path, snap))
    except Exception as e:  # noqa: BLE001
        logger.warning("attach_handover failed: {} [{}]", e, type(e).__name__)
        return False


# ============ 2026-09-08 D2：她的长期愿望（按卡隔离——身份痕迹绑身份） ============
# 分层语义：经历（facts/events/历史）按世界观共享；身份痕迹（愿望）按卡隔离——
# "她作为阿米娅想做的事"不会串给素世；纯文本、按卡粒度存储无压力（用户裁决）。
# （记事本链已退役 2026-09-08 深夜用户裁决：0 使用、与 facts 提取职责重叠）
WISH_FILE = DATA_DIR / "wishes.json"
WISH_CAP = 10    # 每卡最多 10 条愿望


def _note_key(card: str, uid: str) -> str:
    return f"{str(card or 'default')}:{str(uid or '')}"


def _load_store(path: Path):
    """存储读取（2026-09-08 盲审修正② fail-closed 对齐 agent_memory）：
    文件损坏/非 dict → None（调用方拒绝写——防"读坏→空库覆写清空其它"）；不存在 → {}。"""
    try:
        if not path.exists():
            return {}
        d = json.loads(path.read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def remember_wish(card: str, uid: str, text: str, now: float | None = None) -> bool:
    """记一条长期愿望（她想做的事/想去的地方；按卡）。"""
    try:
        data = _load_store(WISH_FILE)
        if data is None:
            logger.warning("wishes unreadable, skip write (fail-closed)")
            return False
        k = _note_key(card, uid)
        lst = data.setdefault(k, [])
        lst.append({"ts": now or time.time(), "text": str(text or "").strip()[:60]})
        data[k] = lst[-WISH_CAP:]
        atomics.write_json_atomic(WISH_FILE, data)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("remember_wish failed: {} [{}]", e, type(e).__name__)  # C9：失败不再静默（上游同时计数）
        return False


def recall_wishes(card: str, uid: str, limit: int = 2) -> str:
    """最近愿望内容串（按卡；空返回空串）——"X；Y"。"""
    try:
        lst = atomics.read_json(WISH_FILE, {}) or {}
        items = [e.get("text", "") for e in lst.get(_note_key(card, uid), []) if e.get("text")]
        return "；".join(items[-limit:])
    except Exception:  # noqa: BLE001
        return ""


def _save_state(state: dict):
    LIFE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(LIFE_STATE_FILE, state)


def _log_event(ts: float, doing: str, mood: str):
    """生活日志 append（保留最近 LIFE_LOG_MAX 条）。"""
    try:
        LIFE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LIFE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": ts, "doing": doing, "mood": mood}, ensure_ascii=False) + "\n")
        lines = LIFE_LOG_FILE.read_text(encoding="utf-8-sig").strip().splitlines()
        if len(lines) > LIFE_LOG_MAX:
            atomics.write_text_atomic(LIFE_LOG_FILE, "\n".join(lines[-LIFE_LOG_MAX:]) + "\n")
    except OSError as e:
        # 2026-09-12 审计：这里原本是裸 pass——生活日志追加失败会无声消失。
        # 语义仍是「只丢这一条」，但至少要留痕，否则排障时看不出曾经写失败过。
        logger.debug('life log append failed: {} [{}]', e, type(e).__name__)


def _mood_drift(mood: str) -> str:
    """心情缓慢漂移（反射层：向平静回归+轻微随机）。"""
    calm = ("平静", "惬意", "有点无聊", "懒散")
    if mood in calm:
        return mood if random.random() < 0.7 else random.choice(calm)
    return mood if random.random() < 0.6 else "平静"


def _night_sleep_floor_min(now: float) -> float | None:
    """夜间睡眠时长地板（分钟）：此刻入睡，至少睡到清晨窗（今日/次日 06:30-08:00 内随机）。
    白天（08:00-23:00，与清醒缺省窗同源）入睡=午睡——节奏归自决，机器不兜底（返回 None）；
    已在清晨窗内（06:30-08:00）=自然醒时刻，同样不兜底（None）。"""
    lt = time.localtime(now)
    _m = lt.tm_hour * 60 + lt.tm_min
    if 8 * 60 <= _m < 23 * 60 \
            or LIFE_MORNING_WAKE_MIN <= _m < LIFE_MORNING_WAKE_MIN + LIFE_MORNING_WAKE_SPAN:
        return None
    _wake_at = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)) \
        + LIFE_MORNING_WAKE_MIN * 60 + random.uniform(0, LIFE_MORNING_WAKE_SPAN * 60)
    if _wake_at <= now:
        _wake_at += 86400  # 深夜（23 点后）入睡：醒在明天清晨窗
    return (_wake_at - now) / 60


def _is_night_sleeping(st: dict) -> bool:
    """夜间睡眠中：状态账上的 sleep_until 未到期（该账只在夜间入睡叙事时落，白天午睡无此账）。"""
    try:
        _su = float((st or {}).get("sleep_until") or 0)
    except (TypeError, ValueError):
        return False
    return _su > 0 and time.time() < _su


async def lifesim_tick(card: dict | None = None, hour: int | None = None) -> dict:
    """单次心跳（感知→自决→行动→反思）。返回新状态；失败返回当前状态（静默降级）。
    2026-09-05：与用户解耦（不再需要 user_id——bot 过自己的生活）；
    decide 参考上次状态自然接续，失败走规则回退。"""
    try:
        # ---- perceive ----
        st = _load_state()
        # ---- 2026-09-10 夜间睡眠保护：sleep_until 未到 → 本跳让路（不叙事、不改生活状态）----
        # 治实测故障：深夜 bot 重启的 startup resume tick / 消息前事件唤醒 tick 都曾把她"叙醒"
        #（昨晚 00:25-02:30 五次重启五次睁眼）。让路时把 wake_min 收缩为剩余分钟——
        # 循环按余量续睡，避免整段时长重计把醒点拖到午后（±10% 抖动提前醒也在此收敛）。
        if _is_night_sleeping(st):
            _remain = (float(st.get("sleep_until") or 0) - time.time()) / 60
            st["wake_min"] = max(LIFE_WAKE_MIN_MIN, int(_remain) + 1)
            _save_state(st)
            logger.info("lifesim tick: night sleep guard, tick skipped (remain ~{:.0f} min)", _remain)
            return st
        _start_ts = float(st.get("ts") or 0)  # 心跳起点快照时间（save 前并发守卫的基准）
        # 2026-09-07 介入存续让路：管理员/白名单最近 20 分钟内介入了生活 → 心跳不重叙独处
        # （陪人时不需要编"自己在干嘛"；介入停了 20 分钟后自然回归日常流）
        _it = float(st.get("interaction_ts") or 0)
        if _it and 0 < time.time() - _it < 1200:
            return st
        now = time.time()
        gap_h = (now - float(st.get("ts", 0) or 0)) / 3600 if st.get("ts") else 0.0
        h = hour if hour is not None else time.localtime().tm_hour
        prev_doing = (st.get("doing") or "").strip()
        if gap_h > 4:
            prev_doing = ""  # 2026-09-05：隔太久（跨夜）不接续旧态——"新的一天从头开始"，防"还没睡"惯性延续
        # 2026-09-06 注入卫生（防时间线自污染锁死）：
        # ① 单行化+截断；② "对TA说话"污染（上次状态其实是叮嘱/问候）不回注——曾因此锁死（输出被当输入复读）
        # 2026-09-08 P2·F1 修正：哨兵收窄为"话语型"词（晚安/辛苦了/还没睡…）——
        # 曾把"博士|主人"裸词当哨兵，互动叙事（"在卧室与博士…"）被整句清零→心跳对刚发生的事失忆（R1）
        prev_doing = re.sub(r"\s*\n+", " ", prev_doing)[:60]
        if re.search(r"(晚安|辛苦了|还没睡|还在吗|休息吧|记得|快点|早点)", prev_doing):
            prev_doing = ""
        prev_mood = st.get("mood") or "平静"
        # ---- decide（LLM 自决，参考上次自然接续；失败规则回退）----
        try:
            from plugins import persona, brain as _brain  # top-level 绝对导入

            sp = persona.build_life_system(card or {}) + _LIFE_PROMPT
            # 2026-09-06 场景感知：场景=bot 生活轨迹的一部分（life_state.scene，agent 自决迁移）
            _scn = (st.get("scene") or "").strip()
            # 2026-09-07：人格卡世界观变化 → 场景清零（防"神殿露台/舰船值班室"同日横跳）
            _uni = str((card or {}).get("universe") or "default")
            if st.get("scene_universe") != _uni:
                st["scene"] = ""
                st["scene_universe"] = _uni
            _scn_priv = _brain._scene_is_private(_scn) if _scn else False
            # 2026-09-07 周节律（日期+星期模块）：一周的情绪底色——活人对"今天星期几"是有体感的
            _wday = time.localtime().tm_wday  # 0=周一
            _week_feel = {
                0: "一周的开始，多少有点倦",
                4: "熬过今天就是周末，心里有点松",
                5: "周末，不用管任务的松弛感",
                6: "周末尾巴，说不清是惬意还是有点舍不得",
            }.get(_wday, "")
            user = f"（今天是周{'一二三四五六日'[_wday]} {h} 点"
            if _week_feel:
                user += f"——{_week_feel}"
            if _scn:
                user += f"，你此刻在{_scn}" + ("（独处，没人看着你）" if _scn_priv else "（公开环境）")
            else:
                user += "，你此刻在哪由你自己的生活决定——这条状态里记得带上【场景：两三个字】标记你所在的地方"
            if gap_h >= 0.1:
                # 2026-09-06：给足时间锚点（上次是几点/隔了多久）——agent 判断"还在继续/已结束/跨夜醒"
                _pt = time.localtime(float(st.get("ts") or 0))
                _daylab = "昨晚" if abs(h - _pt.tm_hour) >= 12 else ("今天" if _pt.tm_hour <= h else "昨天")
                user += f"，距上次心跳 {gap_h:.1f} 小时（上次{_daylab} {_pt.tm_hour} 点）"
            # 2026-09-09 用户实测（凌晨5点测试未触发睡眠行为）：原时间锚只有相对值（距上次X小时），
            # 12B 不会自行推算绝对时刻——"深夜是躺下睡觉"的教学因不知道"现在深夜"而失效。补绝对时钟。
            _hlab = "凌晨" if h < 6 else ("早上" if h < 12 else ("下午" if h < 18 else "晚上"))
            user += f"。现在是{_hlab}{h}点"
            if gap_h >= 4:
                # 2026-09-07 梦+日记：长睡眠醒来——昨天的日记连续性 + 梦的残留（都随 agent 自决）
                _diary = _last_diary()
                if _diary:
                    user += f"。睡前你心里过着这一天：{_diary[:80]}"
                user += "（刚睡醒——若梦里残留了什么，可以带一句，随你）"
            if prev_doing:
                user += f"，上次你在：{prev_doing}"
            # 2026-09-08 P2·F2 轨迹记忆（治 R2 循环）：回看最近生活轨迹——她是连续的
            #（"刚才经历过什么"是事实不是任务；她记得——真人不会忘记自己刚从哪来）
            try:
                _trace = _recent_trace(3)
                if _trace:
                    user += f"，你这一段的生活：{_trace}"
            except Exception:  # noqa: BLE001
                pass
            # 2026-09-08 P2·F2 互动痕迹（治 R1 失忆面；盲审：改第一人称对齐日记腔）
            try:
                _it_ts = float(st.get("interaction_ts") or 0)
                _it_who = str((st.get("doing_with") or {}).get("who") or "TA")
                if _it_ts and 0 < time.time() - _it_ts < 7200:
                    user += f"，{max(1, int((time.time() - _it_ts) // 60))} 分钟前我刚结束一段相处"
                    user += f"（和{_it_who}之间的事是真实的经历，会留下痕迹——不凭空抹掉）"
            except Exception:  # noqa: BLE001
                pass
            # 2026-09-06 日计划：今天的打算（按天生命周期，跨天作废）——agent 自决消化（做/没做/改主意）
            _today = time.strftime("%Y-%m-%d")
            _plan = (st.get("plan") or "") if st.get("plan_day") == _today else ""
            if _plan and not _plan_ok(_plan):
                _plan = ""  # 2026-09-08：占位计划（"还没开始"类）不进提示回注
            if _plan and re.search(r"(已完成|做完了|完成了|收尾)", _plan):
                _plan = ""  # 2026-09-08 P2·F4：计划完成态不再牵引——真实的人做完就放下了
                #（"（已完成）"曾把心跳拽回旧任务续写：勾选/合上/撕茶罐全在已完计划的尾巴上）
            if _plan:
                user += f"，你今天原本打算：{_plan}（做完了/做了一半/改主意了都自然，你说了算）"
            # 2026-09-05：事务融入生活轨迹——你心里记挂的事（接纳后的约定）作为生活的一部分，
            # 心跳自决时自然体现在生活状态里（惦记/做着/想着）——生活轨迹（life_log）随之记载
            try:
                _pmt = _brain._pending_mention_text(_brain._owner_id() or "")
                if _pmt:
                    user += "；" + _pmt.replace("\n\n【你的约定】", "【心里记挂着】").strip()[:160]
            except Exception:  # noqa: BLE001
                pass
            # 2026-09-08 P2·F5 状态底色（治 R4 割裂）：心情是真实的底色——只传事实，不映射行为
            #（表不表露、怎么表露、口是心非与否，按人设与现场自决——心是什么样的就是什么样）
            user += f"，此刻你心里（底色）：{prev_mood}"
            user += "）"
            doing = await llm.complete(
                system=sp, user=user,
                # 2026-09-11：60 token≈70 汉字，"两三短句"经常在此拦腰截断（life_state 存半句，
                # 启动器主页/状态页双双显示半句）——放开到 120 让句子自然收尾；长度感仍由提示层承担
                max_tokens=120, temperature=0.9,
            )
            doing = doing.strip().strip("\"'")  # 2026-09-06 存储卫生
            # 2026-09-07 修复：只单行化，截断延后——先解析自决标记（原【30分钟】被 120 字截断拦腰砍断的根因）
            doing = re.sub(r"[\s\n]+", " ", doing).strip()
        except Exception:  # noqa: BLE001
            doing = ""
        # 2026-09-07：LLM 失败不再用规则文本冒充生活（"在忙些琐碎的事"曾一字不差连写 4 次入轨迹）——
        # 人在没新事件时状态不变化，比机器编一句真实；本次心跳空转，循环按既有节奏重试
        if not doing:
            logger.warning("lifesim tick: llm failed, keep last state (no machine fallback)")
            # 2026-09-10：醒点已过仍账面"睡着" → 清账+节奏回落缺省，防 LLM 故障期一觉睡到下午；
            # 磁盘状态在 LLM 等待窗口内被写过（介入/微更新）则不抢写（对齐 save 前并发守卫口径）
            if float(st.get("sleep_until") or 0) and not _is_night_sleeping(st) \
                    and abs(float(_load_state().get("ts") or 0) - float(st.get("ts") or 0)) < 1e-6:
                st.pop("sleep_until", None)
                st["wake_min"] = LIFE_TICK_SECS // 60
                _save_state(st)
                logger.info("lifesim tick: expired night sleep cleared on llm-fail (wake rhythm reset)")
            return st
        # 2026-09-06 节奏自决：解析【N 分钟后】（容错：漏闭合、裸数字都认）→ 下次唤醒间隔（没给=默认 30 分钟）
        _wm3 = re.search(r"【\s*(\d{1,3})\s*分钟(?:后)?】?", doing)
        if not _wm3:
            # C9：裸数字「N 分钟」限睡眠语境——"看了 30 分钟书"曾被误读成 30 分钟后的唤醒节奏
            _bm = re.search(r"(?:^|\s)(\d{1,3})\s*分钟(?:后)?(?:\s|$)", doing)
            if _bm and _SLEEP_CTX_RE.search(doing):
                _wm3 = _bm
        _wake_min = max(LIFE_WAKE_MIN_MIN, min(LIFE_WAKE_MIN_MAX, int(_wm3.group(1)))) if _wm3 else None
        if _wake_min is None and 8 <= time.localtime().tm_hour < 23:
            _wake_min = 20  # 2026-09-07 清醒时段默认也压到 20 分钟（原默认 30 太钝）——仅"未自决时"的默认值
        # 2026-09-08 盲审+实测：撤销清醒时段 min(_,20) 钳制——她自决的节奏（午睡【50 分钟后】等）
        # 曾被强制 20 分钟打断（"午睡被心跳切换动作"事故的节奏侧根因）；节奏观由提示层教学承担
        #（清醒 10-20 分钟很常见 / 睡眠给足时长），机器不再压她的自决。
        doing = re.sub(r"【\s*\d{1,3}\s*分钟(?:后)?】?", "", doing).strip()
        # 2026-09-07 心情自决：agent 用【心情：…】声明心情（机器只读不漂移；无标记才用漂移兜底）
        # B9：解析上限 8→16（正常心情词 2-6 字，16 已宽容）；剥除正则不设短上限且 】 可选
        # （【心情：超长残片 未闭合时解析失败也要剥得掉——残留会回喂下一轮=时间线自污染）
        _mood_m = re.search(r"【心情[：:]\s*([^】]{1,16})\s*】", doing)
        _mood_set = _mood_m.group(1).strip() if _mood_m else None
        if _mood_m:
            doing = re.sub(r"\s*【心情[：:][^】]{1,32}】?", "", doing).strip()
        # 2026-09-06 日计划：解析【计划：…】（容错：漏闭合】→ 行尾也认）→ 当天计划（agent 模块）
        # 2026-09-07 计划完结自决：agent 输出【计划完成】→ 清掉当天主线（做完了/不想做了都行）
        # 2026-09-07：正则放宽（曾只认紧凑【计划完成】，模型变体写法残片「…。 完成」漏剥入轨迹被下轮回注）
        # C9：计划/心得冒号统一认全半角（[：:]）——模型偶发半角「计划:」曾整段漏剥入轨迹
        _plan_done = bool(re.search(r"【?\s*计划\s*完成\s*】?", doing))
        if _plan_done:
            doing = re.sub(r"【?\s*计划\s*完成\s*】?", "", doing).strip()
            doing = re.sub(r"[。！？\s]完成\s*$", "", doing).strip()  # 行尾孤立「完成」残片兜底
        _pm = re.search(r"【计划[：:]\s*(.{1,40}?)(】|$)", doing)
        _plan_new = _pm.group(1).strip() if _pm else None
        if _plan_new and not _plan_ok(_plan_new):
            _plan_new = None  # 2026-09-08：占位话（"还没开始/暂无"）不算计划——没计划就该不写标记
        doing = re.sub(r"【计划[：:][^】\n]{1,40}】?", "", doing).strip()
        # 2026-09-06 反思沉淀：解析【心得：…】（同上容错）→ 概念性经验 → 程序记忆（agent 层，下轮感知注入）
        _ins = re.search(r"【心得[：:]\s*(.{1,60}?)(】|$)", doing)
        _insight = _ins.group(1).strip() if _ins else None
        doing = re.sub(r"【心得[：:][^】\n]{1,60}】?", "", doing).strip()
        # 2026-09-05：生活自决——"想找TA"标记（2026-09-06 容错：模型漏【】/放句中/括号包 → 全认）
        _want = bool(re.search(r"【?\s*想找TA\s*】?", doing))
        doing = re.sub(r"[\s（(【]*想找TA[\s）)】]*", "", doing).strip()
        # ---- 场景自决迁移（2026-09-07：叙述里去了哪，场景就跟到哪——单一存储 life_state.scene）----
        # B9：解析上限 12→16；剥除同心情（{1,32}】?）
        _scm = re.search(r"【场景[：:]\s*([^】]{1,16})\s*】", doing)
        _scene_new = _scm.group(1).strip() if _scm else None
        if _scm:
            doing = re.sub(r"\s*【场景[：:][^】]{1,32}】?", "", doing).strip()
        # 2026-09-08 承诺闭环：心跳生活叙事里兑现了她自许的约定 → 清账
        #（"算不算兑现"由她的叙事自决——机器只在她声明完成时记账清零。
        #  盲审修正：解析必须在 st["doing"] 赋值之前——曾放赋值后，剥标只改局部、标记残留 state 回喂；
        #  方括号必选（与对话侧一致）——曾全可选，裸叙事"把约定完成了一大半"会误清真承诺）
        if re.search(r"【\s*约定\s*完成\s*】", doing):
            doing = re.sub(r"\s*【\s*约定\s*完成\s*】", "", doing).strip()
            try:
                from plugins import brain as _brain_pc

                # 只替「心跳看得见的那个人」（owner）清账——无 uid 会清到全局最新、可能错清别人的
                _done_c = _brain_pc._complete_bot_promise(_brain_pc._owner_id() or "")
                if _done_c:
                    logger.info("lifesim: promise fulfilled via tick: {}", _done_c[:40])
            except Exception:  # noqa: BLE001
                pass
        # C9：未闭合/超长残留标记兜底剥除（【生活/场景/心情/清醒】解析失败也剥得掉——
        # 残留回喂下一轮=时间线自污染；】可选故对"漏闭合"同样生效）
        doing = re.sub(r"【(?:生活|场景|心情|清醒)\s*[：:]?[^】]{0,32}】?", "", doing).strip()
        doing = _cut_sentence(doing, 120)  # 2026-09-07：按句子截断（标记解析之后；硬切会拦腰断句）

        # ---- act ----
        # 2026-09-07 心情新鲜期：距 mood_ts < 1 小时 → 不漂移（挑逗/事件后的心情保持一段真实感，
        # 曾每 20 分钟向懒散/平静池漂移，挑逗后几分钟就被稀释回"懒散"）。mood_ts 缺失=旧数据，
        # 取 now 起步并把当前值视作"刚出现"（下次心跳从漂移正常演化）。
        _mood_fresh = False
        _pm_ts = float(st.get("mood_ts") or 0)
        if not _pm_ts:
            st["mood_ts"] = now
            _mood_fresh = True
        elif now - _pm_ts < 3600:
            _mood_fresh = True
        mood = _mood_set if _mood_set else (prev_mood if _mood_fresh else _mood_drift(prev_mood))
        if not _mood_set and mood != prev_mood:
            st["mood_ts"] = now  # 漂移真正改变了心情 → 记时（进入新一轮新鲜期）
        st = {**st, "ts": now, "doing": doing, "mood": mood, "want_talk": _want}
        if _mood_set:
            # 2026-09-07：心跳自标同样进新鲜期（曾只更新 mood_source——自标心情 20 分钟后即被漂移覆盖；
            # 2026-09-08 深夜对话通道已退役，现渠道=心跳 tick + 提取 sync_mood）
            st["mood_ts"] = now
            st["mood_source"] = "agent:tick"
        if _scene_new:
            st["scene"] = _scene_new[:12]
        if _wake_min is not None:
            st["wake_min"] = _wake_min
        else:
            st.pop("wake_min", None)
        # ---- 2026-09-10 夜间睡眠账本：入睡叙事+深夜窗 → sleep_until（时长地板睡到清晨窗；
        #      模型自决更长（≤600）则从自决——机器只托底不压自决；白天午睡完全不设账）----
        if _SLEEP_CTX_RE.search(doing):
            _floor = _night_sleep_floor_min(now)
            if _floor is not None:
                if _wake_min is None or _wake_min < _floor:
                    logger.info("lifesim: night sleep floor engaged: wake_min {} -> {:.0f} min (睡到清晨窗)",
                                _wake_min, _floor)
                    _wake_min = int(min(_floor, LIFE_WAKE_MIN_MAX))
                    st["wake_min"] = _wake_min
                st["sleep_until"] = now + _wake_min * 60
            else:
                st.pop("sleep_until", None)  # 白天午睡/已在清晨窗：不设夜间账
        elif st.pop("sleep_until", None):
            logger.info("lifesim: night sleep account cleared (醒叙事)")
        # 日计划（跨天作废；今天有旧计划则继续消化，新计划覆盖）
        # 2026-09-08：存量占位计划自愈（上一版曾把"还没开始"存成真计划——读到即清，等想好了再加）
        if st.get("plan") and not _plan_ok(str(st.get("plan"))):
            st.pop("plan", None)
            st.pop("plan_day", None)
            logger.info("lifesim tick: placeholder plan cleaned (还没开始类占位)")
        _today_s = time.strftime("%Y-%m-%d")
        if _plan_done:
            st.pop("plan", None)
            st.pop("plan_day", None)
        if _plan_new:
            st["plan"] = _plan_new
            st["plan_day"] = _today_s
        elif st.get("plan_day") != _today_s:
            st.pop("plan", None)
            st.pop("plan_day", None)
        # 反思沉淀（agent 层）：心得 → 程序记忆（agent_memory "insight"，按卡隔离）→ 感知注入对话
        if _insight:
            try:
                from . import tools as _tools
                _tools.remember("insight", _insight, persona=str((card or {}).get("name") or ""))
                logger.info("lifesim insight saved: {!r}", _insight[:40])
            except Exception:  # noqa: BLE001
                pass
        # 2026-09-07 并发守卫（save 前）：LLM 等待窗口（秒级）内对话介入/微更新/心情自标可能已写状态——
        # 旧快照整体覆写会冲掉 interaction_ts/doing（"介入存续让路"被心跳冲掉的根因）。
        # 介入落地 → 本次独处叙述对正在对话的处境是错的，心跳整体作废（保留介入现场）；
        # 仅心情更新 → 心跳心情若只是漂移则让给更新的自标心情（agent 在心跳里显式【心情：…】则心跳优先）。
        _fresh = _load_state()
        if float(_fresh.get("interaction_ts") or 0) > _start_ts:
            logger.info("lifesim tick: interaction landed mid-tick, tick discarded (让路)")
            return _fresh
        # B6（2026-09-09 审计）：另一 tick 已落盘检测——启动恢复+循环双心跳/抖动重叠时，
        # 后到的旧快照整体覆写会回滚时间线（新状态被旧状态覆盖）；_start_ts 之后有人写过 ts → 本次作废
        if float(_fresh.get("ts") or 0) > _start_ts:
            logger.info("lifesim tick: newer tick state landed mid-flight, tick discarded (并发双心跳)")
            return _fresh
        if float(_fresh.get("mood_ts") or 0) > _start_ts and not _mood_set:
            for _mk in ("mood", "mood_reason", "mood_source", "mood_ts"):
                if _mk in _fresh:
                    st[_mk] = _fresh[_mk]
        # 2026-09-11 热修九：场景跟随叙事（治场景冻结——夜间私场景声明后字段整天不动，
        # 启动器群聊静默门按过期私场景把 @ 全部拦下）。doing 命中世界观场景库词表时同步
        # scene 字段：命中公开词→公开场所、私密词→私密场所；都不命中→保留原场景（不虚构）。
        # agent 显式【场景：…】声明（_scene_new）仍最优先，已在上方先行应用。
        try:
            from plugins import brain as _brain_sc
            _uni_sc = str((card or {}).get("universe") or "default")
            _sdb = _brain_sc._scene_db() or {}
            _g = _sdb.get(_uni_sc) or _sdb.get("default") or {}
            _hit = None
            for _w in (_g.get("public") or []):
                if _w and _w in doing:
                    _hit = _w
                    break
            if not _hit:
                for _w in (_g.get("private") or []):
                    if _w and _w in doing:
                        _hit = _w
                        break
            if _hit and st.get("scene") != _hit:
                logger.info("lifesim scene follow: {} -> {}", st.get("scene"), _hit)
                st["scene"] = _hit
        except Exception:
            pass  # 场景跟随是尽力而为：读不到场景词库就跳过本轮跟随，不打断 tick
        _save_state(st)
        _log_event(now, doing, mood)
        return st
    except Exception as e:  # noqa: BLE001
        logger.warning("lifesim tick failed: {} [{}]", e, type(e).__name__)  # B7：失败不再静默
        return _load_state()


async def lifesim_loop():
    """后台心跳：**启动即恢复时间线**（看记录→计算时间差→当前在干什么），之后按 bot 自决节奏转。
    （2026-09-05：与用户活跃度解耦——bot 的生活是它自己的，用户不在线也照常；用主人的公共人设卡驱动）。
    幂等、失败静默。"""
    # 启动恢复：立即一次 tick（重启后 bot 不用等 30 分钟——先知道自己"醒在什么时候、在干什么"）
    # 2026-09-06：距上次心跳 <5 分钟的频繁重启不重复恢复（免"重启→立即 tick→复制当前状态"）
    try:
        _st0 = _load_state()
        if time.time() - float(_st0.get("ts") or 0) >= 300:
            from plugins import brain  # top-level 绝对导入

            await lifesim_tick(card=brain._persona_card())
            logger.info("lifesim: startup resume tick done")
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("lifesim startup resume failed: {} [{}]", e, type(e).__name__)  # B7：曾裸吞
    while True:
        try:
            _st = _load_state()
            _wmin = float(_st.get("wake_min", LIFE_TICK_SECS / 60) or LIFE_TICK_SECS / 60)
            # 2026-09-07：±10% 抖动（曾固定间隔整点跳——「心跳节拍器」是机械感来源；自决节奏仍为主导）
            _sleep_s = min(LIFE_WAKE_MIN_MAX, max(LIFE_WAKE_MIN_MIN, _wmin)) * 60 * random.uniform(0.9, 1.1)
            await asyncio.sleep(_sleep_s)
        except Exception:  # noqa: BLE001
            await asyncio.sleep(LIFE_TICK_SECS)
        try:
            from plugins import brain  # top-level 绝对导入

            card = brain._persona_card()  # 不传 user_id = 主人卡（bot 公共形象）
            await lifesim_tick(card=card)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("lifesim loop tick error: {} [{}]", e, type(e).__name__)  # B7：曾裸吞


def life_text(group_safe: bool = False) -> str:
    """对话注入：当前状态 + 久别时附「这段时间的生活」摘要。
    2026-09-08：清死参数 user_id——生活状态全局单份（bot 只有一个身体），函数体从未用到。
    2026-09-08 P2 盲审修正（D1）：group_safe=True（群聊）时对生活叙事做敏感收敛——
    复用 memory._fact_is_sensitive 的敏感提示词（项目既有群聊红线），命中则降级为抽象描述，
    防心跳私密叙事经此入口串进群聊/任意对话。"""
    try:
        st = _load_state()
        now = time.time()
        # 2026-09-07 修复：久别判定/摘要基准改「距上次真实互动」（interaction_ts）——
        # 曾用 state.ts 双重死：①私聊回复前事件唤醒 tick 先刷 ts → gap 恒 <2h，久别分支几乎不可达；
        # ②日志过滤基准 now-2h 与「状态已 2h 未更新」矛盾（日志与 ts 同步写）→ 摘要恒空（从未输出过内容）
        _last_contact = float(st.get("interaction_ts") or 0) or float(st.get("ts", 0) or 0)
        gap = now - _last_contact
        doing = st.get("doing") or ""
        mood = st.get("mood") or "平静"
        if not doing:
            return ""
        _plan_txt = st.get("plan") or ""
        if st.get("plan_day") != time.strftime("%Y-%m-%d"):
            _plan_txt = ""
        if _plan_txt and not _plan_ok(_plan_txt):
            _plan_txt = ""  # 2026-09-08：占位计划（"还没开始"类）不注入对话
        _plan_note = f"（今天打算：{_plan_txt}）" if _plan_txt else ""
        # 2026-09-07：日常底色定性——防止模型把生活琐事读成"工作量"而对 TA 说忙
        # 2026-09-08 裁决对齐：对话事件=刺激（可反应：冲过去/吃醋/不理），状态只由她自己改；
        # 底色不因对话杂音被推翻，反应与处境分开（盲审对齐：刺激限定"演出/剧情元素"，别抑制真实互动后的合法标记）
        _base_note = ("（这些是你的日常底色，随时可以放下一边陪TA——不算忙；"
                      "对话里的演出/剧情元素只是此刻的刺激——怎么反应由你现场判断，"
                      "你的处境也只有你自己说了算）")
        # 2026-09-08 P2 盲审修正（D1）：群聊敏感收敛——生活叙事命中敏感提示词 → 降级为抽象（私密细节不进公开场合）
        if group_safe:
            doing = _life_sanitize(doing)
        if gap < LIFE_GAP_RECALL_SECS:
            return f"【此刻的你】正在{doing}，心情{mood}。{_plan_note}{_base_note}"
        # 久别：从日志取「上次互动之后」的生活片段（最多 3 条；严格大于排除当前状态那条）
        try:
            lines = LIFE_LOG_FILE.read_text(encoding="utf-8-sig").strip().splitlines()
        except OSError:
            lines = []
        events = []
        for ln in reversed(lines):
            try:
                e = json.loads(ln)
                if float(e.get("ts", 0) or 0) > _last_contact:
                    _ev = e.get("doing", "")
                    events.append(_life_sanitize(_ev) if group_safe else _ev)
                    if len(events) >= 3:
                        break
            except (json.JSONDecodeError, ValueError):
                continue
        extra = "（这段时间你：" + "；".join(events) + "）" if events else ""
        return f"【此刻的你】正在{doing}，心情{mood}。{extra}{_base_note}"
    except Exception:  # noqa: BLE001
        return ""


def _life_sanitize(text: str) -> str:
    """群聊敏感收敛（D1）：命中项目既有敏感提示词（memory._fact_is_sensitive 同源）→ 降级为抽象。
    仅用于公开场合注入；私聊/心跳内部原样。"""
    try:
        from plugins import memory as _memo

        if _memo._fact_is_sensitive({"key": "t", "value": str(text or "")}):
            return "一段属于自己的时光"
    except Exception:  # noqa: BLE001
        pass
    return str(text or "")


DIARY_FILE = DATA_DIR / "life_diary.jsonl"


def _last_diary() -> str:
    """最近一篇生活日记（晨间心跳连续性用）；无则空串。"""
    try:
        lines = DIARY_FILE.read_text(encoding="utf-8-sig").strip().splitlines()
        if lines:
            return str(json.loads(lines[-1]).get("text") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _diary_for(date_str: str) -> str:
    try:
        for ln in DIARY_FILE.read_text(encoding="utf-8-sig").strip().splitlines():
            e = json.loads(ln)
            if e.get("date") == date_str:
                return str(e.get("text") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


async def write_daily_diary(date_str: str, force: bool = False) -> str:
    """把某天的生活轨迹汇成第一人称日记（LLM 提炼自真实轨迹，不虚构；已存在则跳过）。
    由每日复盘循环（≥10 点首个整点刻）触发。返回日记文本（空=未生成）。"""
    if not force:
        _had = _diary_for(date_str)
        if _had:
            return _had
    try:
        import datetime as _dt
        from plugins import persona as _persona

        entries = []
        for ln in LIFE_LOG_FILE.read_text(encoding="utf-8-sig").strip().splitlines():
            try:
                e = json.loads(ln)
                _t = float(e["ts"])
                if _dt.datetime.fromtimestamp(_t).strftime("%Y-%m-%d") == date_str and (e.get("doing") or "").strip():
                    entries.append(_dt.datetime.fromtimestamp(_t).strftime("%H:%M") + " " + e["doing"].strip())
            except Exception:  # noqa: BLE001
                continue
        if len(entries) < 3:
            return ""
        # 2026-09-07 修复：_persona_card 不在本模块——用当前主人卡（与 lifesim_loop 同源）
        from plugins import brain as _brain

        card = _brain._persona_card() or _persona.load_persona(_brain._persona_name() or "default")
        sp = _persona.build_life_system(card or {})
        prompt = (
            "以下是今天的生活轨迹碎片（时间 + 你在做的事）。把它们写成你自己的日记："
            "第一人称、三四句、口吻自然，可以有心绪和取舍，但**不要虚构轨迹里没有发生过的事**。\n"
            + "\n".join(entries[:20])
        )
        text = (await llm.complete(
            system=sp + "\n\n【日记】只输出日记本身，不加标题、不加引号。",
            user=prompt, max_tokens=240, temperature=0.8,
        ) or "").strip()
        if len(text) < 10:
            return ""
        DIARY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DIARY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"date": date_str, "text": text}, ensure_ascii=False) + "\n")
        from loguru import logger

        logger.info("life diary written: date={} len={}", date_str, len(text))
        return text
    except Exception as e:  # noqa: BLE001
        from loguru import logger

        logger.warning("life diary failed: {}", e)
        return ""


# 2026-09-07 微更新（对话事件驱动）：私聊每轮结束后后台比对"对话 vs 当前状态"，
# 有真实处境变化立即写入（不阻塞回复；每用户 45s 节流 + 在-flight 防重）
_MICRO_INFLIGHT: set = set()
_MICRO_LAST: dict = {}


async def micro_update_from_conversation(user_id: str, user_text: str, reply_text: str,
                                         allow: bool = True, who: str = "TA", max_vibe: int = 3,
                                         card: str = "default"):
    # 2026-09-11 热修十二 F4：card=当前人设卡键——下方 vibe 微更新的 active/set_state/clear
    # 全部落当前卡桶（曾恒写 default 桶，与主链按卡读写脱节——热修十分桶遗漏的调用点）
    key = str(user_id)
    now = time.time()
    if not allow:
        return  # 2026-09-07 层级：普通用户不介入主生活状态（其相处在对话记忆/画像层）
    # 2026-09-07 触发权分级：max_vibe=本对话者允许的最深氛围档（3=管理员无限制；2=白名单限次深档；
    # 0=不允许——普通用户已被 allow 挡住，此处防御性兜底）
    _max_tier = max(0, min(3, int(max_vibe)))
    if key in _MICRO_INFLIGHT or now - _MICRO_LAST.get(key, 0) < 45:
        return
    _MICRO_INFLIGHT.add(key)
    try:
        st = _load_state()
        cur = f"在{st.get('scene') or '（未定）'}，{st.get('doing') or '（未定）'}"
        prompt = (
            f"下面是你【说话前】的状态（可能已过时）：{cur}\n"
            f"刚才和 {who} 的对话：{who} 说「{user_text[:80]}」，你答「{reply_text[:80]}」。\n"
            "判断这段对话之后：\n"
            "① 你的处境（位置/正在做的事）变成了什么？\n"
            "· 有移动或新事件（被拉去某处/一起做了什么/手头的事变了）→ "
            '输出新处境（一句话，带上和谁/在哪/做什么，对方用「' + who + '」指代）：'
            '{"doing":"例如 在天台和' + who + '看星星","scene":"天台"}\n'
            "· 纯聊天、处境没变 → doing/scene 输出空字符串\n"
            "② 氛围（vibe）：按对话【双方】——TA 说的和你答的——如实判断，四选一："
            "无 / 暧昧 / 亲密 / 情欲（注意你自己的回答也算数：如果你答得暧昧或越界，氛围就是升级了）\n"
            '输出严格 JSON：{"doing":"…","scene":"…","vibe":"无|暧昧|亲密|情欲"}，无变化则各字段留空或输出 {}\n'
            "只输出 JSON，不输出其他文字。"
        )
        raw = (await llm.complete(
            system="你是状态记录器，只输出 JSON，不输出其他任何文字。",
            user=prompt, max_tokens=120, temperature=0.1,
        ) or "").strip()
        i, j = raw.find("{"), raw.rfind("}")
        if i < 0 or j <= i:
            # B8：失败同样写节流账（微更新失败曾不记账 → 同会话每 45s 窗口一过就重打一次失败的 LLM）
            _MICRO_LAST[key] = now
            logger.debug("life micro-update: unparseable llm output, throttled: user={} raw={!r}", key, raw[:60])
            return
        d = json.loads(raw[i:j + 1])
        nd = str(d.get("doing") or "").strip()[:120]
        ns = str(d.get("scene") or "").strip()[:12]
        _vibe = str(d.get("vibe") or "").strip()[:6]
        # 2026-09-07 氛围维度（用户：bot 自己的回答也会改变状态——你的回答算数）
        # 2026-09-07 触发权分级：档位超对话者上限 → 钳到上限档（白名单「可以挑逗但不能最深入」）
        try:
            from core import special as _sp

            if _vibe and _vibe != "无":
                _tier = _sp.vibe_tier(_vibe)
                # 2026-09-10 审计 P2：未识别自由词按最深档处理再钳（镜像 core/special.apply_agent_markers
                # 同口径 `_decl if _decl > 0 else 3`）——否则 tier=0 时 `0 > _max_tier` 恒假，
                # 自由词原样登记绕过白名单上限
                _tier = _tier if _tier > 0 else len(_sp.VIBE_TIERS)
                if _tier > _max_tier:
                    _vibe = _sp.VIBE_TIERS[_max_tier - 1] if _max_tier > 0 else ""
                if _vibe:
                    if _sp.active(card=card).get("vibe_mood", {}).get("note") != _vibe:
                        _sp.set_state("vibe_mood", _vibe, by="priv:" + key, card=card)
                elif _sp.active(card=card).get("vibe_mood"):
                    _sp.clear("vibe_mood", card)
            elif _sp.active(card=card).get("vibe_mood"):
                _sp.clear("vibe_mood", card)
        except Exception:  # noqa: BLE001
            pass
        if not nd and not ns:
            _MICRO_LAST[key] = now
            return
        st_now = _load_state()  # 2026-09-07：LLM 等待窗口内状态可能已被心跳/介入更新——用新值比较（旧快照会误判/漏判重复）
        if nd == st_now.get("doing") and ns == st_now.get("scene"):
            _MICRO_LAST[key] = now
            return
        apply_interaction(doing=nd, scene=ns, by="priv:" + key, with_qq=key, who=who)
        _MICRO_LAST[key] = now
        from loguru import logger

        logger.info("life micro-update: user={} doing={!r} scene={!r} vibe={!r}", key, nd, ns, _vibe)
    except Exception as e:  # noqa: BLE001
        # B8：失败同样节流+留 debug 日志（曾裸吞——LLM 故障期同一会话反复空跑微更新）
        _MICRO_LAST[key] = now
        logger.debug("life micro-update failed: {} [{}] user={}", e, type(e).__name__, key)
    finally:
        _MICRO_INFLIGHT.discard(key)


def apply_interaction(doing: str = "", scene: str = "", by: str = "", with_qq: str = "", who: str = "TA") -> bool:
    """对话介入生活（2026-09-07）：TA 的互动真实改变了处境/位置——由 agent 以【生活：…】【场景：…】
    标记声明，机器只写入单一状态源。写入后，下一次心跳会以此为基础自然接续（上次你在：…）。
    2026-09-07 身份锚：doing_with={qq,who}——QQ 是唯一凭证（人设称呼会重叠，QQ 不会）。"""
    doing = (doing or "").strip()[:120]
    scene = (scene or "").strip()[:12]
    if not doing and not scene:
        return False
    st = _load_state()
    # ---- 2026-09-10 夜间睡眠保护：睡着时对话不落状态——夜聊照常（对话层不受影响，回复仍在），
    # 但普通消息不再把整段夜间睡眠终结成"准备休息/醒来"（昨晚 01:01 晚安消息即走的这条路径）。
    # 行为设计变化：主人消息同样不唤醒（待用户确认；若要"主人可唤醒"，在此按身份分层放行即可）。
    if _is_night_sleeping(st):
        # 2026-09-10 用户裁决：睡眠中管理员可触发唤醒——主人私聊消息放行并清睡眠账
        # （她能感知"被叫醒"：本轮感知仍带入睡注记+用户唤醒消息，叙事自然衔接；
        #   doing 由本次 interaction 承载）；其余身份维持不唤醒。
        _admin_wake = False
        try:
            from plugins.debug import SUPERUSERS as _su

            _admin_wake = str(by or "") in {str(x) for x in _su}
        except Exception:  # noqa: BLE001
            pass
        if not _admin_wake:
            logger.info("lifesim: interaction during night sleep rejected (普通消息不唤醒): by={} doing={!r}",
                        by or "unknown", doing[:40])
            return False
        st.pop("sleep_until", None)
        logger.info("lifesim: admin wake during night sleep: by={} doing={!r}", by or "unknown", doing[:40])
    if doing:
        st["doing"] = doing
    if scene:
        st["scene"] = scene
    st["ts"] = time.time()
    st["interaction_ts"] = st["ts"]  # 2026-09-07 介入存续：20 分钟内心跳让路（不重叙独处）
    st["interaction_by"] = by or "unknown"
    if with_qq:
        st["doing_with"] = {"qq": str(with_qq), "who": (who or "TA")[:12]}
    _save_state(st)
    # 2026-09-07 介入入轨迹：life_log 记录"和某人做了什么"（久别注入/日记的素材源）
    try:
        LIFE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LIFE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": st["ts"], "doing": doing or f"在{scene}", "mood": st.get("mood", "平静")}, ensure_ascii=False) + "\n")
    except OSError as e:
        # 与上方 life_log 追加处同口径：只丢这一条轨迹，但**留痕**。
        # 2026-09-12 审计补齐——同一文件里两处同样的追加，此前只修了一处（不一致比缺陷更坏）。
        logger.debug("life log append failed (apply_interaction): {} [{}]", e, type(e).__name__)
    return True
