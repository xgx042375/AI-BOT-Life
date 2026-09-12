# -*- coding: utf-8 -*-
"""webgal.inject —— GAL 前端客户端合成事件注入层（批次1'，2026-09-09）。

spec：docs/archive/计划表-GAL前端客户端-2026-09-09.md §3.3 / §3.4 + 用户裁决 v2（〇之二）。

- build_event：构造 OneBot v11 PrivateMessageEvent（主人真实 user_id——页面与 QQ 同身份走同一
  brain 管线：记忆/好感/约定/特殊层级/生活状态全复用）。构造以"不抛"为准绳（nonebot 2.5.0 +
  adapter-onebot v11 实测必填项：time/self_id/post_type/sub_type/user_id/message_type/
  message_id/message/original_message/raw_message/font/sender；to_me 默认 False 需显式 True）。
- CaptureBot：Bot 子类，call_api 全捕获 → 翻译成段落捕获队列（webgal/__init__ 逐帧推 WS），
  **绝不透传真实 NapCat**（计划表 §五#4：web 通道不得触发任何 QQ 账号动作）：
    text 段 → 捕获 {"kind":"text"}（（动作）块照常作为文本捕获——gal 页面演斜体；chat 模式的
      剥除在 brain 发送侧已完成，页面收到的是剥离后文本，捕获层不再二次剥）；
    record 段 → 捕获 silk b64 但**不推帧**（v2 #4 实施取简：批次1' 页面语音走 POST /gal/tts
      client 逐行 POST，bot 不推 seg(voice)）；
    image 段 → 捕获但不推帧（sticker 段属批次2'，v2 #7 批次划分）；
    at/face/其他段 → 忽略；
    set_qq_profile / set_qq_avatar / set_input_status 等设置类与一切未知 API → 空操作（留日志）。
- 注入选型记录（计划表 §五#1 两路均验证过接口存在性）：主路 = nonebot.message.handle_event
  (capture_bot, ev)——nonebot 2.5.0 实测存在，内部建立 current_bot/current_event matcher 上下文，
  chat.send / matcher.send / bot.send 全部落到 CaptureBot；备选 = 手工 set contextvar 后直调
  brain.handle（仅当主路在本机实测不可用时启用，切换点在 webgal.__init__._PIPELINE_RUNNER）。
"""
from __future__ import annotations

import time

from loguru import logger
from nonebot.adapters.onebot.v11 import Bot, Message, PrivateMessageEvent
from nonebot.adapters.onebot.v11.event import Sender

# 合成事件发送者昵称（spec §3.3 定值；只作消息展示/落库 sender_name 用，与 bot 人设无关）。
# 模块常量集中一处，便于将来按裁决调整。
SENDER_NICKNAME = "博士"

# CaptureBot 的虚拟 self_id（v11 事件模型 self_id 注解为 int——必须数字串；
# 取 9 位单数字占位，永不与真实 QQ 号相同；仅 nonebot 内部日志/上下文用）。
CAPTURE_SELF_ID = "9"


def build_event(user_id: str, text: str, nickname: str | None = None,
                extra_segments: list | None = None) -> PrivateMessageEvent:
    """构造主人私聊合成事件（onebot v11）。字段以本机 adapter 模型必填项为准，构造不抛为准绳。

    2026-09-12（Telegram 通道接入）：加 nickname / extra_segments 两个**可选**参数（默认行为不变，
    §42 回归照旧）。理由：这份"必填字段清单"是踩出来的（少一个就构造失败），只允许存在**这一处**
    ——Telegram 通道复用它；复制一份出去，下次 nonebot/adapter 升级时必然漂移。
    extra_segments 供"带媒体的合成事件"（TG 的图/语音存在感知段）追加。
    """
    try:
        from nonebot import get_driver

        self_id = str(getattr(get_driver().config, "self_id", "") or "").strip()
        if not self_id.isdigit():
            self_id = CAPTURE_SELF_ID  # v11 事件模型 self_id 必须数字（int 注解）
    except Exception:  # noqa: BLE001
        self_id = CAPTURE_SELF_ID
    uid = int(str(user_id).strip())
    msg = Message(str(text or ""))
    for _seg in extra_segments or []:
        msg = msg + _seg
    return PrivateMessageEvent(
        time=int(time.time()),
        self_id=self_id,
        post_type="message",
        message_type="private",
        sub_type="friend",
        user_id=uid,
        message_id=-1,
        message=msg,
        original_message=msg,
        raw_message=str(text or ""),
        font=0,
        sender=Sender(nickname=nickname or SENDER_NICKNAME),
        to_me=True,
    )


