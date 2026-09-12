# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""情绪样本生成：POST 长驻 TTS 服务 → F3 EQ → 24k silk。"""
import subprocess, json, sys

import httpx

T = r"E:\robot\data\voice_tmp\ref_ab"
PKG = r"E:\robot\tools\GPT-SoVITS-v4-package\GPT-SoVITS-v4-20250529"
FFMPEG = PKG + r"\ffmpeg.exe"
EQ = ("afftdn=nf=-22,highpass=f=160,equalizer=f=500:t=q:w=1:g=2,"
      "highshelf=f=3200:g=2,lowpass=f=10000,volume=5.6,alimiter=limit=0.93:level=false")

TESTS = [
    ("wuyuA", "cv_E00080", PKG + r"\logs\odin6\5-wav32k\cv_E00080.wav",
     "……はぁ。……お主というやつには、呆れ果てて物も言えぬわ。"),
    ("wuyuB", "cv_E00440", T + r"\ref_E00440_head.wav",
     "……はぁ。……お主というやつには、呆れ果てて物も言えぬわ。"),
    ("gaoao", "cv_E00440", T + r"\ref_E00440_head.wav",
     "ふん。この地上で妾を敬わぬ者など、いまだ生まれておらぬ。"),
    ("hA", "cv_E00470", PKG + r"\logs\odin6\5-wav32k\cv_E00470.wav",
     "んっ……ふぅ……はぁ……くっ、抜け……！ 妾の身体が……熱くて……。"),
    ("hB", "cv_E00467", PKG + r"\logs\odin6\5-wav32k\cv_E00467.wav",
     "あ……うぅ……そこは……やめ……っ！ だめ、と言っているの……！"),
]

for tag, ref, rp, txt in TESTS:
    try:
        r = httpx.post(
            "http://127.0.0.1:8123/tts",
            json={"text": txt, "lang": "日文", "temp": 1.0, "ref": ref, "ref_path": rp},
            timeout=120,
        )
        if r.status_code != 200:
            print(f"[{tag}] HTTP {r.status_code}: {r.text[:120]}")
            continue
        wav = f"{T}\\emo2_{tag}.wav"
        open(wav, "wb").write(r.content)
        eqw = f"{T}\\emo2_{tag}_eq.wav"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", wav, "-af", EQ, eqw], check=True)
        silk = f"{T}\\emo2_{tag}.silk"
        sr = subprocess.run(["node", r"E:\robot\tools\wav2silk.js", eqw, silk], capture_output=True, text=True)
        if sr.returncode == 0:
            print(f"[{tag}] OK silk={__import__('os').path.getsize(silk)}B")
        else:
            print(f"[{tag}] silk fail: {sr.stdout[-200:]}")
    except Exception as e:  # noqa: BLE001
        print(f"[{tag}] ERR: {e}")
print("DONE")
