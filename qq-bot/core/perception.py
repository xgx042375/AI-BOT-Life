"""perception —— 感知装配层（2026-09-06 重写引入，Phase B）。

定位：把"喂给模型的事实"组装成注记文本。原则：
    - 本层只收**纯数据**（时间戳、数值、dict），不碰 nonebot/brain/memory 单例
      （依赖由调用方注入）→ 可独立单测，root 切换无副作用；
    - 输出是**事实注入**（感知），不是指令/拦截——行为规范永远在提示层措辞；
    - 返回 "" 表示无此注记（调用方跳过拼接）。

迁移状态（Phase B 进行中）：时间/关系/熟悉度/会话感知组已迁入；
brain 保留同签名薄壳转发（调用点零改动，Phase E 收割时删除）。
"""
from __future__ import annotations

import datetime as _dt
import re as _re
import time as _time

# 语言感知（2026-09-06：英/日文输入——事实注记；翻译与否由 bot 自决）
_LANG_KANA_RE = _re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_LANG_EN_RE = _re.compile(r"[A-Za-z]")
_LANG_CJK_RE = _re.compile(r"[\u4e00-\u9fff]")


def now_context(now: _dt.datetime | None = None) -> str:
    """当前时间注记（让模型正确理解时间，不答训练数据旧时间）。"""
    now = now or _dt.datetime.now()
    week = "一二三四五六日"[now.weekday()]
    return (
        f"【当前时间】{now.year}年{now.month}月{now.day}日 星期{week} "
        f"{now.hour:02d}:{now.minute:02d}（北京时间 UTC+8）。"
        "涉及时间、时效、最新信息时以此时间为准；模型训练数据中的旧时间/旧数据不代表现状。"
    )


def time_note(last_msg_ts: float | None, now: float | None = None) -> str:
    """时间事实：当前时刻 + 距上次交流时长（修"你怎么还没睡"式时间错位）。

    last_msg_ts：与对方最近一条消息的 epoch 秒（无记录传 None）。
    """
    now = now if now is not None else _time.time()
    try:
        local = _dt.datetime.now()
        parts = [
            f"现在是 {local.month}月{local.day}日 "
            f"周{'一二三四五六日'[local.weekday()]} {local.hour:02d}:{local.minute:02d}"
        ]
        if last_msg_ts:
            ago_s = now - last_msg_ts
            if ago_s > 3600 * 4:
                parts.append(f"距你们上次说话约 {ago_s / 3600:.0f} 小时——隔了一夜/很久，是新的一天了")
            elif ago_s > 3600:
                parts.append(f"距你们上次说话约 {ago_s / 3600:.0f} 小时")
            elif ago_s > 60:
                parts.append(f"距你们上次说话约 {int(ago_s // 60)} 分钟")
        return "\n\n【时间】" + "；".join(parts)
    except Exception:  # noqa: BLE001
        return ""


def familiar_note(intimacy: float, relation_updated_ts: str | None) -> str:
    """熟悉度连续注记：好感数值 + 最近互动时间（连续事实，随相处平滑变化）。"""
    ago = ""
    if relation_updated_ts:
        try:
            last = _dt.datetime.fromisoformat(str(relation_updated_ts)).astimezone()
            d = (_dt.datetime.now(_dt.timezone.utc).astimezone() - last).total_seconds() / 86400
            ago = "（今天聊过）" if abs(d) < 1 else f"（上次聊天约 {max(1, int(d))} 天前）"
        except Exception:  # noqa: BLE001
            pass
    return f"【关系】你对对方的好感 {intimacy:.0f}/100{ago}——随相处自然升温，不用刻意套近乎或故意疏远。"


def timeflow_note(last_user_ts: float | None, now: _dt.datetime | None = None) -> str:
    """时间流动注记：距上次对话多久 + 隔夜/久别的苏醒感知
    （处理"长时间没回/睡着/醒来"情景；last_user_ts=None 或间隔<2 分钟返回空）。"""
    if not last_user_ts:
        return ""
    now = now or _dt.datetime.now()
    gap_min = max(0, int((now.timestamp() - last_user_ts) / 60))
    if gap_min < 2:
        return ""
    parts = [f"距上次对话已过去 {gap_min} 分钟"]
    if gap_min >= 60:
        h = gap_min // 60
        parts[0] = f"距上次对话已过去 {h} 小时" + (f"{gap_min % 60} 分钟" if gap_min % 60 else "")
    if gap_min >= 240:
        if 5 <= now.hour <= 10:
            parts.append("现在是清晨——对方大概是**刚睡醒**（或整夜没回）：你是被晾了一夜/刚醒的状态，清晨起床般地回应（慵懒/有起床气/轻声）。")
        elif now.hour >= 22 or now.hour < 4:
            parts.append("现在已是深夜——你在清醒着等/半夜被吵醒的情境，回复带困意或警惕。")
        elif 11 <= now.hour <= 14:
            parts.append("已到大中午——对方隔了很久才出现（也许在忙/睡过头），回应带点「久违了」的口气。")
        elif 15 <= now.hour <= 21:
            parts.append("隔了几个小时才重新出现——自然的「你终于来了/忙完了？」承接感。")
    return "【时间流动】" + "；".join(parts) + "——按上述时间距离自然调整语气，不要假装刚聊过，也不要说破具体数字。"


