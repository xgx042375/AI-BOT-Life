# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""检查新循环：对话/记忆/矫正规则里"留下"相关分布。"""
import json, sqlite3

db = sqlite3.connect(r"E:\robot\data\memory.db")
db.row_factory = sqlite3.Row
print("=== messages 统计 ===")
for r in db.execute("SELECT role, COUNT(*) c FROM messages GROUP BY role").fetchall():
    print(f"  {r['role']}: {r['c']}")
print()
print("=== 最近 16 条 ===")
for r in db.execute("SELECT id, role, content FROM messages ORDER BY id DESC LIMIT 16").fetchall():
    print(f"[{r['id']}] {r['role']}: {str(r['content'])[:110]}")
print()
print("=== 含'留下/留下/留下'的消息 ===")
for r in db.execute("SELECT id, role, content FROM messages WHERE content LIKE '%留下%' OR content LIKE '%别走%' OR content LIKE '%留下来%' ORDER BY id").fetchall():
    print(f"[{r['id']}] {r['role']}: {str(r['content'])[:130]}")
print()
print("=== facts ===")
for r in db.execute("SELECT key, value, confidence FROM facts").fetchall():
    print(f"  {r['key']}: {str(r['value'])[:120]} ({r['confidence']})")
print()
print("=== summaries (最新3) ===")
for r in db.execute("SELECT content FROM summaries ORDER BY id DESC LIMIT 3").fetchall():
    print("  ", str(r["content"])[:130])
print()
print("=== corrections ===")
print(json.dumps(json.load(open(r"E:\robot\qq-bot\data\corrections.json", encoding="utf-8")), ensure_ascii=False)[:500])
db.close()
