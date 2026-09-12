# 临时诊断：查看真实对话与记忆提取结果
import sqlite3

conn = sqlite3.connect(r"E:\robot\data\memory.db")
conn.row_factory = sqlite3.Row
print("--- messages ---")
for r in conn.execute("SELECT role, content FROM messages WHERE user_id=? ORDER BY id", ("10001",)):
    print(f"[{r['role']}] {r['content'][:80]}")
print("--- facts ---")
for r in conn.execute("SELECT key, value FROM facts WHERE user_id=?", ("10001",)):
    print(r["key"], "=", r["value"])
print("--- relation ---")
for r in conn.execute("SELECT intimacy, mood FROM relation WHERE user_id=?", ("10001",)):
    print("intimacy", r["intimacy"], "mood", r["mood"])
print("--- emotions ---")
for r in conn.execute("SELECT emotion, intensity FROM emotions WHERE user_id=?", ("10001",)):
    print(r["emotion"], r["intensity"])
