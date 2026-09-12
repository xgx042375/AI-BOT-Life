# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
import sqlite3

db = sqlite3.connect(r"E:\robot\data\memory.db")
db.row_factory = sqlite3.Row
rows = db.execute("SELECT user_id, intimacy, mood, updated_ts FROM relation ORDER BY intimacy DESC LIMIT 8").fetchall()
print("=== relation 前8（按亲密度）===")
for r in rows:
    print(f"  {r['user_id']}: intimacy={r['intimacy']:.1f} mood={r['mood']} updated={r['updated_ts']}")
print()
r = db.execute("SELECT intimacy, mood, updated_ts FROM relation WHERE user_id='10001'").fetchone()
print("主人(10001):", dict(r) if r else "无记录")
db.close()
