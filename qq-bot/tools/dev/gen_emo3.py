# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""情绪对照测试：同一句台词 × 5 种情绪参数（参考+温度+语速）→ 24k silk 推送。"""
import subprocess, os

import httpx

T = r"E:\robot\data\voice_tmp\ref_ab"
PKG = r"E:\robot\tools\GPT-SoVITS-v4-package\GPT-SoVITS-v4-20250529"
FFMPEG = PKG + r"\ffmpeg.exe"
EQ = ("afftdn=nf=-22,highpass=f=160,equalizer=f=500:t=q:w=1:g=2,"
      "highshelf=f=3200:g=2,lowpass=f=10000,volume=5.6,alimiter=limit=0.93:level=false")
LINE = "ふん……よいか。妾のそばに来い。"

CASES = [
    ("gaoleng", "cv_E00440", T + r"\ref_E00440_head.wav", 1.00, 1.00),
    ("jifeng", "cv_E00437", T + r"\ref_E00437_head.wav", 1.00, 1.04),
    ("zhandou", "cv_E00065", PKG + r"\logs\odin6\5-wav32k\cv_E00065.wav", 1.05, 1.10),
    ("wenrou", "cv_E00474", PKG + r"\logs\odin6\5-wav32k\cv_E00474.wav", 0.90, 0.92),
    ("wuyu", "cv_E00080", PKG + r"\logs\odin6\5-wav32k\cv_E00080.wav", 0.90, 0.90),
]

for tag, ref, rp, temp, speed in CASES:
    try:
        r = httpx.post(
            "http://127.0.0.1:8123/tts",
            json={"text": LINE, "lang": "日文", "temp": temp, "speed": speed, "ref": ref, "ref_path": rp},
            timeout=180,
        )
        if r.status_code != 200:
            print(f"[{tag}] HTTP {r.status_code}: {r.text[:120]}")
            continue
        wav = f"{T}\\emo3_{tag}.wav"
        open(wav, "wb").write(r.content)
        eqw = f"{T}\\emo3_{tag}_eq.wav"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", wav, "-af", EQ, eqw], check=True)
        silk = f"{T}\\emo3_{tag}.silk"
        sr = subprocess.run(["node", r"E:\robot\tools\wav2silk.js", eqw, silk], capture_output=True, text=True)
        if sr.returncode == 0:
            print(f"[{tag}] OK silk={os.path.getsize(silk)}B dur={len(r.content) / 96000:.1f}s")
        else:
            print(f"[{tag}] silk fail: {sr.stdout[-200:]}")
    except Exception as e:  # noqa: BLE001
        print(f"[{tag}] ERR: {e}")
print("DONE")
