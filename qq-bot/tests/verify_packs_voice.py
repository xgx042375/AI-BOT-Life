# -*- coding: utf-8 -*-
"""verify_packs_voice —— dev_verify_example_packs.py 用的语音插件桩。

只借 plugins.voice 的策略函数（包音色/基调合并），不 import 真插件（那会拉起 TTS 依赖）。
"""
from __future__ import annotations

from core import packs as _packs

VOICES: dict = {"amiya": {"name": "内置音色（桩）"}}   # 内置表占位：验 setdefault 不覆盖
EMO_PARAMS: dict = {"战斗": (1.05, 1.08, 1.5)}         # 内置 9 类之一：验内置优先


def apply_pack_merges() -> None:
    """复刻 plugins/voice.apply_pack_merges 的合并纪律：包供音色/基调 setdefault，内置永远优先。"""
    for key, entry in (_packs.voice_index() or {}).items():
        VOICES.setdefault(str(key), entry)
    for tone in (_packs.tones_index() or []):
        word = str(tone.get("word") or "").strip()
        params = tone.get("voice") or {}
        if word and params and word not in EMO_PARAMS:
            try:
                EMO_PARAMS[word] = (float(params.get("temp", 1.0)),
                                    float(params.get("speed", 1.0)),
                                    float(params.get("semi", 0.0)))
            except (TypeError, ValueError):
                continue
