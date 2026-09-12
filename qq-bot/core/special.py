"""special —— 特殊层级状态机（2026-09-06 重写，全面 agent 化）。

定位（用户裁决，2026-09-09 更新）：特殊层级，非常态行为。进入/解除由 **agent 在回复里用标记
自决登记**（【催眠：…】/【解除：…】等，与情欲氛围【入迷】/【清醒】同构；主人括号内内容视为
绝对事实——教学口径，由调用方注入）；机器按 allow_states 门控落账（仅主人私聊会话）。
bot 的演出全部 agent 自决——本层只存「状态事实」，不判行为、不数指数、不拦台词、不代写演出。

与旧机制（情欲/催眠/洗脑/道具 指数机）的对应断裂：
    - 删词表触发（LUST_TRIGGERS/LUST_END_WORDS/HYPNO_END_WORDS…）
    - 删指数/档位/进度/理性/屈服度/高潮计数
    - 删 tick 循环（道具凭空建情欲状态的 bug 源）
    - 删机器代写演出/告别语；删亲密度 orig 机器改写（好感只属关系层）

状态种类：
    hypno      催眠（身体强制/心理暗示，效果=主人括号原文；10 分钟滑动窗，主人消息顺延）
    brainwash  洗脑（人格改写要求原文；无时效，仅【解除：洗脑】清）
    item:ball  口球（无时效，仅【解除：道具：口球】清；口球物理红线依赖此 kind）
    item:mark  淫纹（同上——2026-09-06 裁决：不做自然衰减）
    item:vibe  震动棒（同上）
    no_fake    不许装了（收起伪装；默认 10 分钟）
    vibe_mood  情欲氛围（agent 自决【入迷】标记进入/【清醒】退出；【解除：情欲氛围】亦可清）
    item:<名>  开放道具集（2026-09-09：未知名消毒后开放登记，展示名写进 entry["label"]）

时间策略（2026-09-11 热修十一，用户裁决）：
    药剂类道具（item:* 消毒名/label 含「药」）随时间减弱——TTL 上限 12 小时
    （base 更短用 base；base=None 无时效也钳为 12h；存量由 normalize_ttls() 幂等归一，
    brain 启动时调用一次）。其余道具（口球/手铐/淫纹/震动棒等开放集）与
    hypno/brainwash/mark/vibe_mood **不受时间影响**——维持各自 DEFAULT_TTL/None 现状，
    仅【解除：…】主动解除。

存储：E:/robot/data/special_state.json（core.atomics 原子写；单全局状态——bot 只有一个身体）。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from loguru import logger

from core import atomics
from core.paths import data_path

STATE_FILE = data_path("special_state.json")

# 内置状态 kind —— 这是**白名单（能力/协议）**，不是展示名：外置它会让第三方装机上整层状态机失效
# （`set_state` 靠它判合法 kind）。道具走开放前缀 `item:*`：名称由主人指令/卡/内容包给出，代码不枚举。
STATES = ("hypno", "brainwash", "no_fake", "vibe_mood")

# 展示名与道具默认效果文案 —— **内容**（2026-09-12 用户裁决：能力接口公开、R18 内容不随仓）。
# 从内容侧 `data/special_content.json` 读：`{"labels": {kind: 名}, "item_notes": {kind: 效果原文}}`（UTF-8）。
# 缺失/损坏 → 空表 + 中性回落（展示名回落 kind 本身；效果文案回落卡/包目录 note）——
# **内容缺失是正常状态**：不报错、不 warning（本仓既有纪律："无包=原行为"）。
SPECIAL_CONTENT_FILE = data_path("special_content.json")


def _load_special_content() -> dict:
    """读内容侧覆盖表（labels / item_notes）。任何异常都当"没有内容"处理，绝不影响状态机。"""
    try:
        if SPECIAL_CONTENT_FILE.is_file():
            d = json.loads(SPECIAL_CONTENT_FILE.read_text(encoding="utf-8-sig"))
            if isinstance(d, dict):
                return d
    except (OSError, ValueError, TypeError):
        pass  # 内容文件缺失/损坏属**正常状态**（内容跟包走，外部输入）→ 走中性缺省；
        # 不写日志：第三方装机上"没有内容文件"是常态，刷 warning 只会淹没真问题。
    return {}


def _content_map(raw) -> dict:
    """只收「非空 str → str」的项（内容文件坏了也只是少几项，不会把状态机带崩）。"""
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v.strip()}


_SC = _load_special_content()
LABELS = _content_map(_SC.get("labels"))          # 展示名；缺 → 回落 kind 本身
ITEM_NOTES = _content_map(_SC.get("item_notes"))  # 内置道具默认效果；缺 → 回落包目录 note
# 滑动窗/默认时长（秒）；None=无时效（仅解除指令清）
DEFAULT_TTL = {
    "hypno": 600,
    "brainwash": None,
    "item:ball": None,
    "item:mark": None,
    "item:vibe": None,
    "no_fake": 600,
    "vibe_mood": None,
}
# 内置道具默认效果原文 —— **内容**（同上：随仓代码不含成人向文案）。
# 读取链：主人指令原文 > 内容侧 `item_notes` > 内容包目录 note > 空。
# 2026-09-11 热修十一（用户裁决）：药剂类随时间减弱，最多维持半天——TTL 上限 12 小时
_ITEM_TTL_MAX = 12 * 3600


def _item_ttl_policy(kind: str, label: str, base_ttl):
    """道具时效策略（2026-09-11 热修十一，用户裁决）——单一实现（set_state/refresh/normalize_ttls 同源）：
    药剂类（item:* 消毒名或展示名含「药」）随时间减弱 → TTL 上限 12h：base 更短用 base，
    base=None（无时效）也钳为 12h；base 超长（如包目录给 48h）截短到 12h。
    其余道具（口球/手铐/淫纹/震动棒等）与全部内置 kind 不受时间影响——原样返回 base
    （None=无时效/数值=原 TTL）。"""
    if str(kind or "").startswith("item:"):
        _name = str(kind).split(":", 1)[1]
        if "药" in _name or "药" in str(label or ""):
            try:
                _b = float(base_ttl) if base_ttl is not None else 0.0
            except (TypeError, ValueError):
                return _ITEM_TTL_MAX
            if _b <= 0:  # 无时效（None/0）也钳为 12h——「最多维持半天」是裁决口径，无豁免
                return _ITEM_TTL_MAX
            return int(min(_b, float(_ITEM_TTL_MAX)))
    return base_ttl

_STATE: dict | None = None  # 进程内缓存（单事件循环；写穿文件）


# ---------- 基础读写 ----------

def _load() -> dict:
    global _STATE
    if _STATE is None:
        # C10：损坏文件 fail-closed——先隔离改名（.corrupt 留取证）再以空状态起步，
        # 防止「读坏 → 拿默认空状态 → 下一次 set_state 原子写回」把坏文件连同原状态一起覆盖清掉
        try:
            _STATE = json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
            if not isinstance(_STATE, dict):
                raise ValueError("special_state is not a dict")
        except FileNotFoundError:
            _STATE = {}
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.warning("special_state corrupt, quarantining: {} [{}]", e, type(e).__name__)
            atomics.quarantine_corrupt(STATE_FILE)
            _STATE = {}
        # 2026-09-11 热修九：v1（全局平铺）→ v2（按人设卡分桶）迁移——状态跟随人设卡（与记忆同口径），
        # 治"凯尔希会话的状态泄漏进阿米娅"。旧平铺档归入 legacy 桶（本项目实测归属凯尔希会话；
        # 恢复/迁移可参照 vault/backups/special-state/ 快照）。
        if isinstance(_STATE, dict) and "cards" not in _STATE and _STATE:
            _STATE = {"cards": {"legacy": _STATE}}
    return _STATE


def _save(st: dict) -> None:
    global _STATE
    _STATE = st
    atomics.write_json_atomic(STATE_FILE, st)


def _norm_card(card: str | None) -> str:
    """卡键归一：空值回落 default 桶（工具/测试友好；主链一律显式传当前人设卡键）。"""
    return (str(card or "").strip() or "default")


def _bucket(st: dict, card: str | None) -> dict:
    """取某张人设卡的状态桶（不存在则建；v2 结构 = {"cards": {card: {kind: entry}}}）。"""
    cards = st.setdefault("cards", {})
    return cards.setdefault(_norm_card(card), {})


# ---------- 基础状态读写（进入/解除通道=agent 自决标记，见 apply_agent_markers） ----------

def set_state(kind: str, note: str = "", *, ttl: int | None = -1, by: str = "", card: str = "default") -> dict:
    """进入/更新一个特殊状态（调用方已做权限门控）。ttl=-1 用各 kind 默认；None=无时效。
    2026-09-09：开放 item:* 道具 kind；展示名写进 entry["label"]（已知 kind 用 LABELS，
    未知道具用冒号后段——debug/状态 等读侧 LABELS.get 不到时仍有可读展示）。
    2026-09-10 Phase 3（robot-pack-v1 item mod）：未知道具命中包登记的预置目录
    （add_item_catalog）时，label/note/ttl 用目录默认——目录项=给默认呈现，
    不改变开放性（开放性=allow_states 门控，照旧）；内置 kind（口球等）不受目录影响。
    2026-09-11 热修九：状态按人设卡分桶（card=当前卡键）——治跨卡泄漏（凯尔希的玩法
    状态不应出现在阿米娅的感知里）。
    2026-09-11 热修十一：药剂类道具 TTL 钳 12h（_item_ttl_policy；显式 ttl 同样受钳）。"""
    kind = str(kind or "").strip()
    if kind not in STATES and not (kind.startswith("item:") and len(kind) > len("item:")):
        return {}
    st = _load()
    b = _bucket(st, card)
    now = time.time()
    _cat = _ITEM_CATALOG.get(kind.split(":", 1)[1], {}) if kind.startswith("item:") else {}
    if ttl == -1:
        ttl = DEFAULT_TTL.get(kind, _cat.get("ttl"))  # 内置 kind 走 DEFAULT_TTL（含 None=无时效）；未知道具可用目录默认时效
    label = LABELS.get(kind) or _cat.get("label") or (kind.split(":", 1)[1].strip() if kind.startswith("item:") else "")
    # 2026-09-11 热修十一：药剂类 TTL 钳 12h（策略单一实现在 _item_ttl_policy；显式传入的 ttl
    # 同样受钳——「最多维持半天」是裁决口径，不因调用方传 None/超长而豁免）
    ttl = _item_ttl_policy(kind, label, ttl)
    # note 默认链：主人括号原文 > 内置道具默认 > 包目录默认 > 空（2026-09-10 顺手修：旧表达式
    # `note or ITEM_NOTES.get(kind)` 对未知道具曾把 None str 化成字面 "None" 进感知注入）
    _def_note = ((ITEM_NOTES.get(kind) or "") if kind.startswith("item:") else "") or (_cat.get("note") or "")
    entry: dict[str, Any] = {
        "note": str(note or _def_note).strip(),
        "label": label or kind,
        "activated_at": now,
        "by": str(by or ""),
    }
    if ttl:
        entry["expires_at"] = now + float(ttl)
    b[kind] = entry
    _save(st)
    return dict(entry)


def refresh(kind: str, *, ttl: int | None = -1, card: str = "default") -> bool:
    """顺延滑动窗（主人消息到达时对 hypno 等调用）；状态不存在返回 False。"""
    st = _load()
    b = _bucket(st, card)
    e = b.get(kind)
    if not e:
        return False
    if ttl == -1:
        ttl = DEFAULT_TTL.get(kind)
    # 2026-09-11 热修十一：药剂类顺延同样受 12h 上限钳制（策略与 set_state 同源）
    ttl = _item_ttl_policy(kind, str(e.get("label") or ""), ttl)
    if ttl:
        e["expires_at"] = time.time() + float(ttl)
        _save(st)
    return True


def clear(kind: str, card: str = "default") -> bool:
    """解除一个状态（解除标记/调用方）。不存在的 kind=无害 no-op。"""
    st = _load()
    b = _bucket(st, card)
    if kind in b:
        del b[kind]
        _save(st)
        return True
    return False


def normalize_ttls() -> dict:
    """存量药剂时效一次性归一（2026-09-11 热修十一，用户裁决；幂等——brain 启动时调用一次）。

    遍历全部卡桶：药剂类 item 条目（消毒名或 label 含「药」，与 _item_ttl_policy 同判据）
    统一按「activated_at + 12h」钳制——已有 expires_at 取 min（截短），没有则补上
    （无时效的存量药剂从此有终局，到期由 active() 惰性清除）。
    普通道具/内置 kind（口球/手铐/淫纹/震动棒/hypno/brainwash…）不受影响；
    重复调用收敛（min 幂等，二次调用零改动零写盘）。返回统计 dict（观测用）。
    """
    st = _load()
    now = time.time()
    clamped = stamped = 0
    for b in (st.get("cards") or {}).values():
        if not isinstance(b, dict):
            continue
        for kind, e in b.items():
            if not (isinstance(e, dict) and str(kind).startswith("item:")):
                continue
            if "药" not in str(kind).split(":", 1)[1] and "药" not in str(e.get("label") or ""):
                continue
            _act = float(e.get("activated_at") or 0) or now
            _cap = _act + float(_ITEM_TTL_MAX)
            if e.get("expires_at"):
                try:
                    _old = float(e["expires_at"])
                except (TypeError, ValueError):
                    continue
                _new = min(_old, _cap)
                if _new < _old - 1e-9:
                    e["expires_at"] = _new
                    clamped += 1
            else:
                e["expires_at"] = _cap
                stamped += 1
    if clamped or stamped:
        _save(st)
        logger.info("special normalize_ttls: clamped={} stamped={}", clamped, stamped)
    return {"clamped": clamped, "stamped": stamped}


def clear_all(card: str = "default") -> list[str]:
    """某张人设卡的全部解除（【解除：全部】标记 / debug 手动逃生口）。返回解除掉的 kind 列表。"""
    st = _load()
    b = _bucket(st, card)
    kinds = list(b.keys())
    if kinds:
        b.clear()
        _save(st)
    return kinds


def active(now: float | None = None, card: str = "default") -> dict[str, dict]:
    """当前有效状态快照（过期条目即时清除——惰性过期，无 tick 线程）。"""
    now = time.time() if now is None else now  # C11：显式判 None（`now or` 会把合法的 now=0 当缺省）
    st = _load()
    b = _bucket(st, card)
    expired = [k for k, e in b.items()
               if isinstance(e, dict) and e.get("expires_at") and float(e["expires_at"]) <= now]
    if expired:
        for k in expired:
            del b[k]
        _save(st)
    return {k: dict(v) for k, v in b.items() if isinstance(v, dict)}


def held_minutes(kind: str, now: float | None = None, card: str = "default") -> int:
    """某状态已持续多少分钟（感知事实「挂了多久」）；不存在返回 -1。"""
    e = active(now, card).get(kind)
    if not e:
        return -1
    return max(0, int((float(time.time() if now is None else now) - float(e.get("activated_at", 0))) // 60))  # C11：同 active 的显式判 None 口径


# ---------- 感知注入 ----------

def perception_text(now: float | None = None, only: set[str] | None = None, card: str = "default") -> str:
    """特殊状态 → 感知事实文本（给模型「此刻身上有什么、要求是什么、多久了」；
    演出怎么做全部 agent 自决，这里不给任何台词模板）。无状态返回空串。
    2026-09-07：only=非空集合时只注入这些 kind（主人专属状态不对其他对话者暴露详情——
    过滤由调用方按对话者身份决定，本层不做权限判定）。
    2026-09-11 热修九：状态按人设卡分桶（card=当前卡键）——跨卡零泄漏。"""
    try:
        act = active(now, card)
        if only is not None:
            act = {k: v for k, v in act.items() if k in only}
        if not act:
            return ""
        parts = []
        for kind, e in act.items():
            # 2026-09-09：开放道具 kind 的展示名在 entry["label"]（LABELS.get 不到）——优先取条目内 label
            label = str(e.get("label") or "").strip() or LABELS.get(kind, kind)
            # 2026-09-08：改走 held_minutes——曾内联重算同一表达式，孤儿 API 与实现双份漂移风险
            held = held_minutes(kind, now)
            line = f"· {label}"
            if e.get("note"):
                # 2026-09-08：vibe_mood 是 agent 自决状态——"主人要求的效果"用词错误
                # （读起来像主人下达的命令，且白名单会话也注入此文案）；按状态来源区分措辞。
                if kind == "vibe_mood":
                    line += f"——此刻氛围：{e['note']}。"
                else:
                    line += f"——主人要求的效果：{e['note']}"
            if held >= 1:
                line += f"（已持续约 {held} 分钟）"
            parts.append(line)
        return (
            "\n\n【特殊状态】你此刻身上正有这些状态（它们是事实，不是演出要求；"
            "此刻怎么表现、说不说破，全部由你按人设与现场自决）：\n" + "\n".join(parts)
        )
    except Exception:  # noqa: BLE001
        return ""


# ---------- agent 自决标记（2026-09-09：固定短语协议退役，特殊层级登记/解除并入标记系统） ----------

# 已知道具别名映射（保留：口球红线 kind=item:ball 依赖此口径；开放道具名经 sanitize_item_name 消毒）
_ITEM_WORDS = {
    "口球": "item:ball", "淫纹": "item:mark", "震动棒": "item:vibe",
}
# note 是主人括号原文、会整段进感知注入——钳到 80 字（超长=感知污染面，沿用旧口径）
_NOTE_CAP = 80
_HYPNO_DEFAULT_NOTE = "身体被强制听话（思想仍在）"
_HYPNO_HINT_PREFIX = "心理暗示（表面照常，心底被写入）："
_HYPNO_HINT_DEFAULT = "无条件服从主人"
_NOFAKE_NOTE = "收起伪装，露出真实态度"  # 2026-09-09：删「高傲」人设词（铁律#2，零人设假设）

# 登记标记：【催眠：效果要求】/【洗脑：要求】/【道具：名称】或【道具：名称：状态】/【不许装了】
# 内容段不设上限（[^】]* 自然以 】 截止）——超长标记也要剥净，钳长只由 _NOTE_CAP 在登记时做
_HYPNO_MARK_RE = re.compile(r"【\s*催眠\s*[：:]\s*([^】]*)\s*】")
_BW_MARK_RE = re.compile(r"【\s*洗脑\s*[：:]\s*([^】]*)\s*】")
_ITEM_MARK_RE = re.compile(r"【\s*道具\s*[：:]\s*([^】]*)\s*】")
_NOFAKE_MARK_RE = re.compile(r"【\s*不许装了?\s*】")
# 解除标记：【解除：催眠】/【解除：洗脑】/【解除：不许装】/【解除：道具：名称】/【解除：情欲氛围】/【解除：全部】
_CLEAR_MARK_RE = re.compile(r"【\s*解除\s*[：:]\s*([^】]*)\s*】")

# 切片标记混排别名（[BODY_OBEY]/[身体遵从]/【BODY_OBEY】/全半角括号混排；大小写不敏感）——
# 2026-09-09 切片泄漏修复：消费端只认全角中文形态，别名曾在所有剥点零命中 → 字面外发+回喂
_OBEY_ALIAS_RE = re.compile(r"[【\[]\s*(?:身体遵从|body[\s_\-]*obey)\s*[】\]]", re.IGNORECASE)
# 兜底剥净：全部状态/氛围/切片标记及英文别名残留（标记本不该出现在对外文本/落库文本里——fail-closed；
# 内容段同样不设上限——超长残留也要剥净）。2026-09-10 审计 P2 追加生活/场景/心情/约定/认真/计划族
# （剥除用途；登记路径在各剥点之前已消费完毕，见 brain 回复链顺序：消费→剥标记→落库/发送兜底）
_PROTOCOL_RESIDUE_RE = re.compile(
    r"[【\[]\s*(?:身体遵从|body[\s_\-]*obey|入迷|沉沦|清醒|回神|催眠|洗脑|道具|不许装了?|解除"
    r"|生活|场景|心情|约定|认真|计划)"
    r"[^】\]]*[】\]]",
    re.IGNORECASE,
)
# 未闭合残片兜底（2026-09-10 审计 P2）：【场景：天台 截断无收口时主正则吃不到——对新标记族做
# 收口可选剥除（既有族保持需闭合，"剥到串尾"的风险面不扩大）；32 字上限同 lifesim tick 残片剥除口径。
# 注意先跑主正则（闭合形态无长度上限优先整段剥净），再跑本正则清残片。
_PROTOCOL_RESIDUE_OPEN_RE = re.compile(r"[【\[]\s*(?:生活|场景|心情|约定|认真|计划)\s*[：:]?[^】\]]{0,32}】?")


def normalize_obey_marker(text: str) -> str:
    """切片标记混排别名 → 官方形态【身体遵从】（单一实现多处调用：切片拆分/落库/账本/历史注入）。"""
    return _OBEY_ALIAS_RE.sub("【身体遵从】", str(text or ""))


def strip_protocol_residue(text: str) -> str:
    """兜底剥净全部状态/氛围/切片标记及别名残留（含未闭合残片），返回净文本（发送前/落库前 fail-closed）。"""
    t = _PROTOCOL_RESIDUE_RE.sub("", normalize_obey_marker(str(text or "")))
    return _PROTOCOL_RESIDUE_OPEN_RE.sub("", t).strip()


def sanitize_item_name(raw: str) -> str:
    """开放道具名消毒：去空白与结构符（【】[]（）()：: 等）、限 8 字；空则返回空串（调用方忽略该标记）。"""
    name = re.sub(r"[\s【】\[\]（）()｛｝{}：:｜|、，,。.…·]+", "", str(raw or ""))
    return name[:8]


def _item_kind(raw: str) -> str:
    """道具名 → 状态 kind：已知名走别名映射（口球红线依赖），未知名消毒后开放登记；空名返回空串。"""
    name = str(raw or "").strip()
    if name in _ITEM_WORDS:
        return _ITEM_WORDS[name]
    clean = sanitize_item_name(name)
    return f"item:{clean}" if clean else ""


def _resolve_clear_target(target: str) -> str:
    """解除目标词 → 状态 kind（2026-09-11 热修十二 F1 抽取：apply_agent_markers 的解除标记与
    apply_owner_facts 的预解析 releases 共用同一映射，防两份口径漂移）。
    「全部」→ "*"（调用方走 clear_all）；无法识别/消毒后空名返回空串（调用方跳过）。"""
    t = str(target or "").strip()
    if not t:
        return ""
    if t == "全部":
        return "*"
    if t == "催眠":
        return "hypno"
    if t == "洗脑":
        return "brainwash"
    if t in ("不许装", "不许装了"):
        return "no_fake"
    if t in ("情欲氛围", "入迷", "氛围"):
        return "vibe_mood"
    return _item_kind(re.sub(r"^道\s*具[：:\s]*", "", t))


# ---------- 预置道具目录（robot-pack-v1 item mod，2026-09-10 Phase 3 接口预留+最小接线） ----------
# 包登记的道具 = 给默认呈现（label/note/ttl），不改变开放性——未登记道具照旧走开放集
# （消毒后登记），登记路径/账本/感知注入全部复用既有口径。运行期接线点：bot.py 启动时
# 遍历 packs（core.packs.items_index → 本函数）；bot.py 加载处幂等（同名覆盖）+失败隔离。
_ITEM_CATALOG: dict[str, dict] = {}  # 消毒名（item: 后段）→ {"label", "note", "ttl"}


def add_item_catalog(entries) -> list[str]:
    """登记预置道具目录（幂等：同名条目覆盖）。entries=[{"name","label","note","ttl_min"}]。
    校验（不合格条目跳过，返回登记成功名单）：name 经 sanitize_item_name 消毒非空、
    label 非空且 ≤12 字（合理长度）、ttl_min 为 >0 数字（无时效道具不必进目录——开放集默认即无时效）。
    只喂 set_state 的默认呈现，不改变开放性（开放性=allow_states 门控，照旧）。"""
    ok: list[str] = []
    for e in (entries or []):
        if not isinstance(e, dict):
            continue
        name = sanitize_item_name(e.get("name"))
        if not name:
            continue
        label = str(e.get("label") or "").strip()
        if not label or len(label) > 12:
            continue
        try:
            ttl_min = float(e.get("ttl_min") or 0)
        except (TypeError, ValueError):
            continue
        if ttl_min <= 0:
            continue
        _ITEM_CATALOG[name] = {
            "label": label[:12],
            "note": str(e.get("note") or "").strip()[:_NOTE_CAP],
            "ttl": int(ttl_min * 60),
        }
        ok.append(name)
    return ok


def _hypno_note(raw: str) -> str:
    """催眠 note：空→默认；以「心理暗示」开头→既有前缀形态（memory.build_context 同口径解析）。"""
    raw = str(raw or "").strip()
    if raw.startswith("心理暗示"):
        hint = raw[len("心理暗示"):].strip("：: ")
        return (_HYPNO_HINT_PREFIX + (hint or _HYPNO_HINT_DEFAULT))[:_NOTE_CAP]
    return (raw or _HYPNO_DEFAULT_NOTE)[:_NOTE_CAP]


def split_obey_slice(reply: str) -> tuple[str, str | None]:
    """催眠切片拆分（函数级，供 brain 发送链与 smoke 行为级断言）：
    归一混排别名 → 按【身体遵从】拆成（主体, 切片）；切片剥净后为空（孤标记退化）→ None。
    2026-09-09 盲审 P2：切片三守卫（别名归一/切片不带前缀/空切片不发）原为 brain 内联逻辑
    只能源码级断言——函数化后行为级可测（[BODY_OBEY] 别名外发/切片带前缀外发/孤标记外发三实锤的回归锚）。"""
    text = normalize_obey_marker(str(reply or ""))
    if "【身体遵从】" not in text:
        return text, None
    main_part, slice_part = text.split("【身体遵从】", 1)
    return main_part.strip(), (strip_protocol_residue(slice_part) or None)


# ---------- 情欲氛围自决标记（既有通道，照旧） ----------

# 2026-09-08：入场标记可携带档位（【入迷：暧昧】/【入迷：亲密】/【入迷：情欲】）——
# 深浅由 agent 声明、机器只按权限钳制（agent 自决优先）；无档位时保持旧行为（按权限上限登记）。
MOOD_ENTER_RE = re.compile(r"【\s*(?:入迷|沉沦)\s*(?:[：:]\s*([^】]{1,6}))?\s*】")
MOOD_EXIT_RE = re.compile(r"【清醒】|【回神】")

# 情欲氛围档位序（从浅到深；2026-09-07 用户裁决：触发权分级——调用方按对话者身份传入允许的最深档）
VIBE_TIERS = ("暧昧", "亲密", "情欲")


def vibe_tier(note: str) -> int:
    """氛围档位序号（1/2/3）；无法识别返回 0。"""
    n = str(note or "").strip()
    for i, t in enumerate(VIBE_TIERS, 1):
        if n == t:
            return i
    return 0


def apply_agent_markers(reply: str, by: str = "", max_tier: int | None = None,
                        allow_states: bool = False, card: str = "default") -> tuple[str, str | None]:
    """从 agent 回复中提取自决标记，剥除标记后返回 (净文本, 事件)。

    事件：\"enter\" / \"exit\" / None——仅指情欲氛围进出；特殊层级的登记/解除内部直接落账+日志。
    机器只登记，不判该不该进（那是 agent 的事）。
    2026-09-07：两标记并存时剥净全部、事件 exit 优先（曾 elif 只剥一个，另一个泄漏进消息）。
    2026-09-07 触发权分级：max_tier=None 主人档（登记为深层事实，行为同旧版）；
    max_tier=1/2 → enter 钳制到对应档位（如白名单最深到"亲密"）；max_tier=0 → 只剥标记不登记。
    2026-09-08 档位声明：标记可携带档位（【入迷：暧昧】/亲密/情欲）——agent 声明深浅，
    机器以声明的档位登记，再按 max_tier 钳制（声明的更深→钳到上限；声明的更浅→如实登记）。
    无档位声明 = 旧行为（按权限上限登记）。
    2026-09-09 特殊层级并入（固定短语协议退役）：allow_states=True（调用方传「私聊且主人」门控，
    机器侧权限钳制）时处理登记/解除标记族：【催眠：效果】【洗脑：要求】【道具：名称[：状态]】
    【不许装了】/【解除：…】/【解除：全部】；False（白名单/普通/群聊）一律只剥不登记不解除。
    多标记并存：全部处理、全部剥净；note 统一钳 80 字；【解除：情欲氛围】/【解除：全部】视同 vibe exit。
    2026-09-11 热修九：card=当前人设卡键——登记/解除/清空全部落在该卡的桶里（跨卡零泄漏）。"""
    text = normalize_obey_marker(str(reply or ""))
    event = None

    # ---- 特殊层级标记：先收集（登记在解除之后生效），再全部剥净；allow_states 才落账 ----
    clears: list[str] = []
    sets: list[tuple[str, str]] = []
    vibe_exit_by_clear = False
    for m in _CLEAR_MARK_RE.finditer(text):
        target = m.group(1).strip()
        if not target:
            continue
        kind = _resolve_clear_target(target)
        if kind:  # 无法识别的解除目标 → 跳过（映射口径单一实现，见 _resolve_clear_target）
            clears.append(kind)
    for m in _HYPNO_MARK_RE.finditer(text):
        sets.append(("hypno", _hypno_note(m.group(1))))
    for m in _BW_MARK_RE.finditer(text):
        sets.append(("brainwash", str(m.group(1) or "").strip()[:_NOTE_CAP] or "更听话"))
    for m in _ITEM_MARK_RE.finditer(text):
        parts = re.split(r"[：:]", str(m.group(1) or "").strip(), maxsplit=1)
        kind = _item_kind(parts[0])
        if kind:  # 消毒后空名 → 忽略该标记
            sets.append((kind, (parts[1].strip() if len(parts) > 1 else "")[:_NOTE_CAP]))
    if _NOFAKE_MARK_RE.search(text):
        sets.append(("no_fake", _NOFAKE_NOTE))
    if sets:
        # 观测（2026-09-10 道具 buff 复测）：标记解析结果与登记许可——区分"模型没输出标记"vs"输出了但 allow_states=False"
        logger.info("special: marks parsed {} (allow_states={})", [k for k, _ in sets], allow_states)
    text = _CLEAR_MARK_RE.sub("", text)
    text = _HYPNO_MARK_RE.sub("", text)
    text = _BW_MARK_RE.sub("", text)
    text = _ITEM_MARK_RE.sub("", text)
    text = _NOFAKE_MARK_RE.sub("", text)
    if allow_states and (clears or sets):
        for kind in clears:
            if kind == "*":
                if "vibe_mood" in active(card=card):
                    vibe_exit_by_clear = True
                _gone = clear_all(card)
                logger.info("special marker clear_all: kinds={} by={}", _gone, by)
            else:
                if kind == "vibe_mood":
                    vibe_exit_by_clear = True
                if clear(kind, card):
                    logger.info("special marker clear: kind={} by={}", kind, by)
        for kind, note in sets:
            set_state(kind, note, by=by, card=card)
            logger.info("special marker set: kind={} note={!r} by={}", kind, str(note)[:20], by)

    # ---- 情欲氛围（既有逻辑不变；解除标记清 vibe 视同 exit——进/退并存时 exit 优先一致） ----
    had_exit = bool(MOOD_EXIT_RE.search(text)) or vibe_exit_by_clear
    _m_enter = MOOD_ENTER_RE.search(text)
    had_enter = bool(_m_enter)
    _decl_note = str((_m_enter.group(1) or "").strip()) if _m_enter else ""
    text = MOOD_EXIT_RE.sub("", text)
    text = MOOD_ENTER_RE.sub("", text)
    if had_exit:
        event = "exit"
    elif had_enter:
        event = "enter"
    text = text.strip()
    if event == "enter":
        _cap = 3 if max_tier is None else max(0, min(3, int(max_tier)))
        if _cap <= 0:
            event = None  # 触发权管控：只剥不登记
        else:
            _decl = vibe_tier(_decl_note)  # agent 声明的档位（0=未声明/无法识别）
            _tier = min(_decl if _decl > 0 else 3, _cap)
            if _tier < 3:
                _note = VIBE_TIERS[_tier - 1]
            else:
                _note = "对话氛围正浓，情欲已被唤起"
            set_state("vibe_mood", _note, by=by, card=card)
    elif event == "exit":
        clear("vibe_mood", card)
    return text, event


# ---------- 主人括号事实·机器直接登记（2026-09-10 预解析管线） ----------
# 链路：主人私聊轮含全角括号 → brain 并行小 LLM 拆解括号内事实（plugins/brain.preparse_owner_facts）
# → stage2 注入约束块（core/reply）→ generate 正常返回后 brain 调用本函数落账。
# 与 apply_agent_markers（agent 自决标记）互补：这里是「主人括号=绝对事实」的机器直登通道——
# 复用同一套 kind/消毒/展示名口径（_item_kind/_hypno_note/_NOTE_CAP/set_state），不另立账本。

_OWNER_FACTS_ITEMS_MAX = 4  # 单轮登记道具数上限（预解析列表滥用防护）


def apply_owner_facts(facts: dict, by: str = "", card: str = "default") -> list[str]:
    """主人括号内事实 → 特殊层级状态登记/解除（brain 预解析管线；generate 正常返回才调用，抛异常不落账）。

    facts=预解析输出：{"hypno": "催眠效果或空", "brainwash": "洗脑要求或空",
    "items": [{"name": "道具名", "state": "状态或空"}], "releases": ["解除目标", ...]}。
    releases → 先清后登（镜像 apply_agent_markers「登记在解除之后生效」的既有顺序语义：
    同一轮内同 kind 先 clear 后 set）；目标解析与解除标记共用 _resolve_clear_target 同口径
    （全部→clear_all(card)；其余按映射表 clear；无法识别/消毒后空名跳过）。
    hypno/brainwash 非空 → set_state 对应 kind（note 走 _hypno_note/钳 80 字既有口径）；
    items → 逐个经 _item_kind 消毒映射（口球→item:ball 等已知名；未知名→开放 item:<消毒名>，
    展示名由 set_state 既有 label 逻辑落 entry）后 set_state(item:*, note=state)。
    空 facts/非 dict/字段全空 → 空列表 no-op。返回登记的 kind 列表（不变；cleared 只进日志）。
    2026-09-11 热修九：card=当前人设卡键（状态按卡分桶，跨卡零泄漏）。
    2026-09-11 热修十二 F1：补 releases 解除语义——此前通道只有登记语义，主人括号里的「解除」
    被反登记成新事实（误登记/漏解除根因）。"""
    if not isinstance(facts, dict):
        return []
    registered: list[str] = []
    cleared: list[str] = []
    _rels = facts.get("releases")
    _rel_seen = isinstance(_rels, list) and bool(_rels)
    if _rel_seen:  # 先清：同一轮内解除先于登记生效（解除不是新事实登记）
        for _r in _rels[:_OWNER_FACTS_ITEMS_MAX]:
            _kind = _resolve_clear_target(_r)
            if not _kind:
                continue
            if _kind == "*":
                cleared.extend(clear_all(card))
            elif clear(_kind, card):
                cleared.append(_kind)
    _hyp = str(facts.get("hypno") or "").strip()
    if _hyp:
        set_state("hypno", _hypno_note(_hyp), by=by, card=card)
        registered.append("hypno")
    _bw = str(facts.get("brainwash") or "").strip()
    if _bw:
        set_state("brainwash", _bw[:_NOTE_CAP], by=by, card=card)
        registered.append("brainwash")
    _items = facts.get("items")
    if isinstance(_items, list):
        for _it in _items[:_OWNER_FACTS_ITEMS_MAX]:
            if not isinstance(_it, dict):
                continue
            _kind = _item_kind(_it.get("name"))
            if not _kind:  # 消毒后空名 → 忽略该项（与 apply_agent_markers 同口径）
                continue
            set_state(_kind, str(_it.get("state") or "").strip()[:_NOTE_CAP], by=by, card=card)
            registered.append(_kind)
    if registered or cleared or _rel_seen:
        logger.info("special: owner facts applied: kinds={} cleared={} by={}", registered, cleared, by)
    return registered
