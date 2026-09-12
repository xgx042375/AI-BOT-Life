# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
# 用法：cd qq-bot && .venv\Scripts\python.exe tools\dev\verify_data_plugins.py
# 副作用：临时建 data/plugins 夹具并在结束时删除；**目标目录已存在则拒绝运行**（不碰用户数据）。
# -*- coding: utf-8 -*-
"""验证 data/plugins/ 自动发现（2026-09-12 S3）——**跑完自清理，不留残留**。

要证明的三件事：
  ① 子包与单文件 .py 两种形态都能被装上；
  ② **导入即抛的坏插件不得带崩 bot**（隔离失败、记 warning、继续）；
  ③ 非包目录（无 __init__.py）被安静跳过。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

ROBOT = pathlib.Path(r"E:\robot")
QQBOT = ROBOT / "qq-bot"
VENV_PY = QQBOT / ".venv" / "Scripts" / "python.exe"
PLUGINS = ROBOT / "data" / "plugins"

PROBE = r'''
import json, nonebot, bot  # noqa: F401  import bot 触发全部装载路径（run() 在 __main__ 下，不会起服务）
names = sorted(p.name for p in nonebot.get_loaded_plugins())
print("PROBE_JSON " + json.dumps({"plugins": names}, ensure_ascii=False))
'''


def main() -> int:
    if PLUGINS.exists():
        print(f"  [跳过] {PLUGINS} 已存在——不碰用户数据（测试需空目录）")
        return 0
    made = []
    try:
        (PLUGINS / "p_smoke_good").mkdir(parents=True)
        (PLUGINS / "p_smoke_good" / "__init__.py").write_text(
            "# 夹具：子包形态第三方插件\n", encoding="utf-8")
        made.append("p_smoke_good")

        (PLUGINS / "p_smoke_file.py").write_text(
            "# 夹具：单文件形态第三方插件\n", encoding="utf-8")
        made.append("p_smoke_file")

        (PLUGINS / "p_smoke_bad").mkdir()
        (PLUGINS / "p_smoke_bad" / "__init__.py").write_text(
            "raise RuntimeError('夹具故意在导入期抛错')\n", encoding="utf-8")
        made.append("p_smoke_bad")

        (PLUGINS / "assets_only").mkdir()   # 无 __init__.py：不是包，应被安静跳过
        made.append("assets_only")

        env_note = subprocess.run([str(VENV_PY), "-c", PROBE], cwd=str(QQBOT),
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=300)
        out = (env_note.stdout or "") + (env_note.stderr or "")
        line = next((l for l in out.splitlines() if l.startswith("PROBE_JSON ")), "")
        if not line:
            print("  [X] 探针未返回结果（bot.py 装载可能整体失败）")
            print("      " + out.strip().splitlines()[-1][:200] if out.strip() else "      (无输出)")
            return 1
        names = json.loads(line[len("PROBE_JSON "):])["plugins"]

        checks = [
            ("子包插件已装载", "p_smoke_good" in names),
            ("单文件插件已装载", "p_smoke_file" in names),
            ("坏插件未装载", "p_smoke_bad" not in names),
            ("非包目录未装载", "assets_only" not in names),
            # 插件名是**模块末段**（brain 而非 plugins.brain）——2026-09-12 实测
            ("框架 13 插件未受影响",
             {"brain", "webgal", "voice", "memory", "persona"} <= set(names)),
            ("启动未被坏插件中断（探针正常返回）", True),
            ("坏插件被记进 failed（日志不说谎）",
             "failed=['p_smoke_bad(" in out.replace('"', "'") or "p_smoke_bad(" in out),
        ]
        fails = 0
        for name, ok in checks:
            print(f"  {'✅' if ok else '❌'} {name}")
            fails += 0 if ok else 1
        print(f"  plugins 总数: {len(names)}")
        return 0 if fails == 0 else 1
    finally:
        shutil.rmtree(PLUGINS, ignore_errors=True)
        print(f"  [清理] 夹具目录已删除（{', '.join(made)}）")


if __name__ == "__main__":
    raise SystemExit(main())
