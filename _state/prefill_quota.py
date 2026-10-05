import sqlite3
from pathlib import Path
from datetime import datetime

DB = Path(r"D:\tiktok-ai-sourcing\data\tiktok_market.db")
UUID = "test-uuid-001"
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

con = sqlite3.connect(str(DB))
cur = con.cursor()
for action, cnt in [("find", 10), ("collect", 5)]:
    cur.execute(
        "INSERT OR REPLACE INTO user_usage (uuid, action, count, created_at, updated_at) VALUES (?,?,?,?,?)",
        (UUID, action, cnt, now, now),
    )
con.commit()
con.close()
print(f"prefilled {UUID}: find=10, collect=5")
