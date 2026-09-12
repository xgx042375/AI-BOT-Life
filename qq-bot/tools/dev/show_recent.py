# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
import sqlite3

db = sqlite3.connect(r"E:\robot\data\memory.db")
db.row_factory = sqlite3.Row
rows = db.execute("SELECT id, role, content, ts FROM messages WHERE user_id='10001' ORDER BY id DESC LIMIT 24").fetchall()
for r in reversed(rows):
    print(f"[{r['id']}] {r['role']}: {str(r['content'])[:110]}")
db.close()
