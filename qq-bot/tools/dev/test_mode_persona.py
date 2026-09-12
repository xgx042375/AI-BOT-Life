# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""验证恶堕模式提示词注入（独立脚本，不依赖 nonebot 运行时）。"""
import json, sys

sys.path.insert(0, r"E:\robot\qq-bot")
from plugins.persona import build_system_prompt, load_persona  # noqa: E402

card = load_persona("_aoding_")
alt = card.get("alt_persona") or {}
print("keyword:", repr(alt.get("keyword")))
print("alt desc 前40:", repr((alt.get("description") or "")[:40]))
print("alt rules 前40:", repr((alt.get("response_rules") or "")[:40]))

p0 = build_system_prompt(card, "【记忆】测试", mode=0)
p1 = build_system_prompt(card, "【记忆】测试", mode=1)
print("\n--- mode=0 人设段 ---")
print(p0[:180])
print("\n--- mode=1 人设段 ---")
print(p1[:180])
print("\nmode=1 含恶堕:", "恶堕" in p1, "| mode=0 含恶堕:", "恶堕" in p0)
