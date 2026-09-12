# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""训练进度监控：自动检测活动训练目录（A 角色 LoRA / B 表情差分 LoRA），从 checkpoint 推算进度。"""
import os
import re
from pathlib import Path
from datetime import datetime

CANDIDATES = [Path(r"E:\robot\anima_char_lora_v3"), Path(r"E:\robot\anima_emote_lora")]
OUT = Path(r"E:\robot\data\train_progress.txt")


def pick_active() -> tuple[Path, list, int]:
    """选最近有 checkpoint 更新的目录；无 checkpoint 用目录 mtime。"""
    best, best_ts = None, 0
    for base in CANDIDATES:
        cps = list(base.rglob("*.safetensors"))
        ts = max((f.stat().st_mtime for f in cps), default=base.stat().st_mtime if base.exists() else 0)
        if ts > best_ts:
            best, best_ts, best_cps = base, ts, cps
    steps = []
    for f in best_cps if best else []:
        m = re.search(r"(\d+)\.safetensors$", f.name)
        if m:
            steps.append((int(m.group(1)), f.stat().st_mtime))
    steps.sort()
    return best or CANDIDATES[0], steps, (3000 if "v3" in str(best) else 1500)


def main():
    base, steps, total = pick_active()
    now = datetime.now().timestamp()
    lines = [f"训练目录: {base.name}（目标 {total} 步）"]
    if not steps:
        lines.append("checkpoint: 暂无（仍在首批 300 步前）")
    else:
        max_step, ts = steps[-1]
        lines.append(f"最新 step: {max_step}/{total}")
        if len(steps) >= 2:
            prev_step, prev_ts = steps[-2]
            spd = (max_step - prev_step) / max(1, (ts - prev_ts) / 60)
            remain = (total - max_step) / spd if spd > 0 else 0
            lines.append(f"速度: ~{spd:.0f} 步/8min → 预计剩余 {remain:.0f} 分钟")
        for s, mtime in steps[-3:]:
            lines.append(f"  {s} 步 @ {datetime.fromtimestamp(mtime).strftime('%H:%M:%S')}")
    try:
        import subprocess

        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        lines.append(f"GPU: {gpu}")
    except Exception:  # noqa: BLE001
        pass
    text = f"--- 训练进度 {datetime.now().strftime('%H:%M:%S')} ---\n" + "\n".join(lines) + "\n"
    OUT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
