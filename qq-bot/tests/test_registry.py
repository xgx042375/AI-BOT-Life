# -*- coding: utf-8 -*-
"""工具注册表（2026-09-09 H-1 能力总线）独立测试：注册/查重/获取/不存在/调用分发/livesource 接线。

直跑（无 pytest 依赖，assert 自解释）：
    python tests/test_registry.py

隔离保证（不 import 真 agent 包、不碰任何运行文件）：
- agent/registry.py 仅标准库——importlib 按文件路径直载，不触发 agent/__init__.py 的
  langgraph 重链；
- 造一个 "agent" 包外壳挂住上面载入的真实 registry 模块——livesource 内部的
  `from agent import registry`（惰性导入）解析到**同一实例**，全程不 import 真包；
- plugins/livesource/__init__.py 按文件路径直载（仅依赖 nonebot.log，可独立导入），
  LIVE_DANMAKU_ENABLED 保持缺省 → 禁用态，测试内临时置 _ACTIVE=True 后还原。
"""
import asyncio
import importlib.util
import os
import sys
import types
from pathlib import Path

_HERE = Path(__file__).resolve()
_QQBOT = _HERE.parents[1]
_REGISTRY_FILE = _QQBOT / "agent" / "registry.py"
_LIVE_FILE = _QQBOT / "plugins" / "livesource" / "__init__.py"


