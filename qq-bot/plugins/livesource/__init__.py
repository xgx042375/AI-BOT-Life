# -*- coding: utf-8 -*-
"""直播弹幕接入骨架（B2 禁用态，2026-09-08）：配置开关默认关闭，关闭即零副作用。

真实接入路径（接通时按此序装配；本模块只供归一化/分发骨架，不自建连接）：
    blivedm 客户端（open_live(...) → client.start()）
      → on_danmaku 回调（client.add_handler，消息形状=DanmakuMessage：uname/uid/msg 属性）
      → _normalize(msg) 归一化为 {"source": "live", "uid": str, "name": str, "text": str}
      → register_handler(fn) 注册的 async 处理钩子 fn(payload)（消费侧如何回复由其自决；
        2026-09-09 H-1 改一删一：钩子不再存手写全局，以工具卡 "livesource.danmaku"
        落 agent/registry——agent 包为惰性导入，禁用态加载仍零副作用）
    钩子未注册：消息直接丢弃，并只记一次性日志（防弹幕刷屏刷日志）。

开关（.env，默认 false）：
    LIVE_DANMAKU_ENABLED 缺省/false → 模块加载短路：不 import blivedm、不建任务、零副作用；
    true 且未装 blivedm → 日志明确提示 pip install blivedm，并保持禁用
    （依赖只声明在 pyproject [project.optional-dependencies] live，不安装）。
"""
import os

from nonebot.log import logger

_TRUTHY = {"1", "true", "yes", "on"}

# 模块内可变状态（禁用态下一律不触碰）
_TOOL_NAME = "livesource.danmaku"  # 注册表工具卡名（H-1：handler 注册并入能力总线）
_ACTIVE = False          # 开关开启且 blivedm 可导入 → True（分发总门）
_NO_HOOK_WARNED = False  # "无钩子丢弃"一次性日志标记


def _env_flag(name: str, default: bool = False) -> bool:
    """读 .env 开关（nonebot driver config 优先——nonebot.init 已把 .env 载入 config；
    os.environ 兜底）。纯读函数：驱动未初始化/字段缺失不抛，测试友好。"""
    try:
        from nonebot import get_driver

        v = getattr(get_driver().config, name.lower(), None)
        if v is not None:
            return str(v).strip().lower() in _TRUTHY
    except Exception:  # noqa: BLE001
        pass
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def _normalize(msg) -> dict | None:
    """弹幕消息 → 归一化 payload（纯函数，单测友好）。
    兼容 dict 与属性对象（blivedm DanmakuMessage=uname/uid/msg 属性形状）；
    text 取 text/msg 字段，剥空白；无有效文本 → None（无可分发内容）。"""
    try:
        if isinstance(msg, dict):
            uid = str(msg.get("uid", "") or "")
            name = str(msg.get("name", msg.get("uname", "")) or "")
            text = str(msg.get("text", msg.get("msg", "")) or "").strip()
        else:
            uid = str(getattr(msg, "uid", "") or "")
            name = str(getattr(msg, "name", getattr(msg, "uname", "")) or "")
            text = str(getattr(msg, "text", getattr(msg, "msg", "")) or "").strip()
        if not text:
            return None
        return {"source": "live", "uid": uid, "name": name, "text": text}
    except Exception:  # noqa: BLE001
        return None


def register_handler(fn) -> bool:
    """注册弹幕处理钩子（async fn(payload: dict) -> Any）；传 None 注销。返回恒 True。

    2026-09-09 H-1 改一删一：手写 _HANDLER 全局并入 agent/registry 工具卡——注册即落一张
    "livesource.danmaku" 卡（fn=钩子），传 None 摘卡；重注册=先摘旧卡再落新卡（注册表对
    同名查重抛错）。对外语义不变，分发侧见 _dispatch。铁律 #1：此卡只是能力清单，
    不供 12B 选路。agent 包惰性导入——保持禁用态模块加载零副作用（不引 langgraph 链）。
    """
    from agent import registry  # noqa: PLC0415  惰性导入（见 docstring）

    registry.unregister(_TOOL_NAME)
    if fn is not None:
        registry.register(registry.ToolCard(
            name=_TOOL_NAME,
            description="直播弹幕处理钩子：消费归一化弹幕 payload {source,uid,name,text}；如何回复由消费侧自决",
            fn=fn,
            failure=registry.FAIL_RETURN_NONE,  # 钩子异常在 _dispatch 吞掉记日志，不向弹幕源抛
            needs_llm=False,  # 触发=开关+弹幕事件，不经 12B 判定
        ))
    return True


async def _dispatch(payload: dict) -> bool:
    """归一化消息分发：未启用 → 拒绝；无钩子 → 丢弃 + 一次性日志。返回是否已被消费。
    2026-09-09 H-1：钩子改从注册表取（livesource.danmaku 卡）——查卡/导入失败同视为无钩子
    丢弃（返回 False），分发语义与手写全局时代一致。"""
    global _NO_HOOK_WARNED
    if not _ACTIVE:
        return False
    try:
        from agent import registry  # noqa: PLC0415  惰性导入（同 register_handler）

        card = registry.get(_TOOL_NAME)
        handler = card.fn if card is not None else None
    except Exception as e:  # noqa: BLE001
        logger.warning("live danmaku registry lookup failed: {}", e)
        return False
    if handler is None:
        if not _NO_HOOK_WARNED:
            _NO_HOOK_WARNED = True
            logger.warning("live danmaku arrived but no handler registered — dropped（register_handler 未调用）")
        return False
    try:
        await handler(payload)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("live danmaku handler failed: {}", e)
        return False


# ---- 开关判定（模块加载即评估；关闭 → 至此短路：不 import blivedm、零副作用）----
ENABLED = _env_flag("LIVE_DANMAKU_ENABLED", False)

if ENABLED:
    try:
        import blivedm  # noqa: F401  真实客户端装配点（open_live → add_handler(on_danmaku) → _normalize → _dispatch）
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "LIVE_DANMAKU_ENABLED=true 但 blivedm 未安装（{}）——pip install blivedm 后重启生效；已保持禁用", e
        )
    else:
        _ACTIVE = True
