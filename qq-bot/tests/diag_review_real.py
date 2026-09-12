# -*- coding: utf-8 -*-
"""复盘链真库诊断（2026-09-08，用户要求：修复后手动跑一遍本地数据验证复盘生效）。

前置：本地引擎在跑（11434 主模型 + 11435 embedding）。可用
  powershell -File tools\\start-embedding.ps1
  E:\\robot\\tools\\llama.cpp\\llama-server.exe -m <gemma gguf> ... （或直接启动 bot）
用法：.venv/Scripts/python.exe tests/diag_review_real.py [日期，默认昨天]

做四件事（全部针对 E:\\robot\\data\\memory.db 真实库）：
  ① 覆盖窗口对照：统计指定日期全天消息数 vs 其中 10:00-24:00（旧复盘方案永远丢失的时段）
  ② 真跑一次 run_daily_review（LLM 提取 facts+review，真实写库）
  ③ 验证落库：daily_reviews 有该日行、本次新增 facts 均有 fact_embeddings（修复前为 0）
  ④ 验证注入：build_context 产物含「昨日复盘」
"""
import asyncio
import sys

sys.path.insert(0, r"E:\robot\qq-bot")

import nonebot

nonebot.init()

from datetime import datetime, timedelta, timezone

from plugins import memory as _mem


def _win_counts(owner: str, date_str: str):
    """统计指定本地日期的全天消息数与 10:00-24:00 消息数（UTC 区间换算，同 store 口径）。"""
    d0 = datetime.fromisoformat(date_str).astimezone()
    start = d0.astimezone(timezone.utc).isoformat()
    end = (d0 + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    ten = (d0 + timedelta(hours=10)).astimezone(timezone.utc).isoformat()
    with _mem.db._lock:
        day = _mem.db._conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE user_id=? AND ts>=? AND ts<?", (owner, start, end)
        ).fetchone()[0]
        late = _mem.db._conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE user_id=? AND ts>=? AND ts<?", (owner, ten, end)
        ).fetchone()[0]
    return day, late


async def main():
    owner = str(list(nonebot.get_driver().config.superusers)[0])
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        date_str = (datetime.now().astimezone() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"owner={owner}  复盘日期={date_str}")

    # ① 覆盖窗口对照
    day, late = _win_counts(owner, date_str)
    print(f"\n[① 覆盖窗口] {date_str} 全天消息 {day} 条；其中 10:00-24:00 {late} 条"
          f"（旧方案传 now.date()，这 {late} 条永远进不了日复盘）")
    if day == 0:
        print("!! 该日无对话记录，换一个日期再跑（如今天）：python tests/diag_review_real.py 2026-09-08")
        return

    # ② 真跑复盘（真实写库）
    fact_before = {(f["key"], f["value"]) for f in _mem.db.get_facts(owner, limit=200)}
    emb_before = {k for k, _ in _mem.db.get_fact_embeddings(owner)}
    rev_before = _mem.db.get_latest_daily_review(owner)
    print(f"\n[② 跑复盘] run_daily_review({owner!r}, {date_str!r}) …（LLM 提取，约 0.5-2 分钟）")
    ok = await _mem.run_daily_review(owner, date_str)
    print(f"run_daily_review => {ok}")
    if not ok:
        print("!! 复盘失败：查引擎是否在跑（11434），及 bot.log 中 'daily review failed'")
        return

    # ③ 落库验证
    rev = _mem.db.get_latest_daily_review(owner)
    is_new = not (rev_before and rev_before["date"] == date_str)
    print(f"\n[③ 落库] daily_reviews[{owner},{date_str}] {'新增' if is_new else '已存在(upsert覆盖)'}")
    print(f"  复盘文本（{len(rev['content'])} 字）：{rev['content']}")
    fact_after = {(f["key"], f["value"]) for f in _mem.db.get_facts(owner, limit=200)}
    emb_after = {k for k, _ in _mem.db.get_fact_embeddings(owner)}
    new_facts = fact_after - fact_before
    new_embs = emb_after - emb_before
    print(f"  本次新增 facts：{len(new_facts)} 条")
    for k, v in sorted(new_facts):
        has_emb = "✓有向量" if k in new_embs else ("（已有）" if k in emb_before else "✗无向量!")
        print(f"    - {k}: {v[:40]}  [{has_emb}]")
    no_emb = {k for k, _ in fact_after} - emb_after
    if no_emb:
        print(f"  ⚠ 仍有 {len(no_emb)} 个事实无向量（多为 embedding 服务不可用期写入的存量）：{sorted(no_emb)[:5]}")
    else:
        print("  所有 facts 均有向量，语义召回可全部覆盖 ✓")

    # ④ 注入验证
    ctx = await _mem.build_context(owner)
    has_rev = "昨日复盘" in ctx
    print(f"\n[④ 注入] build_context 含「昨日复盘」：{has_rev}")
    for line in ctx.splitlines():
        if "复盘" in line:
            print(f"    {line[:100]}…")
    print("\n== 诊断完成 ==")


asyncio.run(main())
