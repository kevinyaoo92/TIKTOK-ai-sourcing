import sqlite3
from pathlib import Path

DB = Path(r"D:\tiktok-ai-sourcing\data\tiktok_market.db")

con = sqlite3.connect(str(DB))
con.execute("""
CREATE TABLE IF NOT EXISTS user_usage (
    uuid TEXT NOT NULL,
    action TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (uuid, action)
)
""")
con.commit()

cur = con.cursor()
cols = [r[1] for r in cur.execute("PRAGMA table_info(user_usage)")]
print("user_usage 字段:", cols)
print("本地库已建表")
con.close()