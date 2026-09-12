# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""清空对话派生的记忆（facts/summaries/events + embeddings），保留 relation/emotions/messages。"""
import shutil, sqlite3, datetime

db_path = r"E:\robot\data\memory.db"
stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
shutil.copy2(db_path, db_path + f".bak_mem_clean_{stamp}")
print("backup ok:", db_path + f".bak_mem_clean_{stamp}")

db = sqlite3.connect(db_path)
cur = db.cursor()
for t in ["facts", "summaries", "events", "fact_embeddings", "event_embeddings"]:
    n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    cur.execute(f"DELETE FROM {t}")
    left = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"{t}: {n} -> {left}")
db.commit()
for t in ["relation", "emotions", "messages"]:
    n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"(keep) {t} = {n}")
db.close()
print("DONE")
