"""「长消息」思考判据的参数标定：用**真实消息语料**量触发率，而不是拍脑袋定数。

背景（2026-09-12）：判据一是 `len(text) > 120`（`brain._THINK_MSG_LEN`）——**按字符数**算。
英文 120 字符只有约 20 个词，会让英文用户频繁误触发思考（每条命中在本地引擎上要付 10-20 s）。
本脚本比较三种口径在同一语料上的触发率：

  ① 旧口径   `len(text) > 120`（纯字符数）
  ② 新口径   `等效长度 > 120`，其中 CJK 按 1 算、其余按 3.5 字符算 1（见 `EQUIV_LATIN`）
  ③ 参考     纯 CJK 计数阈值

**验收标准**：**纯中文消息的触发集合必须与旧口径完全一致**（零回归）；差异只出现在含拉丁文本的消息上。

用法：
    cd qq-bot && .venv\\Scripts\\python.exe tools\\dev\\think_len_calib.py [--limit 5000] [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROBOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROBOT / "data" / "memory.db"
THRESH = 120
EQUIV_LATIN = 3.5      # 拉丁/数字/半角标点：3.5 字符折 1 个"等效长度"单位


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x3000 <= o <= 0x303F      # CJK 标点
            or 0x3400 <= o <= 0x4DBF   # 扩展 A
            or 0x4E00 <= o <= 0x9FFF   # 基本汉字
            or 0xF900 <= o <= 0xFAFF   # 兼容汉字
            or 0xFF00 <= o <= 0xFFEF)  # 全角形式


def equiv_len(text: str) -> float:
    """等效长度：CJK 字 1 个算 1；空白不计；其余每 EQUIV_LATIN 个字符算 1。

    纯 CJK 文本的取值**等于字符数**（与旧口径逐字一致 → 中文侧零回归）。
    """
    n = 0.0
    for ch in text or "":
        if ch.isspace():
            continue
        n += 1.0 if is_cjk(ch) else 1.0 / EQUIV_LATIN
    return n


def latin_ratio(text: str) -> float:
    s = [c for c in (text or "") if not c.isspace()]
    if not s:
        return 0.0
    return sum(0 if is_cjk(c) else 1 for c in s) / len(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--limit", type=int, default=5000, help="取最近 N 条用户消息")
    args = ap.parse_args()
    db = Path(args.db)
    if not db.is_file():
        print(f"找不到 {db}（记忆库不在本机？）")
        return 1
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT content FROM messages WHERE role='user' AND content IS NOT NULL "
        "ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()
    con.close()
    texts = [str(r[0]) for r in rows]
    n = len(texts)
    if not n:
        print("语料为空")
        return 0
    old = [t for t in texts if len(t) > THRESH]
    new = [t for t in texts if equiv_len(t) > THRESH]
    pure_cjk = [t for t in texts if latin_ratio(t) == 0.0]
    mixed = [t for t in texts if 0.0 < latin_ratio(t) < 0.4]
    latin_dom = [t for t in texts if latin_ratio(t) >= 0.4]

    def rate(xs, base=None):
        return f"{len(xs)}/{len(base) if base is not None else n} = {len(xs)/ (len(base) if base is not None else n) *100:.1f}%"

    print(f"语料：最近 {n} 条用户消息（{db.name}）")
    print(f"  纯 CJK：{len(pure_cjk)} ｜ 中文夹拉丁(<40%)：{len(mixed)} ｜ 拉丁为主(>=40%)：{len(latin_dom)}")
    print("\n触发率（阈值等效 120）：")
    print(f"  ① 旧口径 纯字符数      ：{rate(old)}")
    print(f"  ② 新口径 等效长度      ：{rate(new)}")
    print(f"  纯 CJK 子集：旧 {rate([t for t in old if latin_ratio(t)==0.0], pure_cjk)}  "
          f"→ 新 {rate([t for t in new if latin_ratio(t)==0.0], pure_cjk)}   ← **必须相同**")
    print(f"  拉丁为主子集：旧 {rate([t for t in old if latin_ratio(t)>=0.4], latin_dom)}  "
          f"→ 新 {rate([t for t in new if latin_ratio(t)>=0.4], latin_dom)}")
    only_old = [t for t in texts if len(t) > THRESH and equiv_len(t) <= THRESH]
    only_new = [t for t in texts if len(t) <= THRESH and equiv_len(t) > THRESH]
    print(f"\n两口径差异：旧命中而新不命中 {len(only_old)} 条 ｜ 新命中而旧不命中 {len(only_new)} 条")
    print("（前者=被旧口径误触发的含拉丁消息；后者应为 0——新口径不会把短消息判长）")
    if only_old:
        print("\n被旧口径误触发的样例（按长度排序，取前 5）：")
        for t in sorted(only_old, key=len, reverse=True)[:5]:
            print(f"   [{len(t)} 字符 → 等效 {equiv_len(t):.1f}] {t[:70].replace(chr(10),' ')}")
    print("\n等效长度对照表（供人工确认口径合理）：")
    for label, sample in (("中文 60 字", "字" * 60), ("中文 120 字", "字" * 120),
                          ("英文 120 字符", "a" * 120), ("英文 300 字符", "a" * 300),
                          ("英文 420 字符", "a" * 420), ("英文 'hello world' ×20", "hello world " * 20)):
        print(f"   {label:22s} → 等效 {equiv_len(sample):6.1f}  {'★ 触发' if equiv_len(sample) > THRESH else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
