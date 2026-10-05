import argparse
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(r"D:\tiktok-ai-sourcing\data\tiktok_market.db")

ap = argparse.ArgumentParser()
ap.add_argument("--db", default=str(DEFAULT_DB))
args = ap.parse_args()

db = Path(args.db)
print(f"目标库: {db}")
if not db.exists():
    raise SystemExit(f"数据库不存在: {db}")

con = sqlite3.connect(str(db))
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
print("建表完成")
con.close()