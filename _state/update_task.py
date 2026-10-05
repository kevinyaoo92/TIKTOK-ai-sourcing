from pathlib import Path
from datetime import datetime

p = Path(r"D:\tiktok-ai-sourcing\_state\CURRENT_TASK.md")
s = p.read_text(encoding="utf-8")

stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
block = f"""

## 额度链路闭环 {stamp}

### 本次完成
- 后端新增 `app/user_usage.py::get_usage(db_path, uuid)`：只查不减，返回 find/collect 的 used/limit/remaining。
- 后端新增 `GET /api/usage?uuid=...` 路由（`frontend/server.py::do_GET`）。
- 前端 `opportunity.html` 顶部新增 `#quota-bar`，页面加载调用 `refreshQuota()` 显示 "找货剩余 X/10 · 采集剩余 Y/5"。
- 找货成功后自动调用 `refreshQuota()` 刷新额度。
- 妙手采集成功后自动调用 `refreshQuota()` 刷新额度。

### 验证结果
- `GET /api/usage?uuid=test-uuid-001` → 200，返回 find/collect 双额度。
- 浏览器：新 UUID 显示 10/10；点一次找货后显示 9/10；额度扣减生效。

### 关键修复
- 路由误插到 `do_POST`，前端是 GET 请求导致 404；已挪到 `do_GET` 的 `/api/active_period` 前。
- `do_GET` 已有 `qs = parse_qs(parsed.query)`，路由内直接引用 `qs`。

### 剩余 Phase 1 任务
- [ ] 端到端额度耗尽测试（找货点到 0 / 采集点到 0）
- [ ] PHASE1_SPEC.md 任务状态同步勾选
- [ ] OPEN_ISSUES.md 头部 P0 清理
- [ ] 域名解析 + HTTPS（等备案）
"""
s = s.rstrip() + "\n" + block
p.write_text(s, encoding="utf-8")
print("CURRENT_TASK.md updated")
