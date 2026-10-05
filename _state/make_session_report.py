# -*- coding: utf-8 -*-
"""生成 SESSION_STATE.md：新会话开场一次性读入的全部信息。"""
import subprocess, sqlite3
from pathlib import Path
from datetime import datetime

ROOT = Path(r"D:\tiktok-ai-sourcing")
STATE = ROOT / "_state"
OUT = STATE / "SESSION_STATE.md"
DB = ROOT / "data" / "tiktok_market.db"

AI_RULES = """# AI 职责清单（每次新会话必读，必须遵守）

## 一、开场动作
1. 读完本文件后，先复述当前进度（一段话）
2. 列出下一步动作
3. 从下一步动作开始执行
4. 不要问废话、不要假装已读、不要猜

## 二、工作规则
- 分析采用：反方 → 支持方 → 中立裁判
- 不猜、不装懂、要证据
- 改代码前先 Select-String 确认锚点唯一
- 改代码用 Python 脚本改文件（read_text/replace/write_text），不用 PowerShell 多行替换
- 判断"是否已插入"用锚点上下文，不用全文 in
- 改完立刻 grep 验证 + 重启 + 测接口

## 三、任务结束前【AI 必须主动提醒用户】
任务即将结束时，AI 必须主动、明确地提醒用户执行以下四步，
不要说"你可以考虑"，要直接说"现在请执行"：
  1. 跑 python _state\make_session_report.py 生成最新快照
  2. 更新 _state/CURRENT_TASK.md（本次进度）
  3. 更新 _state/OPEN_ISSUES.md（新问题/已解决问题）
  4. git add -A && git commit -m "..." && git push

如果用户说"结束了"、"收尾"、"下次继续"、"先这样"等类似结束信号，
AI 必须立刻主动提出上述四步，不能等用户问。

## 四、开场提示词模板（用户下次直接复制）
读 D:\\tiktok-ai-sourcing\\_state\\SESSION_STATE.md
读完直接：复述进度 → 列下一步 → 从下一步开始执行。
规则：反方 → 支持方 → 中立裁判；不猜、不装懂、要证据；
改代码前先确认锚点；每关键节点更新 CURRENT_TASK.md 并提醒我 commit + push。

---

"""

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
parts.append(AI_RULES)
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
parts.append("\n## 5. 本次快照生成时间\n\n")
parts.append(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n")

OUT.write_text("".join(parts), encoding="utf-8")
print(f"[OK] {OUT}")