def _load_module(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# 1) 注册表直载（仅标准库）
registry = _load_module("agent.registry", _REGISTRY_FILE)

# 2) "agent" 包外壳 → 挂真实 registry 实例（livesource 的惰性导入走这里，不触真包/langgraph）
_agent_stub = types.ModuleType("agent")
_agent_stub.__path__ = [str(_QQBOT / "agent")]
_agent_stub.registry = registry
sys.modules["agent"] = _agent_stub

# 3) livesource 直载（禁用态加载，零副作用）
os.environ.pop("LIVE_DANMAKU_ENABLED", None)
live = _load_module("livesource_registry_test", _LIVE_FILE)


def _expect(fn, exc_type, what: str):
    try:
        fn()
    except exc_type as e:
        return e
    raise AssertionError(f"{what}: 未抛 {exc_type.__name__}")


# ---- 注册表本体 ----

def test_register_and_get():
    """命令式注册 → get 取回同一张卡，all_tools 可见。"""
    card = registry.ToolCard(name="t.a", description="测试卡A", fn=lambda: "a",
                             failure=registry.FAIL_RAISE, needs_llm=True)
    assert registry.register(card) is card, "register 应原样返回卡片"
    assert registry.get("t.a") is card, "get 应取回同一实例"
    assert card in registry.all_tools(), "all_tools 应含新卡"
    registry.unregister("t.a")
    print("ok: 命令式注册/取回")


def test_duplicate():
    """查重：同名再注册 → ValueError（防静默覆盖），原卡不受影响。"""
    card = registry.ToolCard(name="t.dup", description="第一张", fn=lambda: 1)
    registry.register(card)
    twin = registry.ToolCard(name="t.dup", description="第二张", fn=lambda: 2)
    _expect(lambda: registry.register(twin), ValueError, "同名重注册")
    assert registry.get("t.dup") is card, "查重失败后原卡应原封不动"
    registry.unregister("t.dup")
    print("ok: 同名查重（ValueError，原卡不覆盖）")


def test_decorator_forms():
    """装饰器双形态：带参 @register(name=...) 与裸 @register——原函数原样返回，调用点照旧。"""
    @registry.register(name="t.deco", description="带参装饰器", needs_llm=True)
    async def tool_a(q: str):
        return f"echo:{q}"

    card = registry.get("t.deco")
    assert card is not None and card.fn is tool_a, "装饰器应登记原函数且原样返回"
    assert card.needs_llm is True and card.failure == registry.FAIL_RETURN_NONE
    assert asyncio.run(tool_a("x")) == "echo:x", "原函数行为不变"
    assert asyncio.run(card.fn("y")) == "echo:y", "调用分发：经卡调用等价于直接调用"

    @registry.register
    def tool_b(x):
        return x + 1

    bare_card = registry.get(f"{tool_b.__module__}.{tool_b.__qualname__}")
    assert bare_card is not None and bare_card.fn is tool_b, "裸装饰器应按 模块.限定名 自注册"
    assert tool_b(1) == 2
    registry.unregister("t.deco")
    registry.unregister(f"{tool_b.__module__}.{tool_b.__qualname__}")
    print("ok: @register 带参/裸形态（原函数返回、经卡分发一致）")


def test_get_missing_and_unregister():
    """不存在：get → None（只读查询不抛）；unregister 幂等（缺失 False，命中 True）。"""
    assert registry.get("t.nope") is None
    assert registry.unregister("t.nope") is False
    card = registry.ToolCard(name="t.gone", description="待注销", fn=None)
    registry.register(card)
    assert registry.unregister("t.gone") is True
    assert registry.get("t.gone") is None, "注销后应取不到"
    print("ok: 不存在返回 None / unregister 幂等")


def test_card_validation():
    """卡片字段校验：空名/非法失败语义/不可调用 fn 一律构造即炸（fail fast）。"""
    _expect(lambda: registry.ToolCard(name="  ", description="x", fn=None), ValueError, "空名")
    _expect(lambda: registry.ToolCard(name="t.bad", description="x", fn=None, failure="boom"),
            ValueError, "非法 failure")
    _expect(lambda: registry.ToolCard(name="t.bad", description="x", fn="not-callable"),
            ValueError, "fn 不可调用")
    print("ok: ToolCard 字段校验")


def test_all_tools_copy():
    """all_tools 返回副本：调用方增删不污染注册表。"""
    before = registry.all_tools()
    n = len(before)
    before.append(registry.ToolCard(name="t.pollute", description="脏", fn=None))
    assert len(registry.all_tools()) == n, "注册表不应被返回值污染"
    assert registry.get("t.pollute") is None
    print("ok: all_tools 副本隔离")


# ---- livesource 接线（H-1 改一删一：_HANDLER 全局 → 注册表工具卡）----

def test_livesource_disabled_untouched():
    """禁用态基线：开关缺省 → ENABLED/_ACTIVE 均 False，接线改造未破坏零副作用加载。"""
    assert live.ENABLED is False and live._ACTIVE is False, "开关缺省应为禁用态"
    assert live._TOOL_NAME == "livesource.danmaku"
    assert not hasattr(live, "_HANDLER"), "手写 _HANDLER 全局应已删除（改一删一）"
    print("ok: livesource 禁用态基线（_HANDLER 已删）")


def test_livesource_wiring():
    """接线全链：落卡 → 分发消费 → 重注册换钩子 → fn=None 防御 → 传 None 注销 → 摘卡丢弃。"""
    seen = []

    async def hook(p):
        seen.append(p)

    assert live.register_handler(hook) is True, "register_handler 对外仍返回 True"
    card = registry.get("livesource.danmaku")
    assert card is not None and card.fn is hook, "注册即落 livesource.danmaku 卡"
    assert card.needs_llm is False and card.failure == registry.FAIL_RETURN_NONE, \
        "卡片语义：触发不经 LLM、失败静默降级"

    # 未启用：拒绝分发优先于注册表（不触卡）
    assert asyncio.run(live._dispatch({"t": 1})) is False and seen == []

    live._ACTIVE = True  # 测试内启用（真实启用只能由模块底部开关判定完成）
    try:
        assert asyncio.run(live._dispatch({"t": 1})) is True and seen == [{"t": 1}], \
            "启用后分发应消费 payload 并返回 True"

        # 重注册=换钩子：先摘旧卡再落新卡，不触发注册表查重
        async def hook2(p):
            seen.append(("h2", p))

        assert live.register_handler(hook2) is True
        assert registry.get("livesource.danmaku").fn is hook2
        assert asyncio.run(live._dispatch({"t": 2})) is True
        assert seen == [{"t": 1}, ("h2", {"t": 2})], "分发应路由到新钩子"

        # 防御路径：卡在但 fn=None → 视为无钩子丢弃 + 一次性日志标记
        registry.unregister("livesource.danmaku")
        registry.register(registry.ToolCard(name="livesource.danmaku", description="防御", fn=None))
        live._NO_HOOK_WARNED = False
        n_seen = len(seen)
        assert asyncio.run(live._dispatch({"t": 3})) is False
        assert len(seen) == n_seen and live._NO_HOOK_WARNED is True
        registry.unregister("livesource.danmaku")  # 清防御卡

        # 传 None 注销：摘卡 → 同样无钩子丢弃 + 一次性日志标记
        assert live.register_handler(None) is True
        assert registry.get("livesource.danmaku") is None, "传 None 应摘卡"
        live._NO_HOOK_WARNED = False
        assert asyncio.run(live._dispatch({"t": 4})) is False and live._NO_HOOK_WARNED is True
    finally:
        live._ACTIVE = False  # 还原禁用态
    assert asyncio.run(live._dispatch({"t": 5})) is False, "还原后回到拒绝分发"
    print("ok: livesource 接线（落卡/分发/换钩子/fn=None 防御/None 注销/还原）")


def test_livesource_uses_same_registry_instance():
    """livesource 惰性导入解析到测试载入的同一 registry 实例（外壳挂接生效，未 import 真包）。"""
    assert sys.modules.get("agent.registry") is registry
    assert _agent_stub.registry is registry
    assert "livesource.danmaku" not in {c.name for c in registry.all_tools()}, "接线测试应自清理"
    print("ok: 同一 registry 实例（agent 包外壳挂接）")


def main():
    tests = [test_register_and_get, test_duplicate, test_decorator_forms,
             test_get_missing_and_unregister, test_card_validation, test_all_tools_copy,
             test_livesource_disabled_untouched, test_livesource_wiring,
             test_livesource_uses_same_registry_instance]
    for t in tests:
        t()
    print(f"\nALL PASS ({len(tests)} tests)")


if __name__ == "__main__":
    main()
