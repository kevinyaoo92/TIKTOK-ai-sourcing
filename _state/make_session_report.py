# -*- coding: utf-8 -*-
"""生成 SESSION_STATE.md：新会话开场一次性读入的全部信息。"""
import subprocess, sqlite3
from pathlib import Path
from datetime import datetime

ROOT = Path(r"D:\tiktok-ai-sourcing")
STATE = ROOT / "_state"
OUT = STATE / "SESSION_STATE.md"
DB = ROOT / "data" / "tiktok_market.db"

def sh(cmd):
    try:
        return subprocess.check_output(cmd, cwd=str(ROOT), shell=True,
                                       stderr=subprocess.STDOUT,
                                       encoding="utf-8", errors="replace")
    except subprocess.CalledProcessError as e:
        return f"[ERR] {e.output}"

def db_info():
    if not DB.exists():
        return "DB 不存在"
    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    lines = []
    for name, in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall():
        try:
            n = cur.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        except Exception:
            n = "?"
        lines.append(f"  - {name}: {n} 行")
    try:
        row = cur.execute(
            "SELECT value FROM app_config WHERE key='active_period'"
        ).fetchone()
        period = row[0] if row else "?"
    except Exception:
        period = "?"
    con.close()
    return f"active_period = {period}\n" + "\n".join(lines)

def read(p):
    p = STATE / p
    if not p.exists():
        return f"[缺] {p.name}"
    return f"### {p.name}\n\n" + p.read_text(encoding="utf-8", errors="replace")

parts = []
parts.append(f"# SESSION STATE — {datetime.now():%Y-%m-%d %H:%M}\n")
parts.append("## 1. Git\n\n```\n")
parts.append(sh("git log --oneline -10"))
parts.append("\n---\n")
parts.append(sh("git status --short") or "(clean)")
parts.append("\n---\n")
parts.append(sh("git branch -vv"))
parts.append("\n```\n")
parts.append("## 2. 数据库\n\n```\n" + db_info() + "\n```\n")
parts.append("## 3. _state 文件\n")
for f in ["CURRENT_TASK.md", "OPEN_ISSUES.md",
          "PRODUCT_ROADMAP.md", "PHASE1_SPEC.md",
          "WORK_ERRORS.md"]:
    parts.append(read(f) + "\n")
parts.append("## 4. 关键路径\n\n```\n")
parts.append("server.py     : frontend/server.py\n")
parts.append("user_usage.py : app/user_usage.py\n")
parts.append("前端          : frontend/static/opportunity.html\n")
parts.append("DB            : data/tiktok_market.db\n")
parts.append("```\n")
parts.append("\n## 5. 新会话开场提示\n\n")
parts.append("读 SESSION_STATE.md，然后：复述进度 → 列下一步 → 直接执行。\n")

OUT.write_text("".join(parts), encoding="utf-8")
print(f"[OK] {OUT}")
