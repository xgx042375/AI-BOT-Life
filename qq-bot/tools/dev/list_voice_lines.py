# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""列出语料中目标角色的候选台词（按 9 类情绪挑选前的核对视图）。"""
import io
import sys

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
PQ = r"E:\robot\tools\GPT-SoVITS-v4-package\GPT-SoVITS-v4-20250529\voices\_arknights_voices.parquet"
TITLES = ("交谈1", "交谈2", "交谈3", "信赖提升后交谈1", "信赖提升后交谈2", "信赖提升后交谈3",
          "戳一下", "信赖触摸", "作战中1", "作战中2", "作战中3", "作战中4", "行动失败",
          "闲置", "问候", "进驻设施", "新年祝福", "周年庆典", "精英化晋升1", "精英化晋升2",
          "行动出发", "行动开始", "任命助理", "任命队长", "编入队伍", "干员报到", "晋升后交谈1")
IDS = ("char_003_kalts", "char_140_whitew", "char_002_amiya", "char_103_angel", "char_263_skadi")

df = pd.read_parquet(PQ)
for cid in IDS:
    s = df[df["char_id"] == cid]
    print(f"===== {cid}  ({len(s)} rows)")
    for t in TITLES:
        for _, row in s[s["voice_title"] == t].iterrows():
            dur = float(row["time"] or 0)
            txt = str(row["voice_text"])
            flag = "OK " if 3.0 <= dur <= 8.0 and 6 <= len(txt) <= 30 else "   "
            print(f"  {flag}[{t}] {dur:.1f}s {txt[:36]}")
