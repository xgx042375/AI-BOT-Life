# -*- coding: utf-8 -*-
"""verify_packs —— 定向校验新增的 5 个 example.* 内容包（不进 smoke 套件）。

用 venv python 从 qq-bot 目录跑：
    .venv/Scripts/python.exe tests/dev_verify_example_packs.py

做什么：
  1. 双包根指向真实随仓 qq-bot/packs（+ 一个空用户根，避免本机 data/packs 的环境态干扰）；
  2. plugins.persona / plugins.voice 换桩（verify_packs_persona.py / verify_packs_voice.py），
     这样只借它们的策略函数，不拉起 bot 依赖；
  3. loguru 装 sink 捕获 WARNING+ —— 坏包跳过一律 warning 留原因，**任何一条 WARNING 即失败**；
  4. reload() + discover()：断言 7 个随仓示例包全被发现，并逐个打印 name/type/root/类型专属内容；
  5. tool 包专测：默认（env 关）load_tools() 必须返回 [] 且**不 import**；清场单装载时只装载它，
     验证 entry 自注册的 ToolCard 真能跑（结果用完即 unregister，不留注册表污染）。
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

QQBOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QQBOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

for _mod in ("plugins", "plugins.persona", "plugins.voice", "agent", "agent.registry"):
    sys.modules.pop(_mod, None)
import plugins.persona as persona_stub    # noqa: E402  （verify_packs_persona.py）
import plugins.voice as voice_stub        # noqa: E402  （verify_packs_voice.py）

from loguru import logger                 # noqa: E402
from core import packs                    # noqa: E402

FAILS: list[str] = []
WARNINGS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not cond:
        FAILS.append(label + (f" ({detail})" if detail else ""))


logger.remove()
logger.add(lambda m: WARNINGS.append(m.record["message"]), level="WARNING")

# ---- 1. 双包根：真实随仓 + 空用户根 ----
EMPTY_USER = QQBOT.parent / "data" / "_verify_empty_packs_root"   # 不存在=常态（空根）
packs.USER_PACKS_DIR = EMPTY_USER
packs.BUILTIN_PACKS_DIR = QQBOT / "packs"
packs.reload()
found = packs.discover()

# ---- 2. 发现面 ----
print("== 发现（双根: %s） ==" % packs.BUILTIN_PACKS_DIR)
EXPECT_SEVEN = {"example.sakura", "example.stage", "example.item", "example.emotion",
                "example.world", "example.voice", "example.tool"}
names = {p.name for p in found}
check("7 个随仓示例包全被发现", names == EXPECT_SEVEN,
      f"缺={sorted(EXPECT_SEVEN - names)} 多={sorted(names - EXPECT_SEVEN)}")
for p in found:
    print(f"   - {p.name:<16} type={p.type:<8} root={p.root:<8} dir={p.dir.name}")

MINE = {
    "example.item": "item", "example.emotion": "emotion", "example.world": "world",
    "example.voice": "voice", "example.tool": "tool",
}
for nm, ty in MINE.items():
    pk = packs.get(nm)
    check(f"{nm}: 被发现且 type={ty}", pk is not None and pk.type == ty,
          "" if pk is None else f"type={pk.type!r} root={pk.root!r} path={pk.dir}")

# ---- 3. type=item：items_index() ----
print("\n== type=item：items_index() ==")
items = [it for it in packs.items_index() if it["pack"] == "example.item"]
print("   " + repr(items))
check("item 三条目录项（name/label/note/ttl_min 都被读到）",
      [it["name"] for it in items] == ["魔法茶杯", "星尘茶匙", "暖手炉"]
      and items[0]["label"] == "魔法茶杯" and items[0]["ttl_min"] == 5
      and items[1]["note"].startswith("搅动时") and items[1]["ttl_min"] is None,
      f"keys={list(items[0]) if items else '无'}")

# ---- 4. type=emotion：tones_index() ----
print("\n== type=emotion：tones_index() ==")
tones = [t for t in packs.tones_index() if t["pack"] == "example.emotion"]
print("   " + repr(tones))
check("emotion 三条基调（含空 voice 的「欲言又止」）",
      [t["word"] for t in tones] == ["得意", "困倦", "欲言又止"]
      and tones[0]["voice"] == {"temp": 0.9, "speed": 1.08, "semi": 0.5}
      and tones[1]["voice"] == {"temp": 0.85, "speed": 0.92, "semi": -0.5}
      and tones[2]["voice"] == {} and tones[0]["card_key"] == "",
      f"words={[t['word'] for t in tones]}")
voice_stub.apply_pack_merges()
check("带完整 voice 的词进 EMO_PARAMS，空 voice 不进",
      voice_stub.EMO_PARAMS.get("得意") == (0.9, 1.08, 0.5) and "欲言又止" not in voice_stub.EMO_PARAMS)

# ---- 5. type=world：world_index() ----
print("\n== type=world：world_index() ==")
world_all = packs.world_index()
world = {u: v for u, v in world_all.items() if u == "stellar-teahouse"}
print(f"   world_index() 里共有 {len(world_all)} 个 universe 键：{sorted(world_all)}")
print("   universe=stellar-teahouse（本包）:")
for e in world.get("stellar-teahouse", []):
    print(f"     always={e['always']!s:<5} priority={e['priority']:<3} keys={e['keys']} text={e['text'][:24]}…")
wl = world.get("stellar-teahouse", [])
print("   → 跨包合并：example.sakura/world.json 与 example.world/world.json 共用 universe=stellar-teahouse")
check("world universe=stellar-teahouse 跨包合并 4 条且 priority 降序",
      len(wl) == 4 and [e["priority"] for e in wl] == sorted((e["priority"] for e in wl), reverse=True)
      and [e["priority"] for e in wl] == [10, 0, 0, 0], f"条数={len(wl)} prio={[e['priority'] for e in wl]}")
check("本包 3 条都在（含 always:false 的 keys 预留条目）",
      any("货运港第 7 码头" in e["text"] for e in wl)
      and any("杯口缺角" in e["text"] for e in wl)
      and sum(1 for e in wl if e["keys"] == ["观景层", "货运船队"] and not e["always"]) == 1,
      f"always:true={sum(1 for e in wl if e['always'])} always:false={sum(1 for e in wl if not e['always'])}")
_inj = persona_stub.build_system_prompt({"description": "x", "universe": "stellar-teahouse"})
check("world 真注入面：always 文本进提示词（priority 降序），always:false 的不进",
      "【世界观设定】" in _inj and "第 7 码头" in _inj and "杯口缺角" in _inj
      and "观景层夜里" not in _inj
      and _inj.index("第 7 码头") < _inj.index("杯口缺角"))

# ---- 6. type=voice：voice_index() ----
print("\n== type=voice：voice_index() ==")
vi = {k: v for k, v in packs.voice_index().items() if k == "example_teahouse"}
print("   " + repr(vi))
ent = vi.get("example_teahouse", {})
check("voice 键 example_teahouse（key/name/lang/personas/绝对路径/refs/emo_refs 都读到）",
      ent.get("name") == "示例音色·云汀" and ent.get("lang") == "zh"
      and ent.get("personas") == ["樱"] and os.path.isabs(ent.get("gpt", ""))
      and os.path.isabs(ent.get("sovits", "")) and os.path.isabs(ent.get("ref_dir", ""))
      and ent.get("refs") == "voice_refs.json"
      and ent.get("emo_refs") == {"温柔": "招呼熟客", "得意": "炫耀茶艺"},
      f"keys={sorted(ent)}")
voice_stub.apply_pack_merges()
check("voice 合并进 VOICES（setdefault，不覆盖内置）",
      voice_stub.VOICES.get("example_teahouse", {}).get("name") == "示例音色·云汀"
      and any(k in voice_stub.VOICES for k in ("amiya", "odin")))

# ---- 7. type=tool：发现但默认不装载 ----
print("\n== type=tool：发现但默认不装载 ==")
tool_pk = packs.get("example.tool")
print(f"   声明 tools={tool_pk.manifest.get('tools')} permissions={tool_pk.manifest.get('permissions')}"
      f" limits={tool_pk.manifest.get('limits')}")
check("tool 包被 by_type('tool') 发现", "example.tool" in {p.name for p in packs.by_type("tool")})
os.environ.pop("PACKS_ENABLE_PY", None)
check("默认（env 关）py_tools_enabled()=False 且 load_tools()==[]",
      packs.py_tools_enabled() is False and packs.load_tools() == [])
check("entry 未被 import（模块不在 sys.modules，且没有落盘痕迹）",
      "local_time" not in {m for m in sys.modules if "local_time" in m}
      and not (tool_pk.dir / "imported.marker").exists()
      and not (tool_pk.dir / "loaded.marker").exists())
check("entry 文件确实存在（缺失会 warning，那就是坏示例）",
      (tool_pk.dir / "local_time.py").is_file())

# ---- 8. tool 单装载行为级验证（entry 自注册的卡真能跑） ----
print("\n== tool 单装载：entry 自注册的 ToolCard 真能跑 ==")
import agent.registry as reg                       # noqa: E402


def _load_only_example_tool() -> list:
    """只留 example.tool 可被发现 → load_tools() 必只装载它（避免动本机其它 tool 包）。"""
    keep, packs._CACHE["packs"] = packs._CACHE["packs"], None
    keep_roots = packs._CACHE["roots"]
    packs.discover = lambda: [tool_pk]              # 临时打桩：发现面只有它
    try:
        return packs.load_tools()
    finally:
        del packs.discover                        # 还原模块属性
        packs._CACHE["packs"] = keep
        packs._CACHE["roots"] = keep_roots


reg.unregister("example.localtime")
os.environ["PACKS_ENABLE_PY"] = "1"
try:
    mods = _load_only_example_tool()
finally:
    os.environ.pop("PACKS_ENABLE_PY", None)
card = reg.get("example.localtime")
print(f"   load_tools() -> {[m.__name__ for m in mods]}；注册表 -> "
      f"{[(c.name, c.failure, c.needs_llm) for c in reg.all_tools()]}")
check("env 开：entry 被装载且自注册一张 ToolCard", len(mods) == 1 and card is not None)
if card is not None:
    res = card.fn("TZ")
    if hasattr(res, "__await__"):
        import asyncio
        os.environ["TZ"] = "8"
        try:
            res = asyncio.run(res)
        finally:
            os.environ.pop("TZ", None)
    print("   example.localtime(TZ=8) -> " + repr(res))
    check("ToolCard.fn 可执行且返回时间字典",
          isinstance(res, dict) and res.get("zone") == "TZ" and "iso" in res and "weekday" in res)
reg.unregister("example.localtime")
check("用完即注销（不留注册表污染）", reg.get("example.localtime") is None)

# ---- 9. warning 面 ----
print("\n== loader warning ==")
if WARNINGS:
    for w in WARNINGS:
        print("   WARNING: " + str(w))
check("loader 零 warning（坏包一律 warning 留原因）", not WARNINGS, f"{len(WARNINGS)} 条")

print("\n== 结果 ==")
if FAILS:
    for f in FAILS:
        print("   FAIL  " + f)
    print(f"RESULT: FAIL ({len(FAILS)} 项)")
    sys.exit(1)
print("RESULT: PASS（发现 7 包 / 5 个新包类型专属内容齐全 / 零 warning）")
