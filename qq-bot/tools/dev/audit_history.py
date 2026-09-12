# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""复查历史清理执行情况：扫描 messages 表——高频意象/句式骨架/整句重复/两两相似度。
用法: python tools/audit_history.py [--user 10001] [--n 200]
"""
import argparse, json, sqlite3, sys
from collections import Counter

DB = r"E:\robot\data\memory.db"

# 模板污染检查项
IMAGERY_WORDS = ["雷霆", "雷光", "雷电", "雷雨", "指尖", "虚空", "轻点", "划过", "拂过", "描摹", "摩挲",
                 "咬住下唇", "咬唇", "下唇", "抬眸", "垂眸", "低语", "轻笑", "呢喃", "耳畔", "耳语"]
FRAME_PATTERNS = ["不过是", "罢了", "牢笼", "囚笼", "游戏", "宿命", "命运", "枷锁", "连神明", "凡人",
                  "妾身", "想逃", "挣脱", "沉沦", "沦陷", "轻浮", "轻薄", "大胆", "荒唐"]
# 骨架短语（跨回复重复的套路短语）
SKELETONS = ["不过是", "连神明都", "想逃", "想玩的", "一场游戏", "牢笼罢了", "囚笼罢了",
             "你的...", "你的…", "什么", "竟敢", "不知死活", "岂敢", "放肆"]


def main(user_id: str, n: int):
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, role, content, ts FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, n),
    ).fetchall()
    db.close()
    rows = list(reversed(rows))
    total = len(rows)
    asst = [r for r in rows if r["role"] == "assistant"]
    user = [r for r in rows if r["role"] == "user"]
    print(f"== 总览: 消息 {total} 条 (assistant {len(asst)}, user {len(user)}) ==")

    # 1. 意象词
    img_cnt = Counter()
    for r in asst:
        for w in IMAGERY_WORDS:
            c = r["content"].count(w)
            if c:
                img_cnt[w] += c
    print("\n== 1. 高频意象词（assistant 回复中）==")
    for w, c in img_cnt.most_common(30):
        print(f"  {w}: {c} 次")
    if not img_cnt:
        print("  (无)")

    # 2. 框架句式
    fr_cnt = Counter()
    for r in asst:
        for w in FRAME_PATTERNS:
            c = r["content"].count(w)
            if c:
                fr_cnt[w] += c
    print("\n== 2. 框架句式词（assistant 回复中）==")
    for w, c in fr_cnt.most_common(30):
        print(f"  {w}: {c} 次")
    if not fr_cnt:
        print("  (无)")

    # 3. 整句重复（跨回复完全相同/近同的句子）
    print("\n== 3. 整句重复（相同句子出现>=2次）==")
    sent_cnt = Counter()
    for r in asst:
        for s in r["content"].replace("（", "\n（").replace("）", "）\n").split("\n"):
            s = s.strip().strip("*").strip("（）")
            if len(s) >= 4:
                sent_cnt[s] += 1
    dup = {s: c for s, c in sent_cnt.items() if c >= 2}
    for s, c in sorted(dup.items(), key=lambda x: -x[1])[:20]:
        print(f"  x{c}  {s}")
    if not dup:
        print("  (无整句重复)")

    # 4. 最近 12 条 assistant 两两骨架相似度（2-gram Jaccard）
    print("\n== 4. 最近 12 条 assistant 两两骨架相似度（2-gram Jaccard, >0.30 标注）==")
    import re
    def grams(s, n=2):
        s = re.sub(r"[（*）\s，。！？…~；;：:、\"'“”‘’]", "", s)
        return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else set()
    recent = asst[-12:]
    flagged = 0
    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            a, b = grams(recent[i]["content"]), grams(recent[j]["content"])
            if not a or not b:
                continue
            jac = len(a & b) / len(a | b)
            if jac > 0.30:
                flagged += 1
                print(f"  [{i} vs {j}] Jaccard={jac:.2f}")
                print(f"    A: {recent[i]['content'][:60]!r}")
                print(f"    B: {recent[j]['content'][:60]!r}")
    print(f"  (高相似对: {flagged} 组)")

    # 5. 模板自锁链：连续 assistant 回复的骨架重叠
    print("\n== 5. 连续回复骨架自锁（相邻两轮 Jaccard）==")
    lock = 0
    for i in range(1, len(asst)):
        a, b = grams(asst[i - 1]["content"]), grams(asst[i]["content"])
        if a and b:
            jac = len(a & b) / len(a | b)
            if jac > 0.25:
                lock += 1
                print(f"  {asst[i-1]['id']}->{asst[i]['id']} Jaccard={jac:.2f}: {asst[i]['content'][:50]!r}")
    print(f"  (相邻高重叠: {lock} 对)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="10001")
    ap.add_argument("--n", type=int, default=200)
    args = ap.parse_args()
    main(args.user, args.n)
