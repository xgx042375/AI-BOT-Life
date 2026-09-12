"""core 包 —— 机器人基础设施层（2026-09-06 重写引入）。

职责：路径规范、原子文件 IO、跨模块共享的工具件。
不依赖 nonebot/LLM，可独立单测；插件与 agent 层统一从这里取基础设施。

模块：
    paths    唯一数据根与常用路径（消除 qq-bot/data 与 E:/robot/data 的双根分裂）
    atomics  JSON/文本原子读写（tmp+os.replace；读一律 utf-8-sig 防 BOM）
"""
from . import atomics, paths

__all__ = ["atomics", "paths"]
