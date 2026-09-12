# -*- coding: utf-8 -*-
r"""harness/driver —— 事件桩 + DummyBot + 状态夹具（驱动完整 handle）。"""
import asyncio
import json
import os
import time
from pathlib import Path

import nonebot  # noqa: F401  触发插件环境

from core import atomics
from core import special as _special
from core.paths import DATA_ROOT

STATE_FILES = [
    "life_state.json", "special_state.json", "persona_select.json",
    "intimate_whitelist.json", "persona_mode.json", "special_state.json.old",
]


def _first_superuser() -> int:
    r"""OWNER_ID 参数化（2026-09-10 P2）：os.environ / qq-bot\.env 的 SUPERUSERS（JSON 数组）
    取首个；缺失或解析失败回落本机主理人号。"""
    raw = os.environ.get("SUPERUSERS", "")
    if not raw:
        try:
            envf = Path(__file__).resolve().parent.parent / ".env"
            for _line in envf.read_text(encoding="utf-8-sig").splitlines():
                _s = _line.strip()
                if _s.startswith("SUPERUSERS") and "=" in _s:
                    raw = _s.split("=", 1)[1].strip()
                    break
        except OSError:
            raw = ""
    try:
        _arr = json.loads(raw)
        if isinstance(_arr, list) and _arr:
            return int(_arr[0])
    except (ValueError, TypeError):
        pass  # 兜底：读不到 / 不是整数 → 用下面那行的**占位**身份
    # 占位号（与 .env.example 的 SUPERUSERS=["10001"] 同款）；真实身份走 env/.env SUPERUSERS。
    # 2026-09-12 隐私修复：这里原先回落的是**主人真号**，而本文件随 Core 包发行 → 号跟着包出门。
    # 审计 O 项现已机器盯住这类"发行物带本机身份"的泄漏（值从 .env 运行时派生，不硬编码）。
    return 10001


OWNER_ID = _first_superuser()  # 主理人 QQ（优先 env SUPERUSERS 首个；与 cfg.superusers 一致；用例主要以 owner 身份跑）


class DummyBot:
    """接收 bot 交互的假 Bot：发送录音、未知 API no-op（可观测、不炸链）。"""

    def __init__(self):
        self.sent = []          # 发送的消息体（send 调用收集）
        self.sent_meta = []     # (event 类型, message, kwargs)
        self.calls = []         # 其它 API 调用记录（set_qq_profile 等）

    async def send(self, event, message, **kwargs):
        self.sent.append(message if isinstance(message, str) else str(message))
        self.sent_meta.append((type(event).__name__, str(message), kwargs))

    def __getattr__(self, name):
        # brain 可能调 bot.set_qq_profile / bot.call_api / set_group_card 等——录制 no-op
        async def _rec(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None
        return _rec


def build_event(kind: str, text: str, user_id: int = OWNER_ID, group_id: int = 22222222):
    """构造 onebot v11 事件桩（私聊/群聊）。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent, Message

    if kind == "group":
        return GroupMessageEvent(
            time=int(time.time()), self_id=11111111, post_type="message",
            message_type="group", sub_type="normal", message_id=1,
            group_id=group_id, user_id=user_id,
            message=Message(text), raw_message=text, font=0,
            sender={"user_id": user_id, "nickname": "测", "role": "member"},
        )
    return PrivateMessageEvent(
        time=int(time.time()), self_id=11111111, post_type="message",
        message_type="private", sub_type="friend", message_id=2, user_id=user_id,
        message=Message(text), raw_message=text, font=0,
        sender={"user_id": user_id, "nickname": "测", "card": "", "role": "member",
                "sex": "unknown", "age": 0},
    )


class StateFixture:
    """状态夹具：备份→写入→恢复（绝不动真实状态）。"""

    def __init__(self, patch: dict):
        self.patch = patch or {}
        self._bak = {}

    def __enter__(self):
        for name in STATE_FILES:
            p = DATA_ROOT / name
            if p.exists():
                self._bak[name] = p.read_bytes()
        for name, data in self.patch.items():
            # 2026-09-12 审计修复：夹具写的是**真实状态文件**（DATA_ROOT 下），原先走裸
            # write_text——进程中途被杀会留下半截 JSON，而读侧一律"损坏→默认值"，
            # 等于一次崩溃静默清零用户状态（正是 core/atomics 模块 docstring 记的那类事故）。
            atomics.write_json_atomic(DATA_ROOT / name, data)
        _special._STATE = None  # 清进程内缓存（读文件为准）
        return self

    def __exit__(self, *exc):
        for name, raw in self._bak.items():
            atomics.write_bytes_atomic(DATA_ROOT / name, raw)
        for name in self.patch:
            if name not in self._bak:
                p = DATA_ROOT / name
                if p.exists():
                    p.unlink()
        _special._STATE = None

    @staticmethod
    def vibe(note: str = "暧昧"):
        return {"special_state.json": {"vibe_mood": {
            "note": note, "activated_at": time.time(), "by": "harness", "expires_at": None}}}

    @staticmethod
    def hypno(note: str = "身体被强制听话"):
        return {"special_state.json": {"hypno": {
            "note": note, "activated_at": time.time(), "by": "harness", "expires_at": time.time() + 3600}}}


def run_scene(kind: str, text: str, patch: dict | None = None, user_id: int = OWNER_ID) -> dict:
    """驱动一次完整消息处理（同步入口）。返回 {reply, sent, calls, state}。"""
    from plugins import brain as _brain

    bot = DummyBot()
    ev = build_event(kind, text, user_id=user_id)
    with StateFixture(patch or {}):
        try:
            asyncio.run(_brain.handle(bot, ev))
        except RuntimeError:
            # handle 内可能 create_task 需要运行中的 loop——用已在事件循环的 wrapper
            async def _w():
                await _brain.handle(bot, ev)
            asyncio.run(_w())

    return {
        "reply": (bot.sent[-1] if bot.sent else ""),
        "sent": bot.sent,
        "calls": bot.calls,
        "state": json.loads((DATA_ROOT / "state").read_text()) if False else None,
    }