def owner_uid() -> str:
    """主人 user_id：superusers 第一个（bot.py /launcher/persona 同款取法）。取不到返回空串。"""
    try:
        from plugins import debug as _dbg

        su = getattr(_dbg, "SUPERUSERS", None)
        uid = next(iter(su), None) if su else None
        if uid:
            return str(uid)
    except Exception:  # noqa: BLE001
        pass
    try:
        from nonebot import get_driver

        su = getattr(get_driver().config, "superusers", set()) or set()
        uid = next(iter(su), None)
        if uid:
            return str(uid)
    except Exception:  # noqa: BLE001
        pass
    return ""


_ADAPTER = None  # 模块级单例：Adapter.__init__ 每次都会向 FastAPI 追加注册 6 条路由（v11 _setup），
# 每轮新建=路由表与实例无界增长（2026-09-09 盲审 P2-1）——首建复用，未注册不收任何真实连接。


def new_capture_adapter():
    """CaptureBot 所需的 Adapter 实例（仅作 Bot 构造参数的持有者，未注册不收任何连接）。"""
    global _ADAPTER
    if _ADAPTER is None:
        from nonebot import get_driver
        from nonebot.adapters.onebot.v11 import Adapter

        _ADAPTER = Adapter(get_driver())
    return _ADAPTER


class CaptureBot(Bot):
    """全捕获 Bot：call_api 一律不触达真实协议端（计划表 §五#4 红线）。

    send_private_msg / send_msg / send_group_msg → 解析 Message 段进捕获队列；
    其余（set_qq_profile/set_qq_avatar/set_input_status/send_poke/未知 API）→ 空操作。
    捕获队列 `captured` 供 webgal.__init__ 在一轮结束后逐帧取（ [{"kind":...}, ...] ）。
    """

    def __init__(self, adapter=None, self_id: str = CAPTURE_SELF_ID):
        if adapter is None:
            adapter = new_capture_adapter()
        super().__init__(adapter, self_id)
        self.captured: list[dict] = []

    async def call_api(self, api: str, **data):  # noqa: ANN003
        """覆写：捕获发送类、空操作其余——绝不透传真实 NapCat。永不抛（web 通道故障只留日志）。"""
        try:
            if api in ("send_private_msg", "send_msg", "send_group_msg"):
                self._capture_message(data.get("message"))
            else:
                # 设置类（改资料/头像/正在输入/戳一戳等）与未知 API：空操作（web 通道零 QQ 动作）
                logger.debug("webgal capture no-op: api={}", api)
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("webgal capture failed: api={} [{}: {}]", api, type(e).__name__, e)
            return None

    def _capture_message(self, message) -> None:
        """Message → 捕获队列（文本推帧；voice/sticker 本批只捕获不推帧——见模块注释）。"""
        msg = message
        if isinstance(msg, str):
            msg = Message(msg)
        if msg is None:
            return
        for seg in msg:
            seg_type = str(getattr(seg, "type", "") or "")
            seg_data = getattr(seg, "data", None)
            seg_data = seg_data if isinstance(seg_data, dict) else {}
            if seg_type == "text":
                txt = str(seg_data.get("text") or "")
                if txt.strip():
                    self.captured.append({"kind": "text", "text": txt})
            elif seg_type == "record":
                # silk b64 捕获不推帧：批次1' 页面语音走 POST /gal/tts（v2 #4 实施取简定案）
                f = str(seg_data.get("file") or "")
                if f.startswith("base64://"):
                    self.captured.append({"kind": "voice_b64", "data": f[len("base64://"):], "push": False})
            elif seg_type == "image":
                # sticker/表情段批次2' 再推（v2 #7）；本批捕获留档不推帧
                self.captured.append({"kind": "sticker_b64", "data": str(seg_data.get("file") or ""), "push": False})
            # at / face / reply / 其他段：忽略


def is_capture_bot(bot) -> bool:
    """bot 是否合成 CaptureBot（gal 门放行判据——合成事件天然放行）。"""
    return isinstance(bot, CaptureBot)
