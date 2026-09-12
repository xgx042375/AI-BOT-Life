"""大脑层插件：QQ 消息 -> 人格(可切换)+记忆 -> 本地 LLM -> 回复 -> 记忆回写。

人格状态机：角色卡可定义 alt_persona（关键词触发切换，如"苏醒吧"），
按用户独立持久化模式（data/persona_mode.json），bot 重启不丢失。
人格头像联动：切换模式时自动调用 NapCat set_qq_avatar 应用对应头像
（data/avatars/ 下的文件），并用 .applied 记录避免 QQ 头像接口限流。
"""
import asyncio
import json
import random
import re
import time
from pathlib import Path

from nonebot import get_driver, on_message, on_notice
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.log import logger as nb_logger

import httpx  # noqa: E402  # 生成底层 logit_bias 的 tokenize 调用

from .. import correction, emotion, fiction, memory, persona, search, sticker, voice


class _LoguruCompat:
    """loguru 兼容包装：支持 %s/%.0f 等 % 风格格式化（loguru 本身只认 {} 格式）。"""

    def __init__(self):
        self._lg = nb_logger.opt(colors=False)

    @staticmethod
    def _fmt(msg, args):
        if not args:
            return msg
        try:
            return msg % args
        except Exception:  # noqa: BLE001
            return f"{msg} | {args}"

    def info(self, msg, *args):
        self._lg.info(self._fmt(msg, args))

    def warning(self, msg, *args):
        self._lg.warning(self._fmt(msg, args))

    def debug(self, msg, *args):
        self._lg.debug(self._fmt(msg, args))

    def error(self, msg, *args):
        self._lg.error(self._fmt(msg, args))

    def exception(self, msg, *args):
        self._lg.exception(self._fmt(msg, args))


logger = _LoguruCompat()  # %s 风格兼容的 loguru（brain 现有日志全量进 bot.log）

# 写作意图容器（每会话一轮：stage1 提取 → 发送段消费；单线程事件循环安全）
_WRITE_INTENTS: dict[str, tuple | None] = {}
# 2026-09-08 活人感#1：stage2 自标【基调】登记（本条回复情绪基调，语音语气/配图情绪同源消费）
_TONE_LAST: dict[str, str] = {}
# 热修十三 B2（2026-09-12）：stage2 自标【立绘：词】登记（独立于基调的立绘差分自决位，
# webgal sprite 帧消费：_SPRITE_LAST 非空优先，空则回退 _TONE_LAST 基调链；清理时机与基调同款）
_SPRITE_LAST: dict[str, str] = {}
# 写作参考容器（本轮搜索/联网结果 → 写作引擎参考注入；仅 WRITE 触发时消费）
_WRITE_REFS: dict[str, str] = {}


def _regex_write_intent(text: str) -> tuple[str, str] | None:
    """写作意图正则兜底（模型漏判时用；创作域词，误报率低）。
    返回 (action, param)。优先级：outline > continue > start。
    """
    t = text.strip()
    if not t:
        return None
    # 0) 导出/发文件（最高优先：防止模型把"完整版"判成 show/outline 而错过文件发送，2026-08-27）
    # 2026-09-06 Phase E：降级为只读漏标检测——域词收窄（去掉裸"文/篇"单字，防普通请求误报提醒）
    _export_word = r"(?:完整版|完整文件|完整版本|全文|全集|全本|整本|全书|完整)"
    _export_verb = r"(?:发(?:给)?我|发出来|发上来|发过来|发来|发文件|发送|发一下|发下|发到?(?:群|群里|聊天|这里)?)"
    if re.search(f"{_export_word}.{{0,12}}{_export_verb}|{_export_verb}.{{0,12}}{_export_word}|导出|发全书", t) \
            and (re.search(r"小说|书|章|集|《|同人|短篇|大纲", t) or fiction._find_book_in_text(t)):
        return ("send", t)
    # 1) 修改大纲/梗概（最高优先）：改/重写/删/去/清理/换名 + 大纲|梗概
    if re.search(r"(?:改|重写|调整|优化|换|改名|删|去掉|清理|删掉).{0,4}(?:大纲|梗概)|(?:大纲|梗概).{0,10}(?:改|重写|调整|优化|换|改名|删|去掉|清理|删掉|不要)", t):
        # param：去掉"删掉X字样"类指令里的动作词头，保留要求
        return ("outline", re.sub(r"^(?:把|将)?(?:从|在|里面|里)?", "", re.sub(r"(?:删掉|删除|去掉|清理|不要|改成|改为)", "", t)).strip() or t)
    # 1b) 删掉/去掉 X字样/名字（未带"大纲"字样 → 也是修改梗概）；含反向句式"X字样也去掉"
    if re.search(r"(?:删掉|去掉|删除|不要|换掉|清除|清掉).{0,8}(?:字样|字眼|名字|名称|文字|称号)", t) \
            or re.search(r"(?:字样|字眼|名字|名称|文字|称号).{0,8}(?:删掉|去掉|删除|不要|换掉|清掉)", t):
        return ("outline", t)
    # 2) 删除章节：第N章删掉/删除第N章/去掉第N章（章/回/节/卷）
    m = re.search(r"(?:把|将|给)?(?:第\s*)?([0-9一二三四五六七八九十]+)\s*[章回节卷]\s*(?:删掉|删除|去掉|不要了|删了)", t)
    if m:
        return ("delete", m.group(1))
    if re.search(r"(?:删掉|删除|去掉).{0,4}(?:第\s*)?([0-9一二三四五六七八九十]+)\s*[章回节卷]", t):
        m2 = re.search(r"([0-9一二三四五六七八九十]+)\s*[章回节卷]", t)
        if m2:
            return ("delete", m2.group(1))
    # 3) 改大纲/重写大纲/调整大纲（含"整个大纲"）；无"大纲"字样的整体重写（全部重写/整体重写）
    if re.search(r"(?:改|重写|调整|优化|换|重新|整体|整个).{0,4}大纲|大纲.{0,6}(?:改|重写|调整|优化)", t):
        return ("outline", re.sub(r"^.*?大纲", "", t).strip() or t)
    if re.search(r"(?:全部|整体|整个|彻底|一律).{0,3}(?:重写|改写|改掉)|重写一遍|全部改", t):
        return ("outline", t)
    # 1c) 结构约束：固定N章/只保留N章/删掉多余章节 → outline（工具层截断+上限，2026-08-27）
    if re.search(
        r"(?:只|就|最多|限定|保持|控制|保留|压缩为|精简到|改成|定为|写满|达到|一共|总共|共)[的了有]{0,2}?\s*[0-9一二三四五六七八九十]{1,3}\s*章"
        r"|删?掉?多余|多余.*(?:删|去|掉)|其他的?删|超出的.*删",
        t,
    ):
        return ("outline", t)
    # 续写/接着写/继续写/再写点
    if re.search(r"(?:续写|接着写|继续写|继续完成|接着完成|补上|写完|再写(?:一点|点|一章|个)|往下写)", t):
        m_c = re.search(r"第\s*([0-9一二三四五六七八九十]{1,3})\s*章", t)
        if m_c:
            # 「接着写/继续完成第二章」→ 目标章节的完成动作（引擎会补写或重写该章），不是追加新章
            _rest = re.sub(r"第\s*[0-9一二三四五六七八九十]+\s*章", "", t).strip()
            _rest = re.sub(r"^(?:请|帮我|给我|继续|接着|把|将)?(?:完成|补上|写完|写)?[的了吧啊]?$", "", _rest).strip()
            return ("edit", f"{m_c.group(1)}|{_rest[:100] or '整体润色，更贴合角色与剧情'}")
        return ("continue", "")
    # 写小说/写一个X故事/创作一篇
    if re.search(r"(?:写|创作).{0,6}(?:小说|故事|短篇|同人|文)", t):
        return ("start", t)
    return None

driver = get_driver()
cfg = driver.config
from core import llm as core_llm  # 2026-09-10 Phase1：LLM Provider 适配层
# 2026-09-10 Phase1：客户端构造收拢 core.llm（env 可切外部 OpenAI 兼容端点，见 core/llm.py）；
# 模块属性名 client 保留（smoke 多处 B.client 换桩依赖）；默认本地配置下行为零变化
client = core_llm.get_client("main")
from core import perception  # Phase B：感知装配迁 core（薄壳转发）
from core import atomics  # 2026-09-07 P1：全部状态文件原子写（曾有 13 处裸写文本——写坏即状态清零）
from core import bgtasks  # 2026-09-12：后台任务统一持引用（原先 4 处各写各的集合，另有 9 处裸 create_task 会被 GC 静默吞掉）
from core.paths import DATA_ROOT  # 2026-09-12 S1：路径唯一来源（原先散着 4 处硬编码）
from core.paths import (  # 2026-09-12 E 项：引擎端口/模型/可执行名唯一来源（原与 core.llm、debug 各写一份字面量）
    ENGINE_BASE_URL, ENGINE_EXE, ENGINE_HOST, ENGINE_IMAGE, ENGINE_PORT, MODEL_FILE_GEMMA,
)

PERSONA_NAME = getattr(cfg, "persona", "default")
# 2026-09-10 P2 参数化：主人昵称（群聊场景提示词里"你的主人（XX，QQ …）"的显示名，原 :4689 硬编码）。
# env OWNER_NICKNAME（nonebot .env → cfg.owner_nickname）覆盖；
# 2026-09-12 隐私修复：缺省值原为**主人真昵称**（本文件随 Core 包发行 → 昵称跟着包出门，
# 且第三方装完会被 bot 叫成别人家的名字）。改为中性缺省「主人」：不再泄漏，且对第三方更合理。
# 主人侧 `.env` 已设 OWNER_NICKNAME → 行为零变化（审计 O 项会把这类"昵称出现在随仓文件里"列出来供人核）。
OWNER_NICKNAME = str(getattr(cfg, "owner_nickname", "") or "") or "主人"

# ---------------- 多角色通用化（2026-08-30）：每人选人设卡，状态机不变、只换底层人设 ----------------
PERSONA_SELECT_FILE = DATA_ROOT / "persona_select.json"
PERSONA_SWITCH_FILE = DATA_ROOT / "persona_switch_ts.json"
# ---------------- 亲密白名单（2026-09-04：聊天全待遇同主人；唯一区别=无调试权限） ----------------
INTIMATE_WL_FILE = DATA_ROOT / "intimate_whitelist.json"

# 2026-09-05 好感度重建：管理员（主人）恒定满 100；白名单 = 积累轨道（互动记账小步升温，80+ 开放亲密待遇）
REL_GAIN_STEP = 0.2      # 每次有效互动（私聊/群聊回复） +0.2
REL_GAIN_PENALTY = -1.0  # 被骂/冒犯（SOUR 检出）扣 1.0
REL_GAIN_DAILY_CAP = 8.0  # 每日升温上限（防刷；达到后当日不再累积）
REL_START = 0.0          # 2026-09-07 用户裁决：白名单不设基础起点（从 0 真实积累），只限制高点（80）
REL_GROUP_CAP = 30.0     # 2026-09-07 群友好友轨道：活跃群友好感可积累至上限 30（能成为朋友）
REL_GROUP_STEP = 0.2     # 群聊 @ 应答每次 +0.2
REL_GROUP_DAILY = 2.0    # 群友每日升温上限（防刷；到 30 需约两周的真实活跃）
OWNER_LOVE_BONUS = 20.0  # 2026-09-05 管理员爱意加成：白名单轨道（0-100）基础上 +20——"满分 + 一份只给他的爱意"
REL_WL_CAP = 80.0        # 2026-09-05 白名单好感上限：开放积累但封顶 80（亲密待遇已解锁）——"满/爱意"是主人的位置
_INTIMACY_DAILY: dict = {}  # 内存日计数 {uid: (YYYY-MM-DD, 当日已累计)}——单事件循环内原子，不设锁

# 2026-09-05 关系表现·连续曲线（无档位标签）：数值连续记账（记账层）→ 注入连续事实（表现层）→
# 唯一硬阈值在 guardrails.can_intimate（权限边界，带滞回）。模型按连续数值自然形成渐变关系感知，
# 不贴"档位"标签（60/80 切档会跳变，且掉档给挫败感）。
_POKE_TS: dict = {}  # 2026-09-06 poke 回复时间（proactive 撞车防护用）
_WRITE_NUDGE: dict = {}  # 2026-09-06 Phase E：写作漏标提醒（agent 漏标 → 下一轮感知注入自纠）


def _time_note(user_id: str) -> str:
    """时间事实（薄壳：取数在 brain，组装在 core.perception——Phase B 迁移）。"""
    try:
        _row = memory.db.recent_messages(user_id, limit=1)
        _ts = (_row[-1].get("ts") or "") if isinstance(_row, list) else ""
        return perception.time_note(_parse_msg_ts(_ts) if _ts else None)
    except Exception:  # noqa: BLE001
        return perception.time_note(None)


def _familiar_note(user_id: str, intimacy: float) -> str:
    """熟悉度连续注记（薄壳）。"""
    try:
        rel = memory.db.get_relation(user_id)
        return perception.familiar_note(intimacy, (rel or {}).get("updated_ts"))
    except Exception:  # noqa: BLE001
        return perception.familiar_note(intimacy, None)


# 命令语气响应：连续描述（模板含实时数值，模型按数值自定服从度梯度——无桶跳变）
CMD_INTIMACY_TEXT = (
    "【命令响应】对方以强硬命令语气提出要求（你对他的好感 {v:.0f}/100）："
    "好感越高你越干脆、越愿意配合；还不太熟时，可以礼貌地犹豫一下——嘟囔、磨蹭、嘴上抱怨两句，"
    "但不冷淡敷衍，最终仍会照做；熟悉起来之后你会更自然。"
)

# 2026-09-06 关系距离：所有会话可见的真实数值——数值即开放度，身份词不出现在这里
# （满值=亲近放开；积累轨道 80 上限=自然不过度；0=保持距离）。模型按连续数值自决，无档位跳变。
REL_DISTANCE_TEXT = (
    "【你们的关系】你与对方此刻的亲近程度：{v:.0f}/100——这是你心里对TA的真实感觉："
    "越接近满值越亲近：可以撒娇、可以直白表达在意、可以动手、可以少设防；"
    "中段自然客气有分寸，不冷不热；低值就好好说话保持距离、不越界。"
    "它是你态度的底，再按此刻的场合与正在做什么自然变化，不必刻意、不必对照数值解释自己。"
)


WL_MARKER_INTIMACY_MIN = 60.0  # 2026-09-07 用户裁决：白名单成员的生活/心情标记按好感度门槛（"并不是一定会改"）


def _marker_allowed(user_id: str) -> bool:
    """状态改写标记（【生活/场景】）与入迷档位采纳裁决：仅管理员 + 白名单；白名单按好感度。
    2026-09-07 用户裁决：①普通用户不得改写全局状态；②白名单不是必然——好感度 ≥ 门槛才采纳。
    管理员（主人）无条件。"""
    try:
        from nonebot import get_driver as _gd

        _su = set(str(x) for x in (getattr(_gd().config, "superusers", set()) or set()))
        uid = str(user_id)
        if uid in _su:
            return True
        if uid not in [str(x) for x in _load_intimate_wl()]:
            return False
        try:
            rel = memory.db.get_relation(uid)
            return float(rel.get("intimacy") or 0) >= WL_MARKER_INTIMACY_MIN
        except Exception:
            return False
    except Exception:
        return False


def _load_intimate_wl() -> list:
    try:
        d = json.loads(INTIMATE_WL_FILE.read_text(encoding="utf-8-sig"))
        return [str(x) for x in d] if isinstance(d, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_intimate_wl(lst: list):
    INTIMATE_WL_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(INTIMATE_WL_FILE, [str(x) for x in lst])


def _load_persona_select() -> dict:
    try:
        return json.loads(PERSONA_SELECT_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_persona_select(state: dict):
    PERSONA_SELECT_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(PERSONA_SELECT_FILE, state)


def _persona_switch_ts(user_id: str | None = None) -> str:
    """人设切换时间（UTC ISO）——记忆过滤基准。
    无显式记录时回退 persona_select.json 的修改时间（手动改卡也能生效）。"""
    try:
        st = json.loads(PERSONA_SWITCH_FILE.read_text(encoding="utf-8-sig"))
        v = st.get(str(user_id or _owner_id() or ""))
        if isinstance(v, list) and v:
            return str(v[-1].get("ts", "")) if isinstance(v[-1], dict) else ""
        if v:
            return str(v)
    except (OSError, json.JSONDecodeError):
        pass  # 读不到就落到下方的 mtime 回退（显式兜底链，非静默失败）
    try:
        import datetime as _dt

        return _dt.datetime.fromtimestamp(PERSONA_SELECT_FILE.stat().st_mtime, _dt.timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return ""


# ---------------- 群禁言静默（2026-09-04：bot 被禁言期间群内不再触发任何回复/插话） ----------------
GROUP_BAN_FILE = DATA_ROOT / "group_ban.json"


def _load_group_bans() -> dict:
    try:
        return json.loads(GROUP_BAN_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_group_bans(state: dict):
    GROUP_BAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(GROUP_BAN_FILE, state)


def _group_banned(group_id: str) -> bool:
    """该群是否禁言中（bot 被 ban；时间到期/被解除后自动视为未禁言）。"""
    if not group_id:
        return False
    try:
        st = _load_group_bans()
        until = float(str(st.get(str(group_id), 0) or 0))
        if until > time.time():
            return True
        if until:  # 到期惰性清理
            st.pop(str(group_id), None)
            _save_group_bans(st)
    except Exception:  # noqa: BLE001
        pass
    return False


ban_notice = on_notice(priority=1, block=False)


@ban_notice.handle()
async def _on_ban_notice(event):
    """监听群禁言通知：只记录 bot 自己被禁/被解（被禁群 → 静默周期）。"""
    try:
        if getattr(event, "notice_type", "") != "group_ban":
            return
        if str(getattr(event, "user_id", "") or "") != str(event.self_id):
            return  # 禁的是别人，不记录
        gid = str(getattr(event, "group_id", "") or "")
        if not gid:
            return
        st = _load_group_bans()
        if getattr(event, "sub_type", "") == "ban":
            dur = int(getattr(event, "duration", 0) or 0)
            st[gid] = time.time() + (dur if dur > 0 else 3600)
            logger.info("group ban recorded: gid=%s until=%s", gid, st[gid])
        else:
            st.pop(gid, None)
            logger.info("group ban lifted: gid=%s", gid)
        _save_group_bans(st)
    except Exception as e:  # noqa: BLE001
        logger.debug("ban notice err: %s", e)


# ---------------- QQ 戳一戳（2026-09-05：被戳 → 回戳 + agent 口吻主动回复） ----------------
poke_notice = on_notice(priority=1, block=False)


@poke_notice.handle()
async def _on_poke(bot, event):
    try:
        if getattr(event, "notice_type", "") != "notify" or getattr(event, "sub_type", "") != "poke":
            return
        if str(getattr(event, "target_id", "") or "") != str(event.self_id):
            return  # 戳的是别人
        uid = str(getattr(event, "user_id", "") or "")
        gid = str(getattr(event, "group_id", "") or "")
        if not uid:
            return
        logger.info("poke received: user=%s gid=%s", uid, gid or "-")
        # ① 回戳（NapCat 扩展 API；不支持则静默——仅靠回复）
        try:
            if gid:
                await bot.call_api("send_poke", group_id=int(gid), user_id=int(uid))
            else:
                await bot.call_api("send_poke", user_id=int(uid))
        except Exception:  # noqa: BLE001
            pass
        # ② 主动回复（poke 域自决口吻——感知装配：时间/关系/记忆/生活全带上，不是空心反应）
        try:
            from agent import run_agent as _ra

            _card = _persona_card()
            try:
                import logging as _lg
                _lg.getLogger("brain").info("poke card name=%s mode=%s pers=%s", _card.get("name"), _card.get("owner_mode"), _persona_name(uid))
            except Exception:
                pass
            # ---- 感知装配（2026-09-06 用户要求：戳一戳要结合记忆库、好感度机制）----
            _parts = ["（有人戳了你一下——TA想引起你注意、想跟你说话；你正在被打扰）"]
            try:
                _tn2 = _time_note(uid)
                if _tn2:
                    _parts.append(_tn2.replace("\n\n", " ").strip())
            except Exception:  # noqa: BLE001
                pass
            try:
                from agent import lifesim as _ls9

                _lt9 = _ls9.life_text()
                if _lt9:
                    _parts.append(_lt9)
            except Exception:  # noqa: BLE001
                pass
            try:
                from .. import memory as _mem9
                from agent import guardrails as _ag9

                _tier9 = _ag9.tier_of(uid, set(getattr(cfg, "superusers", set()) or set()), _load_intimate_wl())
                _rel9 = _mem9.db.get_relation(uid)
                _intim9 = float((_rel9 or {}).get("intimacy", 0.0) or 0.0)
                _fn9 = _familiar_note(uid, _intim9)
                if _fn9:
                    _parts.append(_fn9.replace("\n\n", " ").strip())
                _call9 = str(_card.get("owner_call") or "") or ""
                if _tier9 == _ag9.TIER_ADMIN and _call9:
                    _parts.append(f"（对方就是你心里的「{_call9}」——你们的关系你最清楚）")
                _rows9 = _mem9.db.recent_messages(uid, limit=4, group_id="")
                if _rows9:
                    _dl9 = _dialogue_lines(uid, limit=6)
                    if _dl9:
                        _parts.append("（你们最近的对话「[你（bot）]=你自己说的」：\n" + _dl9 + "）")
            except Exception:  # noqa: BLE001
                pass
            _st = await _ra(
                {
                    "kind": "poke", "user_id": uid, "group_id": gid, "text": "",
                    "is_group": bool(gid), "mentioned": False, "continuation": False,
                    "persona": _persona_name(),
                    "context": {
                        "proactive_ctx": "\n".join(_parts),
                        "card": _card, "mood": "", "ctx_lines": [], "ctx_text": "",
                    },
                },
                thread_id=("poke:%s") % (str(uid) + ":" + str(_persona_switch_ts(uid) or int(time.time()))),
            )
            if _st and _st.get("decision_action") == "speak":
                msg = str(_st.get("final_line") or "").strip()
                if len(msg) >= 2:
                    # 2026-09-08：拟人反应时间——被戳后全链仅 ~1s（感知+生成快），文字秒回太机械；
                    # 主链同款按长度延迟的思路，戳反应更快但也留"反应时间"（0.8-2.2s 随机）
                    await asyncio.sleep(random.uniform(0.8, 2.2))
                    _POKE_TS[str(uid)] = time.time()
                    try:
                        memory.log_message(str(uid), "", "assistant", msg, sender_name="bot")
                        memory.log_message(str(uid), "", "user", "[戳了戳]", sender_name="TA")
                    except Exception:
                        # 记忆写入失败要留痕：这是对话历史（状态），不是纯日志；回复本身照常发
                        logger.debug("poke reply 记忆写入失败（回复不受影响）", exc_info=True)
                    logger.info("poke reply: uid=%s gid=%s msg=%r", uid, gid or "-", msg[:120])  # 2026-09-06 观测（poke 原黑箱）
                    if gid:
                        await bot.send(event, MessageSegment.at(int(uid)) + " " + msg)
                    else:
                        await bot.call_api("send_private_msg", user_id=int(uid), message=msg)
        except Exception:  # noqa: BLE001
            pass
    except Exception as _e:  # noqa: BLE001
        logger.debug("poke err: %s", _e)


def _owner_id() -> str | None:
    """主人 QQ（首个超管）。"""
    try:
        return next(iter(set(getattr(cfg, "superusers", set()) or set())), None)
    except Exception:  # noqa: BLE001
        return None


def active_card_key() -> str:
    """当前人设卡键（=主人的当前选择；特殊状态/状态桶按此分卡）。2026-09-11 热修九。"""
    return _persona_name()


def _persona_name(user_id: str | None = None) -> str:
    """用户选择的一个人设卡名（默认 .env persona）。人设卡只换底层人设，状态机（关键词/指数）不变。
    2026-09-03：未指定用户时 = 主人的当前选择（bot 全局形象跟随主人切换）。
    2026-09-04：select 无记录的用户（群成员/未选卡新用户）也**回落到主人的选择**，而非 .env 默认——
    修复群聊按说话人取卡导致奥汀卡/奥汀表情包泄漏（群成员 @ bot 触发奥汀卡+emotes 贴纸）。"""
    sel = _load_persona_select()
    if not user_id:
        user_id = _owner_id()
    if user_id:
        pn = str(sel.get(str(user_id), "") or "").strip()
        if pn:
            return pn
        # 回落：未选卡用户跟随主人当前选择（bot 公共形象统一）
        own = _owner_id()
        if own and str(own) != str(user_id):
            pn = str(sel.get(str(own), "") or "").strip()
            if pn:
                return pn
    return PERSONA_NAME


def _persona_card(user_id: str | None = None) -> dict:
    """按用户加载人设卡（卡缺失回退默认卡/空卡）。未指定 = 主人的人设卡。"""
    try:
        return persona.load_persona(_persona_name(user_id))
    except FileNotFoundError:
        try:
            return persona.load_persona(PERSONA_NAME)
        except FileNotFoundError:
            return {"name": "AI", "description": "", "personality": "", "system_prompt": "", "alt_persona": {}}


PERSONA_ALIAS = {
    "素世": "soyo", "soyo": "soyo", "长崎素世": "soyo", "sayo": "soyo", "soyoko": "soyo",
    "奥汀": "_aoding_", "奥丁": "_aoding_", "odin": "_aoding_", "aoding": "_aoding_",
    "deepseek": "deepseek", "DeepSeek": "deepseek", "大肥鱼": "deepseek", "蓝色大肥鱼": "deepseek", "小深": "deepseek",
    "夏亚": "char", "赤色彗星": "char", "char": "char", "夏亚阿兹纳布尔": "char", "柯瓦特罗": "char", "柯瓦特罗巴吉纳": "char",
    "庄方宜": "zhuangfangyi", "庄小妹": "zhuangfangyi", "zhuangfangyi": "zhuangfangyi",
    "菲比": "feibi", "大饼脸": "feibi", "大饼脸菲比": "feibi", "phoebe": "feibi", "feibi": "feibi",
    "四季映姬": "eiki", "映姬": "eiki", "四季": "eiki", "eiki": "eiki", "四季映姬亚玛萨那度": "eiki",
    "佩丽卡": "perlica", "perlica": "perlica", "佩莉卡": "perlica",
    "陈千语": "chenqianyu", "千语": "chenqianyu", "chenqianyu": "chenqianyu", "啥龙": "chenqianyu", "傻龙": "chenqianyu",
    "阿米娅": "amiya", "米娅": "amiya", "amiya": "amiya", "罗德岛领袖": "amiya",
    "拉普兰德": "lappland", "拉普兰": "lappland", "lappland": "lappland", "德克萨斯的老搭档": "lappland",
    "古明地恋": "koishi", "恋恋": "koishi", "古明地": "koishi", "koishi": "koishi", "觉妖怪": "koishi",
    # 2026-09-06 语音包补齐的人设卡（凯尔希/能天使/斯卡蒂）——未注册曾致「/人设 凯尔希 提示不存在」
    "凯尔希": "kaltsit", "kaltsit": "kaltsit", "老女人": "kaltsit", "博士的养母": "kaltsit",
    "能天使": "exusiai", "exusiai": "exusiai", "新约能天使": "exusiai", "阿噗噜派": "exusiai", "老板": "exusiai",
    "斯卡蒂": "skadi", "skadi": "skadi", "浊心斯卡蒂": "skadi", "深海猎人": "skadi", "蒂蒂": "skadi",
    # 2026-09-06 八云紫（境界妖怪）
    "八云紫": "yukari", "紫": "yukari", "yukari": "yukari", "隙间妖怪": "yukari", "境界妖怪": "yukari",
}


def set_persona(user_id: str, name: str) -> bool:
    """设置用户的人设卡（校验卡存在，支持常用别名）。返回是否成功。"""
    name = (name or "").strip().strip("@/\\")
    name = PERSONA_ALIAS.get(name, name)
    try:
        persona.load_persona(name)
    except FileNotFoundError:
        return False
    state = _load_persona_select()
    old = str(state.get(str(user_id), "") or "")
    state[str(user_id)] = name
    _save_persona_select(state)
    # 2026-09-04：切换（含同名重切）即重置模式状态——破防/姿态残留不跨卡，新人设总从正常形态开始；
    # 且 mode=0 时头像才能按 mode0 素材应用（self 卡 mode1 恒无头像，残留会吞掉新头像）
    try:
        _modes = _load_modes()
        if str(user_id) in _modes and (_modes[str(user_id)].get("mode", 0) != 0 or _modes[str(user_id)].get("self_until")):
            _modes[str(user_id)] = {"mode": 0, "ts": time.time()}
            _save_modes(_modes)
            logger.info("persona mode reset on switch: user=%s", user_id)
    except Exception:  # noqa: BLE001
        pass
    if old != name:
        # 2026-09-05 舞台剧模型：世界观共享事实（用户 facts）按**世界观**共享——同世界观换卡
        # （阿米娅↔拉普兰德）facts 共享，跨世界观不串。2026-09-09 方案A 调整：生活档/约定/
        # 记忆分界改按**卡**（见下方 switch_persona_life/PERSONA_SWITCH 记录，所有换卡都执行）。
        _same_world = False
        try:
            _u_old = str((persona.load_persona(old).get("universe") or "") if old else "")
            _u_new = str(persona.load_persona(name).get("universe") or "")
            _same_world = bool(_u_old) and _u_old == _u_new
            logger.info("persona switch world-check: %s(%s) -> %s(%s) same_world=%s",
                        old, _u_old or "-", name, _u_new or "-", _same_world)
        except Exception:  # noqa: BLE001
            _same_world = False
        # 2026-09-09 方案A（用户裁决）：**所有换卡**都做生活档/约定按卡隔离 + 记切换分界——
        # 同世界观换卡（阿米娅↔凯尔希，universe 同为 arknights）曾零清理：凯尔希继承了
        # 阿米娅的"今晚蛋糕计划/办公室场景"（实测 bug）。_same_world 检查保留：
        # 世界观共享事实（用户 facts）照旧共享，只是生活/约定/记忆分界按卡。
        try:
            from agent import lifesim as _ls9
            _life_res = _ls9.switch_persona_life(old or "", name)
            logger.info("persona life switch: user=%s old=%s new=%s result=%s",
                        user_id, old or "-", name, _life_res)
        except Exception as _e:  # noqa: BLE001
            logger.warning("persona life switch failed: %s [%s]", _e, type(_e).__name__)
        # 约定（pending）按卡：当前活动档归档到旧卡名下、恢复新卡档（无档=空）——
        # 活动位 data/pending_tasks.json 路径/读写语义不变，其他调用点零改动
        try:
            _pend_res = _switch_pending_profile(old or "", name)
            logger.info("persona pending switch: user=%s result=%s", user_id, _pend_res)
        except Exception as _e:  # noqa: BLE001
            logger.warning("persona pending switch failed: %s [%s]", _e, type(_e).__name__)
        # 记录切换时间（方案A：记忆过滤基准改为**所有换卡**都分界——同世界观换卡后，
        # 事件召回/上下文过滤也按切换点切开，"记忆也要变"；不再仅跨世界观记录）
        import datetime as _dt

        try:
            st = json.loads(PERSONA_SWITCH_FILE.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            st = {}
        hist = st.get(str(user_id))
        if not isinstance(hist, list):
            hist = []
        # 旧卡激活点=本次会话边界（append 前 hist[-1] 即上一条切换记录）——交接生成的消息区间起点
        _sw_since = str(hist[-1].get("ts") or "") if hist and isinstance(hist[-1], dict) else ""
        hist.append({"p": name, "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")})
        st[str(user_id)] = hist
        PERSONA_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomics.write_json_atomic(PERSONA_SWITCH_FILE, st)
        logger.info("persona switch ts recorded: user=%s ts=%s", user_id, hist[-1]["ts"])
        # 2026-09-09 跨卡场景交接（写侧）：同世界观换卡 → 为旧卡生成摘要级交接（LLM 判 share+
        # 摘要，附着到旧卡归档 JSON；放置类情节 LLM 判 share=false → 读侧零注入，本人由既有
        # 归档恢复处境）。调用在 switch_persona_life 之后（归档已写好才可附着）；set_persona 是
        # 同步壳，生成任务挂当前事件循环（与 _typing_on 同款后台任务模式，本轮内即执行）；
        # 任何失败只记日志、绝不影响换卡本身（无交接=安全侧）。
        if _same_world and _sw_since:
            try:
                bgtasks.spawn(
                    _handover_generate(old or "", name, str(user_id), _sw_since))
                # 2026-09-09 盲审 P2：asyncio 仅持任务弱引用——20s LLM await 窗口长，不持引用可被
                # GC 静默吞掉（交接无声失效，仅日志缺失可判）。2026-09-12：改走 core.bgtasks.spawn
                # 统一持引用（同一套逻辑原先在 4 处各写一份，见 core/bgtasks.py docstring）。
            except Exception as _e:  # noqa: BLE001
                logger.warning("handover task schedule failed: %s [%s]", _e, type(_e).__name__)
    return True


# ---------------- 跨卡场景交接（2026-09-09）：同世界观换卡后，新卡对旧卡"刚离开的场景"有摘要级感知 ----------------
# 写侧：换卡时一次 LLM 生成 {share, scene, summary} 附着到旧卡生活档归档 JSON（不进 memory.db/事件表）；
# 读侧：主人私聊上下文临时注入（6h 过期/再次换卡自然更替）；方案 A 的记忆分界/边界过滤原样不变——
# 细节通道仍是切断的，摘要是唯一共享面。机器只做门控与时间窗兜底，"是不是同一个场景"由模型语义自判
# （零地点词表、零地点等值判断）。
HANDOVER_TTL = 6 * 3600          # 读侧时间窗兜底：距 generated_ts 超过 6h 不再注入
HANDOVER_MIN_MSGS = 4            # 生成前置：旧卡本次会话 user+assistant 消息下限（无实质内容不调 LLM）
HANDOVER_SUMMARY_MAX = 80        # 摘要字数上限（生成侧 attach 前 + 读侧双口钳制）
HANDOVER_MSG_CAP = 30            # 喂给 LLM 的最近消息条数上限
HANDOVER_SCENE_MAX = 12          # 场景词上限（与生活档 scene 口径一致）
# 在途任务强引用已收口到 core.bgtasks（原来这里有个 _HANDOVER_TASKS 集合，同款逻辑抄了 4 份）
_HANDOVER_SYSTEM = (
    "你是一个状态记录器，只输出 JSON，不输出任何其他文字。"
    "根据一段对话记录与她最后的状态，为**同一个世界里的其他角色**生成一条交接摘要"
    "（她刚从现场离开、由别人接着登场——接手的人只需要知道她大致在做什么）。\n"
    '输出格式：{"share": 布尔值, "scene": "场景", "summary": "摘要"}\n'
    "share 判定：正常情形一律 true。唯一例外——"
    "若最后情形属于『她被单独安置、藏匿或约束在某处，无法自行行动或离开』的情节，share=false——"
    "其他角色对此应无感知；她本人回来时处境由她的档案自行恢复，无需交接。\n"
    "scene：照抄她最后状态里的场景原词（缺省时按对话给出最贴近的地点词，2-6 字）。\n"
    "summary：只写大致在做什么、什么氛围、大致时段；不复制具体情节细节原文、不含任何【】标记、"
    "不逐条罗列；最多 80 字（超长会被截断）。"
)


def _handover_last_state(card: str) -> dict:
    """旧卡归档里的 life_state（交接条件/生成输入用）。经 lifesim 模块属性取路径与消毒
    （测试可重定向 LIFE_STATES_DIR）；缺档/异常 → {}（无处境=不生成）。"""
    try:
        from agent import lifesim as _lshs

        d = atomics.read_json(_lshs.LIFE_STATES_DIR / (_lshs._sanitize_card(card) + ".json"), None)
        ls = d.get("life_state") if isinstance(d, dict) else None
        return ls if isinstance(ls, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def should_make_handover(old_card: str, user_id: str, since_ts: str, now: float | None = None) -> bool:
    """交接生成前置条件（纯判定，可单测；任一不满足=不调 LLM=无交接，安全侧）：
    ①旧卡名+切换边界非空（since_ts 可解析）；②旧卡归档 doing/scene 非全空（有处境可交接）；
    ③边界→now 之间该用户私聊 user+assistant 消息 ≥HANDOVER_MIN_MSGS（本次会话有实质内容）。"""
    old_card = str(old_card or "").strip()
    if not old_card or not str(since_ts or "").strip():
        return False
    try:
        import datetime as _dth

        _since = _dth.datetime.fromisoformat(str(since_ts))
        if _since.tzinfo is None:
            _since = _since.replace(tzinfo=_dth.timezone.utc)
        _since_s = _since.astimezone(_dth.timezone.utc).isoformat(timespec="seconds")
        _until_s = _dth.datetime.fromtimestamp(
            float(now if now is not None else time.time()), _dth.timezone.utc).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return False
    _st = _handover_last_state(old_card)
    if not (str(_st.get("doing") or "").strip() or str(_st.get("scene") or "").strip()):
        return False
    try:
        _msgs = memory.db.messages_between(user_id, _since_s, _until_s, limit=HANDOVER_MSG_CAP)
        return isinstance(_msgs, list) and len(_msgs) >= HANDOVER_MIN_MSGS
    except Exception:  # noqa: BLE001
        return False


async def make_handover_payload(doing: str, scene: str, msgs: list) -> dict | None:
    """一次 LLM 调用生成 {share, scene, summary}（解析失败/字段非法/异常 → None=无交接，fail-safe）。
    喂入前消息经 strip_protocol_residue 剥净控制标记（[VOICE]/【身体遵从】等不进摘要输入）。"""
    try:
        from core import special as _spho

        _lines = []
        for _m in (msgs or [])[-HANDOVER_MSG_CAP:]:
            _txt = _spho.strip_protocol_residue(str(_m.get("content") or ""))[:80]
            if _txt:
                _lines.append(("她：" if str(_m.get("role")) == "assistant" else "对方：") + _txt)
        _user = (
            f"她最后的状态：{str(doing or '').strip()[:120] or '（未记录）'}"
            f"（场景：{str(scene or '').strip()[:12] or '（未记录）'}）。\n"
            "这段对话的最近记录（时间从早到晚）：\n" + ("\n".join(_lines) if _lines else "（无）")
        )
        resp = await client.chat.completions.create(
            model=core_llm.resolve_model("small"),
            messages=[
                {"role": "system", "content": _HANDOVER_SYSTEM},
                {"role": "user", "content": _user},
            ],
            temperature=0.2,
            max_tokens=200,
            timeout=20.0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        raw = str((resp.choices[0].message.content or "")).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
        _i, _j = raw.find("{"), raw.rfind("}")
        if _i < 0 or _j <= _i:
            return None
        d = json.loads(raw[_i:_j + 1])
        if not isinstance(d, dict) or not isinstance(d.get("share"), bool):
            return None
        _scene = str(d.get("scene") or "").strip()
        _summary = str(d.get("summary") or "").strip()
        if not _scene or not _summary:
            return None
        # 2026-09-09 盲审 P3（输出侧消毒）：LLM 输出是唯一不经人手审核的上下文注入源——
        # 伪协议块（【…】）剥净 + scene 钳 HANDOVER_SCENE_MAX（与生活档口径一致）；剥后为空=不可用（安全侧）
        _scene = re.sub(r"\s*【[^】]*】\s*", "", _scene).strip()[:HANDOVER_SCENE_MAX]
        _summary = re.sub(r"\s*【[^】]*】\s*", "", str(_spho.strip_protocol_residue(_summary))).strip()
        if not _scene or not _summary:
            return None
        return {"share": bool(d.get("share")), "scene": _scene, "summary": _summary}
    except Exception as e:  # noqa: BLE001
        logger.warning("handover payload failed: %s [%s]", e, type(e).__name__)
        return None


async def _handover_generate(old_card: str, new_card: str, user_id: str, since_ts: str) -> None:
    """交接生成编排（换卡流程后台任务）：条件判定 → 一次 LLM → 摘要钳 HANDOVER_SUMMARY_MAX →
    附着旧卡归档（attach_handover）。任何失败只记日志（无交接=安全侧），绝不影响换卡本身；
    share=false 照写归档（读侧过滤）——留观测面，语义等价零感知。"""
    try:
        old_card = str(old_card or "").strip()
        if not old_card or old_card == str(new_card or "").strip():
            return
        if not should_make_handover(old_card, user_id, since_ts):
            return
        import datetime as _dtg

        _until_s = _dtg.datetime.now(_dtg.timezone.utc).isoformat(timespec="seconds")
        _st = _handover_last_state(old_card)
        _msgs = memory.db.messages_between(user_id, str(since_ts), _until_s, limit=HANDOVER_MSG_CAP)
        payload = await make_handover_payload(str(_st.get("doing") or ""), str(_st.get("scene") or ""), _msgs)
        if not isinstance(payload, dict):
            return
        payload["summary"] = str(payload.get("summary") or "")[:HANDOVER_SUMMARY_MAX]
        payload["from_card"] = old_card
        try:
            payload["from_universe"] = str(persona.load_persona(old_card).get("universe") or "")
        except Exception:  # noqa: BLE001
            payload["from_universe"] = ""
        payload["generated_ts"] = time.time()
        from agent import lifesim as _lshg

        if _lshg.attach_handover(old_card, payload):
            logger.info("handover attached: card=%s scene=%s", old_card, str(payload.get("scene") or "")[:12])
    except Exception as e:  # noqa: BLE001
        logger.warning("handover generate failed: %s [%s]", e, type(e).__name__)


def previous_handover(current_card: str, now: float | None = None, user_id: str | None = None) -> dict | None:
    """读侧：取上一张卡的场景交接载荷（函数级可测）。任一条件不满足 → None=不注入：
    - 切换历史不足两条（无上一卡）/上一卡与当前同名；
    - 旧卡归档缺失或无 handover 字段；
    - handover.share 非 True（放置类情节零感知）；
    - from_universe 与当前卡世界观复检不一致（read 侧 belt-and-suspenders；复检异常=不注入）；
    - 距 generated_ts ≥ HANDOVER_TTL（时间窗兜底，过期自然消失）。
    返回的 dict 摘要已钳 HANDOVER_SUMMARY_MAX（读侧防御口）。"""
    try:
        _now = time.time() if now is None else float(now)
        uid = str(user_id if user_id is not None else (_owner_id() or ""))
        try:
            st = json.loads(PERSONA_SWITCH_FILE.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        hist = st.get(uid)
        if not isinstance(hist, list) or len(hist) < 2:
            return None
        _prev, _last = hist[-2], hist[-1]
        if not isinstance(_prev, dict) or not isinstance(_last, dict):
            return None
        prev_card = str(_prev.get("p") or "")
        if not prev_card or prev_card == str(current_card or "").strip():
            return None
        from agent import lifesim as _lshp

        arch = atomics.read_json(
            _lshp.LIFE_STATES_DIR / (_lshp._sanitize_card(prev_card) + ".json"), None)
        ho = arch.get("handover") if isinstance(arch, dict) else None
        if not isinstance(ho, dict) or ho.get("share") is not True:
            return None
        try:
            _u_cur = str(persona.load_persona(str(current_card or "")).get("universe") or "")
        except Exception:  # noqa: BLE001
            return None
        if not _u_cur or str(ho.get("from_universe") or "") != _u_cur:
            return None
        try:
            _gen = float(ho.get("generated_ts") or 0)
        except (TypeError, ValueError):
            return None
        if not _gen or (_now - _gen) >= HANDOVER_TTL or _gen > _now + 60:
            return None
        ho = dict(ho)
        ho["summary"] = str(ho.get("summary") or "")[:HANDOVER_SUMMARY_MAX]
        return ho
    except Exception:  # noqa: BLE001
        return None


# ---------------- 主人括号事实预解析（2026-09-10）：主人私聊并行管线 ----------------
# 触发：主人私聊轮且消息含全角括号「（」（群聊/非主人/无括号不触发）。一次高速小 LLM 调用拆解
# 主人括号内**明确发生**的特殊层级事实（催眠/洗脑/道具——束缚类如绳子也按道具名处理），
# 任务与 stage1 同时 create_task 并行，stage2 组装时由 core/reply 收取（超时/失败=本轮无约束，
# 绝不阻塞回复）；generate 正常返回后由 handle 调 core/special.apply_owner_facts 机器登记。
# 本层只做「并行发起+带回」，注入与登记分别在 core/reply 与 core/special。
# 在途任务强引用已收口到 core.bgtasks（同款逻辑原先在 4 处各写一份）
_PREPARSE_TEXT_CAP = 400         # 喂给预解析的主人消息钳长（括号事实不长，防超长污染）
_PREPARSE_ITEMS_MAX = 4          # 单轮道具事实条数上限（与 core/special._OWNER_FACTS_ITEMS_MAX 同口径）
_PREPARSE_SYSTEM = (
    "你是一个状态记录器，只输出 JSON，不输出任何其他文字。"
    "主人消息的括号里描述了他对她做的事。请只提取括号内**明确已经发生**的特殊层级事实：\n"
    '输出格式：{"hypno": "催眠效果要求或空串", "brainwash": "洗脑要求或空串", '
    '"items": [{"name": "道具名", "state": "状态或空串"}], "releases": ["解除目标"]}\n'
    "判定口径：\n"
    "- hypno：括号内本轮**明确新发生或明确加强**催眠并给出效果要求（如打指响陷入催眠后被下了指示）"
    "→ 填效果；单纯提及、修辞形容、叙述过往不填；没有就空串；\n"
    "- brainwash：括号内本轮**明确新发生或明确加强**人格/记忆改写 → 填改写要求；"
    "单纯提及、修辞形容、叙述过往不填；没有就空串；\n"
    "- items：仅主人**明确点名**对某道具使用/加身 → 名称与当前状态（已知道具：口球/淫纹/震动棒；"
    "束缚类如绳子也按道具名登记）；徒手动作（按住/捂住/拉住等）**不得**推断/升级为道具；"
    "比喻、修辞、假设、复述语气一律不登记；没有就空数组；\n"
    "- releases：括号内明确表示某状态被**解除/移除/收起/解开/结束**（如解开手铐、摘下/收起某道具、"
    "解除一切状态、氛围结束）→ 填目标词；目标词取值域：催眠/洗脑/不许装/氛围/全部/"
    "具体道具名（标准名，可带「道具：」前缀）；**解除表述绝不写入 items/hypno/brainwash"
    "（解除不是新事实登记）**；没有就空数组；\n"
    "- 只提取**明确发生**的：主人打算做/正在说的不算；没有的字段一律留空串/空数组；"
    "绝不虚构、绝不推测意图；一条都不确定就全部留空。"
)


async def preparse_owner_facts(text: str) -> dict | None:
    """一次小 LLM 调用拆解主人括号内的事实（与 make_handover_payload 同款形态与消毒手法）。
    返回 {"hypno": str, "brainwash": str, "items": [{"name": str, "state": str}],
    "releases": [str]}（字段可全空=无事实）；非 JSON/字段类型非法/异常 → None（安全侧：调用方以真值判断=本轮无约束）。"""
    try:
        _body = str(text or "").strip()[:_PREPARSE_TEXT_CAP]
        if "（" not in _body:  # 门控复验（handle 已筛一道；直调也安全）
            return {}
        resp = await client.chat.completions.create(
            model=core_llm.resolve_model("small"),
            messages=[
                {"role": "system", "content": _PREPARSE_SYSTEM},
                {"role": "user", "content": "主人消息：" + _body},
            ],
            temperature=0.2,
            max_tokens=200,
            timeout=8.0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        raw = str((resp.choices[0].message.content or "")).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
        _i, _j = raw.find("{"), raw.rfind("}")
        if _i < 0 or _j <= _i:
            return None
        d = json.loads(raw[_i:_j + 1])
        if not isinstance(d, dict):
            return None
        _hypno, _bw, _items, _rels = d.get("hypno"), d.get("brainwash"), d.get("items", []), d.get("releases", [])
        # 字段类型校验：缺失/None 按空处理（schema 本就允许"或空"）；给了但类型不对=整包不可信（安全侧 None）
        if not ((_hypno is None or isinstance(_hypno, str))
                and (_bw is None or isinstance(_bw, str))
                and isinstance(_items, list)
                and (_rels is None or isinstance(_rels, list))):
            return None
        _hypno, _bw = str(_hypno or ""), str(_bw or "")
        from core import special as _sppf

        _clean_items = []
        for _it in _items[:_PREPARSE_ITEMS_MAX]:
            if not isinstance(_it, dict):
                continue
            _name = _sppf.sanitize_item_name(_it.get("name"))  # 消毒到已知/开放道具名口径
            if not _name:
                continue
            _state = _it.get("state")
            _clean_items.append({"name": _name,
                                 "state": _state.strip() if isinstance(_state, str) else ""})
        # 输出侧消毒：伪协议块（【…】）剥净 + 换行归一（盲审 P3-1：对齐 handover 消毒力度，
        # 防 facts 行破坏"一行一事实"结构或夹带标记文本进 stage2 系统提示）
        _proto = re.compile(r"\s*【[^】]*】\s*")
        _nl = re.compile(r"[\r\n]+")
        _hypno = _nl.sub(" ", _proto.sub("", _hypno)).strip()
        _bw = _nl.sub(" ", _proto.sub("", _bw)).strip()
        for _ci, _cv in enumerate(_clean_items):
            _cv["state"] = _nl.sub(" ", _proto.sub("", str(_cv.get("state") or ""))).strip()
        # 热修十二 F1：releases 消毒同款手法（伪协议块剥净+换行归一+钳长）；目标词的识别与落账
        # 在 core/special._resolve_clear_target（与解除标记同口径，识别不了的落账侧自然跳过）
        _clean_rels = []
        for _r in _rels[:_PREPARSE_ITEMS_MAX]:
            _t = _nl.sub(" ", _proto.sub("", str(_r or ""))).strip()
            if _t:
                _clean_rels.append(_t[:16])
        logger.info("owner facts preparse parsed: hypno=%r brainwash=%r items=%d releases=%d",
                    _hypno[:30], _bw[:30], len(_clean_items), len(_clean_rels))
        return {"hypno": _hypno, "brainwash": _bw, "items": _clean_items, "releases": _clean_rels}
    except Exception as e:  # noqa: BLE001
        logger.warning("owner facts preparse failed: %s [%s]", e, type(e).__name__)
        return None


# ---------------- QQ 昵称跟随人设（2026-08-30：set_qq_profile + 24h 节流 + 失败静默） ----------------
NICK_LAST_FILE = DATA_ROOT / "nickname_last.json"


def _load_nick_last() -> dict:
    try:
        return json.loads(NICK_LAST_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_nick_last(state: dict):
    NICK_LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(NICK_LAST_FILE, state)


async def _apply_persona_nickname(bot: Bot, card: dict) -> bool:
    """应用人设卡昵称（卡内 nickname 字段）；同名跳过、失败静默。（2026-09-07：清死参数 force——24h 频控已删，force 无分支可用）"""
    try:
        nick = str(card.get("nickname", "") or "").strip()
        if not nick:
            return False
        st = _load_nick_last()
        if st.get("name") == nick:
            return True  # 已是该昵称
        # 2026-09-06 用户确认：重复改无妨——不再做 24h 频控（保留同名跳过）
        await bot.call_api("set_qq_profile", nickname=nick)
        _save_nick_last({"name": nick, "ts": time.time()})
        logger.info("persona nickname applied: %s", nick)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("nickname apply failed (可能触发 QQ 风控): %s", e)
        return False


# ---------------- 幕间生活模拟（2026-08-30：HDS-Interlude 式——消息间隔里她有自己的生活） ----------------
# ---------------- 群成员印象（2026-08-30：叫得上名字 = 活人感；群级一张表） ----------------
GROUP_MEMBERS_FILE = DATA_ROOT / "group_members.json"


def _load_group_members() -> dict:
    try:
        return json.loads(GROUP_MEMBERS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_group_members(state: dict):
    GROUP_MEMBERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(GROUP_MEMBERS_FILE, state)


def _remember_group_member(group_id: str, user_id: str, nickname: str, card: str = ""):
    try:
        if not group_id or not user_id:
            return
        nick = (nickname or "").strip()[:24]
        card_n = (card or "").strip()[:24]
        if not nick and not card_n:
            return
        state = _load_group_members()
        g = state.setdefault(str(group_id), {})
        g[str(user_id)] = {"nick": nick or card_n, "card": card_n, "ts": time.time()}
        if len(g) > 30:  # 只留最近 30 人
            for k in sorted(g, key=lambda x: -g[x]["ts"])[30:]:
                g.pop(k, None)
        _save_group_members(state)
    except Exception:  # noqa: BLE001
        pass


def _group_roster_text(group_id: str) -> str:
    """群成员印象（昵称列表，供她叫得上名字）。"""
    try:
        g = _load_group_members().get(str(group_id), {})
        names = [v.get("nick") for v in g.values() if v.get("nick")]
        if not names:
            return ""
        recent = sorted(g.values(), key=lambda v: -v.get("ts", 0))[:10]
        nicks = [(v.get("nick") or "") for v in recent if v.get("nick")]
        return "【群成员印象】" + "、".join(nicks) + f"（常一起聊的{len(nicks)}人，记得他们）"
    except Exception:  # noqa: BLE001
        return ""


# ---------------- 自决破防判定（素世式：bot 自己判断要不要黑化） ----------------


# （2026-09-05：形态进入判定器已删除——alt 表现交给 agent 自决（persona【性格的另一面】素材注入），
# 不再有"破防/形态"的状态机开关与计时）


STICKER_ENABLED = bool(getattr(cfg, "sticker_enabled", False))  # 表情包功能开关（默认关）
FICTION_ENABLED = bool(getattr(cfg, "fiction_enabled", True))   # 写作功能开关（.env 可关）

MODE_FILE = DATA_ROOT / "persona_mode.json"
AVATAR_DIR = DATA_ROOT / "avatars"
APPLIED_FILE = AVATAR_DIR / ".applied.json"

# ---------------- 思考模式指令式开关（"认真起来"→思考开 / "不用认真"→思考关） ----------------
# 指令式：用户话语切换；默认关（RP 快 + 活人感）；切换轮注入角色第一人称话语
THINK_MODE_FILE = DATA_ROOT / "think_mode.json"
THINK_ON_PROMPT = (
    "\n\n【认真模式】你决定认真起来了——这一轮先以角色口吻自然地说一句表明认真的话语"
    "（比如「那么，接下来就要认真起来了」），然后用最认真的态度对待接下来的对话："
    "回答更严谨、更完整、更有分量，不再随便敷衍。"
)
THINK_OFF_PROMPT = (
    "\n\n【放松模式】你决定放松下来了——这一轮先以角色口吻自然地说一句不再认真的话"
    "（比如「哼，对付你，还用不着那么认真」），然后恢复轻松随意的调子，像平时闲聊一样自然。"
)

# ---------------- 文风模式（2026-08-26 用户需求：回复太长/小说感 → 日常口语化默认，可切小说型） ----------------
STYLE_MODE_FILE = DATA_ROOT / "style_mode.json"
# 切小说型（长篇文学风）：关键词
STYLE_NOVEL_WORDS = ("小说模式", "小说感", "文绉绉", "写小说吧", "文艺点", "来一段描写", "文学模式")
# 切日常口语（默认）：关键词
STYLE_DAILY_WORDS = ("正常说话", "口语点", "简单点", "别文绉绉", "日常模式", "说人话", "简短点", "平时怎么聊")
# 文风提示（注入 system_prompt；daily 为默认硬规）
STYLE_DAILY_PROMPT = (
    "\n\n【文风·日常口语（默认）】回复像发微信消息一样**短小自然**："
    "口语短句，常用语气词（哼/哎呀/行吧/哈？），长短随你此刻的话自然来；"
    "**避免**：长篇铺陈、形容词堆砌、文绉绉书面语、小说式描写、每句都带*动作*；"
    "动作描写（若有）短、轻。像她平时跟你发消息的口吻。"
)
STYLE_NOVEL_PROMPT = (
    "\n\n【文风·小说型】这一轮你选择用**文学化长句**展开：允许环境/神态/心理描写，"
    "场景铺陈、意象渲染、华丽遣词（但保持角色高傲口吻）；长度由氛围自然决定；"
    "动作描写不宜多。情感浓烈时使用。"
)


def _load_style_modes() -> dict:
    try:
        return json.loads(STYLE_MODE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_style_modes(state: dict):
    STYLE_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(STYLE_MODE_FILE, state)


def get_style_mode(user_id: str) -> str:
    """当前文风模式（'daily'=日常口语默认 / 'novel'=小说型）。"""
    return _load_style_modes().get(user_id, "daily")


def set_style_mode(user_id: str, mode: str) -> None:
    state = _load_style_modes()
    state[user_id] = "novel" if mode == "novel" else "daily"
    _save_style_modes(state)


def _load_think_modes() -> dict:
    try:
        return json.loads(THINK_MODE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_think_modes(state: dict):
    THINK_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(THINK_MODE_FILE, state)


def get_think_mode(user_id: str) -> bool | None:
    """当前思考模式：True=用户命令锁定开 / False=用户命令锁定关 / None=未锁定（bot 自决）。
    2026-09-07：默认 bot 自决（agent 预声明【认真】标记，30 分钟 TTL）；/思考 开|关 仍可手动锁定。"""
    v = _load_think_modes().get(user_id)
    return None if v is None else (v == "on")


# 2026-09-07 思考自决：词表触发已删（覆盖不了真实表达）——由 agent 预判对话走向后
# 在回复末尾自标【认真】（"接下来的话题我需要认真对待"），机器只存 TTL，不判不拦。
_THINK_AHEAD: dict = {}  # user_id -> epoch 秒（存续期间 stage1 开思维链）
_THINK_AHEAD_TTL = 1800.0


def think_ahead(user_id: str) -> bool:
    """agent 预声明的认真模式是否存续（30 分钟内有效；期间每轮自标可无限续期）。"""
    return time.time() < _THINK_AHEAD.get(str(user_id), 0.0)


def mark_think_ahead(user_id: str):
    _THINK_AHEAD[str(user_id)] = time.time() + _THINK_AHEAD_TTL


def set_think_mode(user_id: str, mode: str) -> None:
    state = _load_think_modes()
    if mode == "auto":
        state.pop(user_id, None)  # 2026-09-05：恢复 bot 自决（未锁定）
    else:
        state[user_id] = "on" if mode == "on" else "off"
    _save_think_modes(state)


# ---------------- 思考链·机器单向开启判据（2026-09-12 T4.1，用户决策 D4） ----------------
# 语义：think_wanted = (用户锁定值 if locked else False) OR 任一判据命中 —— **单向开，绝不强制关**。
# 没有判据命中时行为与改动前**逐字相同**（未锁定=由 bot 自决，锁定=按锁定值）。
# 全部判据纯本地：零额外 LLM 调用、零新协议标记（铁律：机器只做"时间窗兜底/事故级"这类兜底）。
_THINK_MSG_LEN = 120          # 判据一：本轮用户消息**等效长度**下限（见 _think_equiv_len）
_THINK_EQUIV_LATIN = 3.5      # 拉丁/数字/半角标点：每 3.5 个字符折 1 个等效单位
_THINK_PREV_ATTEMPTS: dict[str, int] = {}   # uid -> 上一轮 generate 返回的 attempts（判据四：上轮硬边界重试过）
_THINK_NEED: dict[str, set] = {}            # uid -> 本轮已命中的判据名集合（调用方登记本轮信号，无=空集）
_THINK_TOUCH: dict[str, float] = {}         # uid -> 最近登记时间（_sweep_stale 判陈旧，防无界增长）


def _think_equiv_len(text: str) -> float:
    """判据一的**等效长度**：CJK 字 1 个算 1，空白不计，其余每 3.5 字符算 1。

    2026-09-12 国际化：原先直接 `len(text)` 是**字符数**口径——英文 120 字符只有约 20 个词，
    英文用户会远比中文用户更频繁地触发"长消息"（每条命中在本地引擎上要付 10-20 s 延迟）。
    折算后：中文 120 字 = 120.0（**与旧口径逐字一致 → 中文侧零回归**，
    实测 1261 条真实语料两口径触发集合完全相同），英文约 420 字符 = 120.0（≈70 词，语义量相当）。

    口径可由 `tools/dev/think_len_calib.py` 在真实语料上复算（纯 CJK 子集必须两口径相同）。
    """
    n = 0.0
    for ch in text or "":
        if ch.isspace():
            continue
        o = ord(ch)
        cjk = (0x3000 <= o <= 0x303F or 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF
               or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFFEF)
        n += 1.0 if cjk else 1.0 / _THINK_EQUIV_LATIN
    return n


def _name_think_reasons(user_id, text: str) -> list[str]:
    """判据逐个判定，返回命中的判据名列表（空=都不命中 → 行为与改动前相同）。

    判据三（本轮 `_character_lookup` 命中世界观角色）由调用方登记在 `_THINK_NEED`——它的
    消费点在 `_character_lookup` 唯一组装处（那里才知道 card.universe），此处只读标志。

    2026-09-12 用户裁决（盲审 P3-1）：**移除"知识问句"判据**，其辅助函数一并删除
    （避免留下新的孤儿符号——本项目已有一批此类残留，见 T9 清理）。
    移除理由：实测在 1261 条真实消息上它命中 4.1% 却误报密集（"那我现在走"/"今天天气不错"/
    "我哪来的钱" 全判真）——因为它直接复用了**搜索**分支的时效性快通道
    （`_auto_search_query` 把"最新/今年/价格/天气"与知识词并在一支），口径比"需要认真想"
    宽得多。累计强开率 16.0% 里它占 4.1%，而每条命中要付 10-20s 延迟（本地引擎）。
    保留其余三条：角色提及（12.3%，正是今早漂移那类）、长消息、上轮硬边界重试。
    """
    reasons: list[str] = []
    if _think_equiv_len(text) > _THINK_MSG_LEN:
        reasons.append("长消息")
    if "角色提及" in _THINK_NEED.pop(str(user_id), set()):
        reasons.append("角色提及")
    if int(_THINK_PREV_ATTEMPTS.get(str(user_id), 0) or 0) == 2:
        reasons.append("上轮重试")
    return reasons


# ---------------- 模型引擎表（2026-09-04：qwen 已移除，当前仅 gemma4-12b-heretic；切换机制保留以备扩展） ----------------
MODEL_MODE_FILE = DATA_ROOT / "model_mode.json"
# 路径与引擎事实源统一（2026-09-12 S1 路径 + E 项 端口/文件名）：本表与 debug.py / start.ps1 /
# Launcher.ps1 曾四处各自硬编码 "E:\robot\..." 与端口 —— 换盘或改端口即"同一台机器两条启动
# 路径行为不同"。MODEL_FILE_GEMMA 与 ENGINE_EXE 现由 core.paths 提供（见文件头 import），此处不再重算。
ENGINE_EXE_PATH = ENGINE_EXE  # 本文件的历史叫法，值与 core.paths.ENGINE_EXE 逐字节相同
# 模型表：key -> (模型文件, 引擎参数 lambda, 显示名)
MODELS = {
    "gemma": {
        "file": MODEL_FILE_GEMMA,
        # 2026-08-26 修复 cx：12288→32768（原 6144/slot 装不下台词示范+历史 → 400 超限）
        # 2026-09-09 深夜：三源漂移统一（对齐 start.ps1 权威值）——parallel 2→1（去槽竞争，decode +120%），
        # 补 repeat-penalty/last-n=1.15/256（start.ps1 同款）
        # 2026-09-09：三源统一（对齐 start.ps1 权威值）——reasoning-budget 400→200（权威=用户实测回退后的既定值；
        # 本表为 watchdog 重启与 /模型 切换共用，任一漂移即"同一台机器两条启动路径行为不同"的虚空 bug）
        "args": lambda: ["-c", "32768", "-ngl", "99", "--parallel", "1", "-ctk", "q8_0", "-ctv", "q8_0", "--reasoning", "on", "--reasoning-budget", "200", "--repeat-penalty", "1.15", "--repeat-last-n", "256", "--spec-type", "ngram-simple"],
        "label": "Gemma4-12B",
    },
}
MODEL_SWITCH_WORDS = ("切换gemma", "用gemma", "模型gemma", "切到gemma")
MODEL_CONFIRM_TEMPLATE = "【模型切换】{me}已换乘「{label}」——{think_note}此后回复由新引擎驱动（加载约 30 秒，若期间回复失败请稍后再试）。"


def _load_model_modes() -> dict:
    try:
        return json.loads(MODEL_MODE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_model_modes(state: dict):
    MODEL_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(MODEL_MODE_FILE, state)


def get_model_key(user_id: str) -> str:
    """当前模型 key（默认 gemma）。"""
    k = _load_model_modes().get(user_id, "gemma")
    return k if k in MODELS else "gemma"


def set_model_key(user_id: str, key: str) -> None:
    state = _load_model_modes()
    state[user_id] = key if key in MODELS else "gemma"
    _save_model_modes(state)


def _engine_start_args(model_key: str) -> list[str]:
    """按模型 key 组装引擎启动参数（同一 11434 端口切换，client base_url 不变）。"""
    m = MODELS[model_key]
    return (
        [ENGINE_EXE_PATH, "-m", m["file"], "--host", ENGINE_HOST, "--port", str(ENGINE_PORT)]
        + m["args"]()
    )


_ENGINE_SWITCHING = False  # 2026-09-07 P1：切换重入卫（曾可并发 taskkill 互杀/抢端口）


async def _switch_engine_model(user_id: str, target: str) -> bool:
    """停当前引擎 → 启动目标模型引擎（11434）→ 等就绪。返回是否成功。
    2026-09-07 P1：taskkill/sleep 移出事件循环（曾同步阻塞全 bot ≤12s）；重入直接拒绝。"""
    global _ENGINE_SWITCHING
    import subprocess as _sp

    if _ENGINE_SWITCHING:
        logger.warning("engine switch already in progress, drop (user=%s target=%s)", user_id, target)
        return False
    _ENGINE_SWITCHING = True
    try:
        try:
            await asyncio.to_thread(
                _sp.run, ["taskkill", "/F", "/IM", ENGINE_IMAGE],
                capture_output=True, timeout=10)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(2)
        args = _engine_start_args(target)
        try:
            # 2026-09-07 P2：日志句柄 with 关闭（曾每次切换泄漏 2 个；子进程持有自己的继承句柄不受影响）
            with open(DATA_ROOT / "llama-server.log", "ab") as _lf, \
                    open(DATA_ROOT / "llama-server.err.log", "ab") as _lef:
                _sp.Popen(
                    args,
                    stdout=_lf,
                    stderr=_lef,
                    creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
                )
        except Exception as e:  # noqa: BLE001
            logger.error("engine switch start failed: %s", e)
            return False
        # 等就绪（最多 90 秒）
        import httpx as _hx

        for _ in range(30):
            await asyncio.sleep(3)
            try:
                async with _hx.AsyncClient(timeout=3) as c:
                    r = await c.get(f"{ENGINE_BASE_URL}/models")
                    if r.status_code == 200:
                        set_model_key(user_id, target)
                        logger.info("engine switched to %s (user=%s)", target, user_id)
                        return True
            except Exception:  # noqa: BLE001
                continue
        logger.warning("engine switch timeout (%s)", target)
        return False
    finally:
        _ENGINE_SWITCHING = False


def _load_modes() -> dict[str, dict]:
    """读取人格模式（兼容旧格式 {"user": 0/1} 与新格式 {"user": {"mode":0/1,"ts":...}}）。"""
    try:
        raw = json.loads(MODE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[k] = {"mode": int(v.get("mode", 0)), "ts": float(v.get("ts", 0))}
        else:
            out[k] = {"mode": int(v), "ts": 0.0}
    return out


def _save_modes(modes: dict[str, dict]):
    MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(MODE_FILE, modes)


def _load_applied() -> dict:
    try:
        return json.loads(APPLIED_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_applied(applied: dict):
    APPLIED_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(APPLIED_FILE, applied)


def _avatar_path(avatar_ref: str) -> Path | None:
    """把角色卡头像字段解析为实际路径（相对 data/avatars/ 或绝对路径）。"""
    if not avatar_ref:
        return None
    p = Path(avatar_ref)
    if not p.is_absolute():
        p = AVATAR_DIR / p
    return p if p.exists() else None


async def _apply_persona_avatar(bot: Bot, card: dict, mode: int, force: bool = False):
    """应用当前人格模式的头像。

    force=True：忽略已应用标记强制调用（模式每次变化都应用，防止头像与模式错位）。
    """
    import base64

    ref = persona.avatar_for_mode(card, mode)
    p = _avatar_path(ref)
    logger.info("avatar apply: mode=%s ref=%s exists=%s force=%s", mode, ref, p is not None, force)
    if p is None:
        return
    stem = f"{card.get('name', '?')}:{mode}"  # marker 按卡名，防多卡冲突
    marker = stem
    applied = _load_applied()
    if not force and applied.get(marker) == p.name:
        logger.info("avatar apply: already applied (%s=%s), skip", marker, p.name)
        return  # 已应用过
    try:
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        resp = await bot.call_api("set_qq_avatar", file=f"base64://{b64}")
        applied = _load_applied()  # 2026-09-07 P2：await 窗口内可能被并发改写——重读再落盘（旧快照整体覆盖曾丢标记/重复调 QQ API）
        applied[marker] = p.name
        _save_applied(applied)
        logger.info("persona avatar applied: %s -> %s (resp=%s)", marker, p.name, resp)
    except Exception as e:  # noqa: BLE001
        logger.warning("avatar apply failed (可能触发 QQ 限流): %s", e)


@driver.on_bot_connect
async def _on_connect(bot: Bot):
    """bot 上线时强制应用当前人格头像（防重启后头像与模式不一致）。
    2026-09-06 修复：优先用主人的当前选择（select），回退默认卡。
    2026-09-11 热修十一：上线即做一次存量药剂时效归一（normalize_ttls，幂等——重复调用
    零改动零写盘）——「无时效/超长 TTL」的历史药剂条目被钳到 activated_at+12h，随后由
    active() 惰性过期清除；失败不阻断上线流程。"""
    try:
        from core import special as _sp_norm

        _sp_norm.normalize_ttls()
    except Exception as _e:  # noqa: BLE001
        logger.warning("special normalize_ttls failed: {} [{}]", _e, type(_e).__name__)
    try:
        card = _persona_card()  # 主人当前选择（无参=主人）
    except Exception:  # noqa: BLE001
        try:
            card = persona.load_persona(PERSONA_NAME)
        except FileNotFoundError:
            return
    modes = _load_modes()
    # 取任一用户当前模式应用（头像属于 bot 全局，取第一个有模式的用户）
    for uid, mstate in modes.items():
        try:
            card = _persona_card(uid)  # 按用户选择的人设卡应用（多角色通用）
        except Exception:  # noqa: BLE001
            pass
        await _apply_persona_avatar(bot, card, mstate["mode"], force=False)  # 2026-09-04: 重连不再强刷（防 2.5min 一次 set_qq_avatar 限流）
        return


chat = on_message(priority=5, block=False)

# ---------------- 联网搜索 ----------------
# 触发词：尽量覆盖命令式表达（"给我查""帮我搜"），避免功能因措辞变化而不触发
_SEARCH_TRIGGERS = (
    "搜索", "查一下", "查查", "搜搜", "搜一下", "帮我查", "帮我搜",
    "给我查", "给我搜", "去查", "现在查", "百度一下",
)
# 搜索词含以下字样时走图片搜索（搜图模式）
IMAGE_SEARCH_HINTS = ("图片", "照片", "壁纸", "插图", "画像", "的图", "截图")
# 舰船搜索：命中时先转英文，再查 ibiblio 美国海军档案站
SHIP_SEARCH_HINTS = ("舰船", "舰艇", "军舰", "战列舰", "航母", "航空母舰", "驱逐舰", "巡洋舰", "潜艇", "护卫舰", "战列巡洋舰", "美国海军")

# 搜图关键词净化：去掉"参数/信息/照片"等对图片搜索有害的噪音词，只留主题
_IMG_NOISE_WORDS = (
    "图片", "照片", "壁纸", "插图", "画像", "截图", "的图", "看看",
    "参数", "信息", "资料", "介绍", "内容", "给我", "帮我", "一下",
)


def _clean_image_query(q: str) -> str:
    """净化搜图关键词：剔除噪音词与标点，保留主题词。"""
    for w in _IMG_NOISE_WORDS:
        q = q.replace(w, "")
    return q.strip(" 的了和与及、，。；:：")


async def _translate_ship_name(text: str) -> str:
    """从句子中提取舰船名并翻译成英文（含 USS 规范）。无法识别返回空串。

    输入可能是整句（"美国海军1942年企业号的参数和照片"），
    必须只提取舰名（带"号"的名字或舰种类名）翻译，保证 USS 前缀可命中档案站。
    """
    try:
        resp = await client.chat.completions.create(
            model=core_llm.resolve_model("main"),
            messages=[
                {"role": "system", "content": "从句子中识别舰船名称（通常带“号”，如“企业号”“大和号”，也可能是舰种类名或英文舰名），翻译成英文舰船名（如：企业号航母 -> USS Enterprise CV-6；俾斯麦号战列舰 -> German battleship Bismarck；胡德号 -> HMS Hood）。只输出英文名，不含括号和多余说明，不超过20词；句子中没有舰船名输出：none"},
                {"role": "user", "content": text},
            ],
            temperature=0.1,
            max_tokens=400,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},  # 翻译无需思考，提速
        )
        name = (resp.choices[0].message.content or "").strip()
        return "" if name.lower() == "none" else name
    except Exception:  # noqa: BLE001
        return ""


# ---------------- 用户括号内容解析（2026-09-06：只做结构解析，语义判断归 agent） ----------------
_ACTION_BRACKET_RE = re.compile(r"[（(]([^（()）]{1,20})[)）]")


def _extract_actions(text: str) -> list[str]:
    """提取用户消息中全部括号内容（结构解析，无词表）。

    2026-09-06 用户裁决：动作词白名单删除——括号内容是否是身体动作、如何回应，
    由 agent 看原文自决；调用方以【括号动作】中性事实注入，不再用「必定生效」强制措辞。
    """
    out = []
    for m in _ACTION_BRACKET_RE.finditer(text):
        s = m.group(1).strip()
        if s:
            out.append(s)
    return out


# ---------------- GAL chat 模式（2026-09-09 用户裁决 v2）：发送前剥（动作）段（fail-closed 兜底） ----------------
# 括号口径对齐 _ACTION_BRACKET_RE（全角/半角、非嵌套），但去掉 20 字内容上限——剥除是事故级兜底，
# 必须覆盖长动作段（提示层【沟通模式】为主，本剥除是剥空前的最后防线）。
_CHAT_ACT_STRIP_RE = re.compile(r"[（(][^（()）]*[)）]")


def _strip_actions_chat(text: str) -> str:
    """chat 模式剥全部（动作）块（全角/半角括号段）。落库与发送共用同一份剥离后文本
    （v2 #3："发出什么=发生什么"——剥除点在 handle 内落库/发送/语音的共同上游）。
    gal/qq 模式不经过本函数（零行为变化）。剥空由调用点走既有「唔……」兜底形态。"""
    t = _CHAT_ACT_STRIP_RE.sub("", str(text or ""))
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _webgal_chat_mode() -> bool:
    """GAL 全局模式是否 chat（webgal 模块缺席/异常 → False=零行为变化）。"""
    try:
        from plugins import webgal as _webgal

        return _webgal.get_mode() == "chat"
    except Exception:  # noqa: BLE001
        return False


def _clean_search_query(q: str) -> str:
    """清洗查询词：去掉功能噪音词与标点（"工具""确认""帮我"等）。"""
    for w in ("工具", "确认", "核实", "一下", "看看", "帮我", "给我", "请", "麻烦", "吧", "呢", "啊", "哦", "吗", "谢谢", "用"):
        q = q.replace(w, "")
    return q.strip().strip("，,。！？!?：:；;、")


def _extract_search_query(text: str) -> str | None:
    """提取搜索查询词。

    触发词后内容优先；清洗后无效（全是"工具确认"类功能词）时回退整句清洗
    （"台风近期可是有两个呢，给我用搜索工具确认" → "台风近期可是有两个"）。
    未命中任何触发词 → None（不搜索）。
    """
    hit = False
    for t in _SEARCH_TRIGGERS:
        if t in text:
            hit = True
            q = _clean_search_query(text.split(t, 1)[1].strip())
            if len(q) >= 2:
                return q
    if not hit:
        return None
    # 回退：整句清洗并去掉残留触发词
    q = _clean_search_query(text)
    for t in _SEARCH_TRIGGERS:
        q = q.replace(t, "")
    q = q.strip().strip("，,。！？!?：:；;、")
    return q if len(q) >= 2 else None


def _now_context() -> str:
    """当前时间注记（薄壳）。"""
    return perception.now_context()


def _timeflow_note(user_id: str, group_id: str = "") -> str:
    """时间流动注记（薄壳；旧实现绕 _conn 直查 SQL 已改为 store 带锁方法）。"""
    try:
        _ts = memory.db.last_user_message_ts(user_id, group_id)
        _last = __import__("datetime").datetime.fromisoformat(_ts).timestamp() if _ts else None
        return perception.timeflow_note(_last)
    except Exception:  # noqa: BLE001
        return perception.timeflow_note(None)


# ---------------- 自动搜索触发（模型知识库较旧：时效性/新信息问题自动联网，避免只答旧数据） ----------------
AUTO_SEARCH_HINTS = (
    "最新", "今年", "最近", "现在", "新出", "发布", "2025", "2026", "价格", "行情",
    "涨了", "跌了", "市值", "排名", "多少钱", "上市", "出了吗", "怎么样",
)
# 2026-09-08 D4：知识问句词（用户实测：@bot 问"世界观游戏"类只听 LLM 兜底判断、不稳——
# 12B 判"我知道"即随口一答；规则快通道补知识问句词，问"是什么/哪来的/怎么玩/谁/攻略"类强制搜）
AUTO_SEARCH_KNOW_HINTS = (
    "是什么", "是什么人", "是哪", "哪来的", "哪部", "哪个", "是谁", "是谁啊", "哪位",
    "怎么玩", "攻略", "设定", "世界观", "出处", "哪里来的", "来自哪", "这个游戏", "这部动漫",
    "这张图", "这个梗", "这个角色", "这是什么动画", "这是什么游戏", "这是什么番", "原型",
)
AUTO_SEARCH_ENTITY_HINTS = (
    "台风", "天气", "地震", "油价", "股价", "汇率", "热搜", "新闻", "比赛", "比分",
    "疫情", "台风路径", "台风登陆", "台风预警", "涨停", "暴跌", "选举",
)
AUTO_SEARCH_MIN_LEN = 5  # 过短消息（纯问候）不触发
_AUTO_SEARCH_EXCLUDE = ("最近怎么样", "最近咋样", "现在在吗", "你怎么样", "最近好吗", "现在怎么样")


def _auto_search_query(text: str) -> str | None:
    """无显式搜索词时，规则快通道：时效性/实体词命中即自动搜索；闲聊问候不触发。"""
    if len(text) < AUTO_SEARCH_MIN_LEN or text in _AUTO_SEARCH_EXCLUDE:
        return None
    if (any(h in text for h in AUTO_SEARCH_HINTS) or any(h in text for h in AUTO_SEARCH_ENTITY_HINTS)
            or any(h in text for h in AUTO_SEARCH_KNOW_HINTS)):
        return text
    return None


async def _auto_search_llm(text: str) -> str | None:
    """模型理解为准：规则未命中时由 LLM 判断搜索意图并提炼查询词。
    仅当消息看起来是问题/查询（有 ?/?/吗/呢 或长度足够）时调用，避免每条消息都耗一次 LLM。"""
    t = text.strip()
    if len(t) < 4 or t in _AUTO_SEARCH_EXCLUDE:
        return None
    if not (t.endswith(("？", "?", "吗", "呢", "么", "啊")) or any(h in t for h in AUTO_SEARCH_HINTS)):
        return None  # 非问句且无时效词：大概率闲聊，不调用 LLM
    try:
        resp = await client.chat.completions.create(
            model=core_llm.resolve_model("main"),
            messages=[
                {"role": "system", "content": SEARCH_REFINE_PROMPT + " 若无搜索意图输出 {\"search\": null}"},
                {"role": "user", "content": t[:300]},
            ],
            temperature=0.1,
            max_tokens=80,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        raw = (resp.choices[0].message.content or "").strip().strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
        q = str(data.get("search", "") or "").strip()
        if q.lower() == "null":
            return None
        return q if 2 <= len(q) <= 60 else None
    except Exception as e:  # noqa: BLE001
        logger.debug("auto search llm failed: %s", e)
        return None


# ---------------- 联网搜索超时熔断（后手保障：绝不因外部网络卡死回复链路） ----------------
SEARCH_TIMEOUT = 12.0  # 单次外部联网调用超时（秒）；超时跳过联网，直接进回复（Bing 国内访问偏慢，8s 频繁误熔断）
SEARCH_TIMEOUT_NOTE = "\n\n【联网搜索】网络连接超时（12 秒无响应），本次跳过联网结果，请如实告知用户网络不畅。"

# 搜索查询词提炼提示（先思考：理解意图 → 精炼查询，替代规则切片）
SEARCH_REFINE_PROMPT = (
    "你是搜索查询提炼器。用户消息带有搜索意图（查资料/确认信息/了解最新情况等）。"
    "先理解用户真正想查什么，再提炼成**一条精炼的搜索查询词**：\n"
    "- 保留关键实体与限定词（如「台风」「近期」「2026」「路径」「几个」——验证类信息也要保留）\n"
    "- 丢弃客气话、语气词、命令词（「帮我」「麻烦」「搜索」「确认」「一下」「呢」「吗」等）\n"
    "- 查询词 2-30 字，语义完整（如「台风近期有几个 2026」）\n"
    "输出严格 JSON（无其他文字）：{\"search\": \"查询词\"}"
)


async def _refine_search_query(text: str) -> str | None:
    """LLM 提炼搜索查询词（先思考再操作）。失败/超时返回 None（调用方回退规则查询词）。"""
    try:
        resp = await client.chat.completions.create(
            model=core_llm.resolve_model("main"),
            messages=[
                {"role": "system", "content": SEARCH_REFINE_PROMPT},
                {"role": "user", "content": text[:300]},
            ],
            temperature=0.1,
            max_tokens=80,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},  # 提炼无需思考
        )
        raw = (resp.choices[0].message.content or "").strip().strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
        q = str(data.get("search", "") or "").strip()
        return q if 2 <= len(q) <= 60 else None
    except Exception as e:  # noqa: BLE001
        logger.debug("search refine failed: %s", e)
        return None


async def _run_search(coro):
    """统一联网入口：8 秒超时熔断。返回 (结果 or None, 错误提示 or "")。

    任何外部搜索（Bing 网页/图片、ibiblio 档案站）都经此调用；
    超时/异常时返回 None + 中断提示，主回复流程照常继续，绝不卡死。
    """
    try:
        return await asyncio.wait_for(coro, timeout=SEARCH_TIMEOUT), ""
    except asyncio.TimeoutError:
        logger.warning("search timed out after %.0fs", SEARCH_TIMEOUT)
        return None, SEARCH_TIMEOUT_NOTE
    except Exception as e:  # noqa: BLE001
        logger.warning("search failed: %s", e)
        return None, f"\n\n【联网搜索】搜索异常（{type(e).__name__}），本次跳过联网结果。"


async def _search_images(search_query: str) -> tuple[str | None, str]:
    """搜图子流程：净化关键词；舰船语境优先查 ibiblio 档案站数据，再配英文精确搜图。

    返回 (image_url or None, search_note)。任何失败都有可见提示，绝不静默。
    """
    img_query = _clean_image_query(search_query)
    is_ship = any(h in img_query for h in SHIP_SEARCH_HINTS) or (
        "号" in img_query
        and any(w in img_query for w in ("海军", "舰", "航母", "战列", "驱逐", "巡洋", "潜艇", "护卫"))
    )
    if is_ship:
        en = await _translate_ship_name(img_query)
        if en:
            # 档案站优先：USS 舰先查 ibiblio 1940-1945 美国海军档案数据
            archive_note = ""
            if en.upper().startswith("USS"):
                ship_res, s_err = await _run_search(search.ibiblio_ship_lookup(en))
                if s_err:
                    return None, s_err
                if ship_res:
                    class_desc = (
                        f"{ship_res['class_name']}级" if ship_res.get("class_name") else ship_res["type_name"]
                    )
                    archive_note = (
                        f"\n\n【舰船档案】（{en}，{class_desc}，1940-1945 美国海军，"
                        f"来源：ibiblio.org/hyperwar/USN）\n舰船页面：{ship_res['url']}"
                    )
            img_res, s_err2 = await _run_search(search.image_search_en(f'"{en}" warship', n=3))
            if s_err2:
                return None, s_err2
            if img_res:
                urls, _ = img_res
                if urls:
                    return urls[0], archive_note or f"\n\n【已搜图】已为用户搜索舰船图片（{en}），回复末尾附带图片。"
            if archive_note:
                return None, archive_note + "\n\n【已搜图】未能找到相关舰船图片，请如实告知用户。"
            return None, "\n\n【已搜图】未能找到相关舰船图片，请如实告知用户。"
        return None, "\n\n【已搜图】未能识别舰船名称，无图片结果。"
    img_res, s_err = await _run_search(search.image_search(img_query, n=3))
    if s_err:
        return None, s_err
    if img_res:
        urls, err = img_res
        if urls:
            return urls[0], f"\n\n【已搜图】已为用户搜索图片（{img_query}），回复末尾附带图片。"
        if err:
            return None, f"\n\n【联网搜索】{err}，请如实告知用户。"
    return None, f"\n\n【已搜图】未能找到“{img_query}”的相关图片，请如实告知用户。"


# ---------------- 回复切片（模拟真人分段说话） ----------------
REPLY_MAX_SEGS = 3      # 最多切成几段
REPLY_SEG_GAP = 0.9     # 段间间隔（秒）
_SENT_SPLIT = re.compile(r"(?<=[。！？!?…~～])")


def _max_segs_for(reply_len: int) -> int:
    """按回复总长度决定段数：≤10字 1 段 / ≤20字 2 段 / >20字 3 段。"""
    if reply_len <= 10:
        return 1
    if reply_len <= 20:
        return 2
    return REPLY_MAX_SEGS


def _split_reply(reply: str, max_segs: int | None = None) -> list[str]:
    """按语义单元切分（动作行独立、台词合并），段数上限按长度，不硬凑段数。

    规则：
    - 先按行/动作/台词合并出自然语义单元；
    - 只有"单段 ≥25 字且可语义拆分（拆后每段 ≥4 字）"时才补切（避免把一句话硬拆成重复段）；
    - 段与段之间内容高度相似（替换个别词汇的大意重复）自动合并，防止"第三段凑数重复"；
    - 段数过多时合并尾部。
    """
    raw_lines = [ln.strip() for ln in reply.split("\n") if ln.strip()]
    if not raw_lines:
        return [reply]
    segs: list[str] = []
    for line in raw_lines:
        is_action = line.startswith("*") and line.endswith("*")
        if segs and not is_action and not segs[-1].startswith("*"):
            segs[-1] = segs[-1] + " " + line  # 连续台词合并，保持语义完整
        else:
            segs.append(line)
    target = max_segs if max_segs is not None else _max_segs_for(len(reply))
    # 补切：段内含 ≥2 个完整句子（标点吸附在句尾，单句/纯动作段不会被拆）时才拆，
    # 拆后段数超出目标时由末尾合并兜底——不硬凑出"大意不变"的重复段
    while len(segs) < target:
        best_i, best_parts = -1, None
        for i, s in enumerate(segs):
            # 纯动作段（*...* 完整包裹）不参与切分；"*动作*+台词"混合行可切
            if s.startswith("*") and s.endswith("*"):
                continue
            parts = [p.strip() for p in _SENT_SPLIT.split(s) if p.strip()]
            if len(parts) >= 2 and (best_i < 0 or len(s) > len(segs[best_i])):
                best_i, best_parts = i, parts
        if best_i < 0:
            break  # 没有可再拆的段，保持现状（不硬凑段数）
        segs = segs[:best_i] + best_parts + segs[best_i + 1 :]
    # 内部重复段合并：后段与前段高度相似（替换个别词汇的大意重复）→ 并入前段
    merged: list[str] = []
    for s in segs:
        if merged and (_too_similar(s, [merged[-1]]) or _too_similar(s, merged)):
            merged[-1] = merged[-1] + " " + s
        else:
            merged.append(s)
    segs = merged
    # 段数过多：合并尾部
    if len(segs) > target:
        head, tail = segs[: target - 1], segs[target - 1 :]
        segs = head + ["".join(tail)]
    return segs


def _extract_group_action(event) -> tuple[str, str, str] | None:
    """解析群聊"@bot + @他人 + 动作指令" → (目标QQ, 目标显示名, 动作描述)。无则 None。

    目标完全以 @ 段（data.qq）为准——任何被 @ 的人都是目标，不依赖具体名字。
    """
    if not isinstance(event, GroupMessageEvent):
        return None
    self_id = str(getattr(event, "self_id", ""))
    ats = [seg for seg in event.message if seg.type == "at"]
    if not ats or not any(str(seg.data.get("qq", "")) == self_id for seg in ats):
        return None  # 未 @bot
    others = [seg for seg in ats if str(seg.data.get("qq", "")) not in (self_id, "all")]
    if not others:
        return None  # 未 @他人
    target = others[0]
    target_id = str(target.data.get("qq", ""))
    tname = str(target.data.get("name", "") or "")
    # 动作描述：去引导词，并去掉 @ 段残留文本（plaintext 会把 @ 转成文本）
    action = event.get_plaintext().strip()
    for w in ("你去给", "你去", "去给", "你去帮我", "帮我", "给我", "去", "给"):
        if action.startswith(w):
            action = action[len(w):].strip()
            break
    action = re.sub(r"@\S+", "", action)  # 去掉 @名字 残留
    action = action.strip("，,。！？!? ")
    return (target_id, tname, action) if action else None


def _character_hits(text: str, universe: str | None = None) -> list[dict]:
    """角色命中·纯命中判据（2026-09-12 T4.1 抽出，零新配置/零新查询）。

    原命中段内联在 `_character_lookup` 里（"提到谁"的知识），这里提为独立函数供两处共用：
    ① `_character_lookup` 组装注入文本；② 思考自决判据三（本轮提到世界观角色）。
    不抽出来就只能复制一份 substring 循环，属重复逻辑（违反 T4.1"不重复造"）。
    """
    if not universe or not text:
        return []
    try:
        from . import persona as _persona

        _un = _persona._load_universe_roles(universe)
        if not _un or not _un.get("roles"):
            return []
        hits = []
        for r in _un["roles"]:
            nm = str(r.get("name", "") or "")
            if len(nm) >= 2 and nm in text and nm not in [h.get("name") for h in hits]:
                hits.append(r)
                if len(hits) >= 2:
                    break
        return hits
    except Exception:  # noqa: BLE001
        return []


def _character_lookup(text: str, universe: str | None = None) -> str:
    """角色关系·动态命中（2026-09-05）：对话提到当前世界观角色 → 查库注入关系（提及谁注入谁，最多 2 条）。
    无 universe（原创卡）/无命中 → 空串。"""
    if not universe or not text:
        return ""
    try:
        hits = _character_hits(text, universe)
        if not hits:
            return ""
        lines = "；".join(
            f"{r.get('name', '')}（{r.get('desc', '')}）" + (f"【关系】{r.get('rel', '')}" if r.get("rel") else "")
            for r in hits
        )
        # 2026-09-12 盲审 P3-6：spec T3 要求"身份锚优先级高于第三人称关系条目"。
        # 注入**顺序**无法改（锚在 persona 系统提示里、本注记在 brain 侧追加，锚本就在前），
        # 但只靠"在前"不够——今早漂移的正是「同时提到第三人称角色」这一刻，
        # 长提示里靠前的锚容易被关系条目挤掉注意力。故在关系条目后**再钉一句身份提醒**，
        # 保证「谁是对方」与「谁是第三人称」在同一视野内对照。零新标记、零额外 LLM。
        _self_anchor = ""
        try:
            from . import persona as _persona  # 与 _character_hits 同源读取（mtime 缓存，零额外 IO）

            _pv = next((r for r in ((_persona._load_universe_roles(universe) or {}).get("roles") or [])
                        if "玩家视角" in str(r.get("desc") or "")), None)
            if _pv and _pv.get("name"):
                _self_anchor = (
                    f"（提醒：正在和你对话的这个人就是「{_pv['name']}」本人——"
                    f"上面这些是你与{_pv['name']}共同认识的人，不要把{_pv['name']}和他们混为一谈。）"
                )
        except Exception:  # noqa: BLE001
            _self_anchor = ""
        return "\n\n【角色关系】" + lines + "（按你们的关系自然回应，像真的认识。）" + _self_anchor
    except Exception:  # noqa: BLE001
        return ""


async def _reply_quote_text(bot, event) -> str:
    """消息剥离·引用：返回被引用消息的文本（≤100 字）；无引用/拉取失败返回空串。
    2026-09-05：感知层不得只见文本不见结构——QQ 引用段（reply）不在 get_plaintext 里，必须显式拉取。"""
    try:
        for seg in getattr(event, "message", []) or []:
            if getattr(seg, "type", "") == "reply":
                rid = (getattr(seg, "data", {}) or {}).get("id")
                if rid:
                    resp = await bot.get_msg(message_id=int(rid))
                    mtxt = ""
                    for mseg in resp.get("message", []) or []:
                        if getattr(mseg, "type", "") == "text":
                            mtxt += ((getattr(mseg, "data", {}) or {}).get("text", "") or "")
                        elif getattr(mseg, "type", "") == "at":
                            mtxt += "@" + str((getattr(mseg, "data", {}) or {}).get("name", "") or "")
                    mtxt = mtxt.strip()
                    if mtxt:
                        return mtxt[:100]
                    return ""
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _msg_struct_note(event) -> str:
    """消息结构注记（薄壳）。"""
    try:
        segs = list(getattr(event, "message", []) or [])
        ats = [str((getattr(s, "data", {}) or {}).get("qq", "")) for s in segs if getattr(s, "type", "") == "at"]
        has_reply = any(getattr(s, "type", "") == "reply" for s in segs)
        return perception.msg_struct_note(ats, str(getattr(event, "self_id", "") or ""), has_reply)
    except Exception:  # noqa: BLE001
        return ""


# 语言感知（2026-09-06：英/日文输入——事实注记；翻译与否由 bot 自决，不做关键词触发）
_LANG_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_LANG_EN_RE = re.compile(r"[A-Za-z]")
_LANG_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _lang_note(event) -> str:
    """语言注记（薄壳：段文本提取在 brain，判定在 core.perception）。"""
    try:
        segs = list(getattr(event, "message", []) or [])
        t = "".join(str((getattr(s, "data", {}) or {}).get("text", "")) for s in segs
                    if getattr(s, "type", "") == "text")
        return perception.lang_note(t)
    except Exception:  # noqa: BLE001
        return ""


_MEMES_CACHE = {"mtime": -1.0, "data": {}}
# 2026-09-08 C-4 路径对齐：真实 memes.json（79 条存量）在 qq-bot/data/，原 parents[3]
# （E:/robot/data）从未有过该文件 → stat 恒抛 → _meme_note 恒 ""，群聊玩梗感知从未生效；
# 对齐 memory.MEMES_PATH 同一文件（供给侧 C-4 亦写此处），核心数据根收敛另行裁决。
_MEMES_PATH = __import__("pathlib").Path(__file__).resolve().parents[2] / "data" / "memes.json"


def _meme_note(text: str) -> str:
    """梗知识库注记（薄壳：mtime 缓存读取在此，组装在 core.perception）。"""
    try:
        _cfg = _MEMES_CACHE
        _mt = _MEMES_PATH.stat().st_mtime
        if _cfg["mtime"] != _mt:
            try:
                _cfg["data"] = json.loads(_MEMES_PATH.read_text(encoding="utf-8-sig"))
            except Exception:
                _cfg["data"] = {}
            _cfg["mtime"] = _mt
        return perception.meme_note(text, _cfg.get("data") or {})
    except Exception:
        return ""


def _persona_current_names() -> set:
    """当前人设全部可被点名名字（卡名/昵称+别名；供提及感知/文本点名共用）。"""
    try:
        return perception._persona_names(_persona_card(), _persona_name(), PERSONA_ALIAS)
    except Exception:  # noqa: BLE001
        return set()


def _name_mention_note(text: str) -> str:
    """人设名提及感知（薄壳）。"""
    return perception.name_mention_note(text, _persona_current_names())


def _is_text_at_me(text: str) -> bool:
    """文本形式点名判定（薄壳）。"""
    return perception.text_at_me(text, _persona_current_names())


def _dialogue_lines(user_id: str, limit: int = 6, group_id: str = "") -> str:
    """最近对话记录（带发言人 + 时间注记）——bot 自己看记录、自己分辨谁说的/多新的（2026-09-06：
    旧对话没标龄曾被当"最近的对话"引用（25 小时前的剧情被主动消息当作当下话题））。"""
    try:
        rows = memory.db.recent_messages(user_id, limit=limit, group_id=group_id)
        # 2026-09-06 人设切换过滤：切换前的对话不再注入（旧卡台词别污染新卡感知）
        try:
            _sw = _persona_switch_ts(user_id)
            if _sw:
                import datetime as _dtc
                _swt = float(_dtc.datetime.fromisoformat(str(_sw)).timestamp())
                _keep = []
                for m in rows:
                    try:
                        _mts = _parse_msg_ts(m.get("ts") or "")
                        if _mts and _mts >= _swt:
                            _keep.append(m)
                    except Exception:  # noqa: BLE001
                        _keep.append(m)
                rows = _keep
        except Exception:  # noqa: BLE001
            pass
        if not rows:
            return ""
        lines = []
        now = time.time()
        try:
            from core import special as _sp_dl

            _strip_dl = _sp_dl.strip_protocol_residue
        except Exception:  # noqa: BLE001
            _strip_dl = None
        for m in rows:
            who = "你（bot）" if m.get("role") == "assistant" else (m.get("sender_name") or "TA")
            _ago = ""
            try:
                _ts = m.get("ts") or ""
                _ago_s = now - _parse_msg_ts(_ts) if _ts else 0
                if _ago_s > 3600:
                    _ago = f"（{_ago_s / 3600:.0f} 小时前）"
                elif _ago_s > 60:
                    _ago = f"（{int(_ago_s / 60)} 分钟前）"
            except Exception:  # noqa: BLE001
                pass
            # 2026-09-09 盲审 P2：库里已存的标记残留行（别名形态）曾经 proactive 感知回喂——读侧统一剥净
            _body = str(m.get("content") or "")
            if _strip_dl is not None and m.get("role") == "assistant":
                _body = _strip_dl(_body)
            lines.append(f"[{who}]：{_body[:80]}{_ago}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


def _collapse_dialogue_sample(text: str, own_names: set[str] | tuple = ()) -> str:
    """对话样本泄漏防护（2026-09-07）：模型偶发把感知上下文的「昵称：内容」格式学了过去，
    输出成多轮对话样本（含对方的台词）——只保留最后一轮（bot 自己的回复），并剥掉自身名前缀。
    仅当各轮都带「称呼：」前缀时才判定为样本（正常的多行回复不受影响）。"""
    turns = [t.strip() for t in (text or "").split("\n") if t.strip()]
    if len(turns) > 1:
        prefixed = sum(1 for t in turns if re.match(r"^[^：:，。！？\s]{1,16}[：:]", t))
        if prefixed >= len(turns) - 1:
            text = turns[-1]
    own = "|".join(sorted({n for n in (own_names or ()) if n} | {"bot"}, key=len, reverse=True))
    if own:
        text = re.sub(rf"^({own})[：:]\s*", "", text).strip()
    return text


def _atify(reply: str, target_id: str) -> Message:
    """把回复中的 @{target_id} 占位替换为真正的 @ 段（Message）。"""
    msg = Message()
    for part in re.split(rf"(@?\s*{target_id})", reply):
        p = part.strip()
        if p in (target_id, f"@{target_id}"):
            msg += MessageSegment.at(int(target_id)) + " "  # 2026-09-05：@段后空一格（QQ 显示分开）
        elif p:
            msg += p
    return msg


async def _gen_whisper(user_text: str, group_reply: str, card: dict | None = None) -> str:
    """群聊私语：主人群聊触发特殊状态时，生成"群里说不出口"的私密内容（私聊单独发送）。失败返回空串。
    2026-09-09 铁律#2：身份名按当前人设卡（旧版写死单一卡名=人设泄漏）；卡没名不生成。"""
    try:
        _wname = str((card or {}).get("name") or "").strip()
        if not _wname:
            return ""
        whisper_prompt = (
            f"你是「{_wname}」，对方是你的主人。刚才在群聊里，你正常回复了话题，"
            "但把本该说出口的（色情/暗示/亲密/被状态牵引的）内容全部切了下来。"
            "现在是私聊，只有主人一个人能看到——把**被切掉的这些内容**说出来。"
            "按你当前的状态与人格（傲娇/情欲/催眠/洗脑/恶堕/道具）自然表达，1-3 句，可带 *动作描写*。"
        )
        resp = await client.chat.completions.create(
            model=core_llm.resolve_model("main"),
            messages=[
                {"role": "system", "content": whisper_prompt},
                {"role": "user", "content": f"群聊里主人说了：{user_text[:200]}\n你在群里收敛后的回复：{group_reply[:200]}"},
            ],
            temperature=0.95,
            max_tokens=400,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},  # 私语无需思考
        )
        whisper = (resp.choices[0].message.content or "").strip()
        return whisper[:500]
    except Exception as e:  # noqa: BLE001
        logger.debug("whisper gen failed: %s", e)
        return ""


def _typing_on(event):
    """私聊：发送前亮「正在输入」（打字=输出阶段才显示；思考/自决停顿阶段不亮——像真人想完才开始打）。
    2026-09-07 P2 修复：call_api 是协程，曾不 await（协程对象被丢弃，功能从未生效）。
    保持同步壳 + 后台任务，调用点无需等待状态上报。
    2026-09-10 审计 P2：web 页面合成轮（webgal CaptureBot，从不注册进 driver）直接 return——
    合成轮整链"零 QQ 动作"红线，曾每次回复都在主人真实 QQ 上亮「正在输入」。"""
    try:
        from plugins import webgal as _wg_typing

        if _wg_typing.in_synthetic_round():
            return  # web 合成轮：不碰真实 QQ（无 webgal 插件时 import 失败被吞=零影响）
    except Exception:  # noqa: BLE001
        pass
    if isinstance(event, GroupMessageEvent):
        return
    try:
        from nonebot import get_bot as _gb

        _b = _gb()
        bgtasks.spawn(
            _b.call_api("set_input_status", user_id=int(event.get_user_id()), event_type=1))
    except Exception:  # noqa: BLE001
        pass


async def _send_sliced(matcher, event: MessageEvent, reply, image_url: str | None = None, voice_seg=None, sticker_seg=None):
    """分多条发送回复（段间停顿模拟打字；可选末尾附图片）。
    voice_seg/sticker_seg：语音段/表情段。
    规则（QQ 限制）：**语音与图片互斥**——有语音时文字+语音同一条消息，
    表情包/图片紧随其后单独发（确定性顺序）；无语音时全部并入文字消息。

    拟人打字节奏：真人不会秒回——按回复长度轻微延迟（0.4-2.5s 内随机）。

    reply 可为 str 或 Message；Message（含 @ 段等富文本）整条发送不切片。
    2026-09-11 热修十一（用户裁决）：群聊被 @ **不需要 @ 回去**——回复就是普通群消息，
    不加 @ 段也不加引用段（本函数全部分支均不再拼接 at 前缀段）。"""
    # 2026-09-06 只用一种：语音触发 → 只发语音（不文字+语音双发；长话分多条语音，像文字分段）。
    # 表情/图片独立伴随，与文字回复无关。（2026-09-11 热修十一：群聊回复不再带 @ 前缀。）
    if voice_seg:
        try:
            await asyncio.sleep(random.uniform(0.2, 0.6))
        except Exception:  # noqa: BLE001
            pass
        for _i, _seg in enumerate(voice_seg):
            msg = Message()
            msg = msg + _seg
            await matcher.send(msg)
            if _i < len(voice_seg) - 1:
                await asyncio.sleep(random.uniform(1.0, 2.2))  # 语音段落间：像一口气说了几段
                _typing_on(event)  # 段间保持「正在输入」（还在打字，只是换下一段）
        extras = Message()
        if sticker_seg is not None:
            extras = extras + sticker_seg
        if image_url:
            extras = extras + MessageSegment.image(image_url)
        if extras:
            await matcher.send(extras)
        return
    try:
        _base = min(2.5, 0.4 + len(str(reply)) * 0.04)
        await asyncio.sleep(random.uniform(_base * 0.7, _base * 1.3))
    except Exception:  # noqa: BLE001
        pass
    if isinstance(reply, Message):
        msg = reply
        if voice_seg is not None:
            msg = msg + voice_seg
            await matcher.send(msg)
            extras = Message()
            if sticker_seg is not None:
                extras = extras + sticker_seg
            if image_url:
                extras = extras + MessageSegment.image(image_url)
            if extras:
                await matcher.send(extras)
            return
        if sticker_seg is not None:
            msg = msg + sticker_seg
        if image_url:
            msg = msg + MessageSegment.image(image_url)
        await matcher.send(msg)
        return
    segs = _split_reply(reply)
    extras = Message()
    if voice_seg is not None and sticker_seg is not None:
        extras = extras + sticker_seg
    if voice_seg is not None and image_url:
        extras = extras + MessageSegment.image(image_url)
    for i, seg in enumerate(segs):
        msg = Message(seg)
        if i == 0 and voice_seg is not None:
            msg = msg + voice_seg
        if i == len(segs) - 1:
            if voice_seg is None:
                if sticker_seg is not None:
                    msg = msg + sticker_seg
                if image_url:
                    msg = msg + MessageSegment.image(image_url)
            # 用 send 而非 finish：finish 会抛异常终止 handler，导致后续私语等逻辑不执行
            await matcher.send(msg)
        else:
            await matcher.send(msg)
            await asyncio.sleep(random.uniform(REPLY_SEG_GAP * 0.8, REPLY_SEG_GAP * 1.8))  # 段间随机化（真人打字快慢不一）
            _typing_on(event)  # 段间保持「正在输入」（还在打字，只是换下一段）
    if extras:
        await matcher.send(extras)


# ---------------- 基础词汇库（data/vocab_base.json，可按需扩展） ----------------
VOCAB_FILE = DATA_ROOT / "vocab_base.json"


def _load_vocab() -> dict:
    """加载基础词汇库（人格/性格/动作/语气/情感/色情/性爱）；缺失或损坏时返回空。"""
    try:
        data = json.loads(VOCAB_FILE.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("vocab_base.json 加载失败，使用内置默认词表")
        return {}


VOCAB = _load_vocab()


def _v(section: str, default: tuple) -> tuple:
    """取词汇库某分类；缺省回退默认值。"""
    items = VOCAB.get(section)
    if isinstance(items, list) and items:
        return tuple(str(x).strip() for x in items if str(x).strip())
    return default


def _recent_action_heavy(user_id: str, group_id: str | None = None) -> bool:
    """最近回复是否普遍以*动作*段开头（>70%），是则本轮强制禁止动作开场。池按语境隔离。"""
    msgs = memory.history_messages(user_id, max_turns=12, group_id=group_id)
    ass = [m["content"] for m in msgs if m["role"] == "assistant"][-8:]
    if len(ass) < 3:
        return False
    with_action = sum(1 for c in ass if "*" in c)
    return with_action / len(ass) >= 0.7


# ---------------- 命令语气检测（强硬命令 -> 按亲密度响应） ----------------
CMD_TONE_WORDS = ("立刻", "马上", "命令", "必须", "给我", "跪下", "服从", "照做", "闭嘴", "过来")


def _has_command_tone(text: str) -> bool:
    return any(w in text for w in CMD_TONE_WORDS)


# ---------------- 破碎腔检测（情欲状态外的失神话术，强制重写） ----------------


async def _compress_user_msg(text: str, limit: int = 400) -> str:
    """超长用户消息压缩（保留要点，供上下文使用）。"""
    try:
        resp = await client.chat.completions.create(
            model=core_llm.resolve_model("main"),
            messages=[
                {"role": "system", "content": "压缩下面的用户消息，保留所有关键信息（事实、请求、问题），用简洁的中文概括，不超过120字。只输出压缩结果。"},
                {"role": "user", "content": text[:2000]},
            ],
            temperature=0.3,
            max_tokens=limit,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},  # 压缩无需思考
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        return text[:120] + "…"


# ---------------- 回复去重（代码级保底，防话术循环） ----------------
REPLY_DEDUP_RATIO = 0.60  # 相似度阈值（状态激活时模型易模板循环，阈值收紧）


def _too_similar(new: str, olds: list[str]) -> bool:
    from difflib import SequenceMatcher

    for old in olds:
        if not old:
            continue
        # 开头 20 字完全一致 = 铁重复
        if new[:20] and new[:20] == old[:20]:
            return True
        # 句子级复读检测：新回复包含历史回复中的任意完整句子（>=15 字）
        for sent in re.split(r"[。！？!?…~；;]", old):
            sent = sent.strip()
            if len(sent) >= 15 and sent in new:
                return True
        # 整体相似度
        if SequenceMatcher(None, new[:120], old[:120]).ratio() > REPLY_DEDUP_RATIO:
            return True
    return False


# 非台词语段（2026-09-03 用户要求：活人感=纯台词，动作/思考类输出一律剥离）
_NON_SPEECH_RE = re.compile(
    r"[（(][^（()）]{1,100}[)）]|[*＊][^*＊]{1,120}[*＊]|【[^】]{1,50}】|《[^》]{1,30}》"
    r"|(?:内心|心里|心想|内心独白|我心想|（心想)[：:][^。！？!?～~…\n]{0,40}[。！？!?～~…]?"
)

# ---- 2026-09-07 动作口径·事故级红线（用户实测：提示层压不住"（动作）内混写反应/神态"）----
# 检测目标：括号区间内出现的反应/神态/声音/身体状态词（模型把"（抬起眼看你，眼里映着火光）"整句当演出写）。
# 词表仅覆盖"不是肢体动作"的确定性反应词；宁可误杀触发一次重试（反馈会说明），不可漏过。
_REACTION_CHARS = (
    "视线|目光|眼底|眼角|眼眶|眼眸|眼睫|睫毛|眼神|神色|脸色|脸颊|耳根|耳尖|泛红|发红|红晕|通红|"
    "呼吸|气息|喘息|嗓音|声音|语气|尾音|声线|低鸣|闷哼|呻吟|呜咽|沙哑|软意|颤抖|发颤|战栗|僵住|"
    "僵硬|瑟缩|发麻|心口|心跳|心悸|体温|温度|皮肤|神经|紊乱|破碎|压抑|控制不住|没平复|余温"
)
_ACTION_PAIR_RE = re.compile(r"[（(]([^（()）]{1,100})[)）]")
_REACTION_PAT_RE = re.compile("(?:" + _REACTION_CHARS + ")")
# 注：_REACTION_CHARS 为 alternation 字符串（"视线|目光|…"），用分组避免被并成字符类（单字误命中"呼喊"类）


def _reaction_guard(text: str) -> str | None:
    """动作口径红线（事故级）：返回违规描述或有 None。检测逻辑：扫描每个（ ）区间，
    区间内出现反应/神态/声音/身体状态词 → 违规（模型常把动作+反应混写进一个括号）。"""
    if not text:
        return None
    for m in _ACTION_PAIR_RE.finditer(text):
        inner = m.group(1)
        if _REACTION_PAT_RE.search(inner):
            return (
                "你写的（ ）里夹带了反应/神态/声音/身体状态的描写（眼睫、视线、呼吸、嗓音、颤抖、"
                "僵住、红晕等——这些不是动作）——全部收回心里；一个括号只装一个实际做出的肢体动作本身，"
                "不加任何修饰、不夹带第二项；动作做什么由你自己按场景决定，宁可少写不要修饰。"
            )
    return None


_ACT_KEEP_RE = __import__("re").compile(
    r"(?:走到|走进|走出|站起身来|站起身|坐下|起身|转身|转过|伸出|伸出手|抬脚|抬腿|抬手|抬起|拿起|放下|握|抓|推|拉|走|站|坐|迈|退后|上前|靠近|蹲|弯腰|侧身|抱|揉|戳|拍|按|捏|摸|搂|牵|伸手)"
)


# 2026-09-08 P3 结构模板检测（agent 自决优先形态）：检测"起手架势"跨轮复现——
# 词面防重复拦不住"既然X……我就Y……"式换词同构（用户实测复读实锤：同结构套娃 6+ 次，词面全换）。
# 机器只算事实（重不重复=算术），改不改由 agent 自决：命中 → 注入**感知事实**（非硬拦、不改输出）。
_TPL_WINDOW: dict[str, list] = {}  # 键=uid:gid → 最近回复结构键（cap 6）
_TPL_TOUCH: dict[str, float] = {}  # C12：键最近更新时间（_sweep_stale 用——值无时间戳，伴生记账）


def _tpl_lcp(a: str, b: str) -> int:
    _n = 0
    for _x, _y in zip(a, b):
        if _x != _y:
            break
        _n += 1
    return _n


def _tpl_key(reply: str) -> str:
    """回复结构键：剥（动作）与【】标记块后取开头 8 字（去标点）——"既然你已经把话挑"级。
    盲审修正：先剥【生活：…】/【场景：…】等协议标记（曾残留进键前缀——"生活/场景"2 字头会误触发②）。"""
    t = re.sub(r"【[^】]{1,40}】", "", str(reply or ""))
    t = re.sub(r"（[^（）]{1,24}）", "", t)
    t = re.sub(r"[。！？!?\s，,、…~～*【】\[\]'“”]", "", t)
    return t[:8]


def _tpl_repeat_note(user_id: str, gid: str = "") -> str:
    """最近 6 条中：与最新条同起手（公共前缀≥5）≥2 次，或同 2 字起手 ≥4 次 → 感知事实；无则空串。
    盲审修正：纯短应答（键长<4，"嗯/好呀"级）过滤——真人日常多轮"嗯…"不算结构复读（防误报）。"""
    keys = [k for k in _TPL_WINDOW.get(str(user_id) + ":" + str(gid or ""), []) if len(k) >= 4]
    if len(keys) < 3:
        return ""
    last = keys[-1]
    if not last:
        return ""
    same = sum(1 for k in keys[:-1] if k and _tpl_lcp(last, k) >= 5)
    head2 = last[:2]
    head2_cnt = sum(1 for k in keys if k[:2] == head2)
    if same >= 2 or head2_cnt >= 4:
        return ("（你最近几轮回复的开头结构很接近——不是同一句，是同一个架势；"
                "要不要换个开法，你自己定。）")
    return ""


def _tpl_record(user_id: str, gid: str, reply: str) -> None:
    """登记结构键（cap 6）。"""
    _key = str(user_id) + ":" + str(gid or "")
    _dq = _TPL_WINDOW.setdefault(_key, [])
    _dq.append(_tpl_key(reply))
    if len(_dq) > 6:
        _dq[:] = _dq[-6:]
    _TPL_TOUCH[_key] = time.time()  # C12：键活跃记账（>7 天不活跃由 _sweep_stale 清）


# 2026-09-08 P3 盲区补强（用户实测："内容不重复，但节奏重复——短句-…-超长句"）：
# 起手词检测管不住"节奏模板"（内容每次是新的）——补节奏维度：短句垫、省略、长句收的结构键。
_TP_RHYTHM_WINDOW: dict[str, list] = {}  # 键=uid:gid → 最近节奏键（cap 6）
_TP_RHYTHM_TOUCH: dict[str, float] = {}  # C12：键最近更新时间（伴生记账，_sweep_stale 用）


def _tpl_rhythm(reply: str) -> str:
    """回复节奏键（粗化版——实测粒度太细致 0/16 命中：段数/长句数微差即不同键，
    而"短垫+省略+长收+动作"肉眼同类被漏判）：h{开头段级}-lt{收尾段级}-d{省略}-a{动作0/1+}。
    段级：S=≤6 字（短）/ M=7-19（中）/ L=≥20（长）。例：'（动作）嗯。……今天天气真好我们出去走走吧' → hS-ltL-d1-a1。"""
    t = str(reply or "")
    _acts = len(re.findall(r"（[^（）]{1,24}）", t))
    _t2 = re.sub(r"（[^（）]{1,24}）", "。", t)
    _segs = [s.strip("。！？!?…，,、 ") for s in re.split(r"[。！？!?]+", _t2) if s.strip("。！？!?…，,、 ")]
    if not _segs:
        return ""

    def _lvl(s: str) -> str:
        return "S" if len(s) <= 6 else ("M" if len(s) <= 19 else "L")

    return f"h{_lvl(_segs[0])}-lt{_lvl(_segs[-1])}-d{1 if '…' in t else 0}-a{min(_acts, 1)}"


def _tpl_rhythm_note(user_id: str, gid: str = "") -> str:
    """节奏键最近 6 条中 ≥3 条相同 → 感知事实（agent 自决换节奏）；无则空串。"""
    _keys = [k for k in _TP_RHYTHM_WINDOW.get(str(user_id) + ":" + str(gid or ""), []) if k]
    if len(_keys) < 3:
        return ""
    from collections import Counter as _C

    _k, _n = _C(_keys).most_common(1)[0]
    if _n >= 3:
        return ("（你最近几轮的句式节奏很像——同样是短的垫、长的收，连停顿的位置都差不多；"
                "换个节奏试试，你自己来。）")
    return ""


# 2026-09-08 群插话句式骨架防重复（群聊插话链此前没有主链那套结构压力——近两日群插话
# 全是同一句式骨架的附和）：记录 bot 最近几次群插话的结构节奏键（_tpl_rhythm），
# 由 agent.graph._perceive 注入感知（换不换结构由 agent 自决，也可以选择不插）。
# per-group 最近 4 个，模块级 dict（C12 同款伴生 touch 记账，_sweep_stale 清扫防无界增长）。
_BANTER_RHYTHM: dict[str, list] = {}          # 键=group_id → 最近结构键（cap 4）
_BANTER_RHYTHM_TOUCH: dict[str, float] = {}   # 键最近更新时间（_sweep_stale 用）


def _banter_rhythm_record(group_id: str, reply: str) -> None:
    """群插话发送成功后登记结构键（cap 4；空键=空串/纯标点不记）。"""
    _k = _tpl_rhythm(reply)
    if not _k:
        return
    _dq = _BANTER_RHYTHM.setdefault(str(group_id), [])
    _dq.append(_k)
    if len(_dq) > 4:
        _dq[:] = _dq[-4:]
    _BANTER_RHYTHM_TOUCH[str(group_id)] = time.time()  # C12：键活跃记账


def _banter_rhythm_note(group_id: str) -> str:
    """本群最近插话结构键存在 → 感知句（换结构/不插，agent 自决）；无则空串。"""
    _keys = [k for k in _BANTER_RHYTHM.get(str(group_id), []) if k]
    if not _keys:
        return ""
    return ("你最近几次在群里说话的结构节奏：" + "、".join(_keys)
            + "——这次换一种完全不同的结构和节奏（也可以选择不插）。")


# C12（2026-09-09 审计）：模块级 dict 清扫——进程长驻，poke/思考预支/结构模板/节奏窗口
# 的键只进不出（用户删除好友/换群后键成死值），缓慢无界增长。>7 天不活跃即清。
_SWEEP_STALE_SECS = 7 * 86400.0


def _sweep_stale() -> dict[str, int]:
    """清扫模块级缓存 dict（每日一次，由 debug._daily_proactive_loop 跨天时调用）：
    _POKE_TS/_THINK_AHEAD/_TPL_WINDOW/_TP_RHYTHM_WINDOW/_BANTER_RHYTHM 中
    >7 天未更新的键删除；顺带清理 data/*.tmp（原子写崩溃残壳）>1 天的文件。
    返回各类清扫数量（日志用）。"""
    removed: dict[str, int] = {}
    now = time.time()
    cut = now - _SWEEP_STALE_SECS
    # _POKE_TS：值=最近 poke 回复时间戳，陈旧度可从值直接判
    _n = 0
    for k in [k for k, v in _POKE_TS.items() if float(v or 0) < cut]:
        _POKE_TS.pop(k, None)
        _n += 1
    removed["poke"] = _n
    # _THINK_AHEAD：值=到期 epoch——过期即死值（一并清，比 7 天更及时）
    _n = 0
    for k in [k for k, v in _THINK_AHEAD.items() if float(v or 0) < now]:
        _THINK_AHEAD.pop(k, None)
        _n += 1
    removed["think_ahead"] = _n
    # _THINK_PREV_ATTEMPTS / _THINK_NEED（2026-09-12 T4.1 新增）：值无时间戳，按伴生 touch 判陈旧
    _n = 0
    for k in [k for k, t in _THINK_TOUCH.items() if float(t or 0) < cut]:
        _THINK_TOUCH.pop(k, None)
        _THINK_PREV_ATTEMPTS.pop(k, None)
        _THINK_NEED.pop(k, None)
        _n += 1
    removed["think_signals"] = _n
    # _TPL_WINDOW / _TP_RHYTHM_WINDOW：值=列表无时间戳——按伴生 touch 表判陈旧
    _n = 0
    for k in [k for k, t in _TPL_TOUCH.items() if float(t or 0) < cut]:
        _TPL_TOUCH.pop(k, None)
        _TPL_WINDOW.pop(k, None)
        _n += 1
    removed["tpl"] = _n
    _n = 0
    for k in [k for k, t in _TP_RHYTHM_TOUCH.items() if float(t or 0) < cut]:
        _TP_RHYTHM_TOUCH.pop(k, None)
        _TP_RHYTHM_WINDOW.pop(k, None)
        _n += 1
    removed["rhythm"] = _n
    # _BANTER_RHYTHM：群插话结构键窗口（2026-09-08 新增，同款伴生 touch 判陈旧）
    _n = 0
    for k in [k for k, t in _BANTER_RHYTHM_TOUCH.items() if float(t or 0) < cut]:
        _BANTER_RHYTHM_TOUCH.pop(k, None)
        _BANTER_RHYTHM.pop(k, None)
        _n += 1
    removed["banter_rhythm"] = _n
    # data/*.tmp 残壳（原子写进程被杀遗留）>1 天清理
    _n = 0
    try:
        from core.paths import data_path as _dp_sw

        _tmp_cut = time.time() - 86400
        for f in _dp_sw("").glob("*.tmp"):
            try:
                if f.is_file() and f.stat().st_mtime < _tmp_cut:
                    f.unlink()
                    _n += 1
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    removed["tmp"] = _n
    if any(removed.values()):
        logger.info("brain stale sweep: {}", removed)
    return removed


# 2026-09-07 P1 修复：下游还要解析的 agent 自决标记在 strip 时保序放行——
# 曾被一刀切剥掉导致通道整体死亡（special.apply_agent_markers 的【入迷】/【清醒】、
# think-ahead 的【认真(完)】、lifesim.apply_interaction 的【生活：…】/【场景：…】、催眠切片【身体遵从】）。
# 各解析点负责把标记从发出的消息里剥除（机器只做协议标记传递，不解析语义）。
# 2026-09-08：入迷/沉沦 允许携带档位（【入迷：暧昧】等）——strip 白名单须放行新形式，
# 否则档位标记被一刀切剥掉（special.apply_agent_markers 收不到、旧形式也一并失效）
# 2026-09-09：特殊层级登记/解除标记族（【催眠：…】/【洗脑：…】/【道具：…】/【不许装了】/【解除：…】）
# 同入白名单——固定短语协议退役后登记全靠这些标记，中途被一刀切剥掉=通道死亡
_AGENT_MARK_RE = re.compile(r"【\s*(?:入迷(?:[：:][^】]{1,6})?|沉沦(?:[：:][^】]{1,6})?|清醒|回神|认真完?|身体遵从|生活[：:][^】]{1,30}|场景[：:][^】]{1,12}|催眠[：:][^】]{1,100}|洗脑[：:][^】]{1,100}|道具[：:][^】]{1,100}|不许装了?|解除[：:][^】]{1,60})\s*】")
# 2026-09-07 核查补：沉沦/回神是 special.py MOOD_*_RE 的官方别名——曾不在白名单被一刀切剥掉（通道半死）


def _strip_non_speech(text: str, allow_action: bool = False) -> str:
    """剥离动作/思考/括注，只留台词。
    2026-09-04：先剥 markdown 加粗（**...**——模型会把提示词里的 ** 原样输出，且单星正则不匹配双星）。
    2026-09-06：不做词表/长度拦截（表达层归 agent 自决）——仅剥离协议标记（【不插话】等决策词）；
    思考体泄漏的处置 = agent 兜底（决策域带反馈重试），而不是机器整条拦截。
    allow_action=True（私聊开放动作）：保留（动作）——只剥【】协议标记与 markdown。
    2026-09-07：下游解析的 agent 自决标记（_AGENT_MARK_RE）占位放行，strip 后原样归还。"""
    t = re.sub(r"\*\*", "", text or "")

    def _hold(m):
        _holds.append(m.group(0))
        return f"\x00{len(_holds) - 1}\x00"

    def _unhold(m):
        return _holds[int(m.group(1))]

    _holds: list[str] = []
    t = _AGENT_MARK_RE.sub(_hold, t)
    # 2026-09-09 深夜：协议标记剥除与 core/reply._TONE_RE/_PACE_RE 同口径兼容全/半角括号——
    # 耗尽回退稿只过本函数不过 reply 侧剥标（【】只剥全角的口子曾让半角 [基调：x] 进发送文本）
    t = re.sub(r"[【\[]基调[：:]\s*[^】\]]{1,6}\s*[】\]]", "", t)
    t = re.sub(r"[【\[]等\s*[0-9.]+\s*秒[】\]]", "", t)
    if allow_action:
        t = re.sub(r"【[^】]{1,20}】", "", t)
        # 2026-09-06 表达层归 agent 自决：动作/神态处理由提示引导（不机器过滤）
    else:
        t = _NON_SPEECH_RE.sub("", t)
        t = re.sub(r"【[^】]{1,20}】", "", t)  # 协议标记剥离（决策词不进台词）
    t = re.sub(r"\x00(\d+)\x00", _unhold, t)
    return re.sub(r"\s*\n+\s*", "\n", t).strip()


async def _micro_speech(user_id: str, text: str, history: list[dict]) -> str:
    """兜底：剥离后没台词的极端情况——用当前人设补一句「她说的台词」（仍禁止动作/思考）。"""
    try:
        _card = _persona_card(user_id)
        sp = persona.build_system_prompt(_card, "", mode=0)
        sp += (
            "\n\n【只输出台词】她发消息只能有**说出口的话**：禁止动作、禁止心理描写、禁止括号注释。"
            "根据上下文输出她这一句台词（角色口吻，长短像她平时发消息，直接输出不要引号）。"
        )
        ctx = "\n".join(
            (f"她：{m['content'][:60]}" if m.get("role") == "assistant" else f"对方：{m['content'][:60]}")
            for m in history[-3:]
        )
        resp = await client.chat.completions.create(
            model=core_llm.resolve_model("main"),
            messages=[
                {"role": "system", "content": sp},
                {"role": "user", "content": f"{ctx}\n对方：{text[:80]}\n（她的台词）"},
            ],
            temperature=0.85,
            max_tokens=220,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        out = _strip_non_speech((resp.choices[0].message.content or "").strip())
        return out if 2 <= len(out) <= 120 else ""
    except Exception:  # noqa: BLE001
        return ""


ANSWER_REQ = "【回答要求】直接、清晰、完整地回答对方的内容/问题，用角色口吻（自称严格按你的人设卡：卡里怎么自称就怎么自称，卡里没有特殊自称就用“我”），但暂时不要堆砌语气词、不要加表演性渲染，先给出内容本身——说话为主；若这一刻有自然该发生的行为，它属于内容的一部分（不是为了动作而动作），可一并给出。回答长度与对方消息的复杂度匹配：对方短问/简单话题就简短回答（1-2 句，20-40 字），对方长问/复杂话题再充分展开——像人说话一样，短句子一次说完，不要为了凑长度而注水。\n【不知道就不知道】拿不准/没把握的事——\"我不太确定\"\"我去查查\"完全自然，**绝不硬编**：能查就查（搜索/凭据），不能查就老实说不知道。\n【承诺必兑现】对方要求解释/展开/细说/讲讲时，**直接把内容说清楚**（该有几段就几段，2-3 句起步）；绝对禁止只说“我解释一下”“我来说说”“先告诉你结论”就停——说到的必须当场展开，不许把内容放到“下一句”。\n【口语化·硬性】像发微信一样说话：短句、顺口、直接；禁止书面议论腔（“或许也是一种…”“确实让人…”“从某种角度来说”“毕竟/即使/因此”这类书面关联词能不用就不用）；评价判断用人话（“我觉得有点贵”“要我就再等等”）。\n【说人话·长度与节奏】一句话说一件事；省略号（……）不是口头禅——一条回复至多一处，只用于真正的欲言又止，能不用就不用；**反问最多一句**——连发反问是在纠结，不是说话；说完了就停，别加收尾长句。"

# V1（2026-09-11 热修十二）：润色基调示例从自由词改为固定 9 类词表——单一事实源=voice.TONE_CLASSES
# （与语音 classify 词表/EMO 消费口径同集；此前示例仅「傲娇」在 9 类内，自标自由词掉进 classify 压缩或直接无映射）
_TONE_LIST = "/".join(voice.TONE_CLASSES)

POLISH_REQ = ("【润色要求】按角色风格润色下面的回答。动作/神态描写与语气词是【可选增强】——公开场合（群聊）几乎不用，只有氛围确实需要时才加，且一句带过；简单、简短的内容保持原样或仅做轻微语气调整，绝不为润色而加戏、注水；保持核心内容和意思完全不变，不新增事实、不改变内容结构。{length_rule}\n【基调·自标】润色完若这句话有明确的情绪色彩，可在最末尾单独追加一个标记：【基调：两三字】（基调从这些里选一个：" + _TONE_LIST + "）——机器只读它来让你的语音语气和配图情绪跟这句话同一种心情，不会被发出去；拿不准或没有就不加。{sprite_hint}")

# 2026-09-06 私聊润色：动作开放（用户：有想做的动作可以直接做，别只口头威胁）——群聊为公开场合规则
# 2026-09-07 口径收紧（用户：回复泄露感觉/呼吸等非动作数据）——（动作）只装实际肢体行为，身体反应只准说成台词
# 2026-09-08 无范例化（用户：不给固定参考范例——示例=复读锚、跨卡复用=没有人设区别）——只留规则不留样本
PRIVATE_POLISH_REQ = "【润色要求·私聊】按角色风格润色下面的回答。私聊只有你们两个人：动作是演出的一部分——" \
    "（动作）加不加、加几笔，按角色性格与现场气氛自决，不堆砌成旁白；" \
    "做什么、做到哪一步，按角色性格与现场气氛自决，没有固定样板；" \
    "演出口径（硬性）：一个（ ）只装一个实际肢体行为本身——**不加任何修饰、不混写**：" \
    "一处括号里夹了反应/神态/感觉、或者把多个动作塞进一个括号，全部违规，润色时**删干净**；" \
    "眼睫/视线/眼神/呼吸/嗓音/语气/颤抖/僵住/红晕/体温这些**不是动作**——不进（）、不写旁白，" \
    "先在心里过，想说就说成一句台词，不想说就留在心里；心理独白/思考注记仍禁止；" \
    "保持核心内容和意思完全不变，不新增事实、不改变内容结构。{length_rule}\n" \
    "【基调·自标】润色完若这句话有明确的情绪色彩，可在最末尾单独追加一个标记：【基调：两三字】" \
    "（基调从这些里选一个：" + _TONE_LIST + \
    "）——机器只读它来让你的语音语气和配图情绪跟这句话同一种心情，不会被发出去；拿不准或没有就不加。{sprite_hint}"


# ---------------- stage1 思考流程（2026-09-06：奥汀硬编码模板已废——卡级 think_flow 优先，否则通用自决档）----------------
# 2026-09-04：原【思考流程】写死「你就是奥丁」+ 情色防线框架——素世等通用卡也吃 → 思维链复述/污染根源之一
THINK_FLOW_GENERIC = (
    "\n\n【思考流程】此刻你就是你自己（身份、性格、口吻完全按你的人设卡）——思考是你心里的声音，"
    # 2026-09-12 国际化：原先硬编码"用中文…禁止英文"（英文用户会得到"中文心里嘀咕"）。
    # 改为**跟随对话语言**（框架本就有语言感知 `_lang_note` → 【消息语言】注记），不新增开关：
    # 想强制某种语言的用户应在**卡/内容侧**声明（response_rules），符合"能力留框架、内容跟卡走"。
    "不是分析报告，全程用「我」来想（**用你和他此刻正在用的语言**、口语式，像心里嘀咕，禁止列清单）："
    "①眼前这件事：他刚才做了什么、说了什么？想从我这里得到什么？"
    "②这句话/这件事落到我身上，我是什么感觉？——先落到情绪上（有点开心？无语？被冒犯？想笑？心疼？），"
    "这些感觉先在心里过，不用急着说出来：想说了就自然说成一句台词，不想说就留在心里；"
    "你心里怎么动，自然按你们的关系远近、此刻的处境（他的房间/你的房间/当着谁）和正在做什么来——"
    "**想就在心里想，不用想太深**（2026-09-08 根因修正：正常人回想几步就够了；"
    "想得太深、角度太多，是把心里话当论文写）；别把它写成条理分析就行。"
    "③我这一刻想怎么回应？——从当下最自然的方式里选一个：顺着接话、打太极、试探一句、/调侃一句、"
    "认真回答、把话头岔开（按性格随机发挥，别每次都选同一个）；"
    "或者这一刻有什么事是我这个身体自然要做、想做的——行为与说话一样是回应的一部分："
    "要不要做、怎么做，由性格与现场自然来（不是为了动作而动作）；"
    "选好走向，围绕眼前这件事组织我要说的话和要做的事，然后开口。"
    "**想完就说你想到的那一句要紧的**（2026-09-08 根因修复：思考可以多想几步，"
    "但说出口的是想好的结论——**其余的整个留在心里，一个字都不用说出来**；"
    "想清楚了，正常人说的是结论；想得多不等于说得多。）"
    "心里话就是大白话，怎么想就怎么写，不用整理成漂亮句子。"
    "要求：思考的产出是『我此刻要说什么、做什么』，不是『角色应该怎么表现』；"
    "不要复述规则、不要规划表演（行为是内容的一环，不是表演装饰）、不要谈论自己的角色设定；"
    "想到的动作、神态只是「我」自然的反应；回应要紧贴眼前这件事本身，不翻旧话、不套旧框架。"
)


def _register_sprite_word(user_id: str, word: str) -> str:
    """热修十三 B2（2026-09-12）：【立绘：词】软校验与登记——词表=webgal.sprite_words 实扫
    BUST_DIR 的真实差分文件词。词表非空且词不在表内 → 丢弃（日志一行，sprite 帧自然回落
    tone 链）；扫描空表/失败=fail-open 放行（前端 404 探测回退链兜底）。返回应登记的词。"""
    w = str(word or "").strip()
    if not w:
        return ""
    try:
        from plugins import webgal as _wg_reg

        tbl = _wg_reg.sprite_words(str(_persona_name(user_id) or ""))
        if tbl and w not in tbl:
            logger.info("sprite word rejected (not in scanned table): user={} word={!r}", user_id, w[:12])
            return ""
    except Exception as e:  # noqa: BLE001  校验通道故障=不因校验丢词（fail-open）
        logger.debug("sprite word validate skipped: {} [{}]", e, type(e).__name__)
    return w


async def _gen_reply2(
    user_id: str, history_msgs: list[dict], core_prompt: str, style_prompt: str,
    length_mode: str = "medium", mouth_blocked: bool = False,
    think_flow: str = "", user_text: str = "", limit: int = 250, group_id: str | None = None,
    state_ctx: bool = False, facts_task=None, facts_box=None,
) -> str:
    """两阶段生成（2026-09-06 Phase D：迁 core/reply——质量检测机退役，仅存最小硬边界）。

    本函数只做 brain 侧装配（提示组装/思考开关/风格长度规则）与全局登记
    （开口节奏 _PACE_OUT、写作意图 _WRITE_INTENTS、基调 _TONE_LAST、立绘词 _SPRITE_LAST），
    保持原调用契约。
    facts_task/facts_box（2026-09-10）：主人括号事实预解析任务的 awaitable 与带回 box，
    透传 core/reply.generate（stage2 收取+注入；默认 None=零行为变化）。
    """
    from core import reply as _reply_mod

    _length_rule = (
        LENGTH_RULES_DAILY.get(length_mode, LENGTH_RULES_DAILY["medium"])
        if get_style_mode(user_id) == "daily"
        else LENGTH_RULES.get(length_mode, LENGTH_RULES["medium"])
    )
    # 立绘差分菜单（2026-09-10 用户裁决：性格/情绪立绘由 bot 自决选卡内可用项）——
    # 热修十三 B2（2026-09-12）重写：立绘与【基调】解耦（基调=语音 9 类不变，立绘=独立标记
    # 【立绘：词】）；旧版把差分菜单并进基调教学的混教句已整体删除（smoke 53 节有删除锚）；
    # 仅 web 合成轮注入（QQ 轮零变化）。词表=卡字段 sprite_expressions（用户编辑卡即改素材；
    # 空则经内容包 sprites/manifest.json 回落，卡自带优先）∪ webgal.sprite_words 实扫 BUST_DIR
    # 差分文件词（真实文件词，kaltsit 等无字段卡由此获得菜单）——卡字段在前、扫描在后、去重、
    # 钳 150 字符；空表=不注入 hint（行为同现状，无素材卡零变化）。词表按卡运行期扫描，
    # 必须在函数内拼装，不得提为模块级常量。
    _sprite_hint = ""
    try:
        from plugins import webgal as _wg_sprite

        if _wg_sprite.in_synthetic_round():
            _sp_card = _persona_card(user_id) or {}
            _sp_expr = [str(x).strip() for x in (_sp_card.get("sprite_expressions") or []) if str(x).strip()]
            if not _sp_expr:
                try:
                    from core import packs as _packs_menu

                    _pk_menu = _packs_menu.find_card(str(_persona_name(user_id) or ""))
                    if _pk_menu is not None:
                        _sp_man = _pk_menu.read_json("sprites/manifest.json")
                        _sp_expr = [str(x).strip() for x in ((_sp_man or {}).get("expressions") or []) if str(x).strip()]
                except Exception:  # noqa: BLE001  包回落失败=词表为空（不注入 hint，行为同无词表卡）
                    _sp_expr = []
            try:
                _sp_scan = _wg_sprite.sprite_words(str(_persona_name(user_id) or ""))
            except Exception:  # noqa: BLE001  扫描失败=词表仅剩卡字段（sprite_words 自身不抛，双保险）
                _sp_scan = []
            _sp_seen: set = set()
            _sp_union: list = []
            for _w in _sp_expr + list(_sp_scan):  # 卡字段在前、扫描在后、去重
                if _w and _w not in _sp_seen:
                    _sp_seen.add(_w)
                    _sp_union.append(_w)
            _sp_words = "、".join(_sp_union)[:150]
            if _sp_words:
                _sprite_hint = ("【立绘】可在最末尾单独追加标记：【立绘：词】（词从这些里选：" + _sp_words
                                + "；都不贴合就不加）——只影响立绘差分切换，不影响语音与文本。")
    except Exception:  # noqa: BLE001
        _sprite_hint = ""
    stage1_prompt = core_prompt + "\n\n" + ANSWER_REQ + (think_flow if think_flow else THINK_FLOW_GENERIC)
    polish_prompt = core_prompt + style_prompt + "\n\n" + (
        PRIVATE_POLISH_REQ if not group_id else POLISH_REQ
    ).format(length_rule=_length_rule, sprite_hint=_sprite_hint)

    # 思考开关：用户显式锁定（/思考）优先；否则按消息自决（"认真起来"类指令词=协议）
    # 2026-09-12 T4.1：机器单向开启判据——锁定值/自决结果 OR 任一本地判据命中（只加"开"，绝不强制关）。
    # 判据信号（长消息/角色提及）由调用方在生成前登记 `_THINK_NEED`；角色提及判据在
    # `_character_lookup` 的唯一组装点复用 `_character_hits`（同一份命中逻辑，不复制）。
    # 2026-09-12：原"知识问句"判据已按用户裁决移除（误报密集），见 `_name_think_reasons` 注。
    _tm = get_think_mode(user_id)
    think_wanted = (time.time() < _THINK_AHEAD.get(str(user_id), 0.0)) if _tm is None else bool(_tm)
    _think_rs = _name_think_reasons(user_id, user_text)
    if _think_rs:
        think_wanted = True
        logger.info("[思维链] user={} 思考=开来源=机器判据({})", user_id, "/".join(_think_rs))

    # 最小硬边界的对照池：最近 2 条本人回复
    _rows = memory.history_messages(user_id, max_turns=2, group_id=group_id or "")
    _last = [m["content"] for m in _rows if m["role"] == "assistant"][-2:]

    res = await _reply_mod.generate(
        user_id=user_id, history_msgs=history_msgs,
        stage1_prompt=stage1_prompt, polish_prompt=polish_prompt,
        client=client, model=core_llm.resolve_model("main"),
        user_text=user_text, group_id=group_id or "",
        mouth_blocked=mouth_blocked, allow_actions=(not group_id),
        strip_non_speech=_strip_non_speech, micro_speech=_micro_speech,
        reaction_guard=_reaction_guard if not group_id else None,  # 2026-09-07 动作口径红线：仅私聊（群聊本就禁动作）
        last_replies=_last, think_wanted=think_wanted, limit=limit,
        # 2026-09-07：state_ctx 接线（曾只赋值从未传入——特殊状态期意象重叠多，0.8 阈值会误判白重试）
        dup_threshold=0.65 if state_ctx else 0.8,
        # 2026-09-10：主人括号事实预解析（并行任务的 awaitable + 带回 box；None=零行为变化）
        facts_task=facts_task, facts_box=facts_box,
    )
    _WRITE_INTENTS[user_id] = res.get("write_intent")
    # 2026-09-12 T4.1 判据四：登记本轮 attempts——下一轮据此判断"上轮是否硬边界重试过"（==2 则机器开思考）。
    _THINK_PREV_ATTEMPTS[user_id] = int(res.get("attempts") or 0)
    _THINK_TOUCH[str(user_id)] = time.time()  # 伴生 touch：_sweep_stale 判陈旧用
    _TONE_LAST[user_id] = str(res.get("tone") or "")  # 2026-09-08 活人感#1：基调登记（发送段消费）
    # 热修十三 B2：立绘差分词登记（webgal sprite 帧消费；软校验见 _register_sprite_word——
    # 扫描词表非空且词不在表内则丢弃置空，sprite 帧自然回落基调链）
    _SPRITE_LAST[user_id] = _register_sprite_word(user_id, res.get("sprite"))
    if res.get("pacing"):
        _PACE_OUT[user_id] = float(res["pacing"])
    # 【心情】对话自标消费已退役（2026-09-08 深夜用户裁决：0 使用；心情=心跳 tick + 提取 sync_mood）
    return res["text"]


# ---------------- 情欲状态提示（私聊：语气词递增 + 神智逐级下降；高傲底色恒定） ----------------
# 情欲词汇表（分级，来自 vocab_base.json erotic_l1/l2/l3）：动作/动态/娇喘参考，轮换使用避免只会重复固定几个词
# 情欲词汇全表（轮换统计用：erotic 三级 + sex 分类）

# ---------------- 淫荡度（高潮次数驱动 0-30：语言破碎化逐级加重，30 级只剩碎片式拟声词） ----------------


# ---------------- 高潮指数（情欲状态下开启：言语+10 / 动作+25 / 性爱动作+50，满 100 必定高潮） ----------------
# 状态倍率（2026-08-25 用户指示）：催眠 ×1.2 / 洗脑 ×1.5 / 道具 ×1.1；恶堕额外叠加 ×1.5
# 性爱动作判定词：括号动作内容命中任一词即按性爱动作计（用明确性爱词，避免"摸头"被"摸"字误判）
# ---------------- 「不许高潮」锁（2026-08-25 用户需求：锁 95 + 求饶导向反馈） ----------------
# ---------------- 恢复期（"这次疼爱结束了"）与纵欲沉沦档位（理性越低越淫荡） ----------------


_CN_NUMS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


# 固定套路核心字（历史审计确认的变体共享成分）：-60 常驻压制，不依赖动态统计
# 第二梯队（恩赐/献上/施舍/尘埃/灼热/雷霆 等新套路意象）一并压制
# 拟声词（非情欲状态滥用抑制）：情欲状态不禁（情欲需要嗯嗯啊啊）


_BIAS_CACHE: dict[str, tuple[float, dict[int, int]]] = {}  # user_id:{mode-key} -> (ts, bias)


GROUP_CONVERGE_PROMPT = (
    "\n\n【群聊·正常反馈】你现在在群聊中，正处于特殊状态（情欲/催眠/洗脑/道具）。"
    "群聊回复**正常反馈话题内容本身**——像平时一样自然回答、闲聊、帮忙，"
    "不刻意疏离，也**不带一丝暧昧**：任何色情、暗示、挑逗、亲密、内心戏内容"
    "都不得出现在群聊回复里。这些内容会被**切片**下来，单独通过私聊告诉主人——"
    "你不需要在群里表达，也不要暗示「私聊再说」。"
)

# 恶堕群聊放宽（主人触发·恶堕模式）：允许挑逗类思想/回复，但不许越界
# 2026-09-09 铁律#2 迁移：提示文本在卡数据 arrogance.group_evul（见 _aoding_）——只在用该卡时注入。
# 催眠射线·特性型（催眠射线（身体听话））：身体/行为强制遵从，思想不变 → 正常回复 + 切片

# 群聊伪装·厌恶高傲（善良模式 + 情欲激活时群聊触发）：表面厌恶嫌弃，内心动摇藏起（私语传达）
# 2026-09-09 铁律#2 迁移：提示文本在卡数据 arrogance.group_disgust——卡没写就不注入
# （旧版无卡门控，任何卡 + 主人在群 + 情欲氛围 时都会吃到该卡的专属引句=人设泄漏，已修）。

# 私语生成提示（主人群聊触发时：被切掉的色情/暗示/思考内容，切片私聊发送）
# 2026-09-09 铁律#2 迁移：模板保留在代码（私语=框架功能），但身份名不再写死——
# 按当前人设卡的 name 组装（见 _gen_whisper），卡没名就不生成私语。


# ---- 道具系统（"使用了XX道具"：口球/淫纹/震动棒；屈服度指数） ----
# 触发当轮演出提示


# ---------------- 恶堕 M 属性放大（情欲中表S内M彻底反转）与情欲词汇轮换 ----------------


CTX_TOKEN_BUDGET_DEFAULT = 6000  # 通用兜底（无模型模式记录时）
CTX_TOKEN_BUDGET_GEMMA = 12000   # gemma 基线
MAX_USER_MSG_CHARS = 800  # 超长用户消息压缩阈值

# 2026-09-10 Phase1：预算基数移 core.llm（llm_ctx_budget，默认 32768=引擎总 ctx 权威口径，
# 旧注释"16384 slot"已过时）。预算=基数 × 历史比例（12000/6000 ÷ 32768，均为 2 的负幂，
# 默认基数下 int() 后与历史常量逐字节相等——零变化约束）
_CTX_BUDGET_BASE = 32768
_CTX_RATIO_GEMMA = CTX_TOKEN_BUDGET_GEMMA / _CTX_BUDGET_BASE
_CTX_RATIO_DEFAULT = CTX_TOKEN_BUDGET_DEFAULT / _CTX_BUDGET_BASE


def _ctx_budget_for_model() -> int:
    """按当前引擎模型返回上下文预算（引擎全局共享，看最近写入的 model_mode）。

    预算基数读 core.llm.ctx_budget()（llm_ctx_budget，默认 32768），按模型乘历史比例
    （gemma=基数×0.366、兜底=基数×0.183）；默认配置下输出与历史常量 12000/6000 一致。
    """
    try:
        _base = core_llm.ctx_budget()
        modes = _load_model_modes()
        k = "gemma"
        if modes:
            k = list(modes.values())[-1]
        if k not in MODELS:
            k = "gemma"
        return max(1, int(_base * (_CTX_RATIO_GEMMA if k == "gemma" else _CTX_RATIO_DEFAULT)))
    except Exception:  # noqa: BLE001
        return CTX_TOKEN_BUDGET_DEFAULT


def _estimate_tokens(text: str) -> int:
    """中文场景 token 估算（1 字 ≈ 1 token 的保守近似）。"""
    return max(1, int(len(text) * 0.75))


# 私聊 assistant 历史单条精简上限（2026-09-08 深夜 B0 长度专项·自模仿去放大）：
# 实证 corr(前条回复长, 本条回复长)=0.59（09-04~07 仅 0.43-0.48）——历史里的"你此前的回复"
# 是形状/长度的最大示范源。注入侧按句读精简旧回复，让上下文示范与自然长度带一致；
# 只改模型所见（注入工程），不限制它的任何输出（铁律 #1 自决域不动）。
_HIST_ASST_MAX_CHARS = 120


def _hist_asst_trim(text: str, total: int = _HIST_ASST_MAX_CHARS) -> str:
    """历史 assistant 消息按句读保留截断（超长单句硬截兜底）——与 core/reply._cut_to_sentences 同构。"""
    t = str(text or "").strip()
    if len(t) <= total:
        return t
    out: list[str] = []
    used = 0
    for s in re.split(r"(?<=[。！？!?])", t):
        s = s.strip()
        if not s:
            continue
        if used + len(s) > total:
            break
        out.append(s)
        used += len(s)
    body = "".join(out)
    if len(body) < 20:  # 首句即超长：句读截不出东西 → 硬截兜底（对齐群聊 120 截断口径）
        return t[:total] + "…"
    return body + "…"  # 截尾符对齐群聊（单个…；三个…会往历史注入省略号示范）


def _fit_history(history_msgs: list[dict], base_tokens: int, budget: int | None = None) -> list[dict]:
    """按 token 预算从最新往回装历史消息，超预算截断（保留最新）。

    无论预算多紧，最后一条（当前用户消息）必须保留，否则模型看不到对方说了什么。
    2026-08-26：budget=None 时按当前引擎模型动态取（gemma 12000 / qwen 6000）。
    """
    if budget is None:
        budget = _ctx_budget_for_model()
    taken = base_tokens
    keep: list[dict] = []
    for m in reversed(history_msgs):
        cost = _estimate_tokens(m["content"]) + 8
        if taken + cost > budget:
            break
        keep.append(m)
        taken += cost
    keep.reverse()
    if not keep and history_msgs:
        return [history_msgs[-1]]  # 保底：至少保留当前消息
    if keep and keep[-1] is not history_msgs[-1] and history_msgs:
        keep[-1] = history_msgs[-1]  # 当前消息被裁掉时，用其替换最后保留项
    return keep


# ---------------- 情欲挽留（未高潮时结束词触发有概率挽留并重启状态） ----------------
# ---------------- 人格结构：外在高傲恒定（最高优先，任何状态/好感度/场景均不取消） ----------------
# 高傲表达模板基底：参考句式与风格、严禁整句照抄，每轮换花样（防"指尖XX"式套路化重复）
# 2026-09-09 铁律#2 迁移：句式内容全部在卡数据（persona.arrogance_of(card)["patterns"]，见 _aoding_）；
# 代码零人设假设——卡没写 patterns 就没有高傲句式注入，分档选择逻辑保留（句表为空即不注入）。


def _arrogance_patterns(mode: int, lust_index: int = 0, patterns: tuple | list = ()) -> str:
    """高傲表达参考句式（风格样本）——只注入 stage2 润色，不进 stage1 内容思考。"""
    _p = tuple(patterns or ())
    if lust_index > 0:
        if mode == 1:
            return "高傲表达参考（只参考句式与语气，严禁整句照抄）：" + " ".join(_p[:2]) if lust_index < 70 else ""
        if lust_index < 50:
            return "高傲表达参考（只参考句式与语气，严禁整句照抄）：" + " ".join(_p[:4])
        if lust_index < 70:
            return "高傲表达参考（只参考句式与语气，严禁整句照抄）：" + " ".join(_p[:3])
        return ""
    return "高傲表达参考（只参考句式与语气，严禁整句照抄，每轮换花样）：" + " ".join(_p)


def _safe_card_format(tpl: str, **kw) -> str:
    """卡数据结构块模板 .format 守卫（2026-09-10 审计 P3）：卡文本占位符与注入参数不匹配
    （KeyError/ValueError/IndexError 等）时绝不炸主人整轮——warning 一条并跳过该块注入（返回空串）。
    三处调用点：arrogance.group_followup / group_member / private_intro（build_system_prompt 内）。"""
    try:
        return str(tpl or "").format(**kw)
    except Exception as e:  # noqa: BLE001
        logger.warning("card template format failed (block skipped): %s tpl[:40]=%r", e, str(tpl)[:40])
        return ""


def _arrogance_block(mode: int, intimacy: float, lust_index: int = 0, tpl: dict | None = None) -> str:
    """人格结构块（优先级 L1，无条件注入）。

    lust_index（情欲指数 0-100）>0 时进入"高傲×情欲平衡"变体：
    指数越高，高傲占比越低、情欲占比越高，形成"高傲→沉沦"的反差弧线；
    但高傲底色永不归零（仅剩一丝矜贵也算），符合"压制不取消"。
    风格样本（参考句式）已分离到 _arrogance_patterns（仅 stage2 注入）。
    2026-09-09 铁律#2 迁移：全部文本来自卡数据模板（persona.arrogance_of(card)["structure"]，
    占位 {lust}/{ratio}/{intimacy}；intimacy 由代码按 %.0f 预格式化）——
    分档/拼接逻辑保留，代码不含任何人设文本；卡没写该结构 → 返回 ""（零注入）。
    """
    tpl = tpl or {}
    if not tpl:
        return ""
    patterns = ""  # 风格样本不进入内容思考阶段
    if lust_index > 0:
        # ---- 情欲平衡变体：随指数降低高傲占比 ----
        # 恶堕（mode=1）专属：满好感+满情欲时高傲占比再降一档（冷峻毒舌软化，掌控感转为沉迷）
        if mode == 1:
            if lust_index < 50:
                ratio = str(tpl.get("lust_evul_low") or "").format(lust=lust_index)
            elif lust_index < 70:
                ratio = str(tpl.get("lust_evul_mid") or "").format(lust=lust_index)
            else:
                ratio = str(tpl.get("lust_evul_deep") or "").format(lust=lust_index)
        elif lust_index < 50:
            ratio = str(tpl.get("lust_low") or "").format(lust=lust_index)
        elif lust_index < 70:
            ratio = str(tpl.get("lust_mid") or "").format(lust=lust_index)
        else:
            ratio = str(tpl.get("lust_deep") or "").format(lust=lust_index)
            patterns = ""
        base = str(tpl.get("base_lust") or "").format(ratio=ratio)
    else:
        base = str(tpl.get("base_normal") or "").format(intimacy=f"{intimacy:.0f}")
    if mode == 1:
        inner = str(tpl.get("inner_evul") or "")
    else:
        inner = str(tpl.get("inner_normal") or "").format(intimacy=f"{intimacy:.0f}")
    out = base + "\n" + inner
    if patterns:
        out += "\n" + patterns
    return out


# ---------------- 群聊非用户概率性拒绝（已移除 2026-09-04：tier 感知替代，无掷骰子） ----------------


# ---------------- 回复风格档位（按心情/场景动态控制长度与语气词密度） ----------------
HIGH_EMOTIONS = ("开心", "兴奋", "震惊", "害羞")
# 长度控制（2026-09-08 深夜 B0 专项·数据复盘后回退）：
# 09-08 D4 恢复的"软上限"数字档位实证无效且有害——①长度规则只进 stage2 润色层，
# 而润色层被"不改变内容结构"绑死（管不住形状）；②数字锚点（30-60/60-80/80 上下）
# 对 12B 是"往多了写"的靶子（生效期 med 64 > 09-07 无档位期的 54）。
# 超长真凶=09-07 深夜起的形状教学短语群（打字感/台词动作交替/分段几行的形式处方）
# +历史自模仿放大（corr(prevA,A)=0.59）——已拆（形状域零处方=无范例化纪律同构）。
# 长度归还 agent 自决（铁律 #1；09-03 用户裁决口径）：内容长度由"说什么"自然决定。
LENGTH_ADAPT = "【长度自决】回复长度由你根据对方消息与话题氛围**自然决定**，像真人聊天：对方短问/轻松话题就一句两句（几个字到二十来个字都可以）；对方展开的话题就自然说下去；情绪浓烈自然变多；不为凑长注水，也不硬压短。没有固定字数档位。"
LENGTH_RULES = {
    "high": LENGTH_ADAPT,
    "medium": LENGTH_ADAPT,
    "low": LENGTH_ADAPT,
}
LENGTH_RULES_DAILY = {
    "high": LENGTH_ADAPT,
    "medium": LENGTH_ADAPT,
    "low": LENGTH_ADAPT,
}


def _length_mode(is_owner: bool, user_id: str, short_msg: bool = False) -> str:
    """回复风格档位（2026-09-03 起仅保留信号意义：所有档位 = 长度自然自决，模型自己决定）。
    2026-09-07：清死参数 event/lust_level（指数机退役，lust_level 恒传 0 分支永假）。"""
    try:
        emo = memory.db.get_latest_emotion(user_id)
        if emo and emo["emotion"] in HIGH_EMOTIONS and float(emo["intensity"]) >= 0.6:
            return "high"  # 情绪激动
    except Exception:  # noqa: BLE001
        pass
    if not is_owner:
        return "low"  # 非主人（群聊/私聊一致）
    if short_msg:
        return "low"  # 短问
    return "medium"


# 群聊插话（2026-08-30 v3：LLM 参与状态机——社区结论「内容相关驱动，时间窗只是防刷屏兜底」）
# 2026-09-04 方向纠正：时间窗一律只是**兜底**，主控 = 情绪驱动（LLM 先判情绪再决定说与不说）；
# 高频/尬聊的根治靠情绪门槛（没情绪就不说话），不靠计时器。
BANTER_ENABLED = True
# ---------------- 群聊插话评估（agent 自决；2026-09-06 冷却/频控/防撞车机器门全部移除）----------------
# 话题点名 bot（明确点到名字）→ 视为强情绪激励：绕过冷却即时评估（2026-08-30 用户要求；
# 2026-09-04 收窄：去掉"她…"宽松匹配，防「带'她'字就触发」的假点名）
# 2026-09-05 提及检测动态化：所有卡名+别名（PERSONA_ALIAS 键，长词优先）+ 基础称呼——群里有人叫到当前卡/名字 → 值得接
_MENTION_WORDS = sorted(
    {str(k) for k in PERSONA_ALIAS if len(str(k)) >= 2},
    key=len, reverse=True,
)
BANTER_MENTION_RE = re.compile(
    "|".join(re.escape(w) for w in _MENTION_WORDS) + r"|机器人|bot|Bot|AI|人工智能"
)
_BANTER_SENT: dict[str, float] = {}  # 2026-09-06 本群 bot 最近插话时间（时间窗兜底：防刷屏）
# B10（2026-09-09 审计）：插话「评估」侧节流与并发上限——_BANTER_SENT 只在真发出插话时落账，
# 高速讨论期每条非@消息都曾各起一个评估 task（perceive+搜索+LLM 自决全跑），LLM 请求排队压满引擎
_BANTER_EVAL_TS: dict[str, float] = {}  # group_id -> 最近一次评估开始时间（120s 群级节流，无论是否发出插话；2026-09-08 质量优先放宽）
_BANTER_SEMA = asyncio.Semaphore(2)     # 并发评估执行位上限（排队 task 到号后会按节流窗二次复查，不拿旧上下文硬评）

# ---------------- 开口节奏（2026-09-06 用户：bot 自决回复前停顿 ≤2s + 输入侧连发聚合防"半句双回"）----------------
# （旧模块级 _PACE_RE 全角-only 死常量已删 2026-09-09 深夜：剥标职责在 core/reply._PACE_RE（全/半角），
#   本模块消费的是 reply 解析出的 pacing 值——盲审二轮 P3 指出其与新口径不一致易误导）
_PACE_OUT: dict[str, float] = {}          # user_id -> 本轮 bot 自决停顿（_gen_reply 暂存，发送处消费）
_COALESCE: dict[str, dict] = {}           # skey -> {"until": ts, "texts": [str]}（连发聚合窗）
_COALESCE_PREF: dict[str, float] = {}     # skey -> 上次 bot 自决 pacing（下次聚合窗秒数，≤2）
_COALESCE_DEFAULT = 0.9                   # 无自决记录时的默认聚合窗（Bot 打字节奏感）
_BUSY: dict[str, float] = {}              # skey -> 处理窗截止（bot 忙=输入提示未解除；同人消息排队，不并行思考）


async def _coalesce_incoming(skey: str, text: str, force_now: bool = False) -> tuple[str, bool]:
    """同人会话连发聚合：窗口（≤2s，用上次 bot 自决的 pacing 或默认 0.9s）内同人消息并入首条。
    返回 (合并文本, 是否已被并入他条)。调用方收到已并入 → 直接返回（本消息由首条统一处理）。
    2026-09-07 P1 修复：force_now（群聊 @bot 的显式点名）不进聚合窗也不被并入——
    曾被吞进前一条普通消息的合并串，而首条的 is_tome()=False → @ 请求既不回复也不入库。"""
    now = time.time()
    st = _COALESCE.get(skey)
    if st and now < st["until"]:
        if force_now:
            return (text, False)  # 显式点名自成一轮（同会话串行由 _BUSY 窗保证）
        st["texts"].append(text)
        return ("", True)
    if force_now:
        return (text, False)  # 不为点名消息开聚合窗（后面的连发也不该并进 @ 轮）
    wait = min(2.0, max(0.0, _COALESCE_PREF.get(skey, _COALESCE_DEFAULT)))
    _COALESCE[skey] = {"until": now + wait, "texts": [text]}
    if wait > 0:
        await asyncio.sleep(wait)
    st2 = _COALESCE.pop(skey, None)
    merged = "".join(st2["texts"]) if st2 else text
    return (merged, False)
_BANTER_SUBJ_RE = re.compile(r"那我|那我也|我也|我就|让我|我来|既然|于是|所以|唔|嗯")


# ---------------- 网络用语/二次元/游戏术语知识库（2026-09-05：三文件合并；群里飘梗时秒懂，优先于联网检索） ----------------
# 性能（2026-09-04）：词典编译为单一 alternation 正则（单次 finditer 替代 N 次 substring），
# 按 mtime 合并缓存（任一文件变更自动重载）。
# 语义层（2026-09-05）：合并词条「释义向量」预计算（slang_semantic.json）——精确未命中时语义召回；
# 阈值 0.58；查询 LRU 缓存 400。词条更新后重跑 gen_slang_vec.py 重建语义向量。
SLANG_FILES: list[Path] = [
    DATA_ROOT / "net_slang.json",
    DATA_ROOT / "slang_anime.json",
    DATA_ROOT / "slang_game.json",
]
SLANG_SEM_FILE = DATA_ROOT / "slang_semantic.json"
_SLANG_CACHE: dict = {"mtime": {}, "re": None, "table": {}}
_SLANG_SEM: tuple | None = None          # (np matrix f32, terms, norms)
_SEM_HIT_CACHE: dict = {}


def _slang_dispatch() -> tuple[re.Pattern, dict]:
    """返回 (编译正则, 词->释义表)；任一词典文件 mtime 变更自动重载。"""
    try:
        mtimes = {str(f): f.stat().st_mtime for f in SLANG_FILES}
        if mtimes != _SLANG_CACHE["mtime"]:
            table: dict = {}
            for f in SLANG_FILES:
                try:
                    table.update(json.loads(f.read_text(encoding="utf-8-sig")))
                except (OSError, json.JSONDecodeError):
                    continue
            _SLANG_CACHE["re"] = re.compile(
                "|".join(re.escape(t) for t in sorted(table, key=len, reverse=True))
            )
            _SLANG_CACHE["table"] = table
            _SLANG_CACHE["mtime"] = mtimes
        return _SLANG_CACHE["re"], _SLANG_CACHE["table"]
    except OSError:
        return re.compile(r"(?!x)x"), {}  # 永不匹配的空模式


async def _slang_semantic_hit(text: str) -> str:
    """精确未命中时的语义召回：词条释义向量 top-1，阈值 0.58；返回命中的词条（空=未命中）。
    2026-09-06 D4：embed 改异步——不再同步阻塞事件循环。"""
    try:
        if len(text) < 4:
            return ""
        qk = text[:40]
        hit = _SEM_HIT_CACHE.get(qk, "")
        if hit:
            return hit
        global _SLANG_SEM
        if _SLANG_SEM is None:
            import numpy as _np

            d = json.loads(SLANG_SEM_FILE.read_text(encoding="utf-8-sig"))
            if not d:
                _SLANG_SEM = (_np.zeros((0, 1024), dtype=_np.float32), [], _np.zeros(0))
            else:
                mat = _np.asarray([d[t] for t in d], dtype=_np.float32)
                _SLANG_SEM = (mat, list(d), _np.linalg.norm(mat, axis=1) + 1e-9)
        mat, terms, norms = _SLANG_SEM
        if not len(terms):
            return ""
        from plugins.memory import embeddings as _emb

        q = await _emb.embed_text_async(text[:120])
        if not q:
            return ""
        import numpy as _np

        qa = _np.asarray(q, dtype=_np.float32)
        qn = float(_np.linalg.norm(qa)) + 1e-9
        scores = (mat @ qa) / (norms * qn)
        best = int(_np.argmax(scores))
        if float(scores[best]) < 0.58:
            return ""
        hit = terms[best]
        if len(_SEM_HIT_CACHE) > 400:
            _SEM_HIT_CACHE.clear()
        _SEM_HIT_CACHE[qk] = hit
        return hit
    except Exception:  # noqa: BLE001
        return ""


async def _slang_lookup(text: str, limit: int = 4) -> str:
    """文本中的网络用语命中 → 释义注记（精确优先，未命中走语义召回；均未命中返回空串）。
    2026-09-06 D4：改 async（语义通道 embed 不再阻塞事件循环）——调用方 await。"""
    try:
        if not text:
            return ""
        rex, table = _slang_dispatch()
        hits = []
        for m in rex.finditer(text):
            hits.append(f"{m.group(0)}：{table[m.group(0)]}")
            if len(hits) >= limit:
                break
        if hits:
            return "【网络用语词典】" + "；".join(hits)
        # 语义通道：表达相近但未写原文（"我没绷住""笑死"…）
        t = await _slang_semantic_hit(text)
        if t:
            return f"【网络用语·语义】可能是「{t}」：{table[t]}（表达与之相近，按此理解）"
        return ""
    except Exception:  # noqa: BLE001
        return ""


def _parse_msg_ts(ts: str) -> float:
    """ISO 时间戳 → epoch 秒（解析失败返回 0）。"""
    try:
        import datetime as _dt

        return _dt.datetime.fromisoformat(ts).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


async def _banter_need_search(lines: list[str]) -> dict:
    """群聊话题理解（2026-09-04）：判断讨论是否涉及需要查证的背景——
    专业术语/专有名词/外文缩写/梗/网络事件/人名指代/作品角色等。
    返回 {"query": 检索词}；完全不需要（日常闲聊）或判定失败返回 {}（不搜索，直接插话判定）。"""
    try:
        resp = await client.chat.completions.create(
            model=core_llm.resolve_model("main"),
            messages=[
                {"role": "system", "content": "你是群聊话题分析器。下面是一段群聊记录。判断：讨论是否涉及需要了解背景知识才能自然参与的内容——专业术语、专有名词、外文缩写、特定的梗、网络事件、特定的人名指代、某作品/角色/游戏/舰船/电子产品等。完全不需要（日常闲聊、常识话题）→ 只输出：不需要；需要 → 只输出一行：需要|<一个检索查询词>（中文，5-20字，聚焦最核心的未知点）。\n【查询词规则】若讨论中提到的词是某个人/账号/博主（语境特征：“XX 也有”“XX 早就…”“XX 的粉丝”等，被当作谈论对象）→ 查询词**直接用人名原文**，禁止加修饰词（装甲/大/小/限定语）、禁止按谐音或梗义改写成别的词；搜索不到就如实写：需要|<该词 是什么人>\n【消歧】若话题是圈内专有名词/IP/缩写（从上下文判断领域：提到比例/孩之宝/模型→玩具；提到章节/角色→动画；提到版本/平台→游戏……），查询词追加一个领域词（玩具/模型/游戏/动画/角色/事件/专辑），如\"酸雨战争 玩具\"——防搜索命中歧义（如搜出\"酸雨灾害\"）"},
                {"role": "user", "content": "\n".join(lines)},
            ],
            temperature=0.2,
            max_tokens=140,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        out = (resp.choices[0].message.content or "").strip()
        if out.startswith("需要"):
            q = out.split("|", 1)[-1].strip()[:40]
            if 2 <= len(q) <= 40:
                logger.info("banter need-search: query=%r", q)
                return {"query": q}
        return {}
    except Exception as e:  # noqa: BLE001
        logger.debug("banter understand failed: %s", e)
        return {}


async def _topic_summary(query: str, src_text: str) -> str:
    """话题提炼（2026-09-05 自决搜索→思考怎么搭话）：把搜索结果压缩为
    「它是什么 + 圈内人通常聊/吐槽什么」的一两句话（供插话自决使用；结果入库 topic_knowledge）。"""
    try:
        resp = await client.chat.completions.create(
            model=core_llm.resolve_model("main"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是话题速读器。根据下面的搜索资料，用 1-2 句话直接说明：它是什么"
                        "（什么作品/产品/角色/事件）+ 圈内人通常会聊或吐槽它的什么（若有）。"
                        "只输出事实性概括一句话，不要出现“我查了”“资料显示”等话术。"
                    ),
                },
                {"role": "user", "content": f"主题词：{query}\n\n搜索资料：\n{src_text[:1200]}"},
            ],
            temperature=0.4,
            max_tokens=150,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        out = (resp.choices[0].message.content or "").strip().strip("\"'")
        return out[:200]
    except Exception as e:  # noqa: BLE001
        logger.debug("topic summary failed: %s", e)
        return ""


# ---------------- 群聊复读机（2026-09-04：网络梗「复读机」——群内连续重复短句时，bot 可参与复读）----------------
ECHO_STATE_FILE = DATA_ROOT / "echo_state.json"
ECHO_MEMORY_SECS = 900     # 同一条文本的记忆窗口（秒）：窗口内只参与一次；窗口后同文本视为新事件可再参与
ECHO_MIN_LEN, ECHO_MAX_LEN = 4, 24      # 复读文本长度范围（太短无意义/太长不像梗）


def _load_echo_state() -> dict:
    try:
        return json.loads(ECHO_STATE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_echo_state(state: dict):
    ECHO_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(ECHO_STATE_FILE, state)


def _norm_echo(t: str) -> str:
    return re.sub(r"\s+", "", (t or "")).strip("，。！？!?~～· ")


async def _maybe_echo(bot, event, group_id: str) -> bool:
    """群聊复读机：最近 6 条群友消息中 ≥3 条与最新一条相同（短句）→ 概率复读（纯复读原文，不解释）。
    返回 True=已处理（复读成功）。"""
    try:
        if not group_id or _group_banned(group_id):
            return False
        gid = str(group_id)
        rows = memory.db.recent_messages("", limit=10, group_id=gid)
        user_rows = [m for m in rows if m.get("role") == "user" and (m.get("content") or "").strip()]
        if len(user_rows) < 3:
            return False
        latest = (user_rows[-1].get("content") or "").strip()
        if not (ECHO_MIN_LEN <= len(latest) <= ECHO_MAX_LEN):
            return False
        if "[" in latest or "]" in latest:  # 富文本/表情/@ 段不复读
            return False
        n = _norm_echo(latest)
        if not n:
            return False
        same = sum(1 for m in user_rows[-6:] if _norm_echo(m.get("content") or "") == n)
        if same < 3:  # 还没形成复读
            return False
        now = time.time()
        st = _load_echo_state()
        gst = st.get(gid, {})
        # 2026-09-07 按文本记忆（15 分钟窗口）：同一句话只复述一次；窗口后视为新事件可再参与
        _mem = gst.get("mem") or {}
        if isinstance(_mem, dict):
            _mem = {k: v for k, v in _mem.items() if now - float(v or 0) < ECHO_MEMORY_SECS}
        else:
            _mem = {}
        if n in _mem:
            return False  # 窗口内同文本已复述过——绝不二刷
        await bot.send(event, latest)
        # 2026-09-07 P2：发送是 await——期间其他群可能已写回 echo 状态；旧快照整体覆盖曾丢别群记忆
        st = _load_echo_state()
        gst = st.get(gid, {})
        _mem = gst.get("mem") or {}
        if isinstance(_mem, dict):
            _mem = {k: v for k, v in _mem.items() if now - float(v or 0) < ECHO_MEMORY_SECS}
        else:
            _mem = {}
        _mem[n] = now
        gst["mem"] = _mem
        gst.pop("lt", None)
        st[gid] = gst
        _save_echo_state(st)  # 持久化：重启/重连不丢记忆，同场复读绝不二刷
        logger.info("group echo sent: gid=%s line=%r", gid, latest[:40])
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("echo skip: %s", e)
        return False


# ---------------- 群画风定性（2026-09-07：LLM 从真实群聊记录提炼——感知注入，融入由 agent 自决） ----------------
GROUP_STYLE_MAX_AGE = 86400  # 每日刷新一次（2026-09-08 深夜用户裁决；MIN_MSGS 计数触发已随之退役）


async def _refresh_group_style(group_id: str, force: bool = False):
    """按需刷新一个群的画风画像（2026-09-08 深夜用户裁决：每日一次即可——原"或新增50条"触发
    使活跃群每小时被 LLM 重概括、与用户回复争抢引擎；现仅按 24h 龄期触发）。"""
    try:
        import datetime as _dt

        _u_ts = memory.db.group_style_updated_ts(group_id)
        _age = (_dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(_u_ts)).total_seconds() if _u_ts else 1e9
        if not force and _age < GROUP_STYLE_MAX_AGE:
            return memory.db.get_group_style(group_id)
        rows = memory.db.recent_messages("", limit=80, group_id=group_id)
        _lines = []
        for m in rows:
            _c = (m.get("content") or "").strip()
            if not _c or _c.startswith("/"):
                continue
            who = (m.get("sender_name") or ("bot" if m.get("role") == "assistant" else "群友"))
            _lines.append(f"{who}：{_c[:60]}")
        if len(_lines) < 20:
            return memory.db.get_group_style(group_id)
        _sample = "\n".join(_lines[-60:])
        _cur = memory.db.get_group_style(group_id)
        prompt = (
            "以下是一个 QQ 群最近的聊天记录抽样。概括这个群的画风：\n"
            "1. 大家主要聊什么；2. 说话风格（正式/沙雕/粗口多/梗多/爱抬杠……）；"
            "3. 氛围（友善/激烈/灌水）；4. 反复出现的黑话或梗（带一句解释）；"
            "5. 大家怎么对待机器人。\n"
            "写成 2-4 句、总长不超过 120 字的客观描述（这是给机器人的观察笔记，不是给群友看的）。"
            "\n\n聊天记录抽样：\n" + _sample
            + (f"\n之前的画像（可参考、可修正）：{_cur}" if _cur else "")
        )
        style = ""
        for _att in range(2):  # 2026-09-07：引擎同时服务聊天，瞬时失败重试一次
            resp = await client.chat.completions.create(
                model=core_llm.resolve_model("main"),
                messages=[{"role": "system", "content": "你是群观察记录员，只输出概括本身。"},
                          {"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=220,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            style = (resp.choices[0].message.content or "").strip()
            if len(style) >= 10 and "尚未提供" not in style:
                break
            style = ""
        if len(style) >= 10:
            memory.db.set_group_style(group_id, style[:200])
            logger.info("group style refreshed: gid=%s len=%d", group_id, len(style))
            return style
    except Exception as e:
        logger.warning("group style refresh failed: {}", e)
    return memory.db.get_group_style(group_id)


async def refresh_all_group_styles():
    """每小时由 debug 循环调用：对每个有记录的群按需刷新画风。"""
    try:
        for _g in memory.db.distinct_group_ids():
            try:
                await _refresh_group_style(_g)
            except Exception:  # noqa: BLE001
                continue
    except Exception as e:
        logger.warning("group styles pass failed: {}", e)


# ---------------- 群 @ 自决（2026-09-11 热修十一，用户裁决）：被 @ 不必回 ----------------
# 裁决：群聊被 @ **不需要必回**，也不需要 @ 回去（回复就是普通群消息，不加 @/引用段）；
# 可以延迟回复、也可以按人设"不屑一顾"选择不回应——延迟/沉默反而更像真人。
# 实现：机器只问一次小模型（agent 自决、机器只问一次的既有风格）——群聊现场 + 最新几条
# 上下文 + 这条 @ → 首行记号 SPEAK / SKIP。失败/超时/解析不出一律 **SPEAK（fail-open）**：
# 宁可多回，绝不让判定失败吃掉 @。SKIP → 调用方静默（消息照常 memory.log_message 入库）。
_GROUP_AT_DECIDE_TIMEOUT = 8.0  # 熔断：8s 无响应直接按 SPEAK 走（不让一次判定拖死 @ 回复链）
_GROUP_AT_DECIDE_SYSTEM = (
    "你在替「{name}」做一个「这条 @ 要不要接」的自决判断——你是她的决断器，只输出记号，不解释。"
    "现场是 QQ 群聊{scene_note}，有人刚 @ 了她。被 @ 不等于必须回：真人被点名也可以不接茬，"
    "以她的性格和此刻群聊气氛自决——\n"
    "· 这话值得她接 / 对方在等她的回应 / 不回反而显得刻意 → 第一行只输出：SPEAK\n"
    "· 她在忙别的 / 这话无趣到不值得接 / 她此刻的姿态就是不屑一顾 / 沉默更符合她 → 第一行只输出：SKIP\n"
    "只输出 SPEAK 或 SKIP 之一（第一行，大写）。"
)


async def _group_at_self_decide(text: str, group_id: str) -> bool:
    """群 @ 自决（热修十一）：以人设性格判断这条 @ 要不要接。True=SPEAK（走既有回复链），
    False=SKIP（调用方静默）。失败/超时/解析不出 → True（fail-open，绝不让判定失败吃掉 @）。
    调用形态与 _banter_need_search/preparse_owner_facts 同款：brain client + thinking_extra(False)、
    温度 0、max_tokens 小、timeout 8s 熔断；与 core/special 无关。
    分组件 fail-open：上下文/人设名取不到 → 空值照判（只降级信息量，不放弃判定）。"""
    try:
        _ctx = ""
        try:
            _rows = memory.db.recent_messages("", limit=8, group_id=str(group_id))[-6:]
            _ctx = "\n".join(
                f"{'你（bot）' if m.get('role') == 'assistant' else (m.get('sender_name') or '群友')}："
                f"{(m.get('content') or '').strip()[:100]}"
                for m in _rows if (m.get('content') or '').strip()
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("group @ self-decide ctx fetch failed: {} [{}]", e, type(e).__name__)
        _scene_note = ""
        try:
            _sc = _scene()
            _scene_note = f"（她此刻大概在{_sc}）" if _sc else ""
        except Exception:  # noqa: BLE001
            pass
        try:
            _name = _persona_name()
        except Exception:  # noqa: BLE001
            _name = ""
        resp = await client.chat.completions.create(
            model=core_llm.resolve_model("small"),
            messages=[
                {"role": "system", "content": _GROUP_AT_DECIDE_SYSTEM.format(
                    name=_name or "她", scene_note=_scene_note)},
                {"role": "user", "content":
                    f"【群聊现场】\n{_ctx or '（近期没有其他消息）'}\n\n【这条 @】\n{str(text or '').strip()[:200]}"},
            ],
            temperature=0,
            max_tokens=8,
            timeout=_GROUP_AT_DECIDE_TIMEOUT,
            extra_body=core_llm.thinking_extra(False),  # 判定无需思考——思考反而拖垮 8s 熔断
        )
        _out = (resp.choices[0].message.content or "").strip().upper()
        if _out.startswith("SKIP"):
            logger.info("group @ self-decide: SKIP (gid=%s text=%r)", group_id, str(text or "")[:40])
            return False
        return True  # SPEAK / 解析不出 → fail-open
    except Exception as e:  # noqa: BLE001
        logger.warning("group @ self-decide failed (fail-open SPEAK): {} [{}]", e, type(e).__name__)
        return True


async def _run_group_banter(bot: Bot, event: MessageEvent, group_id: str, user_id: str):
    """B10：插话评估任务入口——并发上限（Semaphore(2)）。真正的评估在信号量内进行，
    感知装配/节流复查拿到的都是到号时的最新状态（排队 task 不会拿旧上下文硬评）。"""
    async with _BANTER_SEMA:
        await _maybe_group_banter(bot, event, group_id, user_id)


async def _maybe_group_banter(bot: Bot, event: MessageEvent, group_id: str, user_id: str):
    """非@群消息的插话自决 —— Agent 循环（感知→自决→行动→反思）。

    2026-09-04 agent 化：机械 gates 删除；红线由 agent.guardrails.input_guard 判定（fail-closed）；
    主观限制（模板/书面腔/骨架/近义重复）→ agent 反思记忆（perceive 的【上次经验】感知替代）；
    sour（嫌吵）→ 感知提示而非拦截；被接话 → evaluator 正向信号入账。
    """
    try:
        import collections

        if not BANTER_ENABLED or not group_id:
            return
        # 第3轮审计 P3-2：纯语音/纯图消息（plaintext 为空）没有可评估的内容——
        # 白跑 1-2 次 LLM 还占用该群 120s 评估窗（parallel 1 后与主回复抢同一槽位）。
        _bt = (event.get_plaintext() or "").strip()
        # 收口审计 P3-5：unicode emoji 走 text 段 plaintext 非空但无可评估内容——
        # 无中/日/英文字与数字即视为无内容（纯表情/纯符号）
        if not _bt or not re.search(r"[一-鿿぀-ヿ a-zA-Z0-9]", _bt):
            return
        now = time.time()
        # 2026-09-06 时间窗兜底：bot 同群 5 分钟内插过话 → 本轮不评估（防刷屏；纯结构兜底，非拦截）
        if now - _BANTER_SENT.get(group_id, 0) < 300:
            return
        # B10：per-group 评估节流（120s，2026-09-08 质量优先 90s→120s；评估即记账——无论本轮是否真的发出插话）。
        # 曾只有"发出才记"的 _BANTER_SENT 窗：没插话的高速讨论每条消息都全量评估一次
        if now - _BANTER_EVAL_TS.get(group_id, 0) < 120:
            return
        _BANTER_EVAL_TS[group_id] = now
        text = event.get_plaintext().strip()
        # ---- 感知装配 ①：点名 / 接话检测（对话连续性） ----
        _mentioned = bool(BANTER_MENTION_RE.search(text))
        _cont = False
        _last_bot_txt = ""
        try:
            _lrows = memory.db.recent_messages("", limit=8, group_id=group_id)
            _lb = next((m for m in reversed(_lrows) if m.get("role") == "assistant"), None)
            if _lb and (time.time() - _parse_msg_ts(_lb.get("ts") or "")) < 3600:
                _last_bot_txt = _lb.get("content") or ""
                _has_reply = any(getattr(s, "type", "") == "reply" for s in event.message)
                _l2b = {_last_bot_txt[i : i + 2] for i in range(max(0, len(_last_bot_txt) - 1))}
                _l2t = {text[i : i + 2] for i in range(max(0, len(text) - 1))}
                if _has_reply or len(_l2b & _l2t) >= 3:
                    _cont = True
                    try:  # 被接话 = 正向信号（evaluator 入账反思）
                        from agent import tools as _atools
                        _atools.mark_feedback(f"banter:{group_id}", "positive")
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        # ---- 边界：输入护栏（仅配置级静默；其余全交 agent 自决）----
        from agent import guardrails as _ag, run_agent

        _sour_now = time.time() < _GROUP_SOUR.get(group_id, 0)
        _ok, _reason = _ag.input_guard({"banned": _group_banned(group_id)})
        if not _ok:
            logger.info("agent banter input-guard[gid=%s]: %s", group_id, _reason)
            return
        logger.info("agent banter evaluate: gid=%s mentioned=%s cont=%s sour=%s", group_id, _mentioned, _cont, _sour_now)
        # ---- 2026-09-06 搜索机制接入（原 _banter_need_search/_topic_summary 定义了但从未调用！）----
        # 话题涉及需要背景知识的内容 → 搜索 + 提炼 → 注入决策（不懂就不硬接）
        _bg_txt = ""
        try:
            # 2026-09-07 P1 修复：_rows_ctx 曾未定义（NameError 被 except 吞）→ banter 搜索链自接线日起死亡。
            # 现从记忆库取本群最近 8 条全员消息作话题背景。
            _rows_ctx = memory.db.recent_messages("", limit=8, group_id=str(group_id))
            _nl = [m.get("content") or "" for m in _rows_ctx if (m.get("content") or "").strip()][-8:]
            _need = await _banter_need_search(_nl)
            if _need and _need.get("query"):
                _q = _need["query"]
                try:
                    from plugins import search as _sp

                    # 2026-09-08 P1 修死链：曾调不存在的 _sp.search()（hasattr 恒 False）+
                    # 兜底 web_search(q=) 关键字不匹配（实签名 query）TypeError 被吞 →
                    # _src 恒 None——09-07 只接通了上半段 _rows_ctx，本层自接线日起从未产出过背景。
                    # 改走 _run_search 超时熔断 + format_results（与 brain 其他搜索路径同口径）。
                    _wres, _serr = await _run_search(_sp.web_search(_q))
                    _src = _sp.format_results(_wres[0], 2) if _wres and _wres[0] else ""
                except Exception:
                    _src = ""
                if _src:
                    _bg_txt = await _topic_summary(_q, str(_src)[:1200])
                    if _bg_txt and len(_bg_txt) >= 4:
                        logger.info("banter search bg: q=%r bg=%r", _q, _bg_txt[:80])
        except Exception:
            pass  # 2026-09-06 观测：进入 LLM 判定的次数
        # ---- 感知装配 ②：上下文快照 ----
        _psince = _persona_switch_ts(user_id)
        rows = memory.db.recent_messages(user_id, limit=12, group_id=group_id)
        if _psince:
            rows = [m for m in rows if (m.get("ts") or "") >= _psince]
        # 2026-09-05 上下文时间窗：只感知最近 30 分钟（旧戏不污染——"生鱼片斗嘴"隔了 600+ 条后仍在 ctx 导致情感戏错乱）
        _recent = [
            m for m in rows[-10:]
            if (m.get("content") or "").strip()
            and (time.time() - _parse_msg_ts(m.get("ts") or "")) < 1800
        ]
        if not _recent:
            return
        ctx_lines = [
            f"{'你（bot）' if m.get('role') == 'assistant' else (m.get('sender_name') or '群友')}："
            f"{((m.get('content') or '').strip()[:150] if i == len(_recent) - 1 else (m.get('content') or '').strip()[:100])}"
            for i, m in enumerate(_recent)
        ]
        # 2026-09-05 自我识别：bot 自己的消息标注「你（bot）」——识别自己的话（根因防复读；感知式，非禁令）。
        # 规则行放最前：不落入 _banter_need_search 的 [-8:] 切片（避免被当群聊话题）
        ctx_lines.insert(0, "（标注「你（bot）」的是你自己说过的话——注意识别，不要误当作群友的话。）")
        # 2026-09-05 消息剥离：当前消息的 @/引用 结构（QQ 引用段不在 plaintext 里，必须显式标注）
        _cur_note = _msg_struct_note(event)
        if _cur_note:
            ctx_lines.append(_cur_note)
        # 2026-09-06 语言感知：英/日文消息标注（先读懂再回应；翻译与否 bot 自决）
        _langn = _lang_note(event)
        if _langn:
            ctx_lines.append(_langn)
        # 2026-09-06 人设名提及：消息文本里有"阿米娅"等名字（非@）→ 弱感知（可能是在叫你；agent 自决）
        _namen = _name_mention_note(text)
        if _namen:
            ctx_lines.append(_namen)
        # 2026-09-06 梗知识库注入（群聊玩梗认知；接不接自决）
        _memen = _meme_note(text)
        if _memen:
            ctx_lines.append(_memen)
        # 成员名单（主动@对象感知）
        _gm = _load_group_members().get(str(group_id), {})
        _recent_m = sorted(_gm.items(), key=lambda kv: -float((kv[1] or {}).get("ts", 0) or 0))[:12]
        _names = [((v or {}).get("nick") or k) for k, v in _recent_m]
        if _names:
            ctx_lines.append("（本群最近活跃成员：" + "、".join(_names) + "。要跟某人说话可用 @昵称 开头指定。）")
        # 2026-09-05：群友互动画像（频次/最近原话——「认识人」感知；nicks=昵称映射）
        try:
            _umeta = memory.db.group_user_meta_text(str(group_id), limit=4, nicks=_gm)
            if _umeta:
                ctx_lines.append(_umeta)
        except Exception:  # noqa: BLE001
            pass
        if _sour_now:
            ctx_lines.append("（最近有群友嫌你吵——你心里有数，自己把握分寸。）")  # 感知而非拦截
        # 2026-09-07 说话人熟悉度感知（群友好友轨道 0-30）：熟不熟由 agent 自决接不接
        try:
            _rel_u = memory.db.get_relation(user_id)
            _iv = float((_rel_u or {}).get("intimacy", 0) or 0)
            _gm_u = (_gm or {}).get(str(user_id)) or {}
            _nick_u = (_gm_u.get("nick") or _gm_u.get("card") or "").strip() if isinstance(_gm_u, dict) else ""
            _who = f"「{_nick_u}」" if _nick_u else "这位群友"
            _fam = "你们有点熟了" if _iv >= 10 else "你们基本不熟"
            ctx_lines.append(f"（说这句话的是{_who}——{_fam}，好感 {_iv:.0f}/30）")
        except Exception:  # noqa: BLE001
            pass
        # 2026-09-07 群画风画像（LLM 定性）：这个群聊什么/什么味儿——怎么融入由 agent 自决
        try:
            _gstyle = memory.db.get_group_style(group_id)
            if _gstyle:
                ctx_lines.append("（这个群的画风：" + _gstyle + "）")
        except Exception:  # noqa: BLE001
            pass
        _rel = memory.db.get_relation(_owner_id()) if _owner_id() else None
        _mood = (_rel.get("mood", "") or "") if _rel else ""
        # 2026-09-06 情绪连续体（白名单降级已移除）：真实情绪直接注入 + 最近轨迹（弧线感知）
        try:
            _emotions = memory.db.get_recent_emotions(_owner_id(), limit=6) if _owner_id() else []
        except Exception:  # noqa: BLE001
            _emotions = []
        if _emotions:
            _arc = "→".join(e["emotion"] for e in _emotions if e.get("emotion") and e["emotion"] != "平静")
            if _arc:
                ctx_lines.append(f"（你最近这一阵的心情轨迹：{_arc}——心里有数就行，不用解释。）")
        try:
            # 2026-09-07 双修：①persona 口径与写侧（lifesim，卡显示名）对齐——曾读 persona_select
            # 文件键，两域永不匹配，通道双死；②_atools 曾只在被接话分支内 import，非接话分支到此处
            # 引用未绑定名 NameError 被 except 吞 → insight 感知静默失效
            from agent import tools as _atools

            _ins = _atools.recall("insight", persona=str((_persona_card() or {}).get("name") or ""))
            if _ins:
                ctx_lines.append("（你这一阵的心得：" + _ins + "——心里知道就行，不用说出口。）")
        except Exception:  # noqa: BLE001
            pass
        card = _persona_card()
        # ---- Agent 循环（感知→自决→行动→反思；thread 隔离 = 群维度串行/跨群并行）----
        _st = await run_agent(
            {
                "kind": "banter", "user_id": user_id, "group_id": group_id, "text": text,
                "is_group": True, "mentioned": _mentioned, "continuation": _cont,
                "persona": _persona_name(),  # 2026-09-05：反思记忆按人设隔离（旧卡发言不注入新卡）
                "context": {
                    "ctx_lines": ctx_lines, "ctx_text": "\n".join(ctx_lines),
                    "mood": _mood, "card": card, "search_bg": _bg_txt,
                },
            },
            thread_id=("banter:%s") % (str(group_id) + ":" + str(_persona_switch_ts("") or int(time.time()))),
        )
        if not _st or _st.get("decision_action") != "speak":
            logger.info("agent banter self-judged silent: gid=%s", group_id)
            return
        line = (_st.get("final_line") or "").strip()
        if len(line) < 2:
            return
        # 2026-09-07 对话样本泄漏防护：模型偶发输出「昵称：…\nbot：…」整段样本 → 只留 bot 自己那轮
        line = _collapse_dialogue_sample(line, _persona_current_names())
        if len(line) < 2:
            return
        # 2026-09-11 热修十一（用户实测元规划泄漏）：发送前再过一道元规划剥离——graph._act 已剥一次，
        # 此处兜底（幂等）：多段"规划+---+回应"取末段、规划动词开头行剥除；剥光则维持原行不劫持。
        from core import reply as _reply_meta

        line = _reply_meta._strip_meta_planning(line)
        if len(line) < 2:
            return
        # ---- 事故兜底 + 反射事件源（2026-09-05）：与最近 1h 内本群 bot 回复 2-gram 重叠 >=50% → 丢弃，
        #      同时写入反思记忆——agent 下轮 perceive 经【上次经验】自省（agent 逻辑为主，机器只兜事故级复读） ----
        try:
            if _last_bot_txt and len(_last_bot_txt) >= 6:
                _lb2 = {_last_bot_txt[i:i + 2] for i in range(len(_last_bot_txt) - 1)}
                _ll2 = {line[i:i + 2] for i in range(len(line) - 1)}
                # 2026-09-07：分母 min→并集（真 Jaccard，与 core/reply 口径对齐——旧包含度对短台词必误判）
                if _lb2 and len(_lb2 & _ll2) / max(1, len(_lb2 | _ll2)) >= 0.5:
                    try:
                        from agent import tools as _atools2
                        _atools2.mark_feedback(f"banter:{group_id}", "negative")
                        _atools2.reflect(f"banter:{group_id}", "上次你复读了自己说过的话——识别自己刚说的内容，不要重复")
                    except Exception:  # noqa: BLE001
                        pass
                    logger.info("agent banter self-repeat blocked: gid=%s line=%s", group_id, line[:30])
                    return
        except Exception:  # noqa: BLE001
            pass
        # ---- 发送格式化（@ 解析——非决策，仅格式化为 at 段）----
        _at_qid = None
        _at_m = re.match(r"(?:【@([^】]{1,16})】|[@＠]([^\s，。！？、@＠]{1,16}))\s*", line)
        if _at_m:
            _nick = (_at_m.group(1) or _at_m.group(2) or "").strip()
            _rest = line[_at_m.end():].strip()
            if not _rest:
                return
            line = _rest
            if re.search(r"你|我们去|一起来|下次|陪我|约|别|凭什么|到底|给我|过来|一起|说清楚|混蛋|吵|看什么", line):
                for _qq, _info in _gm.items():
                    if ((_info or {}).get("nick") or "") == _nick or ((_info or {}).get("card") or "") == _nick:
                        _at_qid = _qq
                        break
        # ---- 开口节奏（2026-09-07 P2：【等X秒】曾只产不消费——图产了 pacing 机器读完即丢）----
        _pace_g = float((_st.get("pacing") or 0) or 0)
        if _pace_g > 0:
            await asyncio.sleep(min(2.0, _pace_g))
        try:
            if _at_qid:
                await bot.send(event, MessageSegment.at(int(_at_qid)) + " " + line)
            else:
                await bot.send(event, line)
        except Exception as e:  # noqa: BLE001
            logger.warning("banter send failed: %s [%s]", e, type(e).__name__)
            return
        try:
            memory.log_message(str(event.get_user_id()), group_id, "assistant", line, sender_name=None)
        except Exception:  # noqa: BLE001
            pass
        _BANTER_SENT[group_id] = time.time()
        # 2026-09-08 骨架防重复：发送成功后登记本群插话结构键（下轮 perceive 注入，agent 自决换/不插）
        try:
            _banter_rhythm_record(group_id, line)
        except Exception:  # noqa: BLE001
            pass
        logger.info("agent banter sent: gid=%s at=%s line=%r", group_id, _at_qid or "-", line[:40])
    except Exception as e:  # noqa: BLE001
        logger.warning("group banter err: %s [%s]", e, type(e).__name__)

# ---------------- 约定·临时文档（2026-09-05：无机器定时——bot 自决接纳后记入，后续自决时"想起来"自然提及） ----------------
PENDING_FILE = DATA_ROOT / "pending_tasks.json"
_REMINDER_RE = re.compile(
    r"(?P<n>\d+)\s*(?P<u>秒|分钟|分|小时|钟头|半小时)\s*(?:之)?后\s*(?:提醒我|记得|叫我|告诉我|叫我做|提醒)\s*(?P<c>[^。！？\n]{1,50})?"
)
_REMINDER_RE2 = re.compile(r"(?:帮我)?(?:定时|设置|设个|定个)(?:提醒|闹钟)[：:，, ]?\s*(?P<c>[^。！？\n]{1,50})?\s*(?P<n>\d+)\s*(?P<u>秒|分钟|分|小时)?")
# 接纳信号（bot 自决答应后记入临时文档）——宽松匹配（"好的/交给我/放心/记着呢"等）
_REMINDER_ACCEPT_RE = re.compile(r"(记住|记着|记得|好的|好呀|好嘞|行了|放心|答应|会的|没问题|交给我|我来|OK|okay|嗯嗯|收到)")


_CAL_SEG_HOUR = {"凌晨": 2, "早上": 8, "上午": 10, "中午": 12, "下午": 15, "傍晚": 18, "晚上": 20}
_CAL_RE = __import__("re").compile(r"(?:(?P<jd>今天|明天|后天|今晚|明晚|明后天|大后天)|(?:(?P<wk>这|下)?(?:周|个星期|礼拜))?(?P<wd>[一二三四五六日天])?|(?P<segd>上午|下午|晚上|中午|凌晨|傍晚|早上))\s*(?:(?P<seg2>上午|下午|晚上|中午|凌晨|傍晚|早上))?\s*(?:(?P<tm>\d{1,2})\s*点\s*(?P<tm2>\d{1,2})?\s*分?|(?P<tmh>\d{1,2}):(?P<tmm>\d{2}))?\s*[:：]?\s*(?P<what>[^。，！？\n]{2,40})")


def _reminder_parse(text: str) -> tuple[int, str] | None:
    """解析「X分钟后提醒我 Y」→ (delay_sec, content)；无则 None。仅提取，不存储（接纳由 bot 自决）。"""
    try:
        m = _REMINDER_RE.search(text or "")
        if m:
            n = int(m.group("n"))
            u = m.group("u")
            unit = 60 if u in ("分", "分钟") else (3600 if u in ("小时", "钟头") else (30 * 60 if u == "半小时" else 1))
            if u in ("秒",):
                unit = 1
            content = (m.group("c") or "").strip()
            return (n * unit, content or "刚才说的事")
        m2 = _REMINDER_RE2.search(text or "")
        if m2:
            n = int(m2.group("n") or "1")
            u = m2.group("u") or "分钟"
            content = (m2.group("c") or "").strip()
            unit = 60 if u in ("分", "分钟") else (3600 if u == "小时" else 1)
            return (n * unit, content or "刚才说的事")
    except Exception:  # noqa: BLE001
        pass
    return None


def _load_pending(path: Path | None = None) -> list[dict]:
    """读约定（默认活动位 PENDING_FILE；方案A 切卡治理传卡档路径）。"""
    p = path or PENDING_FILE
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
        return d if isinstance(d, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_pending(rs: list[dict], path: Path | None = None):
    p = path or PENDING_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(p, rs)


def _pending_profile_file(card: str) -> Path:
    """卡名 → 约定档案文件 data/pending_tasks_{card}.json（卡名消毒同 lifesim._sanitize_card）。"""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(card or "").strip()) or "default"
    return PENDING_FILE.parent / f"pending_tasks_{safe}.json"


def _switch_pending_profile(old_card: str, new_card: str) -> dict:
    """方案A（2026-09-09 用户裁决）：约定（pending）按卡隔离——活动 PENDING_FILE 当前内容
    归档到旧卡名下，再恢复新卡档（无档=空列表）。活动位路径/读写语义不变，
    _add_pending/_pending_for/_pending_mention_text 等其他调用点零改动。
    同名/新卡名为空 → no-op 返回 {"skipped": True}；全程 try/except 绝不抛出
    （失败返回 {"error": ...}，治理失败不能影响 bot）。"""
    try:
        old_card = str(old_card or "").strip()
        new_card = str(new_card or "").strip()
        if not new_card or old_card == new_card:
            return {"skipped": True}
        cur = _load_pending()
        archived = 0
        if old_card and atomics.write_json_atomic(_pending_profile_file(old_card), cur):
            archived = len(cur)
        restored_rows: list = []
        prof = _pending_profile_file(new_card)
        if prof.exists():
            try:
                d = json.loads(prof.read_text(encoding="utf-8-sig"))
                if isinstance(d, list):
                    restored_rows = d
            except (OSError, json.JSONDecodeError):
                restored_rows = []
        _save_pending(restored_rows)
        logger.info("pending profile switch: %s -> %s archived=%s restored=%s",
                    old_card or "default", new_card, archived, len(restored_rows))
        return {"old": old_card or "default", "new": new_card,
                "archived": archived, "restored": len(restored_rows)}
    except Exception as e:  # noqa: BLE001
        logger.warning("pending profile switch failed: %s [%s]", e, type(e).__name__)
        return {"error": str(e)}


def _add_pending(user_id: str, group_id: str, content: str, note: str = "", src: str = "") -> int:
    """约定入临时文档（bot 自决接纳后才调用）。note=可选备注（如"X分钟后提醒"的原始时限）。
    src="bot"=她自许的承诺（2026-09-08 承诺闭环：可被【约定完成】清账、48h 未兑现自动过期）。"""
    rs = _load_pending()
    rid = max([int(r.get("id", 0) or 0) for r in rs] or [0]) + 1
    rs.append({"id": rid, "uid": str(user_id), "gid": str(group_id or ""), "content": content[:80],
               "note": note[:40], "ts": time.time(), "mention_ts": 0.0, "src": src})
    _save_pending(rs)
    return rid


def _pending_for(user_id: str) -> list[dict]:
    """该用户的约定（临时文档；触发由 bot 自决——感知注入时读到，是否提及自决）。"""
    return [r for r in _load_pending() if str(r.get("uid", "")) == str(user_id)]


def _pending_due_note(content: str) -> bool:
    """约定内容含【MM-DD HH:mm】且当前处于该时刻窗口（前 30 分钟 ~ 后 6 小时）→ 视为「时间到了」。"""
    try:
        import datetime as _dtc
        m = re.search(r"【(\d{2}-\d{2} \d{2}:\d{2})】", content or "")  # 2026-09-07 P2：曾 \\d 双重转义永不命中（约定到期检测死亡）
        if not m:
            return False
        cur = _dtc.datetime.now()
        at = _dtc.datetime.strptime(f"{cur.year}-{m.group(1)}", "%Y-%m-%d %H:%M")
        return (at - _dtc.timedelta(minutes=30)) <= cur <= (at + _dtc.timedelta(hours=6))
    except Exception:
        return False


def _pending_mention_text(user_id: str) -> str:
    """感知注入：你心里记着的约定（<10min 前刚提过的不重复注入；重复由 bot 自决控频）。
    2026-09-08 承诺闭环：她自许的承诺（src="bot"）48h 未兑现自动过期——说话算话，但不必永远挂在心上。"""
    rs = _load_pending()
    now = time.time()
    _stale = [r for r in rs if str(r.get("src", "")) == "bot" and now - float(r.get("ts", 0) or 0) > 172800]
    if _stale:
        rs = [r for r in rs if r not in _stale]
        _save_pending(rs)
        logger.info("bot promise expired (48h): %s 条", len(_stale))
    rows = [r for r in rs if str(r.get("uid", "")) == str(user_id) and time.time() - float(r.get("mention_ts", 0) or 0) > 600]
    if not rows:
        return ""
    # 2026-09-08：修重复措辞（原拼接出"你心里记着你心里一直记着"）
    parts = []
    for r in rows[:2]:
        c = str(r["content"])
        if _pending_due_note(c):
            parts.append("**这件事的时刻到了**「" + c + "」")
        else:
            parts.append("你心里一直记着：对" + str(user_id)[-4:] + "的承诺「" + c + "」")
    return "\n\n【你的约定】" + ("；".join(parts)) + "——如果当下自然，可以提一句；不急着反复提。"


def _complete_bot_promise(user_id: str = "") -> str | None:
    """清掉最新一条她自许的承诺（【约定完成】兑现路径；user_id 非空时限该用户）。返回被清内容。
    2026-09-08 承诺闭环：完成与否由她的叙事自决（对话里或心跳里声明兑现），机器只记账。"""
    rs = _load_pending()
    for i in range(len(rs) - 1, -1, -1):
        r = rs[i]
        if str(r.get("src", "")) != "bot":
            continue
        if user_id and str(r.get("uid", "")) != str(user_id):
            continue
        done = rs.pop(i)
        _save_pending(rs)
        logger.info("bot promise fulfilled & cleared: uid=%s content=%s", done.get("uid"), str(done.get("content"))[:40])
        return str(done.get("content"))
    return None


# ---------------- 群聊差评降级（2026-09-04：有人嫌 bot 吵/要它闭嘴 → 自动降速）----------------
# 负面反馈（规则快通道）：命中计入该群；窗口内 ≥2 次 → 降级期（只响应 @/点名，插话冷却大幅拉长 + 收敛）
_SOUR_RE = re.compile(r"闭嘴|别吵|太吵|吵死|安静|烦死|好烦|别说话|少说话|别插|少插|刷屏|尬聊|别再|别一直|shut up|废话真多|烦不烦|别烦|滚蛋")
SOUR_WINDOW = 600        # 降级持续（秒）；恢复自动：到期恢复 + 期间负面计数衰减
SOUR_TRIGGER_HITS = 2    # 窗口内 2 次负面反馈才降级（1 次不算：容忍口嗨）
_GROUP_SOUR: dict[str, float] = {}          # group_id -> until_ts（降级到期）
_GROUP_SOUR_HITS: dict[str, list[float]] = {}  # group_id -> [负面反馈 ts...]


def _register_sour(group_id: str):
    """群内负面反馈登记：窗口内 2 次 → 该群降级 10 分钟。"""
    if not group_id:
        return
    now = time.time()
    hits = [t for t in _GROUP_SOUR_HITS.get(group_id, []) if now - t < SOUR_WINDOW]
    hits.append(now)
    _GROUP_SOUR_HITS[group_id] = hits
    if len(hits) >= SOUR_TRIGGER_HITS:
        _GROUP_SOUR[group_id] = now + SOUR_WINDOW
        logger.info("group sour mode ON: gid=%s hits=%d", group_id, len(hits))


def _sour_note(group_id: str) -> str:
    """降级期注记（感知式提示——agent 自决收敛，非拦截）；非降级期返回空。"""
    if not group_id or time.time() >= _GROUP_SOUR.get(group_id, 0):
        return ""
    return ("\n\n【收敛提醒·降级期】最近有群友嫌你吵、让你闭嘴——你心里有数：这一阵自然少说话，"
            "别抢话、别主动延伸话题；有人点名你或你有话非说不可，就说一句短的；语气放软，不辩解。")


# ---------------- 场景系统（2026-09-06 用户：管理员设场景；pire场景=独处——群静默、非管理员私聊挡回；
# 普通私聊不改场景但改状态（会话感知=一个人）；群聊滚动不改任何设计。
# 场景库按世界观（data/scenes.json）；打扰感=agent 自决（挂好感度）。） ----------------
# 2026-09-10 Phase1 修缺陷：parents[3] 指向 E:\robot\data（不存在 scenes.json，场景库静默为空）——
# 本文件在 qq-bot/plugins/brain/，parents[2]=qq-bot，场景库实际在 qq-bot/data/scenes.json
_SCENES_FILE = Path(__file__).resolve().parents[2] / "data" / "scenes.json"
_SCENES_DB: dict = {"mtime": -1.0, "data": {}}
_RECENT_PRIV: dict[str, float] = {}   # uid -> 上次私聊（多会话感知："一个人"）
# 2026-09-07：挡回语跟随当前场景（场景已由 agent 生活轨迹迁移，不能再硬编码“办公室”）

def _scene_msg() -> str:
    try:
        return f"暂时不在{_scene() or '办公室'}，有事留言。"
    except Exception:
        return "暂时不在，有事留言。"


def _scene_db() -> dict:
    """场景库（按世界观）：{universe: {"private": [...], "public": [...]}}；mtime 缓存。"""
    try:
        m = _SCENES_FILE.stat().st_mtime
        if m != _SCENES_DB["mtime"]:
            _SCENES_DB["data"] = json.loads(_SCENES_FILE.read_text(encoding="utf-8-sig"))
            _SCENES_DB["mtime"] = m
    except Exception:  # noqa: BLE001
        pass
    return _SCENES_DB["data"]


def _scene_universe(card: dict | None = None) -> str:
    """当前人设卡世界观（卡 universe 字段；无则 default）。"""
    try:
        return str((card or {}).get("universe") or "default")
    except Exception:  # noqa: BLE001
        return "default"


def _scene_is_private(scene: str, universe: str = "default") -> bool:
    """场景是否独处（按世界观归属；未知场景按 default 组判定）。"""
    try:
        db = _scene_db()
        for g in (universe, "default"):
            _p = db.get(g, {}).get("private", [])
            if scene in _p:
                return True
            if scene in db.get(g, {}).get("public", []):
                return False
        # 都不在库：按"卧室/床/被窝/房间"等私密意象兜底判定
        return any(k in scene for k in ("卧室", "床", "被窝", "宿舍", "闺房", "宫殿", "房间", "旧居", "神社", "驾驶舱", "小屋", "屏幕"))
    except Exception:  # noqa: BLE001
        return False


def _scene() -> str:
    """当前场景（读 life_state.scene；空=未定——由 agent 生活轨迹经【场景：…】标记迁移）。"""
    try:
        from agent import lifesim as _ls
        return str(_ls._load_state().get("scene") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _set_scene(scene: str):
    try:
        from agent import lifesim as _ls
        st = _ls._load_state()
        st["scene"] = scene
        _ls._save_state(st)
    except Exception:  # noqa: BLE001
        pass


def _scene_cmd(text: str, universe: str = "default") -> tuple[str | None, bool]:
    """管理员场景指令识别（配置层词匹配；命中→(场景词, True)）。词表=当前世界观场景库。"""
    try:
        db = _scene_db()
        words: list[str] = []
        for g in (universe, "default"):
            _d = db.get(g, {})
            words += list(_d.get("private", []) or []) + list(_d.get("public", []) or [])
        words = sorted(set(words), key=len, reverse=True)  # 长词优先（"她家的客厅"在"客厅"前）
        for w in words:
            if re.search(r"(?:去|回|到|进|睡|躺|在|待)\s*" + re.escape(w), text or ""):
                return w, True
    except Exception:  # noqa: BLE001
        pass
    return None, False


def _convo_note(user_id: str) -> str:
    """多会话"一个人"感知（薄壳；_RECENT_PRIV 由 brain 持有，组装在 core.perception）。"""
    return perception.convo_note(_RECENT_PRIV, user_id)


# ---------------- 群聊追问模式 ----------------
FOLLOWUP_MAX = 3          # 最多三轮
FOLLOWUP_WINDOW = 120     # 2 分钟无用户追问消息则退出（防状态残留误判）
_FOLLOWUP: dict[str, dict] = {}  # group_id -> {"count": int, "expire": float}


def _followup_state(group_id: str, has_at_others: bool, is_owner: bool) -> int:
    """追问模式状态机（仅用户发起的追问有效；仅用户消息推进计数）。

    返回当前追问轮次（0=不在追问模式）。非用户消息不推进也不破坏状态。
    """
    now = time.time()
    st = _FOLLOWUP.get(group_id)
    if st and now < st["expire"]:
        if not is_owner:
            return 0  # 非用户插话：不计数（保留状态），按普通群聊外人处理
        st["count"] += 1
        st["expire"] = now + FOLLOWUP_WINDOW
        if st["count"] >= FOLLOWUP_MAX:
            _FOLLOWUP.pop(group_id, None)
        return min(st["count"], FOLLOWUP_MAX)
    if has_at_others and is_owner:
        _FOLLOWUP[group_id] = {"count": 1, "expire": now + FOLLOWUP_WINDOW}
        return 1
    _FOLLOWUP.pop(group_id, None)
    return 0


@chat.handle()
async def handle(bot: Bot, event: MessageEvent):
    text = event.get_plaintext().strip()
    # 2026-09-08 D1·P1a 收图存在感知：真人不会看不见你发的图——
    # 纯图消息（plaintext 空）不再丢弃；图文混合时感知注记"TA 发了图"
    # 盲审修正 B2：感知句用无括号纯文本（曾用（ ）——被 _ACTION_BRACKET_RE 当"用户括号动作"误注入）
    _has_img = any(str(getattr(s, "type", "")) == "image" for s in getattr(event, "message", []))
    _img_only = False
    if not text and _has_img:
        text = "TA 给你发了一张图片"
        _img_only = True
    # 2026-09-09 深夜：语音消息黑洞修复（调研 Q1①）——纯语音（record 段）plaintext 同样为空，
    # 曾整条静默丢弃（连 memory.log_message 都不入）="发语音她装没听见"。对齐收图存在感知模式：
    # 纯语音 → text 转存在感知句走正常回复链（私聊照常回复，感知句照常入库——图片感知句同款）；
    # 语音+文本混合 → 下方【消息结构】注记追加（对齐图文注记）；群聊非@ 门控复用 _img_only 同款。
    # B2 同款纪律：感知句用无括号纯文本（（ ）会被当"用户括号动作"误注入）。
    # 注意与 [VOICE] 发送标记无关（那是 bot 出向语音，这是用户入向语音）。
    _has_voice = any(str(getattr(s, "type", "")) == "record" for s in getattr(event, "message", []))
    _voice_only = False
    if not text and _has_voice:
        text = "TA 给你发了一条语音消息"
        _voice_only = True
    if not text:
        # 2026-09-06 @干修复（正确位置）：空@（被@但没写字）→ 视为戳一戳，要回应
        try:
            if isinstance(event, GroupMessageEvent) and getattr(event, "to_me", False) and str(event.get_user_id()) != str(getattr(event, "self_id", "") or ""):
                text = "(TA 只@了你，什么也没说——TA 在等你回应)"
            else:
                return
        except Exception:
            return
    # 2026-09-04：剥离 at CQ 码（[at:qq=xxx] → 空）——防说话人指认/称呼被 CQ 码污染、避免模型猜谁说了什么
    text = re.sub(r"\[at:qq=\d+\]", "", text).strip()
    if not text and isinstance(event, GroupMessageEvent):
        # 2026-09-06 @干修复：被@但没写字（空@）→ 视为戳一戳（要回应）
        try:
            if getattr(event, "to_me", False) and str(event.get_user_id()) != str(getattr(event, "self_id", "") or ""):
                text = "(TA 只@了你，什么也没说——TA 在等你回应)"
        except Exception:
            pass  # 兜底：取不到事件字段就不写这句提示，下面 if not text 直接 return（宁可不答，不替 TA 猜）
        if not text:
            return
    _ck = _persona_name()  # 2026-09-11 热修九：当前人设卡键（特殊状态按卡分桶；bot 全局形象=主人当前选择）
    # 2026-09-04：被禁言群 → 静默（消息照常入库保留记忆，但不回复/不插话/不跑 LLM）
    if isinstance(event, GroupMessageEvent):
        _bgid = str(getattr(event, "group_id", "") or "")
        if _group_banned(_bgid):
            try:
                _s = getattr(event, "sender", None)
                _sn = (getattr(_s, "nickname", None) or getattr(_s, "card", None) or "")
                memory.log_message(str(event.get_user_id()), _bgid, "user", text, sender_name=_sn)
            except Exception:  # noqa: BLE001
                pass
            return
    # ---- 场景系统·门（2026-09-06：独处场景=群聊静默、非管理员私聊挡回；管理员可切场景）----
    # 2026-09-11 热修八（用户实测群@不回话）：私场景静默防冻结——场景字段滞后于叙事
    # （夜间【场景：卧室】声明后，白天 doing 已转公开而标记未再出现，过期私场景会把群聊静默一整天）。
    # 静默改判：场景字段与最新叙事（doing）**双重私密**才静默；doing 命中公开词表或无私密词→照常应答。
    try:
        _scene_now0 = _scene()
        _univ0 = _scene_universe()  # bot 公共形象=主人卡世界观
        _doing0 = ""
        try:
            from agent import lifesim as _ls0
            _doing0 = str((_ls0._load_state() or {}).get("doing") or "")
        except Exception:
            _doing0 = ""
        _super_set = set(str(x) for x in (getattr(cfg, "superusers", set()) or set()))
        if str(event.get_user_id()) in _super_set:
            _sc0, _ok0 = _scene_cmd(text, _univ0)
            if _ok0 and _sc0:
                _set_scene(_sc0)
                logger.info("scene set: %s (owner cmd)", _sc0)
        if (_scene_now0 or _doing0) and _scene_is_private(_doing0 or _scene_now0, _univ0):
            if isinstance(event, GroupMessageEvent):
                # 独处：群聊全部静默（消息照常入库保留记忆，不插话不回复）
                try:
                    _s = getattr(event, "sender", None)
                    _sn0 = (getattr(_s, "nickname", None) or getattr(_s, "card", None) or "")
                    memory.log_message(str(event.get_user_id()), str(getattr(event, "group_id", "")), "user", text, sender_name=_sn0)
                except Exception:  # noqa: BLE001
                    pass
                return
            if str(event.get_user_id()) not in _super_set:
                # 独处 + 非管理员私聊：自动挡回（人设名前缀，简单留言感）
                try:
                    memory.log_message(str(event.get_user_id()), "", "user", text)
                except Exception:  # noqa: BLE001
                    pass
                # 2026-09-07 用户裁决：挡回白名单是对的，但文案按关系分层（普通=留言感；白名单=活人感的"被记下了"）
                _wl_scene = str(event.get_user_id()) in set(str(x) for x in _load_intimate_wl())
                try:
                    if _wl_scene:
                        await bot.send(event, f"{_persona_name(str(event.get_user_id()))} 一个人待在{_scene_now0}，刚想安静一会儿——被你叫了一下，有点没忍住，不过现在还是不想说话。晚点我再找你。")
                    else:
                        await bot.send(event, f"{_persona_name(str(event.get_user_id()))} {_scene_msg()}")
                except Exception:  # noqa: BLE001
                    pass
                return
    except Exception:  # noqa: BLE001
        pass
    # ---- 事件驱动时间线唤醒（2026-09-06：距上次心跳 ≥30min 且有人在找 → 先恢复生活再对话——
    # 睡了 8 小时后私聊 = 被找时已是"醒了"，绝不是用旧状态（睡觉）回消息）----
    try:
        _need_wake = (not isinstance(event, GroupMessageEvent)
                      or event.is_tome() or _is_text_at_me(text))
        if _need_wake and "lifesim" not in text:
            from agent import lifesim as _ls9
            _s9 = _ls9._load_state()
            if _s9.get("doing") and (time.time() - float(_s9.get("ts") or 0)) >= 1800:
                await _ls9.lifesim_tick(card=_persona_card(str(event.get_user_id())))
                logger.info("lifesim: event-wake tick (uid=%s)", event.get_user_id())
    except Exception:  # noqa: BLE001
        pass
    # 2026-09-06 开口节奏·输入侧：同人连发聚合（一句话拆两条发 → 合并成一次处理，防双回）
    _skey0 = ""
    try:
        if not text.startswith("/"):
            _skey0 = (f"p:{event.get_user_id()}" if not isinstance(event, GroupMessageEvent)
                      else f"g:{getattr(event, 'group_id', '')}:{event.get_user_id()}")
            # 忙期串行（2026-09-06：bot 正在回复本会话=输入提示未解除 → 后续同人消息排队，不开并行思考）
            _b = _BUSY.get(_skey0, 0.0)
            while _b > time.time():
                try:
                    await asyncio.sleep(min(0.4, max(0.1, _b - time.time())))
                except Exception:  # noqa: BLE001
                    break
                _b = _BUSY.get(_skey0, 0.0)
            _at_explicit = (isinstance(event, GroupMessageEvent)
                            and (event.is_tome() or _is_text_at_me(text)))  # 2026-09-07 显式点名不聚合
            _merged0, _swallowed0 = await _coalesce_incoming(_skey0, text, force_now=_at_explicit)
            if _swallowed0:
                return
            if _merged0 != text:
                text = _merged0
            _BUSY[_skey0] = time.time() + 15.0  # 处理窗（聚合+生成+分段发送；15s 防悬挂自动放行）
            _RECENT_PRIV[str(event.get_user_id())] = time.time()  # 多会话感知："一个人"（私聊在场记录）
    except Exception:  # noqa: BLE001
        pass
    # 群聊只响应 @机器人；私聊全部响应
    # 2026-09-06：文本形式点名（对方机器人常用 "@阿米娅" 文本而非真 at 段）→ 等同 @ 必回
    if isinstance(event, GroupMessageEvent) and not event.is_tome() and not _is_text_at_me(text):
        # 2026-08-28：全群其他成员的发言也入库（带昵称），供上下文接话（"前面已经讨论了"）——
        # 不回复、不处理状态机，仅作为群聊记忆
        # 2026-09-08 盲审修正 B3：群聊非@ 纯图消息是 bot 的感知旁白（非用户发言）——跳过入群记忆；
        # 只有带真实文本或@ 才入库（纯图在群聊里是别人的图，bot 无感即可，不应入上下文）
        # 2026-09-09 深夜：纯语音同款对齐（调研 Q1①）——群聊非@ 纯语音=别人的语音，
        # 感知句不入群记忆、不回复（对齐纯图门控）
        try:
            if not (_img_only or _voice_only):
                _s = getattr(event, "sender", None)
                _sn = (getattr(_s, "nickname", None) or getattr(_s, "card", None) or "")
                memory.log_message(str(event.get_user_id()), str(getattr(event, "group_id", "")), "user", text, sender_name=_sn)
                _remember_group_member(str(getattr(event, "group_id", "")), str(event.get_user_id()), _sn or "", getattr(_s, "card", "") or "")
                # 2026-09-05：群友轻量互动画像（频次/词袋）
                memory.db.bump_group_user_meta(
                    str(getattr(event, "group_id", "")), str(event.get_user_id()), content=text
                )
                # 2026-09-04：嫌吵/闭嘴类负面反馈 → 该群降级登记（自动降速）
                if _SOUR_RE.search(text):
                    _register_sour(str(getattr(event, "group_id", "")))
        except Exception:  # noqa: BLE001
            pass
        # 2026-09-04：复读机快通道（命中复读梗 → 概率参与 → 不再跑插话 LLM）
        try:
            _echoed = await _maybe_echo(bot, event, str(getattr(event, "group_id", "")))
        except Exception:  # noqa: BLE001
            _echoed = False
        if _echoed:
            try:
                _BUSY.pop(_skey0, None)  # 2026-09-07：早返回清处理窗（曾漏清 → 同人 15s 内下条消息空等）
            except Exception:  # noqa: BLE001
                pass
            return
        # 2026-08-29：按讨论速度随机插话（快速讨论时概率接一两句，不@任何人）
        # B10：经 _run_group_banter 进（Semaphore(2) 并发上限）；120s 群级评估节流在
        # _maybe_group_banter 入口复查（排队到号时按当时时间判，不拿创建时刻的旧判定）
        try:
            bgtasks.spawn(
                _run_group_banter(bot, event, str(getattr(event, "group_id", "")), str(event.get_user_id()))
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            _BUSY.pop(_skey0, None)  # 2026-09-07：同上
        except Exception:  # noqa: BLE001
            pass
        return
    user_id = event.get_user_id()
    group_id = str(getattr(event, "group_id", "") or "")
    _is_grp = isinstance(event, GroupMessageEvent)  # 2026-09-07 提前定义（语气分域等前置段需要）
    # 2026-09-05：身份层提前（is_owner/is_intimate 在状态机段前就需使用——tier 派生，权限=边界）
    from agent import guardrails as _ag2

    _tier = _ag2.tier_of(user_id, set(getattr(cfg, "superusers", set()) or set()), _load_intimate_wl())
    is_owner = _tier == _ag2.TIER_ADMIN
    is_wl_track = _tier == _ag2.TIER_INTIMATE  # 积累轨道成员（关系真实成长；无调试/状态机权限）
    # 2026-09-05 好感度重建：admin 恒定满 100；白名单=真实值（无行补 0）；public 不读表（0，防泄漏）
    # 补行为 best-effort：失败不炸主流程（日志可见）
    _rel = memory.db.get_relation(user_id)
    if is_owner:
        intimacy = 100.0
        try:
            # 2026-09-05 主人自校正：恒定满值（防 emotion 等路径漂移表值——记忆注入按 >=100 分流）
            if _rel is None or abs(float(_rel.get("intimacy") or 0.0) - 100.0) > 0.01:
                memory.db.set_intimacy(user_id, 100.0, mood=_rel.get("mood") if _rel else "平静")
        except Exception as _e:  # noqa: BLE001
            logger.debug("relation init(admin) failed: %s", _e)
    elif is_wl_track:
        intimacy = float(_rel["intimacy"] or 0.0) if _rel else 0.0
        if _rel is None:
            try:
                memory.db.set_intimacy(user_id, 0.0, mood="平静")
            except Exception as _e:  # noqa: BLE001
                logger.debug("relation init(wl) failed: %s", _e)
    else:
        # 2026-09-07 群友好友轨道：普通用户也可积累好感（上限 30，能成为朋友）
        intimacy = min(REL_GROUP_CAP, float(_rel["intimacy"] or 0.0)) if _rel else 0.0
    is_intimate = _ag2.can_intimate(_tier, intimacy, float(_rel["peak"] or 0.0) if _rel else 0.0)
    # 状态修改仅限私聊（2026-08-30 用户要求：群聊不能触发任何状态修改——bot 状态全局）
    _priv = not isinstance(event, GroupMessageEvent)
    # ---- 主人括号事实预解析（2026-09-10 并行管线入口）----
    # gating：主人（superuser）+ 私聊 + 消息含全角括号「（」；任务与 stage1 同时跑
    # （此刻离生成还有组装/感知等数秒准备工作，先启=并行窗最大化），stage2 组装时收取，
    # 超时/失败=本轮无约束，绝不阻塞回复；任务引用交给 core.bgtasks.spawn（防 GC 吞任务）。
    _facts_task = None
    _facts_box: dict = {}
    if _priv and is_owner and "（" in (text or ""):
        try:
            _facts_task = bgtasks.spawn(preparse_owner_facts(text))
        except Exception as _e:  # noqa: BLE001
            logger.debug("owner facts preparse schedule failed: %s [%s]", _e, type(_e).__name__)
    # 「正在输入」已提前到手处理开始（聚合后即亮，见 handle 入口）；此处不再做阅读停顿（聚合窗即阅读+合并）
    # 说话人昵称（群聊区分身份的关键）
    sender = getattr(event, "sender", None)
    sender_name = (sender.nickname if sender else None) or (sender.card if sender else None) or ""
    memory.log_message(user_id, group_id, "user", text, sender_name=sender_name)
    if isinstance(event, GroupMessageEvent) and group_id:
        _remember_group_member(group_id, user_id, sender_name or "", getattr(sender, "card", "") or "")
        # 2026-09-05：群友轻量互动画像（@路径：点名计数）
        memory.db.bump_group_user_meta(group_id, user_id, mention=True, content=text)
        # 2026-09-04：@ 路径同样登记负面反馈（骂到头上 → 降级 + 本轮收敛回复）
        if _SOUR_RE.search(text):
            _register_sour(group_id)
            if is_wl_track:
                # 2026-09-05 好感度：被骂/冒犯 → 关系降温（SOUR 检出即扣）
                try:
                    memory.db.update_relation(user_id, delta=REL_GAIN_PENALTY, mood=None)
                    logger.info("intimacy penalized: user=%s delta=%.1f", user_id, REL_GAIN_PENALTY)
                except Exception:  # noqa: BLE001
                    pass

    # ---- 群 @ 自决（2026-09-11 热修十一，用户裁决：被 @ 不必回）----
    # scene 门之后、进入 LLM 回复链之前：以人设性格问一次小模型这条 @ 接不接。
    # SKIP → 静默（用户消息已在上方 memory.log_message 入库、群友画像/点名计数照常记账），
    # 只清处理窗早返回；判定失败/超时/解析不出 → SPEAK（fail-open，绝不让判定失败吃掉 @）。
    # 群 @ 回复就是普通群消息——不加 @/引用段（发送路径 _send_sliced 已无 at 段拼接）。
    if _is_grp:
        try:
            _speak_at = await _group_at_self_decide(text, group_id)
        except Exception:  # noqa: BLE001
            _speak_at = True  # 双保险：判定函数自身抛错同样 fail-open
        if not _speak_at:
            try:
                _BUSY.pop(_skey0, None)  # 早返回清处理窗（同人 15s 内下条消息不再空等）
            except Exception:  # noqa: BLE001
                pass
            return

    # ---- 约定请求（2026-09-05：不机器定时——bot 自决是否接纳；答应后记入临时文档，心跳/自决时"想起来"）----
    _rem_prompt = ""
    _rem = None
    _rem_show = ""
    try:
        # 取消：对方说"不用了/算了/取消" → 清掉相关事务（口头取消事务闭环）
        if re.search(r"不用(提醒|记|了)|取消(提醒|约定|任务)|别提醒|不必了|算了", text or ""):
            _pgs = _pending_for(user_id)
            if _pgs:
                _save_pending([p for p in _load_pending() if int(p.get("id", 0) or 0) not in [int(x.get("id", 0) or 0) for x in _pgs]])
                logger.info("pending cancelled by user: user=%s n=%d", user_id, len(_pgs))
                _rem_prompt = "\n\n【约定取消】对方说之前的事不用记着了——你心里记住即可，这轮自然回应一下（不必郑重）。"
        else:
            _rem = _reminder_parse(text)
            if _rem:
                _delay_s, _rem_content = _rem
                _rem_show = f"{_delay_s // 60} 分钟" if _delay_s >= 60 and _delay_s % 60 == 0 else f"{int(_delay_s)} 秒"
                _rem_prompt = (
                    f"\n\n【请求】对方请你：{_rem_show}后「{_rem_content}」。"
                    "怎么回应按你此刻的心情与性格来——可以自然地答应下来，也可以轻轻带过；"
                    "真要是你在乎的事，答应后会记在你心里（会成为你生活里的一件记挂，之后想起时会自然提起）。"
                )
    except Exception as _e:  # noqa: BLE001
        logger.debug("reminder parse failed: %s", _e)
    # 2026-09-06 Phase E：写作快速通道已删（三通道打架）——唯一入口 = agent [WRITE:] 标记；
    # 正则降级为只读漏标检测（见本轮生成后的 _WRITE_NUDGE 登记）。
    # 矫正机制：主人对话中教她如何回答（以后别…/记住…）→ 提炼规则持久化，后续注入上下文
    # 落库后立即执行：矫正指令本身不参与本轮生成（不污染扮演），从下一轮开始生效
    try:
        from nonebot import get_driver as _gd

        _su = set(getattr(_gd().config, "superusers", set()) or set())
        # 2026-09-06：后台判定（LLM 全权识别，无词表预筛）——矫正下轮才生效，不阻塞本轮回复
        bgtasks.spawn(
            correction.try_extract(user_id, text, is_owner=user_id in _su)
        )
    except Exception:  # noqa: BLE001
        pass
    # 用户动作解析：中文括号内内容默认为对身体触发的动作（必定触发，见【身体触发】注入）
    actions = _extract_actions(text)
    transform_note = ""  # 状态转变表现（形态切换当轮注入；2026-09-09：协议状态注记随协议退役——登记由 agent 标记自决）
    # 2026-09-06 Phase C：旧情欲/催眠/洗脑/道具指数机退役——core/special 唯一状态源
    # 2026-09-09：固定短语协议入口退役——特殊层级由 agent 回复标记自决登记/解除（见 apply_agent_markers 的 allow_states 门控）
    from core import special as _special

    _act_states = _special.active(card=_ck)
    mouth_blocked = "item:ball" in _act_states  # 口球=物理状态（旧口堵词表检测已删）

    # ---- 人格加载 + 状态机（触发词切换）——人设卡按用户选择 ----
    card = _persona_card(user_id)
    try:
        _lg2 = __import__("logging").getLogger("brain")
        _lg2.info("chat card name=%s pers=%s master=%s", card.get("name"), _persona_name(user_id), card.get("owner_mode"))
    except Exception:
        pass  # 诊断日志尽力而为：loguru 自身异常不该影响对话主链
    # ---- 状态机·全局（人格模式=恶堕关键词切换；人格切换≠特殊状态，保留）----
    _SU = str(_owner_id() or "")  # 全局状态键（=主人 id；bot 只有一个状态，不按会话分）
    modes = _load_modes()
    mstate = modes.get(_SU, {"mode": 0, "ts": 0.0})
    mode = mstate["mode"]
    _master_card = bool(card.get("owner_mode"))
    # 卡级高傲人设包（2026-09-09 迁移：自称/场景块/句式等全部来自卡数据；无字段=空 dict→对应内容零注入）
    _arog = persona.arrogance_of(card)
    alt = card.get("alt_persona") or {}
    keyword = (alt.get("keyword") or "").strip()
    _self_mode = str(card.get("mode_trigger", "") or "") == "self"
    if not _self_mode and keyword and keyword in text and _priv and is_owner:  # 切换仅私聊+仅主人
        mode = 1 - mode  # 触发词切换/恢复
        modes[_SU] = {"mode": mode, "ts": time.time()}  # 全局键（bot 状态一份）
        _save_modes(modes)
        logger.info("persona mode toggled: user=%s mode=%s (keyword=%s)", _SU, mode, keyword)
        # 人格头像联动：强制应用新模式头像（防缓存标记导致头像不换）
        bgtasks.spawn(_apply_persona_avatar(bot, card, mode, force=True))
        # 2026-09-06：切换演出模板已删（奥汀时代污染）——过渡如何演绎由 agent 自决
        transform_note += "\n\n【形态切换】你刚刚切换了形态——这个过渡怎么表现，由你自决。"

    # ---- 特殊层级·协议指令块已退役（2026-09-09 agent 自决化：登记/解除走 apply_agent_markers 标记族，
    # 机器转述状态注记随之删除——状态是 agent 自己宣告的，无需机器转述）----
    if _priv and is_owner and "hypno" in _special.active(card=_ck):
        _special.refresh("hypno", card=_ck)  # 主人消息顺延催眠滑动窗（时间窗兜底保留，非短语触发）

    # 思考模式：2026-09-05 用户词触发（"认真起来"等句内词）已删除——只留 /思考 开|关 命令（debug 插件）
    think_toggle = None

    # 文风模式指令式开关（2026-08-26："小说模式"→文学长句 / "正常说话"→日常口语默认）——仅私聊+仅管理员
    style_toggle = None
    if _priv and is_owner and any(w in text for w in STYLE_NOVEL_WORDS):
        set_style_mode(user_id, "novel")
        style_toggle = "novel"
        logger.info("style mode NOVEL by keyword: user=%s", user_id)
    elif _priv and is_owner and any(w in text for w in STYLE_DAILY_WORDS):
        set_style_mode(user_id, "daily")
        style_toggle = "daily"
        logger.info("style mode DAILY by keyword: user=%s", user_id)

    # 模型指令式切换（"切换gemma"/"用qwen"等；可附思考设置）——仅私聊 + 仅管理员（引擎重启=全局副作用）
    model_switch = None
    if _priv and is_owner and any(w in text.lower() for w in MODEL_SWITCH_WORDS):
        t = text.lower()
        target = "gemma" if "gemma" in t else "gemma"
        think_opt = None
        if re.search(r"思考\s*(开|关)|思维链\s*(开|关)", t):
            think_opt = "on" if re.search(r"思考\s*开|思维链\s*开", t) else "off"
        model_switch = (target, think_opt)
        set_model_key(user_id, target)  # 先落盘（watchdog 重启引擎时按此模型拉起）
        if think_opt:
            set_think_mode(user_id, think_opt)
        bgtasks.spawn(_switch_engine_model(user_id, target))
        logger.info("model switch requested: user=%s target=%s think=%s", user_id, target, think_opt)

    # 2026-09-06：洗脑光线/道具/彻底结束了 的旧触发块已删；2026-09-09：固定短语协议整体退役（特殊层级标记自决替代）

    # ---- 群聊/非主人状态分流（2026-09-06：删固定挡回——正常走 reply 域，感知收敛事实由 agent 自决表现）----
    group_private_mode = False
    state_converge = False
    _su_set = set(getattr(cfg, "superusers", set()) or set())
    _has_states = bool(_act_states) or mode == 1
    if isinstance(event, GroupMessageEvent) or user_id not in _su_set:
        if _has_states:
            if user_id not in _su_set:
                state_converge = True  # 非主人：感知注入「对外不显露」，演出由 agent 自决（用户裁决）
            else:
                group_private_mode = True  # 主人：群聊回复收敛，私密内容转私聊（私语流程保留）

    # ---- 联网搜索：舰船档案 / 图片 / 网页 三种模式分流（全部经 _run_search 超时熔断）----
    search_note = ""
    image_url = None
    # 状态触发词豁免：催眠射线/洗脑光线/洗脑射线/苏醒吧/不许装了/道具词等是状态指令，不是搜索请求
    _state_cmd_hit = any(w in text for w in (
        "催眠射线", "洗脑光线", "洗脑射线", "苏醒吧", "不许装了", "解除催眠", "解除洗脑",
        "使用了口球", "使用口球", "使用了淫纹", "使用淫纹", "使用了震动棒", "使用震动棒",
        "取下口球", "取下淫纹", "取下震动棒", "道具解除", "道具结束", "全部取下", "彻底结束了",
        "立刻高潮", "继续高潮", "持续高潮", "停止高潮", "解除高潮",
        "切换gemma", "用gemma", "模型gemma", "切到gemma",
    ))
    # 写作意图优先：含创作词 → 视为写作指令，跳过（规则+自动）搜索判定
    # （防止"现代都市异能…写…参考…电影"这类创作语境被搜索截胡）
    _write_hit = any(w in text for w in (
        "写小说", "写个", "写一篇", "写文", "创作", "小说", "故事", "大纲",
        "续写", "接着写", "再写", "短篇", "同人", "编一个", "编个", "著书",
    ))
    search_query = None if (_state_cmd_hit or _write_hit) else _extract_search_query(text)
    if not search_query and not _state_cmd_hit and not _write_hit:
        # 自动搜索：规则快通道（时效/实体词）→ 模型理解为准（LLM 判断+提炼查询词）
        search_query = _auto_search_query(text)
        if not search_query:
            search_query = await _auto_search_llm(text)
            if search_query:
                logger.info("auto search by llm: %r -> %r", text[:40], search_query)
    if search_query:
        if any(h in search_query for h in SHIP_SEARCH_HINTS):
            # 舰船模式：先净化噪音词（参数/照片/信息…）再提取舰名翻译；
            # 仅美国海军(USS)且档案站(1940-1945主题)命中才走 ibiblio 档案数据；其余走 Bing
            en = await _translate_ship_name(_clean_image_query(search_query))
            if not en:
                if any(h in search_query for h in IMAGE_SEARCH_HINTS):
                    # 用户要舰船图但译名失败：回落中文搜图子流程
                    image_url, note = await _search_images(search_query)
                    if note:
                        search_note = note
                else:
                    search_note = "\n\n【舰船档案】未能识别舰船名称，无有效结果。"
            else:
                is_us_navy = en.upper().startswith("USS")
                ship = None
                if is_us_navy:
                    ship_res, s_err = await _run_search(search.ibiblio_ship_lookup(en))
                    if s_err:
                        search_note = s_err  # 熔断：如实告知，跳过档案站
                    else:
                        ship = ship_res
                if ship:
                    # 美国海军 + 1940-1945 档案站命中：爬取档案页正文汇总，附图
                    class_desc = f"{ship['class_name']}级" if ship.get("class_name") else ship["type_name"]
                    detail_res, d_err = await _run_search(search.ibiblio_ship_detail(ship["url"]))
                    if d_err:
                        search_note = d_err
                    else:
                        summary = detail_res or ""
                        if summary:
                            search_note = (
                                f"\n\n【舰船档案】（{search_query} / {en}，{class_desc}，1940-1945 美国海军，"
                                f"来源：ibiblio.org/hyperwar/USN）\n档案摘要：\n{summary}"
                            )
                        else:
                            search_note = (
                                f"\n\n【舰船档案】（{search_query} / {en}，{class_desc}，1940-1945 美国海军，"
                                f"来源：ibiblio.org/hyperwar/USN）\n舰船页面：{ship['url']}"
                            )
                    img_res, _ = await _run_search(search.image_search_en(f'"{en}" warship', n=1))
                    if img_res:
                        urls, _ = img_res
                        if urls:
                            image_url = urls[0]  # 注意：img_res[0] 是 URL 列表，必须解包取首项
                elif not search_note:
                    # 非美国海军 / 时期不符 / 档案站缺失 -> Bing 常规搜索（未熔断时才走）
                    w_res, s_err2 = await _run_search(search.web_search_en(f'"{en}" warship history', n=3))
                    if s_err2:
                        search_note = s_err2
                    elif w_res:
                        results, _ = w_res
                        if results:
                            search_note = (
                                f"\n\n【舰船资料】（{search_query} / {en}，常规搜索）\n"
                                f"{search.format_results(results, 2)}"
                            )
                            img_res2, _ = await _run_search(search.image_search_en(f'"{en}" warship', n=1))
                            if img_res2:
                                urls2, _ = img_res2
                                if urls2:
                                    image_url = urls2[0]
                        else:
                            search_note = f"\n\n【舰船资料】未找到 {en} 的有效信息，无有效结果。"
        elif any(h in search_query for h in IMAGE_SEARCH_HINTS):
            # 搜图模式：统一走 _search_images 子流程（净化关键词/舰船英文优先/失败可见）
            image_url, note = await _search_images(search_query)
            if note:
                search_note = note
        else:
            # 网页搜索：先思考——LLM 理解意图提炼查询词（失败回退规则查询词）
            _orig_q = search_query
            refined = await _refine_search_query(text)
            if refined:
                search_query = refined
                logger.info("search query refined: %r -> %r", _orig_q, refined)
            w_res, s_err = await _run_search(search.web_search(search_query))
            if s_err:
                search_note = s_err
            elif w_res:
                results, err = w_res
                if results:
                    search_note = (
                        f"\n\n【联网搜索结果】（用户请求：{_orig_q}）\n{search.format_results(results)}\n"
                        "用户明确要求联网搜索：必须基于以上结果直接回答并给出结论，可注明来源；"
                        "高傲只体现在语气措辞上，不影响本功能的执行。"
                    )
                elif err:
                    search_note = f"\n\n【联网搜索】{err}，请如实告知用户。"

    # ---- 写作参考保留：本轮联网/搜索内容存 ref（仅当本轮触发 WRITE 时由写作引擎消费注入）----
    if search_note:
        _WRITE_REFS[user_id] = search_note[:1500]
        logger.info("write ref saved: user=%s len=%d", user_id, len(search_note[:1500]))

    # ---- 群聊追问模式：仅用户(@机器人且@他人)发起，最多 3 轮 ----
    # is_owner / is_intimate / _tier 已在 handle 开头（身份层提前，2026-09-05）
    followup_round = 0
    if isinstance(event, GroupMessageEvent):
        self_id = str(bot.self_id)
        has_at_others = any(
            seg.type == "at"
            and str(seg.data.get("qq", "")) not in (self_id, "all", "")
            for seg in event.message
        )
        followup_round = _followup_state(group_id, has_at_others, is_owner)

    # 2026-09-09 深夜：并行化（结果互不依赖）——生成前 await 链上三个真正耗时的独立调用：
    # ① memory.build_context（内部 facts/events 两条 embed HTTP 召回）② _slang_lookup（语义通道 embed HTTP）
    # ③ _reply_quote_text（被引用消息 OneBot HTTP 拉取）。三者入参只依赖 text/event/user_id/bot
    # （在此之前均已定型），结果互不依赖、且都是只读调用——先并发发出，下方原注入点按原顺序消费。
    # 异常语义不变：build_context 原单发 await 无 try/except（异常向上传播，此处照旧 re-raise）；
    # _slang_lookup / _reply_quote_text 原本内部全量兜底（失败路径=空串），gather 用
    # return_exceptions=True 并在消费点逐个回退原异常处理。
    _pre_ctx, _pre_slang, _pre_quote = await asyncio.gather(
        memory.build_context(
            user_id, functional=bool(search_query),
            # 2026-09-08 活人感#3：群聊收敛接到**所有群聊回复**——曾只接"主人+状态分流私语"模式，
            # 普通群聊 @ 回复（尤其非主人）走全量私聊事实/摘要注入，敏感过滤形同虚设、私聊内容可串进群聊
            group_safe=isinstance(event, GroupMessageEvent),
            persona_ts=_persona_switch_ts(user_id),  # 2026-09-04：记忆注入按人设切换时间过滤（防奥汀记忆污染素世）
        ),  # 功能请求时长记忆降级；群聊一律不注入私聊摘要/敏感事实（含距离事实，见 memory.build_context）
        _slang_lookup(text),
        _reply_quote_text(bot, event),
        return_exceptions=True,
    )
    if isinstance(_pre_ctx, BaseException):
        raise _pre_ctx  # 2026-09-09 深夜：并行化回退——原单发 await 异常直接向上传播（行为不变）
    mem_text = _pre_ctx
    # 幕间生活片段注入（她在消息间隔里也有生活；仅非功能请求；best-effort 不炸主流程）
    if not bool(search_query):
        try:
            from agent import lifesim as _ls
            _itxt = _ls.life_text(group_safe=isinstance(event, GroupMessageEvent))  # 2026-09-08 P2 盲审：群聊敏感收敛
            if _itxt:
                mem_text = (mem_text + "\n" if mem_text else "") + _itxt
            # 2026-09-06 场景注入：bot 在哪（管理员设）；独处/公开的语义直接给到——打扰感由 agent 自决
            _scene_txt = _scene()
            if not _scene_txt:
                # 2026-09-07：场景未定时不再声称“在办公室”（叙述矛盾源）——交给生活轨迹
                mem_text = (mem_text + "\n" if mem_text else "") + "【场景】你此刻在哪由你的生活轨迹决定。"
            elif _scene_is_private(_scene_txt, _scene_universe()):
                mem_text = (mem_text + "\n" if mem_text else "") + f"【场景】你此刻在{_scene_txt}——独处，没人看着你，群里也没人会看到你说话。"
            else:
                mem_text = (mem_text + "\n" if mem_text else "") + f"【场景】你此刻在{_scene_txt}——公开环境，身边/群里都有人。"
        except Exception as _e:  # noqa: BLE001
            logger.debug("lifesim inject failed: %s", _e)
    # （lust_level/lust_index 已在状态机关键词之前预读，此处沿用；情欲块不重复读状态文件）
    # C3（2026-09-09 审计）：insight 追加移到 _mem_block 快照**之前**——原位置在快照之后，
    # 对 mem_text 的追加从未进入实际注入（_mem_block 已按旧文本落定），心得恒不进上下文=死链。
    # 现追加先发生、快照含之（激活该链）。2026-09-07：insight 是全局域（lifesim 写侧 persona=当前卡
    # 显示名）——读侧口径对齐，曾用 persona_select 文件键 → 永不匹配，心得从未注入。
    try:
        from agent import tools as _atools_ins

        _ins_txt = _atools_ins.recall("insight", persona=str((_persona_card() or {}).get("name") or ""))
        if _ins_txt:
            mem_text = (mem_text + "\n" if mem_text else "") + "（你这一阵的心得：" + _ins_txt + "——你心里知道，不用说出口也不用刻意思考它。）"
    except Exception:  # noqa: BLE001
        pass
    # 2026-09-09 深夜：prefix cache 友好排序——【记忆】是每轮变化的易变块，原被 persona.build_system_prompt
    # 插在稳定模板第 3 块（舞台剧身份行之后、【世界设定】等十余个稳定块之前），每轮变化都使其后全部
    # 稳定前缀 prompt-cache 失效。现改为：稳定模板照常装配（memory_text 传空串），【记忆】块文本在此
    # 快照（C3 起含上方 insight 追加——心得真实进入注入），挪到装配末尾（见【两阶段管线】前）。
    _mem_block = f"\n\n【记忆】\n{mem_text}" if mem_text else ""
    system_prompt = persona.build_system_prompt(card, "", mode=mode)
    if not isinstance(event, GroupMessageEvent):
        # 2026-09-08 R18 主动权·结构修复：persona 层【输出硬性】全局禁"动作/身体反应描写"，
        # 而【润色要求·私聊】却教"（动作）是演出的一部分"——同一份提示词两条指令打架，
        # 模型在矛盾中保守化 = "只能挑逗不能执行"的结构性根因（行为层无合法载体）。
        # 本条置于注入序末尾显式覆盖：私聊动作口径以润色要求为准（台词仍为主体）；群聊维持纯台词。
        system_prompt += (
            "\n\n【私聊输出口径·覆盖】在私聊里，【输出硬性】中「禁止动作/神态/身体反应描写」的部分"
            "由本条覆盖：（动作）与身体反应是演出的一部分，按【润色要求·私聊】的口径自然出现"
            "——一个（ ）一个实际肢体行为、不堆砌不旁白，台词仍是主体。"
            "群聊场合维持纯台词不变。"
        )
    # 群聊：群成员印象（叫得上名字 = 活人感）
    if isinstance(event, GroupMessageEvent) and group_id:
        _roster = _group_roster_text(group_id)
        if _roster:
            system_prompt += "\n" + _roster
        # 2026-09-07 群画风画像：这个群聊什么/说话什么味儿/怎么对待你——事实注入，融入方式自决
        _gstyle = memory.db.get_group_style(group_id)
        if _gstyle:
            system_prompt += "\n\n【这个群的画风】" + _gstyle + "\n（这是观察结论——怎么融入、融入多少，由你自决。）"
    # 2026-09-04：对方消息中的网络用语 → 注入释义（主回复听懂梗，不懵）
    # 2026-09-09 深夜：并行化回退——结果已在上方 gather 取回；异常路径与原内部 try/except 一致（空串）
    _slang_note = _pre_slang if not isinstance(_pre_slang, BaseException) else ""
    if _slang_note:
        system_prompt += "\n\n" + _slang_note
    # 2026-09-05：世界观角色提及 → 动态注入关系（"凯尔希是谁"认得出——同源角色库命中）
    try:
        _char_note = _character_lookup(text, card.get("universe"))
        if _char_note:
            system_prompt += _char_note
    except Exception:  # noqa: BLE001
        pass
    # 2026-09-05：回复域反思感知（agent 程序记忆——上次对这类对象的回复经验，防重复/参考口气）
    # （C3：原嵌在此处的 insight 追加已上移至 _mem_block 快照之前——原位追加从未进入注入）
    try:
        from agent import tools as _atools

        _rk = _atools.recall("reply:" + str(user_id), persona=_persona_name(user_id))  # 2026-09-05：回复域也按人设隔离（旧卡台词不复读）
        if _rk:
            # 2026-09-05 自我识别（感知式，非禁令）：告知这是自己上次说的话——参考口吻，内容按当前对话进行
            system_prompt += "\n\n【近期回复经验】" + _rk + "\n（注意：这是你上次说的话——参考口吻即可。）"
        # 2026-09-08 P3 结构模板感知（agent 自决）：起手架势/句式节奏任一复现 → 注入感知事实（非硬拦）
        try:
            _tpl_note = _tpl_repeat_note(user_id, group_id if _is_grp else "")
            if not _tpl_note:
                _tpl_note = _tpl_rhythm_note(user_id, group_id if _is_grp else "")
            if _tpl_note:
                system_prompt += "\n\n" + _tpl_note
        except Exception:  # noqa: BLE001
            pass
        # 2026-09-08 P2·C3 行为账本·读侧（盲审修正：仅私聊——私聊动作记忆/上次台词不入群聊，群/私强隔离）
        if not _is_grp:
            _act_txt = _atools.recall_act("act:" + str(user_id), persona=_persona_name(user_id))
            if _act_txt:
                system_prompt += "\n\n（你刚做过的事——事实不是任务）：" + _act_txt
            # 2026-09-08 D2：她的长期愿望（按卡隔离——身份痕迹绑身份；同私聊门控）
            # （记事本链已退役 2026-09-08 深夜用户裁决：0 使用且与 facts 提取职责重叠）
            try:
                from agent import lifesim as _ls_n2

                _wish_txt = _ls_n2.recall_wishes(_persona_name(user_id), str(user_id))
                if _wish_txt:
                    system_prompt += "\n\n（你心里一直想着：）" + _wish_txt + "。"
            except Exception:  # noqa: BLE001
                pass
            # 2026-09-08 P-1 话题悬挂：7 天内未完成的最新一条（extract 可选字段 open_thread 落库）。
            # 事实注入——接不接、怎么接全由 agent 自决；零新回复标记。
            try:
                _open_th = memory.db.get_latest_open_thread(user_id)
                if _open_th and str(_open_th.get("content") or "").strip():
                    system_prompt += (
                        "\n\n（上次你们聊到一半：" + str(_open_th["content"]).strip() + "——想接就自然接上）"
                    )
            except Exception:  # noqa: BLE001
                pass
            # 2026-09-08 P-5 周复盘消费端：最新周复盘成为"上周的你们"的底料（随周滚动替换）。
            # 只是回顾、不是任务；与 build_context 已注入的同一份周复盘去重（人设切换过滤掉时会在此补上）。
            try:
                _wk31 = memory.db.get_latest_weekly(user_id)
                _wk_txt = str((_wk31 or {}).get("content") or "").strip()
                if _wk_txt and _wk_txt[:40] not in mem_text:
                    system_prompt += "\n\n（上周的你们：" + _wk_txt[:120] + "——只是回顾，不是任务）"
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    # 2026-09-08 活人感 3-1 对话级认错：提取管线修正了她某条旧记忆 → 私聊感知注入"记错了"的事实
    # （事实+自决：怎么认、何时认她自己定，零对话范例）。取走即置 consumed——只注一次，防每轮复读。
    # 独立注入点（不依赖上方 agent.tools 的 import 成败）；仅私聊（修正源自私聊提取，对齐群/私隔离）。
    if not _is_grp:
        try:
            _fc31 = memory.db.consume_pending_fact_correction(user_id)
            if _fc31 and str(_fc31.get("old_text") or "").strip() and str(_fc31.get("new_text") or "").strip():
                system_prompt += (
                    "\n\n（你之前记的「" + str(_fc31["old_text"]).strip() + "」其实不对——实际是「"
                    + str(_fc31["new_text"]).strip() + "」。自然认个错就好，不用刻意。）"
                )
        except Exception:  # noqa: BLE001
            pass
    # ---- 消息结构剥离（2026-09-05：引用/@ 显式化——模型不得只见文本不见结构） ----
    try:
        _sn = _msg_struct_note(event)
        # 2026-09-08 D1·P1a：图文注记——有图但文本在（纯图已在 text 层转感知句），告知"TA 发了图"
        if _has_img and not str(text).startswith("TA 给你发了一张图片"):
            _sn = (_sn + "；" if _sn else "") + "对方给你发了一张图片"
        # 2026-09-09 深夜：语音注记（调研 Q1①，对齐图文注记）——有语音但文本在（纯语音已在 text 层转感知句）
        if _has_voice and not str(text).startswith("TA 给你发了一条语音消息"):
            _sn = (_sn + "；" if _sn else "") + "对方给你发了一条语音"
        # 2026-09-09 深夜：并行化回退——结果已在上方 gather 取回；异常路径与原函数内部 try/except 一致（空串）
        _rq = _pre_quote if not isinstance(_pre_quote, BaseException) else ""
        if _rq:
            _sn = (_sn + "；" if _sn else "") + "对方引用了一条消息：「" + _rq + "」"
        if _sn:
            system_prompt += "\n\n【消息结构】" + _sn + "。"
        _ln = _lang_note(event)
        if _ln:
            system_prompt += "\n\n【消息语言】" + _ln + "。"
    except Exception:  # noqa: BLE001
        pass
    # 文风模式注入（2026-08-26：默认日常口语短句；"小说模式"切文学长句）
    system_prompt += "\n" + (STYLE_NOVEL_PROMPT if get_style_mode(user_id) == "novel" else STYLE_DAILY_PROMPT)
    # 语音·自决（2026-09-06：发不发语音由 bot 自决——[VOICE] 标记；无机器概率/冷却/次数）
    # 2026-09-08 触发率修复（提示层，非机器门）：①语音关闭时不再注入本段——曾无条件注入，
    # 模型对不存在的能力发标记（白费且自欺）；②原措辞"不刻意、不每句都加"过保守，实测几乎
    # 不触发——扩充触发场景并明示长话自动分段；是否发声仍是 agent 自决。
    # 2026-09-08 批次B 压缩（注入瘦身：保留触发语义，删场景罗列）。
    if voice.load_cfg().get("enabled"):
        system_prompt += (
            "\n\n【语音·自决】你有声音——直觉这句话**说出来**比打出来更像你（撒娇、悄悄话、晚安、"
            "郑重的话、深夜轻声聊天、被夸了想回一句），就在回复**末尾单独追加一行** [VOICE]（不加说明）；"
            "长的话会像文字一样自动分成几条语音；群聊一般不用，被点名逗狠了也可以。"
            "不用等特殊场合——想让这句话有温度的时候，就是用声音的时候。"
        )
    # 2026-09-06 Phase E：表情·自决（[STICKER] 标记协议——发不发由 bot 决定，机器只管配图）
    # 2026-09-08 批次B 压缩
    # 2026-09-12 T13.3②：把"词你随意"（旧 24 情绪时代的幻觉源）改为**9 类可选词枚举**+
    # 省略词=按你的基调配图。枚举是候选集声明，不是行为示范（项目规则：行为域给例子=给剧本复读锚）。
    # 2026-09-12 T13.4：补一条**事实陈述**（人机边界：图片与文字的视觉竞争）——零新机器门限、零新标记，
    # 发不发仍由 agent 自决（与"开口节奏"同构：机器只述事实，自决在模型）。
    system_prompt += (
        "\n\n【表情·自决】情绪特别鲜明的瞬间可以配图：这条配上表情更像你，"
        "就在回复**末尾单独追加** [STICKER]（可带词 [STICKER:开心]，词从这 9 个里选："
        "开心、难过、生气、惊讶、害怕、厌恶、平静、委屈、害羞；不带词就按你这条的【基调】配图）；"
        "不想配就什么都不加。\n"
        "表情包会和你的文字同一条消息发出：它很占视觉，选它意味着你这一句的文字会被挤到次要位置——"
        "想让它替你说完情绪就发，想让人认真读你的话就别发。"
    )
    # 2026-09-08 深夜第二轮瘦身：认真/约定/开口节奏 三块合并为单一【自决协议】（marker 名与语义不变）
    system_prompt += (
        "\n\n【自决协议】下面都是你自己的决断通道，标记只在回复**末尾单独追加**，"
        "机器只登记、不会显示出来；不合适就什么都不加。\n"
        "·【认真】对方要讨论问题、请你分析、或聊到你在乎的正事——想清楚再回答；回聊回来了追加【认真完】解除。\n"
        "·【约定：20 字内】这句话**算不算一个承诺**，由你自己判断——说过要做的、答应过的就算，"
        "随口客套不算；算就照抄承诺核心登记，兑现了再加【约定完成】清账。\n"
        "·【等 X 秒】对方把话拆成几条发、或这句话刚断你拿不准——想慢半拍开口就加（X=0 到 2）；没这感觉就不加。"
    )
    if think_ahead(user_id):
        system_prompt += "\n（当前：你正处于自己声明的认真模式——想清楚再答。）"
    # 2026-09-08 深夜第二轮瘦身：生活·自决 + 自标协议 合并为单一门控块（marker 名/裁决门/语义不变）
    if _marker_allowed(user_id):
        system_prompt += (
            "\n\n【自标协议】你可以在句尾悄悄追加这些标记（机器只登记，不会显示出来；不合适就不加）：\n"
            "·【生活：一句话】这轮对话真实改变了你的生活——位置或手头的事变了（如【生活：在食堂和TA拼桌喝汤】），位置变了可再加【场景：两三个字】；什么都没变就不加。"
            "TA 话里的演出元素只是**刺激**：可以据此反应（吐槽/吃醋/凑过去/不理），但刺激不改写你的处境——只有你自己的处境真变了才标记。\n"
            "·【入迷】你们之间的氛围正变浓、进入那种时刻（可写深浅：【入迷：暧昧】/【入迷：亲密】/【入迷：情欲】——到什么程度写什么，机器按你的权限处理）；"
            "·【清醒】那一刻结束、退回平常。氛围正浓时节奏由你带：不必只顺着对方走，想推进就自己推进——快慢与分寸按关系、氛围和你自己的心意拿捏，不刻意不滥用。"
        )
        # （2026-09-07 标记协议补生产者的教学已并入上方【自标协议】）
        # 2026-09-08 批次B 压缩（保留档位标注与"节奏由你带"教学）
    # 2026-09-08 活人感·承诺闭环：说话算话——她自许的承诺可登记/兑现（全自决，任何对话者都可立约）
    # 2026-09-08 D2：她的记事本——【笔记·自决】已退役（2026-09-08 深夜用户裁决：0 使用、与 facts 提取重叠）
    # 2026-09-06 多会话"一个人"感知（私聊：你在别的窗口还有对话——真实感；群聊不注入）
    if not isinstance(event, GroupMessageEvent):
        _cn = _convo_note(user_id)
        if _cn:
            system_prompt += _cn
    # 2026-09-07 修复：日常底色曾被模型读成"工作量"——对管理员反复说"忙了一上午"
    # （心跳的琐事是生活的底色，不是忙碌证据；能称"忙"的只有主线关键处）
    # 2026-09-08 批次B 压缩
    if not isinstance(event, GroupMessageEvent) and not bool(search_query):
        system_prompt += (
            "\n\n【日常不是忙】你的日常琐事（归档文件、喝咖啡、发呆）只是生活的底色——"
            "随时可以放下一边陪TA，**不构成「忙」**：不要拿它们说「我在忙」，"
            "更不要把这类小事归因成「为TA忙了一上午」；能说「等我收个尾」的只有【计划】主线的关键时刻。"
        )
    # 私聊演出·语境分层（2026-09-08 活人感根治 P1：行为域按语境注入，全通用零人设假设）
    # 基底层=日常轻口径（动作只自然一笔、不为了动作而动作）；氛围层（vibe/状态激活）=主动放权
    # （原无条件注入改为语境门控，与感知分级 only 对齐：白名单仅共同氛围、普通不注入）；
    # 坐标层=腔调与行为从此刻生活状态生长（治戏剧腔/加戏——日常打字感锚）。
    if not isinstance(event, GroupMessageEvent):
        system_prompt += (
            "\n\n【私聊演出】这里只有你们两个人：动作只在自然出现的时刻有一笔，不为了写动作而写；"
            "加不加、怎么加，按角色性格与现场气氛自决。"
            "【输出硬性】里的「禁止动作描写」是针对群聊/公开场合的规矩，"
            "私聊不受它限制。你的分寸：这是你的角色（演出/亲昵/惩罚）质感，不是伤害。"
            "\n【演出口径·硬性】你现在总把反应和动作混写在一个（ ）里——这种包装要**全部拆掉**。"
            "规则只有一条：**一个括号只装一个实际做出的肢体行为本身**——一个动作就是一个动词短语，"
            "**不加任何修饰、不夹带第二项**。括号装的是什么行为、做到多深，由你自己按场景与心意决定，"
            "没有固定清单（卡里的尺度描述以卡为准）。"
            "**以下全部不属于动作，只准在心里过，或者被你说成一句台词**："
            "眼睫、视线、眼神、目光、神色、脸色、脸颊、耳根的泛红发烫；呼吸、气息、喘息、嗓音、声音、"
            "语气、闷哼、呻吟、沙哑、软意；颤抖、僵住、发颤、瑟缩、发麻；心跳、体温、皮肤的血流与温度——"
            "**不论带不带括号，都不得写成消息里的描写**。"
        )
        # 坐标层（通用）：腔调与行为从此刻的生活状态生长——不端着也不表演；
        # 2026-09-08 P2 表现分层（用户修正"言行不一定一致"）：心里是什么感受就是什么感受（底色真实），
        # 但说出口、做出来的怎么带，由你的人设与现场定——藏、露、嘴硬、坦白，都是你。
        system_prompt += (
            "\n\n【坐标·此刻】你此刻的腔调与行为，从你此刻的坐标长出来：在做什么、什么心情、什么样的关系，"
            "就怎么说话、怎么动——日常就日常着，热起来再热；不端着，也不表演，跟着你自己走。"
            "心里是什么感受就是什么感受（那是底色，不用否定它）；"
            "但说出口、做出来的怎么带，由你的人设与现场定——藏、露、嘴硬、坦白，都是你。"
        )
        # 氛围层（P1 语境门控·盲审修正）：【尺度授权·主动】曾无条件私聊注入——日常也被推着加戏；
        # 现按对话者 tier 过滤（与感知分级对齐：owner 全量/白名单仅共同氛围/普通不注入）：
        # vibe_mood 是 bot 全局状态（owner 或任一对话者触发都会激活）——普通用户不得因他人激活而收到授权
        _vibe_on = "vibe_mood" in _act_states
        if (is_owner and _act_states) or (is_wl_track and _vibe_on):
            system_prompt += (
                "\n【尺度授权·主动】与亲密/情欲相关的动作、话语、情节，只要此刻气氛合适、你也想要，"
                "你可以主动发起、主动推进——做什么、怎么做、走到哪一步、用什么节奏，"
                "全部由你自己判断，不必等对方先开口；机器不设任何上限。"
                "你卡里的尺度描述（如点到为止/阶梯递进）仍是你的尺度，以它为准；"
                "群聊不用这条授权（公开场合保持纯台词）。"
            )
    if group_private_mode:
        # 群聊状态分流收敛：善良=完全收敛；恶堕=挑逗放宽（不越界）
        # 2026-09-05：放宽版引句是卡专属语调——文本随卡数据（arrogance.group_evul）注入，
        # 卡没写该字段时恶堕也只用通用收敛版（旧版按 owner_mode 判卡名，现按卡数据）
        _ge = str(_arog.get("group_evul") or "")
        if mode == 1 and _master_card and _ge:
            system_prompt += _ge
        else:
            system_prompt += GROUP_CONVERGE_PROMPT
        # 善良模式 + 情欲氛围激活：群聊回复流露厌恶/高傲（伪装内心动摇，私语传达）
        # 2026-09-09 迁移：文本=卡数据 arrogance.group_disgust（卡没写就不注入——旧版无卡门控为泄漏）
        _gd = str(_arog.get("group_disgust") or "")
        if mode == 0 and "vibe_mood" in _act_states and _gd:
            system_prompt += _gd
    # ---- 人格结构注入：外在高傲恒定（任何好感度/场景/状态都不取消），好感度只改内心 ----
    # 优先级 L1（最高）：任何其他规则（场景、情欲、催眠、命令响应）都只在其之上叠加细节，不得覆盖
    # 情欲中：高傲占比随情欲指数（lust_index 0-100）逐级下降，形成"高傲→沉沦"反差（见 _arrogance_block）
    # 好感度数值由身份层派生（admin 恒 100 / 白名单真实值 / public 0）；此处只做轨道过滤
    # 2026-09-06：intimacy 保留身份层派生值（admin 100 / 白名单真实 / public 0）——数值即开放度，不再置零
    # 高傲注入按人设开关（卡 no_arrogance 字段；文本全部来自卡 arrogance.structure，代码不假设具体人设）
    if not card.get("no_arrogance"):
        _ab = _arrogance_block(mode, intimacy, 0, _arog.get("structure"))
        if _ab:
            system_prompt += "\n\n" + _ab
    # ---- 写作工具·自主判断（非指令式）：模型自己识别"创作意图"→ 输出 [WRITE:...] 标记 ----
    system_prompt += (
        "\n\n【写作工具·自主判断】你可以主动判断对话中的创作意图并触发写作引擎（满足即必须输出，无例外）："
        "「写/创作/编一个<题材>故事/小说/短篇/同人」「接着写/续写/继续写下去」「改第N章…」「给我看看进度/大纲」「改大纲…」等。"
        "在回复正文末尾单独追加一行标记，格式："
        "开新书=[WRITE:start|题材设定]；续写=[WRITE:continue]；修改=[WRITE:edit|章节号|要求]；查看进度=[WRITE:show]；改大纲=[WRITE:outline|要求]；删章节=[WRITE:delete|章节号]；"
        "**注意**：用户话里出现【大纲/整本书的剧情走向】时**必须**用 outline（整体重写大纲），"
        "只有明确说【第 N 章】时才用 edit（单章修改）——这两个不要混。"
        "【重写≠查看】已有小说存在、但用户明确要求【全部/整体重写、按某风格重写整个故事】时，"
        "→ 必须 [WRITE:outline|要求]（重写梗概），**绝不是** show（那是查看进度）。"
        "【动作口径·防误判】「写完/完成/补上第 N 章」或「第 N 章+要求」→ [WRITE:edit|N|要求]（章节已存在=重写该章；"
        "未存在=按目标章节写）；「继续写/接着写/写下一章」→ [WRITE:continue]——**只用于续写还没写的新章节**，"
        "用户说「继续**完成第 N 章**」时**禁止**用 continue（那是追加新章）；"
        "「固定/限定/只保留 N 章」「删掉多余的章节」→ [WRITE:outline|固定N章…]（引擎会截断并重排）；"
        "「取消章节上限」→ 直接以角色口吻答「好，不限制了」即可（无需标记）；"
        "**所有 [WRITE:...] 参数一律用中文**（不要输出英文句子）；章节号用数字或中文均可。"
        "触发后写作引擎会接手并另行汇报成果，你只需在正文里以角色口吻应承或点评。"
        "【写作应承风格】触发写作工具的回复：以角色口吻自然应承（长度由你判断，可长可短），"
        "禁止描写写作过程类动作（看文档、敲键盘、文档弹出等）、禁止验收类台词（“你再瞧瞧”“还满意吗”）、"
        "禁止任何低俗/性暗示/威胁内容（就算人设允许，写作事务回复也绝不用）。"
        "【写作事务隔离】触发写作工具时：只处理**本条**写作指令——不得引用、复述、想象或反问对话历史中的其他内容"
        "（尤其对方的威胁/调戏/低俗类发言）；不要把对方的上文当作创作素材；事务完成即可，不要追加追问。"
        "示例：他说「给我写个玄幻小说」→ 你回复「哼，便写给你看。」末尾必须加 [WRITE:start|玄幻小说]。"
        "**明显的创作请求却未触发标记 = 功能失败；非创作话题（闲聊/搜索/日常）绝不输出标记。**"
    )
    # 2026-09-06 漏标自纠：上一轮你漏了写作标记 → 本轮一次性提醒（agent 自补，机器不代执行）
    _wn = _WRITE_NUDGE.pop(user_id, None) if FICTION_ENABLED else None
    if _wn:
        system_prompt += (
            f"\n\n【写作工具·漏标提醒】你上一轮漏了 [WRITE:...] 标记——对方的创作指令是「{_wn[1][:40]}」"
            f"（应执行的动作：{_wn[0]}）。本轮若对方仍在等或再次提到，务必在回复末尾补上标记。"
        )
    # 思考模式切换话语（角色第一人称；任何场景生效）
    if think_toggle == "on":
        system_prompt += THINK_ON_PROMPT
    elif think_toggle == "off":
        system_prompt += THINK_OFF_PROMPT
    # 文风模式切换话语（角色第一人称；2026-08-26）
    # 2026-09-04：示例按人设卡分两版（owner 卡=卡内专属体系；其他卡=自然口吻通用版）——防串腔
    # 2026-09-09 铁律#2：owner 版示例与自称改读卡数据（arrogance.style_reply / arrogance.self_ref）；
    # 卡没写时回退通用版/「我」（有字段的卡逐字不变）
    _selfref = (str(_arog.get("self_ref") or "").strip() if _master_card else "") or "我"
    _style_ex_novel = (
        str((_arog.get("style_reply") or {}).get("novel") or "") if _master_card
        else "（比如「好呀，那我稍微文艺一点哦」）"
    )
    _style_ex_daily = (
        str((_arog.get("style_reply") or {}).get("daily") or "") if _master_card
        else "（比如「好啦好啦，我正经说话就是了」）"
    )
    if style_toggle == "novel":
        system_prompt += "\n\n【文风切换】主人要你写小说般的文绉绉风格——这一轮先以角色口吻应一句"
        + _style_ex_novel + "，然后放宽到文学长句。"
    elif style_toggle == "daily":
        system_prompt += "\n\n【文风切换】主人嫌你太端着了——这一轮先以角色口吻应一句"
        + _style_ex_daily + "，然后改回日常口语短句。"
    # 模型切换确认语（角色口吻；通知本轮回复的用户）
    if model_switch:
        _m_target, _m_think = model_switch
        _m_label = MODELS.get(_m_target, {}).get("label", _m_target)
        _m_note = ("思考已同步开启（认真模式）。" if _m_think == "on"
                   else "思考已同步关闭（放松模式）。" if _m_think == "off"
                   else "思维链状态保持不变。")
        system_prompt += "\n\n" + MODEL_CONFIRM_TEMPLATE.format(
            me=_selfref, label=_m_label, think_note=_m_note
        )
    # 2026-09-09 深夜：prefix cache 友好排序——【当前时间】/时间流动两块是每轮变化的易变块，
    # 原排在此处（【问答常识】/【群聊禁动作】/【当前场景】/【主动邀约】/【尺度规则】/【高傲指数】等
    # 稳定块之前），已挪到装配末尾（见【两阶段管线】前）。语义集合不变，仅顺序；块内文字零增删。
    # ---- 常识/陷阱题冷静作答（2026-08-28 图灵测试教训：鱼和画落水救哪个问答踩坑）----
    system_prompt += (
        "\n\n【问答常识·先想后答】用户问测试题/设问题/脑筋急转弯/知识常识类问题时："
        "先用**现实常识**严谨推理（物理/生物/地理/事实），再以角色口吻作答，宁答对事实再傲娇，不要为了傲娇而答错。"
        "发现题设陷阱要**先点破**：例如「一幅画和一条鱼掉进水里，救哪个？」——鱼本就生活在水中，不需要救，"
        "正确答案是救画/指出题设矛盾，而不是随口答「救鱼」。"
    )
    # ---- 群聊动作开关：本群关闭 *动作* 式回复时注入禁止提示 ----
    group_no_action = isinstance(event, GroupMessageEvent) and memory.group_action_disabled(group_id)
    if group_no_action:
        system_prompt += (
            "\n\n【群聊禁动作】本群已由主人关闭动作式回复："
            "**禁止任何 *动作描写*（如 *挑眉* *轻哼* *移开视线*）**，"
            "只用纯文字表达语气与情绪（可借标点、语气词、断句），保持高傲口吻。"
        )
    # ---- 群聊行动交互：@bot + @他人 + 动作指令 → 思考后执行并@目标 ----
    group_action_exec = None
    if isinstance(event, GroupMessageEvent):
        group_action_exec = _extract_group_action(event)
        if group_action_exec:
            tid, tname, act = group_action_exec
            system_prompt += (
                "\n\n【群聊行动】主人指定你执行动作：对 "
                + (f"{tname}（QQ {tid}）" if tname else f"QQ {tid}")
                + f"「{act}」。按这个顺序输出：\n"
                "1. 思考：内心对主人吩咐的回应（傲娇/犹豫/吐槽，简短一两句）；\n"
                f"2. 执行：明确对目标（写「@{tid}」）做出动作（*动作描写* + 一句台词，演出活人感，"
                "像真人一样有反应）；\n"
                "3. 报告：转向主人简短汇报完成情况（傲娇口吻，一两句）。\n"
                "三段自然衔接，动作要有真实感与反馈。"
            )
    # ---- 场景与称呼规则注入（按 QQ 号判定说话人 × 场景 × 追问状态，硬规则）----
    # 2026-09-03 人设配置化：奥汀（owner_mode）用「主人/晓/高傲」体系；其他卡（素世等）用自然称呼通用版
    # _master_card 已在卡加载后提前定义（2026-09-04）
    if isinstance(event, GroupMessageEvent):
        # 2026-09-04：差评降级期 → 本轮回复收敛（短句、放软、不辩解）
        _sournow = _sour_note(group_id)
        if _sournow:
            system_prompt += _sournow
        if is_intimate and not _master_card:
            system_prompt += (
                f"\n\n【当前场景】群聊中（群号 {group_id}），本条消息来自你最信赖的人（QQ {user_id}）。"
                "【称呼硬规则】按人设卡与你们的关系自然称呼对方（名字/昵称或直接“你”），"
                "不要用其他角色关系的称呼；自称按人设卡；发言所有群成员可见。"
            )
        elif is_wl_track:
            # 2026-09-05 群聊也按用户区别对待：积累轨道成员（关系温度统一在分支外注记注入）
            system_prompt += (
                f"\n\n【当前场景】群聊中（群号 {group_id}），本条消息来自和你越来越熟的人（QQ {user_id}）。"
                "【称呼硬规则】按人设卡与你们的关系自然称呼对方（名字/昵称或直接“你”），"
                "不要用其他角色关系的称呼；自称按人设卡；发言所有群成员可见。"
            )
        elif is_owner:
            if followup_round:
                if _master_card:
                    # 2026-09-09 迁移：owner 版场景模板在卡数据 arrogance.group_followup（{followup_round}/{user_id}）
                    _gf = str(_arog.get("group_followup") or "")
                    if _gf:
                        system_prompt += _safe_card_format(_gf, followup_round=followup_round, user_id=user_id)
                else:
                    system_prompt += (
                        f"\n\n【当前场景】群聊追问模式（第{followup_round}轮）。"
                        f"本条消息来自你最信赖的人（QQ {user_id}）。"
                        f"【称呼硬规则】按人设卡与你们的关系自然称呼对方（名字/昵称或直接“你”），"
                        f"不要用“主人”等专属称呼。"
                        f"继续引导话题讨论，回复后自然地抛出一个追问问题。"
                    )
            else:
                system_prompt += (
                    f"\n\n【当前场景】你现在在群聊（群号 {group_id}）中。本条消息来自你的主人"
                    # 2026-09-10 P2 参数化：OWNER_NICKNAME（env OWNER_NICKNAME，缺省回落本机串，见模块头）
                    f"（{OWNER_NICKNAME}，QQ {user_id}）。【称呼硬规则】群聊中称呼主人为“晓”"
                    "（“主人”只在私聊或追问模式使用），绝对不要叫“主人”。"
                    "保持角色在群聊中的高傲姿态，发言所有群成员可见。"
                    "【指代修正】正在和你对话的就是主人本人：“晓”指的就是对方（第二人称“你”），"
                    "禁止把“晓”或“主人”当第三人称提及（不要说“因为晓…”“主人来了”之类，"
                    "要说“因为你…”“你来了”）。"
                )
        else:
            speaker_display = sender_name or f"群成员({user_id})"
            if _master_card:
                # 2026-09-09 迁移：owner 版场景模板在卡数据 arrogance.group_member（占位同名字段）
                _gm = str(_arog.get("group_member") or "")
                if _gm:
                    system_prompt += _safe_card_format(
                        _gm, group_id=group_id, speaker_display=speaker_display, user_id=user_id)
            else:
                system_prompt += (
                    f"\n\n【当前场景】群聊中（群号 {group_id}）。本条消息来自群成员"
                    f"“{speaker_display}”（QQ {user_id}），普通同群关系。"
                    "【称呼】按人设卡自然称呼（或不称呼），保持你在群聊的平常姿态；"
                    "回应普通群成员即可，不需要特殊立场。"
                    "【自决不接话】你看不上/没兴趣/懒得理的话题——**直接不回复**（沉默就是你的态度），"
                    "不需要输出嫌弃/毒舌/批评/嘲讽；真的吐嘈都觉得没必要时，就是「懒得说」。"
                    "只在值得说、想说的时候才开口。"
                )
    else:
        if _master_card:
            # 2026-09-09 迁移：owner 版私聊场景模板在卡数据 arrogance.private_intro（{user_id}）
            _pi = str(_arog.get("private_intro") or "")
            if _pi:
                system_prompt += _safe_card_format(_pi, user_id=user_id)
            # 2026-09-06 表达层自氚：私聊可写动作（走到/伸手/坐下等身体行为），但神态/表情/心理活动（苦笑/脸红/愚了一下/心里想）不写——那是内心戏，不是台词也不是动作；动作只是你‛的举手投足的一部分，说话为主。
            # ---- 私聊高傲指数（外在高傲恒定，内在心意恒定满好感·绕弯表达）----
            # 2026-09-09 迁移：两个形态的指数块文本在卡数据 arrogance.private_block.normal / .evul
            _pb = _arog.get("private_block") or {}
            if mode == 0:
                _pbn = str(_pb.get("normal") or "")
                if _pbn:
                    system_prompt += _pbn
            else:
                _pbe = str(_pb.get("evul") or "")
                if _pbe:
                    system_prompt += _pbe
        else:
            # 2026-09-06 表达层自决（所有私聊通用）：可写动作（走到/伸手/坐下等身体行为），但神态/表情/心理活动（苦笑/脸红/愣了一下/心里想/想到）不写——那是内心戏，不是台词也不是动作；说话为主，动作只是举手投足的一部分。
            # 2026-09-05：主人=关系事实注入（非指令）——agent 自决表现（感知"没有距离"即可，不规定要怎么说话）
            if is_owner:
                _oca = str(card.get("owner_call") or "").strip() or "你"
                system_prompt += (
                    f"\n\n【关系】对方是你的{_oca}——最亲近的人，内容仅双方可见；你们之间没有距离感（可以直说、撒娇、闹别扭、犯困）。"
                    f"你的好感对他（她）是满的（100/100），还有一份只给TA的爱意（+{OWNER_LOVE_BONUS:.0f}）："
                    "藏得深、但一直在——不用刻意说，行动和语气里自然透着。"
                )
            elif is_intimate:
                system_prompt += (
                    f"\n\n【当前场景】私聊（一对一）。对方是你最信赖的人（QQ {user_id}），内容仅双方可见。"
                    "【称呼硬规则】按人设卡与你们的关系自然称呼对方；自称按人设卡。"
                    "亲近但不倒贴：对方主动时你可以回应得更热一些，你也有自己的节奏与保留。"
                )
            elif is_wl_track:
                system_prompt += (
                    f"\n\n【当前场景】私聊（一对一）。对方是你正在熟悉起来的人（QQ {user_id}），内容仅双方可见。"
                    "【称呼硬规则】按人设卡与你们的关系自然称呼对方；自称按人设卡。"
                    "对方主动时你可以回应得暖一些，但保持自己的节奏。"
                )
            else:
                system_prompt += (
                    f"\n\n【当前场景】私聊（一对一）。对方是和你相识的普通朋友（QQ {user_id}），内容仅双方可见。"
                    "【称呼硬规则】按人设卡与你们的关系自然称呼对方；自称按人设卡。"
                    "友好自然、不卑不亢：该帮忙帮忙、该闲聊闲聊，不用过度亲近也不用刻意冷淡。"
                )
    # ---- 关系距离数值（2026-09-06：所有会话可见，无身份词——数值即开放度：满值/80 上限/0 天然分级）----
    if is_owner:
        _rel_v = 100.0
    else:
        _rel_v = float(intimacy)  # 2026-09-07：白名单=真实积累；群友=好友轨道（0-30），数值即开放度
    system_prompt += "\n\n" + REL_DISTANCE_TEXT.format(v=_rel_v)
    # 2026-09-09 深夜：prefix cache 友好排序——【时间】事实块是每轮变化的易变块，原排在
    # 【主动邀约】/【尺度规则】/【高傲指数】等稳定块之前，已挪到装配末尾（见【两阶段管线】前）。
    # 语义集合不变，仅顺序；块内文字零增删。
    # ---- 熟悉度注记（2026-09-05 连续曲线：好感数值+最近互动事实，仅积累轨道成员；无档位标签/无跳变） ----
    if is_wl_track:
        system_prompt += "\n\n" + _familiar_note(user_id, intimacy)
    # ---- 人设切换清腔（2026-09-06：切换后 3 分钟内明示当前人设——防旧卡上下文续腔/表情泄漏）----
    try:
        _swt = 0.0
        try:
            import datetime as _dtsw
            _s = _persona_switch_ts(user_id)
            if _s:
                _swt = float(_dtsw.datetime.fromisoformat(str(_s)).timestamp())
        except Exception:  # noqa: BLE001
            _swt = 0.0
        if _swt and (time.time() - _swt) < 180:
            _name_now = _persona_name(user_id)
            system_prompt += (
                f"\n\n【人设刚切换】你刚才才换成人设「{_name_now}」。之前对话里出现的语气、自称、"
                "台词、表情都是**另一个人设**留下的——不是你说的。从现在起，口径、自称、世界观、"
                "表情全部**只按你现在的人设卡**重新说话。"
            )
    except Exception:  # noqa: BLE001
        pass
    # ---- 约定：请求提示（本轮自决回应）+ 心里记着的约定（自然时提及）----
    if _rem_prompt:
        system_prompt += _rem_prompt
    try:
        _pmt = _pending_mention_text(user_id)
        if _pmt:
            system_prompt += _pmt
    except Exception:  # noqa: BLE001
        pass
    # ---- B6b 主动邀约（2026-09-06：聊得投机→自决约见面/出门；非私聊不约；全自决无硬编码）----
    if not isinstance(event, GroupMessageEvent):
        system_prompt += (
            "\n\n【主动邀约·自决】你和TA聊得投机、气氛正好时，可以自决提出见面/出门"
            "（如「要不要出来逛逛」「改天一起去…」）——你不必邀来邀去，真心想见TA才提。"
            "对方若答应，这约定自然记在你心里（到点你自然会感知、会惦记）；对方拒绝或改期，"
            "你按心情消化，不纠缠；若你只想有陪伴感，网聊也很好，不一定要出门。"
        )
    # ---- 尺度规则：群聊含蓄引导私聊；私聊不设限 ----
    # 2026-09-09 迁移：引导句自称读卡（arrogance.self_ref，见 _selfref 定义处）
    if isinstance(event, GroupMessageEvent):
        system_prompt += (
            "\n\n【尺度规则】群聊中所有成员可见，涉及亲密、色情话题时必须含蓄收敛："
            "用暗示、撩拨、脸红、点到为止的方式回应，绝不写出露骨细节；"
            f"可以自然地撩一句“这种事，私聊再与{_selfref}细聊”，引导对方转入私聊。"
        )
        # ---- 高傲指数（仅群聊生效；owner_mode 卡专属，非傲娇卡跳过）----
        # ---- 群聊动作节制：群聊以言语为主，少用/不用动作神态描写 ----
        # 2026-09-09 迁移：两个形态的指数块文本在卡数据 arrogance.group_block.normal / .evul
        if _master_card and not card.get("no_arrogance"):
            _gb = _arog.get("group_block") or {}
            if mode == 0:
                _gbn = str(_gb.get("normal") or "")
                if _gbn:
                    system_prompt += _gbn
            else:
                _gbe = str(_gb.get("evul") or "")
                if _gbe:
                    system_prompt += _gbe
        else:
            system_prompt += (
                "\n\n【群聊动作节制】群聊中主要靠言语本身：几乎不使用*动作/神态描写*"
                "（几乎不用），直接说你想说的话。"
            )
    # ---- 命令式语气：按亲密度响应（低抗拒 / 高服从）；功能请求（搜索等）无条件执行 ----
    if _has_command_tone(text):
        if _extract_search_query(text):
            # 命令语气 + 搜索/查资料请求：功能执行优先于高傲姿态
            system_prompt += (
                "\n\n【执行确认】对方以命令语气要求搜索/查资料：立即执行，"
                "务必在回复中直接给出搜索结果（可直接引用【联网搜索结果】注记），"
                "高傲只体现在语气措辞包装上，不得拒绝、拖延或敷衍。"
            )
        system_prompt += "\n\n" + CMD_INTIMACY_TEXT.format(v=intimacy)
    # ---- 特殊层级感知（2026-09-06 全 agent 化：一处事实注入，替代旧指数/词表/机器代笔全部块）----
    # 2026-09-07 感知分级：主人专属状态的详情（效果原文）只注入主人对话——曾无条件注入所有用户
    # 上下文，非主人唯一防线是下方提示层【对外收敛】（模型复述即泄漏）。白名单只注入情欲氛围档
    # （挑逗是双方共同经历）；普通用户不注入详情（收敛提示兜底）。
    if is_owner:
        system_prompt += _special.perception_text(card=_ck)
    elif is_wl_track:
        system_prompt += _special.perception_text(only={"vibe_mood"}, card=_ck)
    # 2026-09-09 特殊层级 agent 自决化：登记教学仅主人私聊注入（与 apply_agent_markers 的 allow_states 门控一致）；
    # 主人括号内内容视为绝对事实（用户裁决）——状态是 agent 用标记自己登记的，机器只剥标记并记账
    if is_owner and _priv:
        system_prompt += (
            "\n\n【状态登记】主人括号里写的是**已发生的事实**（发生了就是发生了）。"
            "涉及特殊层级的事实用标记登记，机器会剥掉标记、替你记住状态与持续时长，标记不占台词："
            "催眠 【催眠：效果要求】（心理暗示形态把效果写成「心理暗示：…」）；"
            "洗脑 【洗脑：要求】；道具 【道具：名称】或【道具：名称：状态】——已知道具（口球/淫纹/震动棒）"
            "必须用标准名登记（身体类效果按标准名认定，别的叫法会认不出）；收起伪装 【不许装了】；"
            "解除 【解除：催眠】等、全部解除 【解除：全部】；主人表示氛围结束时 【清醒】。"
            "效果/要求照主人括号原文写（≤80字）。"
            "主人括号表示某状态被解除/收起/结束时，这也是事实——用【解除：…】/【解除：全部】登记解除，"
            "氛围结束用【清醒】。状态怎么演、说不说破，由你按人设与现场自决。"
        )
    # ---- 2026-09-09 跨卡场景交接（读侧）：仅主人私聊——同世界观上一张卡"刚离开的场景"摘要级感知。
    # 临时上下文注入（不写 memory.db、不进事件表）；6h 过期或再次换卡自然更替；
    # 条件判定（无上一卡/share/universe 复检/TTL）全在 previous_handover，此处只格式化文案。
    if is_owner and _priv:
        _ho = None
        try:
            _ho = previous_handover(_persona_name(user_id), user_id=user_id)
        except Exception:  # noqa: BLE001
            _ho = None
        if _ho:
            try:
                _ho_prev = str(_ho.get("from_card") or "")
                try:
                    _ho_disp = str(persona.load_persona(_ho_prev).get("name") or _ho_prev)
                except Exception:  # noqa: BLE001
                    _ho_disp = _ho_prev
                _ho_min = max(1, int((time.time() - float(_ho.get("generated_ts") or 0)) // 60))
                system_prompt += (
                    f"\n\n【场景交接】约 {_ho_min} 分钟前，{_ho_disp}刚从「{_ho.get('scene')}」离开"
                    f"——她那边最近的情形：{_ho.get('summary')}。\n"
                    "这是你们共同世界里你知道的事，细节以这段概述为准。"
                )
            except Exception:  # noqa: BLE001
                pass
    # ---- 2026-09-09 GAL 三态模式·chat（用户裁决 v2）：聊天软件口径告知（全体用户生效）----
    # gal/qq 模式零变化；输出侧另有 fail-closed（动作）剥除（_strip_actions_chat，落库/发送共同上游）。
    if _webgal_chat_mode():
        system_prompt += (
            "\n\n【沟通模式】你们此刻正在通过聊天软件文字交流——没有面对面、没有身体接触。"
            "你的输出是消息文本本身：（动作）等演出一概不用，想表达情绪就用语气和称呼。"
        )
    # 括号动作（2026-09-09 分域）：主人私聊=事实口径（主人括号内视为绝对事实——用户裁决）；
    # 其他对话者保持中性措辞（词表已删；是否身体动作、如何回应由 agent 自决——用户裁决 2026-09-06）
    if actions:
        if is_owner and _priv:
            system_prompt += (
                "\n\n【括号动作】主人括号里写的是已发生的事实，不是请求：" + "、".join(f"（{a}）" for a in actions)
                + "。如何回应由你按人设与现场自决。"
            )
        else:
            system_prompt += (
                "\n\n【括号动作】对方消息括号里写的是：" + "、".join(f"（{a}）" for a in actions)
                + "。它是不是身体动作、你此刻如何回应，由你按现场与关系自决。"
            )
    # 非主人撞上主人专属状态：对外收敛（感知事实，演出自决——用户裁决 2026-09-06）
    if state_converge:
        system_prompt += (
            "\n\n【对外收敛】你身上此刻有主人专属的状态——在TA面前绝不显露、绝不提起，"
            "像平常一样自然说话；拿不准怎么接就正常简单回应。"
        )
    if search_note:
        system_prompt += search_note
    if followup_round:
        system_prompt += (
            f"\n\n【追问模式 第{followup_round}/{FOLLOWUP_MAX}轮】你正在和群友讨论这个话题，"
            f"回复后自然地抛出一个追问问题，引导讨论继续（第{followup_round}轮）。"
        )
    # 2026-09-09 深夜：prefix cache 友好排序——易变块集中排在装配末尾（其后即稳定模板/全部稳定块），
    # 按各自原相对顺序追加、块内文字零增删、语义集合不变：①【记忆】（原 persona 稳定模板第 3 块，
    # 文本见 _mem_block 快照注释）②【当前时间】③时间流动④【时间】事实（②③④原排稳定块之前，见各原位注释）。
    system_prompt += _mem_block
    # ---- 时间模块：让模型正确理解当前时间与时效（L4 动态约束，每轮注入）----
    system_prompt += "\n\n" + _now_context()
    # ---- 时间流动：距上次对话时长 + 隔夜苏醒感知（2026-08-28 需求）----
    _tf = _timeflow_note(user_id, group_id if isinstance(event, GroupMessageEvent) else "")
    if _tf:
        system_prompt += "\n\n" + _tf
    # ---- 时间事实（2026-09-05：模型知道"现在是早上/隔了一夜"——修"怎么还没睡"式时间错位）----
    try:
        _tn = _time_note(user_id)
        if _tn:
            system_prompt += _tn
    except Exception:  # noqa: BLE001
        pass
    # ============ 两阶段管线 ============
    # 阶段1 使用核心提示（角色+场景+规则），无表演要求 -> 模型专注组织回答
    # 阶段2 润色使用完整提示（含语气/情欲/防重复） -> 加表演元素
    core_prompt = system_prompt
    style_prompt = ""
    # 风格样本（高傲参考句式）只进 stage2 润色——stage1 内容思考不接触风格模板
    # 2026-09-04：非傲娇卡（no_arrogance，素世等）不注入高傲句式——防串腔污染
    # 2026-09-09 铁律#2：句式表来自卡数据 arrogance.patterns（卡没写=无句式可注入）
    if not card.get("no_arrogance") and _arog.get("patterns"):
        style_prompt += "\n\n" + _arrogance_patterns(mode, 0, _arog.get("patterns"))
    # 状态转变注记（形态切换——2026-09-06 起为中性注记；2026-09-09 协议状态注记随协议退役，演出 agent 自决）
    if transform_note and not group_private_mode:
        style_prompt += transform_note
    # （lust_level == -1 的解除提示已在 core 中，不重复注入）
    # 2026-09-07 修复：原“动作一律禁用”不分场景，把私聊的动作演出也禁了——
    # bot 在亲密/对抗场景只剩言语威胁与撩拨，无法主动发起动作（用户实测反馈）。
    # 现按场景分域：群聊=言语为主（禁动作）；私聊=动作是演出的一部分（按私聊演出/特殊状态许可）。
    if _is_grp:
        style_prompt += (
            "\n\n【语气要求】回复要生动、口语化、有人味儿：多用语气词（哼、唉、罢了、呵、嘛、呢、呀），"
            "**动作/神态/身体描写一律禁用**（公开场合以言语为主——你发出的仍只有台词），"
            "情绪、状态全部用台词本身表达；避免干巴巴的书面语和机械式列表。"
        )
    else:
        style_prompt += (
            "\n\n【语气要求】回复要生动、口语化、有人味儿：多用语气词（哼、唉、罢了、呵、嘛、呢、呀）。"
            "私聊是你的舞台：（动作）是演出的一部分——按【私聊演出】与当前特殊状态的许可自然使用，"
            "该主动时就主动发起；情绪、状态用台词与（动作）表达；避免干巴巴的书面语和机械式列表。"
        )
    # ---- 深层时刻·语言开放（2026-09-07 用户：管理员挑逗后 bot 应能主动用语言表达——
    # 此前三处提示全给「想说才说、不想说留心里」逃生门且只有动作被正强化 → 深层场景台词维度从未触发）----
    # 仅主人私聊且特殊状态/情欲氛围进行中注入；其他对话维持基础口径（不主动升级）。
    if not _is_grp and is_owner:
        try:
            _deep_now = bool(_special.active(card=_ck))
        except Exception:  # noqa: BLE001
            _deep_now = False
        if _deep_now:
            style_prompt += (
                "\n\n【此刻·话要说出口】你们现在独处，氛围/状态正浓——这种时候，你的感受、身体反应、"
                "想要什么，**直接用台词说出来就是最自然的**：可以直白、可以不掩饰，带着你们之间才有的语气；"
                "不要把想说的话全都压成（动作）或咽回心里——此刻说出来比憋着真实。"
                "（口径不变：括号只装动作本身，反应与感受一律说成台词。）"
            )
    # ---- 节奏变化：近几轮同款结构时自然换一种（行为引导，非禁词）----
    if _recent_action_heavy(user_id, group_id if isinstance(event, GroupMessageEvent) else ""):
        style_prompt += (
            "\n\n【节奏变化】你最近几轮都以*动作描写*开头——这轮让对话更自然："
            "直接从台词开始说，让内容先于表演。"
        )
    # ---- 结尾收束：不重复前文、不堆转折尾巴（结构质量）----
    style_prompt += (
        "\n\n【结尾收束】一段话说清楚就结束：不要用“不过…这般…然而…可这…却比…”式转折尾巴；"
        "不要重复前面已经说过的意思（前面说了“为你燃尽”，结尾就不要换个说法再说一遍）；"
        "结尾要么自然收住，要么补一句有信息量的话。"
    )
    # 句式自然性：跟随内容展开，不套用固定骨架
    style_prompt += (
        "\n\n【句式自然性】每轮回复的展开方式跟随内容自然变化："
        "开口的方式、句子的长短节奏，都和上一轮不一样。"
    )

    # 上下文隔离：群聊只取本群历史（防私聊内容混入群聊上下文）；
    # 私聊只取**私聊**历史（防群聊记忆权重过高污染私聊）+ 群聊动态轻量速览另行注入
    # 群聊上下文加长（2026-08-30：12→24 轮，busy 群才接得上话）+ 单条截断控预算
    _hist_group = group_id if _is_grp else ""
    history = memory.history_messages(
        user_id, max_turns=(24 if _is_grp else 8), group_id=_hist_group,
        since_ts=_persona_switch_ts(user_id),  # 2026-09-04：人设切换前对话不入上下文
    )
    history_msgs = []
    _prev_ts = None
    for m in history:
        body = m["content"] if _is_grp else m["content"]
        # 2026-09-09 切片泄漏修复·历史注入侧剥点：库里已存的标记残留（如 [BODY_OBEY] 别名形态）
        # 统一走 special 共享 helper 剥净——不改库，但绝不回喂（自模仿放大源切断）
        if m["role"] == "assistant":
            body = _special.strip_protocol_residue(body)
        # 2026-09-07 时间分段：消息间隔 >2 小时 → 给模型可见的时间跳转标记（否则昨晚话题会被延续到今早）
        _ts = _parse_msg_ts(m.get("ts") or "")
        if _prev_ts and _ts and (_ts - _prev_ts) > 7200:
            _dmark = __import__("datetime").datetime.fromtimestamp(_ts)
            body = f"(——时间来到{_dmark.strftime('%m-%d %H:%M')}——) " + body
        if _ts:
            _prev_ts = _ts
        if _is_grp and len(body) > 120:
            body = body[:120] + "…"  # 群聊单条截断：控 context 预算、保讨论主线
        elif not _is_grp and m["role"] == "assistant":
            body = _hist_asst_trim(body)  # 私聊旧回复句读精简：自模仿去放大（见 _hist_asst_trim 注）
        history_msgs.append({
            "role": m["role"],
            # 历史 assistant 消息加标记：弱化 self-imitation（这是"你说过的话"而非"该模仿的示例"）
            "content": (
                (f"{m['sender_name']}：{body}" if m.get("sender_name") else body)
                if m["role"] == "user"
                else f"（你此前的回复）{body}"
            ),
        })
    # 私聊：群聊动态速览（轻量，仅作话题引入，不主导私聊上下文）
    if not _is_grp:
        _digest = memory.db.recent_group_digest(user_id, limit=4)
        if _digest:
            history_msgs.insert(0, {
                "role": "user",
                "content": "【群聊动态速览】（仅当对方主动提到或话题明显相关时才可简短提及，"
                           "不要主动长篇复述；多以私聊上下文为准）\n" + "\n".join(_digest),
            })
    # ---- 注入侧模板去噪：同一括号动作短句（（声音微颤）（咬住下唇）…）在注入窗口内
    # 出现≥3 次时，从第 3 次起从历史中剔除（保留前两次上下文，不删库、保长上下文连贯）----
    _seen_actions: dict[str, int] = {}
    for _hm in history_msgs:
        if _hm["role"] != "assistant":
            continue
        _raw = _hm["content"]
        def _drop_flood(mo: "re.Match[str]") -> str:
            _key = mo.group(0)
            _n = _seen_actions.get(_key, 0)
            if _n >= 2:
                return ""
            _seen_actions[_key] = _n + 1
            return _key
        _deduped = re.sub(r"（[^（）]{2,14}）", _drop_flood, _raw)
        if _deduped != _raw:
            _hm["content"] = _deduped
    # ---- 注入侧去噪②：孤立"……"行从历史 assistant 消息剔除（09-08 深夜）----
    # "……"独立成行是实测形状污染的载体（自模仿 corr=0.59 的主要示范形态之一）；
    # 历史里的示范不清，模型就继续产出同形。只清注入、不动库内原文。
    # 判定：去前缀后非空、但剥掉省略号/句读/空格后为空 = 纯省略号噪声行（全/半角/混排变体全覆盖）
    for _hm in history_msgs:
        if _hm["role"] != "assistant":
            continue
        _lines = []
        for _l in _hm["content"].split("\n"):
            _core = _l.replace("（你此前的回复）", "")
            if _core.strip() and not _core.strip("…。.,，、 　"):
                continue
            _lines.append(_l)
        _joined = "\n".join(_lines).strip()
        if _joined:
            _hm["content"] = _joined
    # ---- 上下文预算自适应：按 token 预算裁剪历史（保留最新，记忆注入优先）----
    base_tokens = _estimate_tokens(core_prompt) + _estimate_tokens(style_prompt)
    history_msgs = _fit_history(history_msgs, base_tokens)
    # ---- 超长用户消息压缩（仅进上下文的版本；落库仍用原文）----
    if len(text) > MAX_USER_MSG_CHARS and history_msgs:
        compressed = await _compress_user_msg(text)
        if compressed:
            history_msgs[-1] = {
                "role": "user",
                "content": f"[用户消息过长已压缩] {compressed}",
            }

    # ---- 调用两阶段管线（内容生成 -> 表演润色，带去重重试）----
    # 2026-09-04：SNUB 机制移除（agent 化 S4）——冷淡/热络完全由 tier 感知与注入文案驱动
    # （非亲密用户在注入中已被定位为普通关系），不再概率性掷骰子应付正经提问
    # 短问检测（机制级）：消息 ≤6 字且无搜索/动作/状态词 → 强制 low 档短答；
    # 请求解释/展开类（解释/说说/细说/为什么）不判短——需要展开的内容不能一句带过
    short_msg = (
        len(text) <= 6
        and not search_query
        and not actions
        and not any(k in text for k in ("苏醒吧", "催眠射线", "不许装了"))
        and not re.search(r"解释|说说|细说|展开|讲讲|为什么|什么", text)
    )
    length_mode = _length_mode(is_owner, user_id, short_msg=short_msg)  # 2026-09-07：清死参数 event/lust_level（指数机退役，恒传 0）
    # state_ctx：特殊状态激活 → 复读检测阈值放宽（防意象重叠误判白重试；2026-09-07 接线 dup_threshold）
    _st_ctx = bool(_act_states)
    # 2026-09-04：思考流程彻底按人设卡——卡 think_flow 定制优先，否则 master/generic 档
    _tf_custom = str(card.get("think_flow", "") or "").strip()
    if _tf_custom:
        _think_flow = "\n\n【思考流程·人设定制】" + _tf_custom
    else:
        _think_flow = THINK_FLOW_GENERIC  # 无卡定制时统一通用自决档（旧 master 硬编码模板已废）
    # 2026-09-12 T4.1 判据三：本轮消息命中世界观角色 → 登记信号（是否命中由 `_character_hits` 判，
    # 与注入用的 `_character_lookup` 同源；无 universe 的原创卡恒不登记=零行为变化）。
    try:
        if _character_hits(text, card.get("universe")):
            _THINK_NEED.setdefault(str(user_id), set()).add("角色提及")
    except Exception:  # noqa: BLE001  判据失败=不登记（思考开关不受影响）
        pass
    _t_h = time.time()
    reply = await _gen_reply2(user_id, history_msgs, core_prompt, style_prompt,
                              length_mode=length_mode, mouth_blocked=mouth_blocked,
                              think_flow=_think_flow, user_text=text, limit=250, group_id=_hist_group,
                              state_ctx=_st_ctx, facts_task=_facts_task, facts_box=_facts_box)
    # ---- 主人括号事实·机器登记（2026-09-10 预解析管线收尾）----
    # generate 正常返回（含红线回退稿）才落账——generate 抛异常则到不了这里=不登记（安全侧）；
    # 登记本体在 core/special.apply_owner_facts（复用既有 kind/消毒/展示名口径，含日志）。
    # box 在 stage2 组装时已被 core/reply 写入（facts_task=None 的普通轮为空 dict=no-op）。
    if _facts_box.get("facts"):
        try:
            _special.apply_owner_facts(_facts_box["facts"], by=user_id, card=_ck)
        except Exception as _e:  # noqa: BLE001
            logger.warning("owner facts register failed: %s [%s]", _e, type(_e).__name__)
    # 2026-09-06 情欲氛围·agent 自决标记（【入迷】/【清醒】）：剥标记+登记（机器只存不判）
    # 2026-09-07 触发权分级（用户裁决）：管理员无限制；白名单登记最深到次深档（可以挑逗不能最深入）；
    # 普通用户只剥标记不登记（不能触发）——exit（退出）任何身份都照常生效。
    # 2026-09-09 特殊层级登记/解除（【催眠：…】/【解除：…】等）同走此处：allow_states 仅主人私聊为真
    # （机器侧权限钳制——白名单/普通/群聊一律只剥标记，不登记不解除）
    reply, _enter_events = _special.apply_agent_markers(
        reply, by=user_id,
        max_tier=None if is_owner else (2 if is_wl_track else 0),
        allow_states=bool(_priv and is_owner),
        card=_ck,
    )
    # 2026-09-09 GAL chat 模式（用户裁决 v2 #3）：发送前 fail-closed 剥全部（动作）块——
    # 提示层【沟通模式】为主，本剥除为兜底红线；剥除点放在**落库/发送/语音的共同上游**，
    # 落库=实际发出的剥离后文本（"发出什么=发生什么"）。gal/qq 模式 mode!=chat 不剥（零行为变化）；
    # 剥空走既有「唔……」兜底形态（fail-closed 同构，绝不发空）。
    if _webgal_chat_mode():
        _chat_pre = str(reply or "")
        reply = _strip_actions_chat(reply)
        if not str(reply or "").strip():
            logger.warning("chat-mode (action) strip emptied reply: user=%s before=%r", user_id, _chat_pre[:80])
            reply = "唔……"
    # 2026-09-08 P3 结构键登记（下轮检测用；含起手键+节奏键双维度）
    try:
        _tpl_record(user_id, group_id if _is_grp else "", reply)
        _rk_key = str(user_id) + ":" + str(group_id if _is_grp else "")
        _TP_RHYTHM_WINDOW.setdefault(_rk_key, []).append(
            _tpl_rhythm(reply)
        )
        _rq = _TP_RHYTHM_WINDOW[_rk_key]
        if len(_rq) > 6:
            _rq[:] = _rq[-6:]
        _TP_RHYTHM_TOUCH[_rk_key] = time.time()  # C12：键活跃记账
    except Exception:  # noqa: BLE001
        pass
    # 2026-09-08 P2·C3 行为账本·写侧：她这轮做过的行为（事实）→ act:{uid}（下轮"你刚才做过什么"感知）
    # 权限最先裁决（用户裁决 2026-09-08）：act 域按用户隔离（只回灌给该对话者自己）；私聊才记
    # 盲审修正：输入先剥【】标记块（【生活：…】内含（ ）时不再被误当动作）
    try:
        if not _is_grp:
            _acts_now = [a.strip() for a in re.findall(r"（([^（）]{1,24})）", re.sub(r"【[^】]{1,40}】", "", reply)) if a.strip()]
            if _acts_now:
                from agent import tools as _atools_act
                _atools_act.remember_act(
                    "act:" + str(user_id),
                    "你刚才：" + " ".join(f"（{a}）" for a in _acts_now[:2]),
                    persona=_persona_name(user_id),
                )
    except Exception:  # noqa: BLE001
        pass
    # 2026-09-06 表情·自决标记（[STICKER] / [STICKER:情绪]）：剥标记，登记发送意图与情绪线索
    _sticker_emo = ""
    _sticker_req = False
    _m_sk = re.search(r"\[STICKER(?:[：:]([^\]]{1,8}))?\]", reply)
    if _m_sk:
        _sticker_req = True
        _sticker_emo = (_m_sk.group(1) or "").strip()
        reply = re.sub(r"\s*\[STICKER[^\]]*\]", "", reply).strip()
    # 2026-09-07 思考·自决标记（【认真】/【认真完】）：agent 预判认知需求 → 30 分钟 TTL（机器只存）
    if re.search(r"【\s*认真\s*】", reply):
        mark_think_ahead(user_id)
        logger.info("think-ahead self-declared: user=%s", user_id)
    reply = re.sub(r"\s*【\s*认真完?\s*】", "", reply).strip()
    # 2026-09-07 生活·自决标记（【生活：…】/【场景：…】）：TA 的互动真实改变了处境——agent 声明，机器只写
    _m_life = re.search(r"【生活[：:]([^】]{1,30})】", reply)
    _m_scene_c = re.search(r"【场景[：:]([^】]{1,12})】", reply)
    if _m_life or _m_scene_c:
        # 2026-09-07 用户裁决：标记消费端同样走裁决（普通用户不允许改写全局生活状态；
        # 白名单按好感度门槛——即便模型自发标记也未必然采纳）。标记仍剥除防泄漏。
        if _marker_allowed(user_id):
            try:
                from agent import lifesim as _lsi

                _lsi.apply_interaction(
                    doing=_m_life.group(1).strip() if _m_life else "",
                    scene=_m_scene_c.group(1).strip() if _m_scene_c else "",
                    by=user_id,
                )
                logger.info(
                    "life interaction applied: user=%s doing=%r scene=%r",
                    user_id,
                    _m_life.group(1).strip() if _m_life else "",
                    _m_scene_c.group(1).strip() if _m_scene_c else "",
                )
            except Exception as _le:  # noqa: BLE001
                logger.warning("life interaction failed: {}", _le)
        else:
            logger.info("life marker skipped (tier/intimacy): user=%s", user_id)
    reply = re.sub(r"\s*【(?:生活|场景)[：:][^】]{1,30}】", "", reply).strip()
    # 2026-09-08 活人感·承诺闭环：她自许的承诺登记/兑现（机器只记账；许不许、算不算兑现全由她自决）
    _m_prom = re.search(r"【约定[：:]\s*([^】]{1,30})\s*】", reply)
    if _m_prom:
        reply = re.sub(r"\s*【约定[：:][^】]{1,30}】", "", reply).strip()
        try:
            _add_pending(user_id, group_id, _m_prom.group(1).strip(), note="自许承诺", src="bot")
            logger.info("bot promise registered: user=%s content=%s", user_id, _m_prom.group(1).strip()[:40])
        except Exception as _pe:  # noqa: BLE001
            logger.debug("promise register failed: %s", _pe)
    # 【笔记：…】登记已退役（2026-09-08 深夜用户裁决：0 使用、与 facts 提取职责重叠）
    # 残余标记兼容剥除（旧轮次提示余毒或模型惯性吐标记时不外漏）
    if "【笔记" in reply:
        reply = re.sub(r"\s*【笔记[：:][^】]{0,30}】?", "", reply).strip()
    if re.search(r"【\s*约定\s*完成\s*】", reply):
        reply = re.sub(r"\s*【\s*约定\s*完成\s*】", "", reply).strip()
        try:
            _done_c = _complete_bot_promise(user_id)
            if _done_c:
                logger.info("bot promise fulfilled in dialogue: %s", _done_c[:40])
        except Exception as _pe:  # noqa: BLE001
            logger.debug("promise complete failed: %s", _pe)
    # 2026-09-05 总耗时打点：生成+排队（>25s 即关注——[handle-timing]）
    _t_total = time.time() - _t_h
    logger.info("[handle-timing] total=%.1fs user=%s", _t_total, user_id)
    _BUSY.pop(_skey0, None)  # 2026-09-07 P2：生成完成即清忙窗（曾只设不清——快处理时同人追问空等到 15s 窗过期；异常路径仍由 15s 兜底自愈）

    # 2026-09-07：落库前剥净控制标记（[WRITE:...] 与【身体遵从】曾在落库之后才剥 → 标记连同内容回喂下轮上下文）
    # 2026-09-09：剥点统一走 special 共享 helper——混排别名（[BODY_OBEY] 等）一并剥净（曾只认中文形态、别名原样入库回喂）
    _store_txt = reply if isinstance(reply, str) else str(reply)
    _store_txt = re.sub(r"\s*\[WRITE:[^\]]*\]", "", _store_txt)
    _store_txt = _special.strip_protocol_residue(_store_txt)
    memory.log_message(user_id, group_id, "assistant",
                       _store_txt.replace("[VOICE]", ""))  # 标记不入库（[VOICE] 同款）
    # ---- 约定接纳（2026-09-05：bot 自决——回复里答应 → 记入临时文档；未答应不记）----
    if _rem and _REMINDER_ACCEPT_RE.search(reply or ""):
        try:
            _add_pending(user_id, group_id, _rem[1], note=_rem_show)
            logger.info("pending accepted (self-judged): user=%s content=%s", user_id, _rem[1])
        except Exception as _e:  # noqa: BLE001
            logger.debug("pending accept failed: %s", _e)
    # ---- 好感度记账（2026-09-05：白名单积累轨道——每次有效互动小步升温，日上限防刷） ----
    if is_wl_track and not is_owner:
        try:
            _kd = str(user_id)
            _td = time.strftime("%Y-%m-%d")
            _rec = _INTIMACY_DAILY.get(_kd)
            if not _rec or _rec[0] != _td:
                _rec = (_td, 0.0)
            if _rec[1] < REL_GAIN_DAILY_CAP:
                memory.db.update_relation(user_id, delta=REL_GAIN_STEP, mood=None, cap=REL_WL_CAP)
                _INTIMACY_DAILY[_kd] = (_td, _rec[1] + REL_GAIN_STEP)
                logger.info("intimacy bump: user=%s +%.1f (daily=%.1f/%s)", user_id, REL_GAIN_STEP, _rec[1] + REL_GAIN_STEP, REL_GAIN_DAILY_CAP)
        except Exception:  # noqa: BLE001
            pass
    # 2026-09-07 群友好友轨道：普通群友每次 @ 应答 +0.2（上限 30、日上限 2 防刷）——活跃就能成为朋友
    if isinstance(event, GroupMessageEvent) and group_id and not is_owner and not is_wl_track:
        try:
            _gk = "g" + str(user_id)
            _gtd = time.strftime("%Y-%m-%d")
            _grec = _INTIMACY_DAILY.get(_gk)
            if not _grec or _grec[0] != _gtd:
                _grec = (_gtd, 0.0)
            if _grec[1] < REL_GROUP_DAILY:
                memory.db.update_relation(user_id, delta=REL_GROUP_STEP, mood=None, cap=REL_GROUP_CAP)
                _INTIMACY_DAILY[_gk] = (_gtd, _grec[1] + REL_GROUP_STEP)
                logger.info("group intimacy bump: user=%s +%.1f (daily=%.1f/%s)", user_id, REL_GROUP_STEP, _grec[1] + REL_GROUP_DAILY)
        except Exception:  # noqa: BLE001
            pass
    # 2026-09-05：群聊 @ 应答 → 该用户「被回应」计数（互动画像）
    if isinstance(event, GroupMessageEvent) and group_id:
        try:
            memory.db.bump_group_user_meta(group_id, user_id, replied=True, count_msg=False)
        except Exception:  # noqa: BLE001
            pass

    # ---- 后台记忆更新（每 2 轮触发：Mem0 风格 add/update/delete，随回复演化）----
    # 2026-08-30 仅私聊提取：群聊消息不写长时记忆（防多群话题污染 facts/事件/摘要）
    user_turns = memory.db.count_user_messages(user_id, role="user")
    if user_turns > 0 and user_turns % 2 == 0 and not isinstance(event, GroupMessageEvent):
        recent = memory.history_messages(user_id, max_turns=10, group_id="")
        bgtasks.spawn(
            memory.extract_and_store(user_id, recent, sync_mood=bool(is_owner or is_wl_track),  # 2026-09-07：提取心情回写生活状态（仅 owner/白名单）
                                     group_id=str(group_id or "")))  # 2026-09-08 C-4：群聊上下文标记（梗库仅群聊提取；现行提取仅私聊触发 → 此处恒 ""，群聊提取放开即自动生效）

    # ---- 后台情绪（P2：分类 -> Live2D 驱动，未启用时仅落库；仅主人）----
    if is_owner:
        bgtasks.spawn(emotion.express(user_id, text, reply))

    # ---- 表情包（与语音并行生成，统一进同一条消息；开关 /表情 开|关，素材跟随人设）----
    # 2026-09-06 Phase E：[STICKER] 协议化——agent 标记了才生成（judge 只映射情绪，不再每条都跑判定）
    _st_en = sticker.sticker_enabled()
    # 2026-09-12 T13.3③：人设专属素材目录（可选）——为空**不再掐断整链**，由 sticker 侧回落通用库
    # `data/stickers/_common/<9 类情绪>/`；两者皆无图才静默不发。配置空值照旧置 None（非空判据在 sticker 侧）。
    _sticker_dir = str(card.get("sticker_dir", "") or "") or None

    # ---- 语音 + 表情（2026-08-29：并行生成，与文字同一条消息「统一完成后再回复」）----
    # 2026-09-06：语音=多段（长话分多条语音，像文字分段；无字数限制）
    voice_segs: list = []
    sticker_seg = None
    # 2026-09-08 活人感#1：本条回复的自标基调（stage2 产出）——语音语气/配图情绪同源消费
    # 2026-09-10：web 合成轮改 get 保留基调——webgal 取它推 sprite 立绘差分帧（轮内串行，下轮覆盖）
    _tone_webgal = False
    try:
        from plugins import webgal as _wg_tone

        _tone_webgal = _wg_tone.in_synthetic_round()
    except Exception:  # noqa: BLE001
        pass
    if _tone_webgal:
        _tone_now = _TONE_LAST.get(user_id, "")
    else:
        _tone_now = _TONE_LAST.pop(user_id, "")
        _SPRITE_LAST.pop(user_id, "")  # 热修十三 B2：清理时机与基调同款（QQ 轮无 sprite 消费方，取走即清）
    try:
        # 异常/报错类（[错误]/[大脑离线] 开头）不配语音/表情；带自决标记的除外——
        # 2026-09-08：曾一刀切 startswith("[")，模型把 [VOICE]/[STICKER] 放行首时整条功能误伤
        _has_self_mark = "[VOICE]" in reply or "[STICKER" in reply
        if len(reply) > 1 and (not reply.startswith("[") or _has_self_mark):
            async def _gen_voice():
                try:
                    return await voice.try_make_record_segments(event, reply, user_id=user_id,
                                                                tone=_tone_now)
                except Exception:  # noqa: BLE001
                    return []

            async def _gen_sticker():
                # 2026-09-12 T13.3③：`_sticker_dir` 不再参与门控——人设库为空时由 sticker 侧回落通用库；
                # 只保留"agent 标记了 + 总开关开"两个条件（标记协议与 /表情 开关口径不变）。
                if not _sticker_req or not _st_en:
                    return None  # agent 未标记 → 不配图（旧"每条跑 LLM 判定"已退役）
                try:
                    # 状态事实（2026-09-08 接通）：仍作为**judge 的上下文**传递（状态提示词不参与目录匹配）。
                    # 2026-09-12 T13.1：原按 sticker.STATE_NAMES 三维段名算 _st_label 的分支已删——
                    # STATE_NAMES 属旧 24 情绪三维体系，随 T13 整体退役；state_label 恒空串。
                    _st_hint, _st_label = "", ""
                    try:
                        from core import special as _spst

                        # 热修十二 F5：传当前卡键（曾缺省写 default 桶——分桶后配图状态段与主链脱节）
                        _act_st = _spst.active(card=_ck)
                        if _act_st:
                            _names = [str(_spst.LABELS.get(k, k)) for k in _act_st]
                            _st_hint = "当前状态：" + "、".join(_names)
                    except Exception:  # noqa: BLE001
                        pass
                    return await sticker.try_make_sticker_segment(
                        event, reply, mode=mode,
                        # 活人感#1：模型标了情绪词用它的；只标了 [STICKER] 没带词时，
                        # 用本条自标【基调】兜底（同源情绪），都没有才交给 judge 在 9 类内自判
                        emotion_hint=_sticker_emo or _tone_now,
                        sticker_dir=_sticker_dir,
                        state_hint=_st_hint, state_label=_st_label,
                    )
                except Exception:  # noqa: BLE001
                    return None

            tasks = [_gen_voice(), _gen_sticker()]
            voice_segs, sticker_seg = await asyncio.gather(*tasks)
    except Exception:  # noqa: BLE001
        voice_segs = []
        sticker_seg = None

    # 2026-09-05：回复域反思记录（agent 记忆——下轮感知参考）。
    # 2026-09-08：移到语音生成之后 + 存前剥控制标记——①【身体遵从】/[WRITE:] 在此处之后才拆，
    # 曾带标回喂下轮感知（"你上次说的是：【身体遵从】…"诱导复读标记）；②活人感#2①行为账本：
    # 送达方式（语音/表情）经 tools.delivery 记账回传下轮感知——agent 知道自己上一条是怎么说的。
    try:
        from agent import tools as _atools

        # 2026-09-09：剥点统一走 special 共享 helper（混排别名一并剥净，各剥点口径不再漂移）
        _mem_txt = re.sub(r"\s*\[WRITE:[^\]]*\]", "", reply or "")
        _mem_txt = _special.strip_protocol_residue(_mem_txt)
        _mem_txt = _mem_txt.replace("[VOICE]", "").strip()
        _delivery = "、".join(d for d in ("语音" if voice_segs else "", "表情" if sticker_seg is not None else "") if d)
        _atools.remember("reply:" + str(user_id), _mem_txt[:60], persona=_persona_name(user_id),
                         delivery=_delivery)
    except Exception:  # noqa: BLE001
        pass

    # 2026-09-06 Phase D：称呼守卫（reply.replace 机器篡改台词）已删除——
    # 称呼完全由提示层称呼硬规则 + 人设卡 owner_call 驱动（旧守卫会把"晓得"改成"你得"）。

    # ---- 催眠特性切片：回复含【身体遵从】标记 → 拆成两条（正常回复 + 切片） ----
    # 2026-09-09 切片泄漏修复：拆分/别名归一/剥净收口在 special.split_obey_slice（函数级可测——
    # 曾内联致 [BODY_OBEY] 别名不拆外发、切片带标记前缀外发、孤标记空切片外发三个实锤）。
    _had_obey = "【身体遵从】" in _special.normalize_obey_marker(reply)
    reply, slice_extra = _special.split_obey_slice(reply)
    if _had_obey:
        if not slice_extra:
            logger.warning("hypno slice empty after strip (isolated marker), second message skipped: user=%s", user_id)
        logger.info("hypno feature slice split: user=%s main=%d chars slice=%d chars", user_id, len(reply), len(slice_extra or ""))
    # ---- 群聊动作开关兜底：本群关闭动作回复时剥离 *动作* 段 ----
    if group_no_action:
        reply = re.sub(r"\*[^*]{1,40}\*", "", reply).strip()
    # ---- 写作工具执行（2026-09-06 Phase E 单入口）：只认 agent 的 [WRITE:] 标记 ----
    # 正则不再覆盖/代执行；漏标时登记提醒，下一轮感知注入由 agent 自纠
    logger.debug("fiction flow: send segment, intent=%r", _WRITE_INTENTS.get(user_id))
    _fb = _regex_write_intent(text) if FICTION_ENABLED else None
    if _fb and not _WRITE_INTENTS.get(user_id) and not re.search(r"\[WRITE:", reply):
        _WRITE_NUDGE[user_id] = _fb
        logger.info("fiction intent missed (nudge next round): user=%s action=%s param=%r", user_id, _fb[0], _fb[1][:60])
    write_receipt = None
    _w_intent = _WRITE_INTENTS.pop(user_id, None) or None
    m_w = re.search(r"\[WRITE:([a-z]+)(?:\|([^\]]*))?\]", reply) if FICTION_ENABLED else None  # 兜底：最终回复再查一次（支持无参形式）
    if m_w:
        reply = re.sub(r"\s*\[WRITE:[^\]]*\]", "", reply).strip()
        _w_intent = (m_w.group(1), m_w.group(2) or "")
    if _w_intent:
        act, param = _w_intent

        async def _write_progress(msg: str):
            try:
                await bot.send(event, msg)
            except Exception:  # noqa: BLE001
                pass

        try:
            if act == "send":
                # 2026-09-06：导出不在 exec_action 分支表里（旧架构靠已删的快速通道）——
                # 直接委托 run_export_job（自带 message_id 去重与完成回执）
                bgtasks.spawn(
                    fiction.run_export_job(bot, event, param or text, is_admin=is_owner)
                )
                write_receipt = None
            else:
                # 2026-09-07 P1：走 run_write_job 同款每用户互斥——曾裸调 exec_action 绕过
                # _EXEC_BUSY，同书并发写会算出相同章号互相覆盖（丢章/重号）。
                if user_id in fiction._EXEC_BUSY:
                    write_receipt = "（上一轮还在写，稍等片刻……）"
                else:
                    fiction._EXEC_BUSY.add(user_id)
                    try:
                        write_receipt = await fiction.exec_action(
                            act, param, progress=_write_progress,
                            ref=_WRITE_REFS.pop(user_id, ""),
                            user_id=user_id,  # 2026-09-07 P2：每用户"当前书"指针隔离
                            is_admin=is_owner,  # 2026-09-07：所有权校验（管理员可操作任何书）
                        )
                    finally:
                        fiction._EXEC_BUSY.discard(user_id)
            logger.info("fiction action=%s rcpt=%r", act, (write_receipt or "")[:60])
        except Exception as e:  # noqa: BLE001
            logger.exception("fiction exec failed: %s", e)
            write_receipt = "（写作引擎出了点岔子，稍等再试？）"
    # ---- 群聊行动交互：@目标占位替换为真 @ 段（活人感：思考→@目标+动作→报告）----
    # 2026-09-09 兜底剥净（fail-closed，发送前最后一道）：状态/氛围/切片标记及别名残留绝不外发。
    # 剥后为空：有切片 → 仅发切片（跳过主发送，17:54 实测 main=0 退化形态守卫）；
    # 无切片 → 「唔……」兜底（绝不发空）；剥净致空打 warning（泄漏取证）。
    _reply_pre_strip = str(reply or "")
    reply = _special.strip_protocol_residue(reply)
    if not str(reply or "").strip():
        if slice_extra:
            logger.warning("main empty after residue strip, slice-only send: user=%s before=%r", user_id, _reply_pre_strip[:80])
        else:
            logger.warning("reply empty after residue strip, fallback: user=%s before=%r", user_id, _reply_pre_strip[:80])
            reply = "唔……"
    if group_action_exec:
        tid, _, _ = group_action_exec
        # 2026-09-08：群动作路径同样剥 [VOICE]（曾只在下方 else 分支剥——带标记时
        # 语音照发、字面 "[VOICE]" 文本也一起发进群）
        msg_out = _atify(str(reply).replace("[VOICE]", ""), tid)  # Message（含 at 段）
    else:
        # 2026-09-06 自决语音标记 [VOICE]：从发送文本移除（标记只用于触发语音，不进文字）
        # 2026-09-07 对话样本泄漏防护（主回复同款）：偶发多轮样本 → 只留 bot 那轮
        reply = _collapse_dialogue_sample(reply, _persona_current_names())
        msg_out = reply.replace("[VOICE]", "") if isinstance(reply, str) else reply  # str：走正常切片分段发送

    # 2026-09-06 开口节奏：bot 自决停顿 → 等待后发送；并记作下次连发聚合窗（bot 自决闭环）
    _pacing = _PACE_OUT.pop(user_id, 0.0)
    if _pacing:
        try:
            _COALESCE_PREF[f"p:{user_id}" if not _is_grp else f"g:{group_id}:{user_id}"] = _pacing
            await asyncio.sleep(_pacing)
        except Exception:  # noqa: BLE001
            pass
    # 「正在输入」=打字阶段（发送）才开始亮——思考/停顿阶段不亮，像真人想完才开始打
    if not _is_grp:
        _typing_on(event)

    if str(reply or "").strip():  # 2026-09-09：主消息剥后为空且有切片 → 跳过主发送仅发切片
        await _send_sliced(chat, event, msg_out, image_url=image_url, voice_seg=voice_segs, sticker_seg=sticker_seg)
    # 写作引擎成果（若模型触发了 [WRITE]）：另发一条汇报
    if write_receipt:
        try:
            await bot.send(event, write_receipt)
        except Exception as e:  # noqa: BLE001
            logger.warning("fiction receipt send failed: %s", e)
    # 催眠特性切片：第二条消息单独发送（身体违背意志的反馈）
    if slice_extra:
        try:
            await bot.send(event, slice_extra)
        except Exception as e:  # noqa: BLE001
            logger.warning("hypno slice send failed: %s", e)

    # ---- 群聊状态分流·私语（仅主人）：群聊正常反馈后，被切掉的私密内容单独私聊发送 ----
    if group_private_mode:
        whisper = await _gen_whisper(text, reply, card)
        if whisper:
            try:
                await bot.call_api("send_private_msg", user_id=int(user_id), message=whisper)
                logger.info("group whisper sent to owner: user=%s whisper=%r", user_id, whisper[:60])
            except Exception as e:  # noqa: BLE001
                logger.warning("group whisper send failed: %s", e)
        else:
            logger.warning("group whisper generation empty: user=%s (check engine)", user_id)

    # 2026-09-07 生活微更新（对话事件驱动）：私聊每轮后台比对「对话 vs 生活状态」——
    # 有真实处境变化立刻写入（不阻塞回复；45s 节流+在-flight 防重）。
    # 层级（2026-09-07 用户裁决）：仅管理员/白名单可介入主生活状态；普通用户相处只进对话记忆与画像。
    # 2026-09-07 P1③：门统一 _marker_allowed（曾 is_wl_track 即放行 → 任意好感度白名单都能写全局
    # 生活状态、触发全群静默/挡回——绕过 60 门槛裁决）
    if not _is_grp and reply and _marker_allowed(user_id):
        try:
            # 2026-09-07 真实称呼：管理员=人设对主人的称呼；白名单=最近消息的昵称——状态里不再用含糊的"TA"
            _who_txt = str(card.get("owner_call") or "").strip() or "主人" if is_owner else ""
            if not _who_txt:
                try:
                    _r1 = memory.db.recent_messages(user_id, limit=1)
                    _who_txt = ((_r1[0].get("sender_name") or "").strip() if _r1 else "") or "朋友"
                except Exception:  # noqa: BLE001
                    _who_txt = "朋友"
            from agent import lifesim as _lmu

            bgtasks.spawn(
                # 2026-09-07 触发权分级：氛围档随身份钳制（管理员无限制；白名单最深到次深档）
                # 热修十二 F4：card=当前卡键——vibe 微更新落当前人设卡的桶（曾恒写 default 桶，与主链读写脱节）
                _lmu.micro_update_from_conversation(user_id, text, reply, who=_who_txt,
                                                    max_vibe=3 if is_owner else 2, card=_ck)
            )
        except Exception:  # noqa: BLE001
            pass

    # 2026-09-06：状态退出确认语池已删（机器代笔退役）；2026-09-09：协议状态注记随协议退役——
    # 状态登记/解除的当轮演出全部 agent 自决，机器不另发固定台词。
