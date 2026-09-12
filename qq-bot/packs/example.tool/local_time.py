# -*- coding: utf-8 -*-
"""local_time —— example.tool 的 entry：演示 tool 包的代码形状（默认不加载）。

装载路径（core/packs.py 的 load_tools）：env `PACKS_ENABLE_PY` 显式开启后，
按 pack.json 的 `tools[].entry` 用 importlib 按文件路径导入本模块（**不注册 sys.modules**）。
→ **模块顶层代码只在"被装载"时才执行**：默认（env 关）连 import 都不做，零执行、零子进程。

本模块顶层只做一件事：按 ToolCard 契约（agent/registry.py，接口文档 §6.1）自注册一张工具卡。
没有别的副作用：不写文件、不起进程、不联网——示例包尤其不能有副作用，否则读者会照抄。

权限：pack.json 声明 `permissions: ["env:TZ"]`，本模块只读这一个环境变量（时区）。
沙箱尚未落地（docs/S4-沙盒与声明式权限-spec-2026-09-12.md），当前装载是同进程 exec_module；
未开环境变量失败时不会退回同进程执行（fail-closed，见 §6.3）。
"""
from __future__ import annotations

import datetime as _dt
import os
import time as _time

from agent.registry import FAIL_RETURN_NONE, register


def format_local_time(zone_env: str = "") -> dict:
    """把"现在"格式化成一句人读得懂的本地时间（纯计算 + 一个环境变量，无 IO）。

    zone_env 空 = 用系统本地时区。环境变量能解析成时区就用它——这正是 `env:TZ`
    这条权限声明的用途：包要读环境，就得先声明读哪个。
    """
    zone = ""
    if zone_env:
        raw = str(os.environ.get(zone_env) or "").strip()
        if raw:
            try:
                zone = _dt.timezone(_dt.timedelta(hours=float(raw)))
            except (TypeError, ValueError):
                zone = ""
    now = _dt.datetime.now(zone) if zone else _dt.datetime.now()
    return {
        "iso": now.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": "一二三四五六日"[now.weekday()],
        "zone": zone_env if zone else "system",
        "unix": int(_time.time()),
    }


@register(name="example.localtime", description="取本地当前时间（示例工具包，默认不加载）",
          failure=FAIL_RETURN_NONE, needs_llm=False)
async def local_time(zone_env: str = "TZ") -> dict | None:
    """返回 {"iso","weekday","zone","unix"}；任何异常按 FAIL_RETURN_NONE 语义由调用方处理。"""
    return format_local_time(zone_env)
