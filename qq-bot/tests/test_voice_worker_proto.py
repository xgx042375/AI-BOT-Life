# -*- coding: utf-8 -*-
"""无 HTTP 常驻 worker 协议回归测试：spawn → 2 次合成 → 校验 wav → 杀进程。

与 bot 插件同协议（JSON-lines over stdin/stdout），用系统 python 直接驱动（不依赖 nonebot）。
"""
import base64
import json
import os
import subprocess
import sys
import time

PKG = r"E:\robot\tools\GPT-SoVITS-v4-package\GPT-SoVITS-v4-20250529"

env = {
    **os.environ,
    "gpt_path": os.path.join(PKG, "GPT_weights_v4", "odin7-e15.ckpt"),
    "sovits_path": os.path.join(PKG, "SoVITS_weights_v4", "odin7_e10_s390_l64.pth"),
    "voice_ref_dir": os.path.join(PKG, "logs", "odin6", "5-wav32k"),
    "CUDA_VISIBLE_DEVICES": "0",
    "HF_HUB_OFFLINE": "1",
}

p = subprocess.Popen(
    [os.path.join(PKG, "runtime", "python.exe"), "-u", "voice_tts_worker_daemon.py"],
    cwd=PKG, env=env,
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
)


def call(txt: str, rid: str):
    req = json.dumps(
        {"id": rid, "text": txt, "lang": "日文", "ref": "cv_E00440", "ref_path": "",
         "temp": 0.9, "speed": 1.0},
        ensure_ascii=False,
    )
    p.stdin.write((req + "\n").encode("utf-8"))
    p.stdin.flush()
    line = p.stdout.readline()
    out = json.loads(line.decode("utf-8"))
    wav = base64.b64decode(out.get("wav_b64", "")) if out.get("ok") else b""
    return out, wav


try:
    ok = False
    t0 = time.time()
    out1, w1 = call("こんにちは、博士。", "1")
    print(f"call1 ok={out1.get('ok')} cost={out1.get('cost')} wav={len(w1)}B wall={time.time()-t0:.1f}s")
    out2, w2 = call("おはよう、今日はいい天気だね。", "2")
    print(f"call2 ok={out2.get('ok')} cost={out2.get('cost')} wav={len(w2)}B")
    ok = out1.get("ok") and out2.get("ok") and len(w1) > 20000 and len(w2) > 20000
    print("RESULT:", "PASS" if ok else "FAIL")
finally:
    p.kill()
    sys.exit(0 if ok else 1)