def convo_note(recent_priv: dict, user_id: str, now: float | None = None) -> str:
    """多会话"一个人"感知（私聊注入）：最近 1 小时还有其他私聊在场。

    recent_priv：{user_id: 最近私聊 epoch 秒}（调用方持有，本层不改）。
    """
    now = now if now is not None else _time.time()
    others = [u for u, t in (recent_priv or {}).items() if u != str(user_id) and now - t < 3600]
    if not others:
        return ""
    latest = max(others, key=lambda u: (recent_priv or {}).get(u, 0))
    ago_m = max(1, int((now - recent_priv[latest]) // 60))
    return (
        f"\n\n【会话感知】你心里有数：{ago_m} 分钟前你还在和另一个人私聊（这边窗口又有人来了）。"
        "你是同一个人——但两边对话互相不知道，你也别把那边的事带过来；"
        "你可以自然体现『刚切换过来/还在忙别的』的感觉，也可以完全正常应对，按你此刻怎么想。"
    )


def lang_note(text: str) -> str:
    """当前消息语言注记：日语/英语为主时返回事实说明；纯中文返回空串。
    只把「这是外文」摆在眼前，如何回应由 bot 自决（不做关键词触发翻译）。"""
    try:
        t = (text or "").strip()
        if not t:
            return ""
        kana = len(_LANG_KANA_RE.findall(t))
        if kana >= 2:
            return "本条消息主要是日语——对方在用日文跟你说话，先读懂意思再回应。"
        latin = len(_LANG_EN_RE.findall(t))
        cjk = len(_LANG_CJK_RE.findall(t))
        if latin >= 6 and latin > cjk * 2:
            return "本条消息主要是英语——对方在用英文跟你说话，先读懂意思再回应。"
    except Exception:  # noqa: BLE001
        pass
    return ""


def msg_struct_note(at_ids: list[str], self_id: str, has_reply: bool) -> str:
    """消息结构注记：@ 指向（我/他人）与引用（感知层不得只见文本不见结构）。"""
    try:
        ats = [str(a or "") for a in (at_ids or [])]
        notes = []
        if any(a == str(self_id or "") for a in ats):
            notes.append("本条消息 @ 了你（bot）")
        elif any(a for a in ats if a not in (str(self_id or ""), "all", "")):
            notes.append("本条消息 @ 了群里其他人")
        if has_reply:
            notes.append("本条消息引用了别人的话——按被引用者的指向理解")
        return "；".join(notes)
    except Exception:  # noqa: BLE001
        return ""


def meme_note(text: str, memes: dict) -> str:
    """梗知识库注记：消息命中梗词条 → 注入梗义/接法（接不接由 agent 自决）。
    memes：{梗: {meaning, take}}（读取与缓存由调用方负责）。"""
    try:
        hits = []
        for k, v in (memes or {}).items():
            if k and k in (text or ""):
                hits.append(f"「{k}」：{(v or {}).get('meaning', '')}（要接可以说：{(v or {}).get('take', '')}）")
        if hits:
            return "（群里正好在玩梗：" + "；".join(hits[:2]) + "——你用不用由你自己判断，想说就自然接）"
        return ""
    except Exception:  # noqa: BLE001
        return ""


def _persona_names(card: dict, persona_name: str, alias_map: dict) -> set[str]:
    """当前人设的全部可被点名的名字（卡名/昵称 + 别名表中指向当前卡的所有别名）。"""
    names = {str((card or {}).get("name") or ""), str((card or {}).get("nickname") or "")}
    for alias, key in (alias_map or {}).items():
        if key == persona_name:
            names.add(alias)
    return {n for n in names if len(n) >= 2}


def name_mention_note(text: str, names: set[str]) -> str:
    """人设名提及感知：消息文本里出现当前人设名（非 @）——弱感知，接不接由 agent 自决。"""
    try:
        hit = [n for n in (names or set()) if n in (text or "")]
        if hit:
            return f"（有人在消息里提到了你的名字「{hit[0]}」——也许是在叫你；接不接、怎么接，你自己判断。）"
    except Exception:  # noqa: BLE001
        pass
    return ""


def text_at_me(text: str, names: set[str]) -> bool:
    """文本形式点名判定：消息里出现 @+当前人设名/别名（对方机器人常用文本 @，is_tome 识别不到）。"""
    try:
        return any(("@" + n) in (text or "") for n in (names or set()))
    except Exception:  # noqa: BLE001
        return False
