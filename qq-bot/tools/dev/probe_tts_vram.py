# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
r"""探针：实测当前 GPT-SoVITS v4 worker 的显存/内存足迹（不常驻，跑完自杀）。

用法： runtime\python.exe E:\robot\tools\probe_tts_vram.py <voice_key>
输出：每阶段 nvidia-smi 采样 + 单次合成耗时 + 峰值。
"""
import json
import os
import subprocess
import sys
import threading
import time

PKG = r"E:\robot\tools\GPT-SoVITS-v4-package\GPT-SoVITS-v4-20250529"
VOICES = {
    "kaltsit": ("voices\\kaltsit\\凯尔希-e10.ckpt", "voices\\kaltsit\\凯尔希_e10_s160_l32.pth",
                "voices\\kaltsit\\refs", "voice_refs.json"),
    "amiya": ("voices\\amiya\\阿米娅-e10.ckpt", "voices\\amiya\\阿米娅_e10_s230_l32.pth",
              "voices\\amiya\\refs", "voice_refs.json"),
}


def gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15).stdout.strip()
        used, util = out.split(",")
        return int(used), int(util)
    except Exception:
        return -1, -1


def main(key: str) -> int:
    gpt, sovits, refdir, refsf = VOICES[key]
    env = {
        **os.environ,
        "gpt_path": os.path.join(PKG, gpt),
        "sovits_path": os.path.join(PKG, sovits),
        "voice_ref_dir": os.path.join(PKG, refdir),
        "voice_ref_texts": os.path.join(PKG, refdir, refsf),
        "CUDA_VISIBLE_DEVICES": "0",
        "HF_HUB_OFFLINE": "1",
    }
    print(f"[base] gpu used={gpu()[0]}MiB")
    p = subprocess.Popen(
        [os.path.join(PKG, "runtime", "python.exe"), "-u", "voice_tts_worker_daemon.py"],
        cwd=PKG, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    peak = 0

    def sampler(stop):
        nonlocal peak
        while not stop.is_set():
            u, _ = gpu()
            peak = max(peak, u)
            time.sleep(0.4)

    stop = threading.Event()
    th = threading.Thread(target=sampler, args=(stop,), daemon=True)
    th.start()

    def call(text, rid, **kw):
        req = {"id": rid, "text": text, "lang": "中文", "ref": kw.pop("ref", "默认"),
               "ref_path": "", "temp": 0.95, "speed": 1.0, "prompt_lang": "中文", **kw}
        t0 = time.time()
        p.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        p.stdin.flush()
        line = p.stdout.readline()
        dt = time.time() - t0
        if not line:
            return None, dt, 0
        out = json.loads(line.decode("utf-8"))
        return out, dt, len(out.get("wav_b64", ""))

    try:
        u0, _ = gpu()
        print(f"[after spawn] gpu={u0}MiB")
        out, dt, n = call("你好，博士。", "1")
        print(f"[1st call] ok={out.get('ok')} cost={out.get('cost')} wall={dt:.1f}s gpu={gpu()[0]}MiB")
        out, dt, n = call("天气不错，我们出去走走吧。", "2")
        print(f"[2nd call] ok={out.get('ok')} cost={out.get('cost')} wall={dt:.1f}s gpu={gpu()[0]}MiB")
        out, dt, n = call("越是强大越是脆弱，这就是万物的道理。", "3",
                          ref="精英化晋升1", temp=0.75, speed=0.92, topp=0.8)
        print(f"[3rd deep-emo] ok={out.get('ok')} cost={out.get('cost')} wall={dt:.1f}s gpu={gpu()[0]}MiB")
        time.sleep(1.5)
        print(f"[steady] gpu={gpu()[0]}MiB  PEAK={peak}MiB")
    finally:
        stop.set()
        try:
            p.stdin.close()
        except Exception:
            pass
        p.kill()
        p.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "kaltsit"))
