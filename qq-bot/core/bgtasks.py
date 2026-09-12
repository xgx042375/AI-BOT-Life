"""bgtasks —— 后台任务引用池（2026-09-12）。

**为什么需要它**：`asyncio` 对任务只持**弱引用**——`create_task(...)` 的返回值不保存，
任务可能在执行途中被 GC 回收，表现为**静默不完成**（没有异常、没有日志、功能无声失效）。
Python 官方文档明确要求"保存引用以免任务中途消失"。

本项目已经踩过并分别修过三处（各自一个模块级集合 + 完成回调）：
· `brain._HANDOVER_TASKS`（交接生成，20s LLM 窗口）——注释原文："asyncio 仅持任务弱引用……
  不持引用可被 GC 静默吞掉（交接无声失效，仅日志缺失可判）"
· `brain._PREPARSE_TASKS`（主人括号事实预解析）
· `webgal._INFLIGHT` / `voice._LOCAL_PLAY_TASKS`

修法是同一个，但抄了四份——所以这里给一份公共实现：**新代码一律用 `spawn()`**，
别再各写各的集合（四份副本里任何一份漏掉 done_callback 都会重新漏任务）。

顺带补一件原实现没有的事：**任务异常留痕**。原先后台任务抛异常只走事件循环的默认处理器，
排查时看不到"是谁的任务失败了"；这里在完成回调里以 debug 级别记一笔（含异常类型），
既不留噪声也不丢线索。
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("bgtasks")

# 强引用池：模块级集合（任务完成即 discard，不会无限增长）
_TASKS: set = set()


def _on_done(task) -> None:
    """完成回调：先弃引用，再把异常留痕（顺序无所谓，但一律不重新抛出）。"""
    _TASKS.discard(task)
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:      # 取异常时刚被取消：正常路径，不算失败
        return
    if exc is not None:
        logger.debug("background task failed: %s [%s]", exc, type(exc).__name__)


def spawn(coro) -> "asyncio.Task":
    """把协程交给事件循环跑，并**保住引用**直到它结束。返回 Task（需要时可 await）。"""
    task = asyncio.get_running_loop().create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_on_done)
    return task


def pending() -> int:
    """当前在跑的后台任务数（诊断/测试用；不暴露集合本身，防外部误改）。"""
    return len(_TASKS)
