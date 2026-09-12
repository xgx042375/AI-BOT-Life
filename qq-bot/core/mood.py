"""mood —— 全局心情（2026-09-06，G2 用户裁决）。

心情 = bot 的一份全局属性（不是每人一份）。更新通道：
    1. agent 自标【心情：…】标记（reply 生成时透传回来，core.reply 已剥除）——零额外调用；
    2. 生活心跳的"向平静漂移"回归（agent/lifesim._mood_drift，自然衰减）。
存储：与生活状态同文件（life_state.json 的 mood/mood_reason/mood_source/mood_ts 字段），
    perception 经 lifesim.life_text 注入"此刻的你"——跨会话同一份心情。
"""
from __future__ import annotations

import json
import time

from loguru import logger

from core import atomics
from core.paths import data_path

_STATE_FILE = data_path("life_state.json")


def _read_state() -> dict | None:
    """读生活状态文件；损坏 → 隔离改名（.corrupt 留取证）并返回 None（调用方拒绝写）。
    C10：曾走 atomics.read_json(default={})——坏文件被吞成空 dict 后本模块只补 mood 字段
    原子写回，doing/ts/scene 等其余字段随写即清（生活状态静默清零）。"""
    try:
        st = json.loads(_STATE_FILE.read_text(encoding="utf-8-sig"))
        return st if isinstance(st, dict) else None
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning("life_state corrupt in mood.apply, quarantining: {} [{}]", e, type(e).__name__)
        atomics.quarantine_corrupt(_STATE_FILE)
        return None


def apply(mood: str | None, reason: str = "", source: str = "") -> str | None:
    """登记 agent 自标的心情（剥标记后由 reply 管线透传）。mood 空则忽略。"""
    mood = (mood or "").strip()
    if not mood:
        return None
    try:
        st = _read_state()
        if st is None:
            return None  # C10：损坏已隔离，拒绝在坏文件上覆写
        st["mood"] = mood[:10]
        st["mood_reason"] = (reason or "")[:60]
        st["mood_source"] = (source or "")[:40]
        st["mood_ts"] = time.time()
        atomics.write_json_atomic(_STATE_FILE, st)
        return mood
    except Exception:  # noqa: BLE001
        return None
