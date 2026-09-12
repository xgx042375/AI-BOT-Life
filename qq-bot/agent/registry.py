# -*- coding: utf-8 -*-
"""agent/registry.py —— 工具注册表（2026-09-09 H-1 能力总线）

目的：能力接入统一接口。现状各能力接入各自为政（搜索走 brain 消息判定、梗库/悬挂话题走
memory 提取管道、行为账本走 agent/tools、弹幕走手写 handler 全局）——新能力接入都要改主链。
注册表把能力变成一张张自描述工具卡：新能力接入 = 注册一张卡，不再改 brain 主链。

**铁律 #1（本模块存在的前提，2026-09-09 计划文档 H-1）**：
    注册表只是"能力清单"，不是选路器。触发仍由消息判定 / agent 教学决定，
    **禁止让 12B 按注册表自由选工具**（明确不做 LLM 自由 function-calling）。

本阶段消费方（计划文档 H-1·设计 2）：
    ① livesource 的 danmaku handler 注册（B2 接线：register_handler 内部落卡）；
    ② all_tools() 供未来感知装配时遍历"哪些工具产出的注入块可用"——**本阶段暂无消费方**，
       纯接口预留。调用方拿到卡片自行决定注入什么，不把清单喂给 LLM 选路。

失败语义（failure 字段）约定调用方如何对待执行函数的失败：
    "return_none" —— 失败/无结果返回 None，调用方按"没拿到"处理（静默降级类：搜索、钩子分发）；
    "raise"       —— 失败直接抛异常，调用方自决捕获（数据损坏类宁可炸给人看）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

FAIL_RETURN_NONE = "return_none"  # 失败 → 返回 None（调用方按无结果处理）
FAIL_RAISE = "raise"              # 失败 → 抛异常（调用方自决捕获）


@dataclass
class ToolCard:
    """工具卡：一个能力接入注册表的自我描述（计划文档 H-1·设计 1）。"""
    name: str                        # 全局唯一名，点分命名空间（如 "livesource.danmaku"）
    description: str                 # 触发/能力描述（写给人与感知装配看，不喂 LLM 选路）
    fn: Callable[..., Any] | None    # 执行函数（async/同步皆可；None=占位/已注销语义）
    failure: str = FAIL_RETURN_NONE  # 失败语义：FAIL_RETURN_NONE / FAIL_RAISE
    needs_llm: bool = False          # 执行是否依赖 LLM（如搜索要 12B 提 query=True；钩子分发=False）

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(f"ToolCard.name 必须非空字符串，得到 {self.name!r}")
        if self.failure not in (FAIL_RETURN_NONE, FAIL_RAISE):
            raise ValueError(f"failure 只允许 {FAIL_RETURN_NONE!r}/{FAIL_RAISE!r}，得到 {self.failure!r}")
        if self.fn is not None and not callable(self.fn):
            raise ValueError(f"ToolCard.fn 必须可调用或 None，得到 {type(self.fn).__name__}")


# 模块级注册表（计划文档 H-1·设计 1：list；量级=个位数，线性扫描足够）。
# 自注册时机：能力模块被 import / 接线函数被调用时（@register 装饰器或命令式 register）。
_REGISTRY: list[ToolCard] = []


def get(name: str) -> ToolCard | None:
    """按名取卡；不存在返回 None（只读查询不抛，调用方自决）。"""
    for card in _REGISTRY:
        if card.name == name:
            return card
    return None


def unregister(name: str) -> bool:
    """注销同名卡片；不存在返回 False（幂等不抛）——供重接线先摘旧卡。"""
    for i, card in enumerate(_REGISTRY):
        if card.name == name:
            del _REGISTRY[i]
            return True
    return False


def all_tools() -> list[ToolCard]:
    """全部工具卡（**副本**——调用方增删不影响注册表）。

    本阶段无消费方：预留给未来感知装配遍历（见模块 docstring ②）。
    铁律 #1：不得把返回值作为清单喂给 12B 自由选工具。
    """
    return list(_REGISTRY)


def register(card=None, *, name=None, description="", failure=FAIL_RETURN_NONE, needs_llm=False):
    """登记工具卡，双形态：

    命令式   register(ToolCard(...))                —— 运行时接线（livesource register_handler 用）；
    装饰器   @register(name=..., description=...)   —— 定义处自注册，**原函数原样返回**（不改行为、
                                                        不改调用点——现有执行路径零变化）；
    裸装饰器 @register                              —— 同上，name 缺省取 "模块.限定名"。

    同名已注册 → ValueError（查重防静默覆盖；重接线用 unregister 先摘旧卡）。
    命令式返回卡片本身；装饰器返回原函数。
    """
    if isinstance(card, ToolCard):  # 命令式
        if get(card.name) is not None:
            raise ValueError(f"工具重名：{card.name!r} 已注册（重注册前先 unregister）")
        _REGISTRY.append(card)
        return card

    def _deco(fn):
        card_name = name if name is not None else f"{getattr(fn, '__module__', '')}.{getattr(fn, '__qualname__', '')}"
        register(ToolCard(name=card_name, description=description, fn=fn,
                          failure=failure, needs_llm=needs_llm))
        return fn  # 原函数原样返回：调用点照旧，执行路径零变化

    if callable(card):  # 裸 @register（不带参直接盖在函数上）
        return _deco(card)
    return _deco
