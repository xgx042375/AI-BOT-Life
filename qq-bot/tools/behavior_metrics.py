# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
# -*- coding: utf-8 -*-
r"""行为级指标采样（唯一进度判据；smoke = 回归护栏，不答"方向对不对"）。

2026-09-12（T7.1 同名文件合并）：本文件为**唯一权威副本**。此前仓库根 `tools\behavior_metrics.py`
与 `qq-bot\tools\behavior_metrics.py` 同名不同内容（E13），已把根版本中独有的改进（A1 模板起手词族、
A2 繁简混用、A3 动作密度、A4 长度/形状观测、--since/--until/--priv 过滤、只读打开库、按天分组）
合并进来，根版本已从 git 追踪中移除（`.gitignore:6 tools/` 本就排除整个根 tools 目录）。

用法（必须从 qq-bot 目录跑）：
  cd E:\robot\qq-bot
  .\.venv\Scripts\python.exe tools\behavior_metrics.py                       # 全部历史，按天
  .\.venv\Scripts\python.exe tools\behavior_metrics.py --since 2026-09-09    # 只看某天后（重启实测对比）
  .\.venv\Scripts\python.exe tools\behavior_metrics.py --since 2026-09-09 --priv   # 仅私聊（owner 主场景）

指标：
  A1 模板起手：回复开头 8 字命中"既然/说起来/不过"等模板词族的比例（Top 词族单独列出）
  A2 繁简混用：单条回复同时含繁体专有字与简体虚词（的/一/是/不/了/在）的比例
  A3 动作密度：含（动作）括号的回复占比
  A4 长度：mean/median/p90（剥协议标记后）；另附 行数 med / 孤立"……"行占比（B0 形状观测）

基线（09-08 清污口径，含测试期污染偏置）：动作密度 59% / "既然"系起手 ×55 / 繁简混用 60% / 平均 60 字（中位 58）。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path

# 数据根：qq-bot\tools\ → 上溯两级 = E:\robot\data
DB = Path(__file__).resolve().parents[2] / "data" / "memory.db"

# A1 模板起手词族（P3 防模板的观测面；扩表直接加词）
TEMPLATE_OPENERS = ("既然", "说起来", "不过", "嗯，", "唔", "那个", "其实", "可能", "也许", "果然")
# A2 繁体专有字样本（简繁同形字不入表——只取繁体独有形态，够做混用检测）
TRAD_CHARS = set("們對時說話學會來後裡點麼現在樣於從被讓這還沒關門問間聽見覺得應該級線隨機動員歷歸屬請誰維續雙鳳飛齊龍車馬鳥語氣風雲電報紙書圖圓園廠歷壓噸藥廳曆鐘錶頭顆條隻輛頁紙張數額員嘅冇")
# 简体虚词（判"混用"而非"纯繁体"）
SIMP_CHARS = "的一是不了在"
PROTOCOL_MARK = re.compile(r"\s*【[^】]{1,40}】?")
WRITE_MARK = re.compile(r"\s*\[WRITE:[^\]]*\]")
ACTION_BRACKET = re.compile(r"（[^（）]{1,24}）")


def clean(t: str) -> str:
    """剥协议标记（【基调：…】/[WRITE:…]）后取正文。"""
    t = PROTOCOL_MARK.sub("", t or "")
    t = WRITE_MARK.sub("", t)
    return t.strip()


def opener_key(t: str) -> str:
    """模板起手判定键：剥（动作）/【】后取开头 8 字去标点。"""
    b = ACTION_BRACKET.sub("", t).strip()
    b = re.sub(r"[。！？!?\s，,、…~～*【】\[\]'\"“”]", "", b)
    return b[:8]


def main() -> int:
    ap = argparse.ArgumentParser(description="行为级指标采样（只读 memory.db）")
    ap.add_argument("--since", default="", help="YYYY-MM-DD（含）")
    ap.add_argument("--until", default="", help="YYYY-MM-DD（含）")
    ap.add_argument("--priv", action="store_true", help="仅私聊")
    ac = ap.parse_args()

    if not DB.exists():
        print(f"memory.db 不存在：{DB}")
        return 1

    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
        if not {"ts", "content"} <= cols:
            print("messages 表结构不符合预期：" + ", ".join(sorted(cols)))
            return 1
        gid = "group_id" if "group_id" in cols else "'' AS group_id"
        rows = list(con.execute(
            f"SELECT ts, {gid}, content FROM messages WHERE role='assistant' ORDER BY ts"))
    except sqlite3.Error as e:
        print(f"读取 memory.db 失败：{e}")
        return 1
    finally:
        con.close()

    rows = [r for r in rows if r[2] and clean(r[2])]
    if ac.since:
        rows = [r for r in rows if str(r[0])[:10] >= ac.since]
    if ac.until:
        rows = [r for r in rows if str(r[0])[:10] <= ac.until]
    if ac.priv:
        rows = [r for r in rows if not r[1]]

    by_day: dict[str, list[str]] = {}
    for ts, _gid, content in rows:
        by_day.setdefault(str(ts)[:10], []).append(clean(content))
    if not by_day:
        print("区间内无数据")
        return 0

    print(f"库：{DB}")
    print(f"样本 {len(rows)} 条  (仅私聊={ac.priv}"
          + (f", since={ac.since}" if ac.since else "")
          + (f", until={ac.until}" if ac.until else "") + ")")
    print()
    print("day          n   A1起手%  A2繁简混用%  A3动作密度%   A4 len med/p90   行数med  ……行%")
    for day in sorted(by_day):
        texts = by_day[day]
        n = len(texts)
        a1 = sum(1 for t in texts if any(opener_key(t).startswith(w) for w in TEMPLATE_OPENERS))
        a2 = sum(1 for t in texts if any(c in t for c in TRAD_CHARS) and re.search(f"[{SIMP_CHARS}]", t))
        a3 = sum(1 for t in texts if ACTION_BRACKET.search(t))
        lens = sorted(len(t) for t in texts)
        lines_n = [len([ln for ln in t.split("\n") if ln.strip()]) for t in texts]
        ell = sum(1 for t in texts if any(ln.strip() == "……" for ln in t.split("\n")))
        print(f"{day}  {n:4d}    {100 * a1 / n:6.0f}       {100 * a2 / n:6.0f}        "
              f"{100 * a3 / n:6.0f}       "
              f"{statistics.median(lens):4.0f}/{lens[int(n * 0.9)]:4.0f}          "
              f"{statistics.median(lines_n):3.0f}    {100 * ell / n:5.0f}%")

    # Top 起手（全区间，前 2 字）
    ops: Counter = Counter()
    for _ts, _gid, content in rows:
        k = opener_key(clean(content))
        if k:
            ops[k[:2]] += 1
    top = "  ".join(f"{k}×{v}" for k, v in ops.most_common(5))
    print(f"\nTop 起手（前 2 字，全区间）：{top or '（无样本）'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
