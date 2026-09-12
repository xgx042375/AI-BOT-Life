# -*- coding: utf-8 -*-
"""llm_meter —— 实测「一条消息要花多少钱」（2026-09-12，为 API 成本估算提供真数据）。

为什么需要它：API 开销**不能靠猜**。本工具用三个真数据源钉住估算：
  1. **prompt 内容是真的** —— 走 brain 真实组装（人设/记忆注入/事实预解析/思考/stage2 润色），
     只把 LLM 客户端换成替身：记录每次都发了什么、多大，不发送、不出网、不花钱；
  2. **历史是真的** —— `MEMORY_DB_PATH` 指向 `data/memory.db` 的**临时副本**，
     测量过程绝不写你的真库（store.py 用该环境变量重定向）；
  3. **价格是真的** —— DeepSeek 官方价目表（deepseek-flash = V4.1-Flash，高峰/空闲两档）。

产出：每回合 LLM 调用次数 + 每次 prompt/输出 token 估算 + 单条消息成本 + 按真实日活的月成本。
token 估算口径：CJK 字 ≈ 0.6 token/字，非 CJK ≈ 4 字符/token（DeepSeek 分词器的常用近似）。
注意：这是**估算**，不是账单——真实计费以服务商为准；本工具同时打印字符数，便于你用真实账单校正。

用法（qq-bot 目录下）：.venv\\Scripts\\python.exe tools\\dev\\llm_meter.py [--turn "在吗"]
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 官方价目（元 / 百万 token）：deepseek-flash（V4.1-Flash），2026-09-12 取自
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
PRICE = {
    "peak": {"hit": 0.04, "miss": 2.0, "out": 8.0},
    "off": {"hit": 0.02, "miss": 1.0, "out": 4.0},
}

CALLS: list[dict] = []


def est_tokens(s: str) -> int:
    """CJK ≈ 0.6 token/字；其余 ≈ 4 字符/token。"""
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff")
    other = len(s) - cjk
    return int(cjk * 0.6 + other / 4) + 1


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)
        self.finish_reason = "stop"


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]
        self.usage = None


class _Completions:
    def __init__(self, purpose: str) -> None:
        self.purpose = purpose

    async def create(self, **kw):
        msgs = kw.get("messages") or []
        system = "".join(str(m.get("content") or "") for m in msgs if m.get("role") == "system")
        convo = "".join(f"{m.get('role')}:{m.get('content')}\n" for m in msgs if m.get("role") != "system")
        out = "（抬眼看了看你）唔，我在的呀。刚还在想你会不会这会儿来找我。"
        CALLS.append({
            "purpose": self.purpose,
            "model": kw.get("model"),
            "n_msgs": len(msgs),
            "sys_chars": len(system),
            "convo_chars": len(convo),
            "prompt_tokens": est_tokens(system) + est_tokens(convo),
            "out_tokens": est_tokens(out),
            "stream": bool(kw.get("stream")),
        })
        return _Resp(out)


class _Chat:
    def __init__(self, purpose: str) -> None:
        self.completions = _Completions(purpose)


class FakeClient:
    """AsyncOpenAI 形状的最小替身：只记录，不发送。"""

    def __init__(self, purpose: str) -> None:
        self.purpose = purpose
        self.chat = _Chat(purpose)


def money(calls: list[dict], tier: str, hit_ratio: float) -> float:
    """把一次回合的调用折成钱：hit_ratio = 输入里命中缓存的占比。"""
    p = PRICE[tier]
    total = 0.0
    for c in calls:
        pt = c["prompt_tokens"]
        total += (pt * hit_ratio * p["hit"] + pt * (1 - hit_ratio) * p["miss"] + c["out_tokens"] * p["out"]) / 1e6
    return total


def main() -> int:
    turn = "在吗"
    if "--turn" in sys.argv:
        turn = sys.argv[sys.argv.index("--turn") + 1]

    real_db = ROOT.parent / "data" / "memory.db"
    tmp = Path(tempfile.mkdtemp())
    if real_db.is_file():
        shutil.copy2(real_db, tmp / "memory.db")
        print(f"  真库副本: {tmp / 'memory.db'}（{real_db.stat().st_size // 1024} KB，测量不改真库）")
    else:
        print("  ⚠ 没找到 data/memory.db —— 将用空库测量（上下文会偏小）")
    os.environ["MEMORY_DB_PATH"] = str(tmp / "memory.db")

    import core.llm as cl

    cl.get_client = lambda purpose="main": FakeClient(purpose)  # 必须在插件 import 之前替换

    import bot  # noqa: F401  触发 nonebot.init + 插件加载（插件在 import 期缓存各自的 client）
    import harness.driver as dr
    from plugins import voice as _voice

    async def _no_tts(*a, **kw):
        return None

    _voice.tts_wav = _no_tts  # 测量不合成语音（不拉子进程、不占显存）

    print(f"  跑一轮私聊：{turn!r}")
    try:
        res = dr.run_scene("private", turn)
    except Exception as e:  # noqa: BLE001
        # harness.run_scene 直调 brain.handle（不进 nonebot 事件上下文），故**发送阶段**会缺
        # current_bot 而抛 LookupError——那发生在所有 LLM 调用**之后**，本工具要的数据已收齐。
        res = {"reply": "", "note": f"发送阶段中止（{type(e).__name__}）——不影响调用统计"}
        print(f"  注：{res['note']}")

    print("\n=== 本次回合的 LLM 调用 ===")
    tot_pt = tot_ot = 0
    for i, c in enumerate(CALLS, 1):
        tot_pt += c["prompt_tokens"]
        tot_ot += c["out_tokens"]
        print(f"  {i:>2}. purpose={str(c['purpose']):<10} model={str(c['model'])[:14]:<14} "
              f"msgs={c['n_msgs']:<3} system={c['sys_chars']:>6}字 对话={c['convo_chars']:>6}字 "
              f"→ prompt≈{c['prompt_tokens']:>6} tok  输出≈{c['out_tokens']} tok")
    print(f"\n  合计：{len(CALLS)} 次调用，prompt≈{tot_pt} tok，输出≈{tot_ot} tok")
    print(f"  bot 回复（替身内容）: {res.get('reply','')[:60]}")

    print("\n=== 单条消息成本（元）===")
    print(f"  {'输入缓存命中占比':<16}{'高峰时段':>12}{'空闲时段':>12}")
    for hr in (0.0, 0.5, 0.8, 0.95):
        print(f"  {hr:>6.0%}{'':<10}{money(CALLS,'peak',hr):>12.5f}{money(CALLS,'off',hr):>12.5f}")

    print("\n=== 按真实日活外推（高峰价，命中 80%）===")
    per_msg = money(CALLS, "peak", 0.8)
    for n in (100, 300, 1000):
        print(f"  {n:>4} 条/天 → {per_msg * n:>7.2f} 元/天 ｜ {per_msg * n * 30:>8.2f} 元/月")
    print(f"\n  说明：一轮 = 1 条用户消息触发的全部调用（含思考/润色/事实/情绪等旁路）。")
    print(f"  背景循环（生活心跳/主动问候/日复盘）不在此列，按频率另计。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
