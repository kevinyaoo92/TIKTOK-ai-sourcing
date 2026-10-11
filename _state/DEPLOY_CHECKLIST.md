## 总原则（2026-10-11 确立）

**一切工作向线上靠拢和倾斜。**

- 线上 `118.89.85.175` 是唯一权威运行环境
- 本地 `D:\tiktok-ai-sourcing` 是开发环境，验证通过后立即部署
- 禁止"只在本地验证、不部署"的工作方式
- 每个功能改动完成后，默认动作是：本地验证 → 部署线上 → 线上验证
- 扩展 BACKEND 默认指向 `http://118.89.85.175`（仅本地调试时临时切 127.0.0.1）

---

# 部署检查清单（SOP）

最后更新：2026-10-11
适用场景：本地代码需部署到线上服务器 `118.89.85.175`

## 一、部署前（必做）

### 1.1 版本确认
- [ ] 本地 `git log --oneline -1` 确认目标 commit
- [ ] 线上 `ssh ubuntu@118.89.85.175 "cd /home/ubuntu/frontharbor/app && git log --oneline -1"` 确认当前版本
- [ ] 本地领先线上多少提交，逐个过一遍，确认全都要部署

### 1.2 **扩展 BACKEND 与环境一致（易漏点）**
- [ ] 打开 `frontharbor-extension/background.js` 第 6-8 行
- [ ] **测线上**：`const BACKEND = "http://118.89.85.175";`（127.0.0.1 那行注释掉）
- [ ] **测本地**：`const BACKEND = "http://127.0.0.1:8123";`（118 那行注释掉）
- [ ] 确认后**必须重装扩展**（移除 → 重新加载），仅刷新图标不够（Service Worker 缓存）

### 1.3 服务器侧备份（防回滚）
- [ ] `ssh ubuntu@118.89.85.175 "cd /home/ubuntu/frontharbor/app && git bundle create /home/ubuntu/backup_before_XXX.bundle --all"`
- [ ] 确认备份文件生成

### 1.4 服务器工作区干净
- [ ] `ssh ubuntu@118.89.85.175 "cd /home/ubuntu/frontharbor/app && git status --short"` 应无输出
- [ ] 若有改动，先 `git stash` 或查明原因，禁止直接 merge

## 二、部署步骤

1. 本地打 bundle：`git bundle create _state\deploy_XXX.bundle --all`
2. 验证 bundle：`git bundle verify _state\deploy_XXX.bundle`
3. scp 传输：`scp _state\deploy_XXX.bundle ubuntu@118.89.85.175:/home/ubuntu/deploy_XXX.bundle`
4. 服务器 fetch：`ssh ... "cd /home/ubuntu/frontharbor/app && git fetch /home/ubuntu/deploy_XXX.bundle main:refs/remotes/bundle/deployXXX"`
5. 服务器 merge：`ssh ... "git merge --ff-only refs/remotes/bundle/deployXXX"`
6. 重启服务：`ssh ... "sudo systemctl restart frontharbor"`
7. 检查服务：`ssh ... "systemctl is-active frontharbor"` 应 `active`

## 三、部署后检查（7 项，全过才算成功）

| # | 检查 | 判定标准 |
|---|---|---|
| 1 | 版本号 | `git log -1` 输出目标 commit |
| 2 | 服务状态 | `systemctl is-active frontharbor` = active，无新增错误日志 |
| 3 | 前端页面 | 打开 `http://118.89.85.175/`，选类目进第二界面正常 |
| 4 | 妙手按钮 | 找货弹窗有"登陆妙手"，卡片有"妙手采集"+"进入妙手采集箱" |
| 5 | 1688 找货回归 | 跑一次找货，结果正常（成功 1-3 个 或 提示无货源） |
| 6 | 数据库兼容性 | 关键表行数未异常变化（keyword_metric 28014 / opportunity_analysis 2493） |
| 7 | 扩展任务接口 | `/api/poc_task/pending` 无 Basic Auth 返回 200，其他 API 保持 401 |

**任一项不过** → 立即停下报告，不继续。

## 四、回滚方案

若部署后发现问题：
1. `ssh ... "cd /home/ubuntu/frontharbor/app && git reset --hard <旧commit>"`
2. `ssh ... "sudo systemctl restart frontharbor"`
3. 检查服务 active + 页面正常

## 五、已知易漏点（防重犯）

1. **扩展 BACKEND 未切环境** —— 本次踩坑，测线上时扩展还在轮询本地
2. **Service Worker 缓存** —— 改 background.js 后必须移除重装扩展，刷新图标不够
3. **服务器连不上 GitHub** —— 只能用 bundle 方式
4. **中文参数传参** —— 用 Python 脚本，不用 PowerShell
5. **服务器工作区不干净** —— merge 会冲突

## 六、验证边界（不可越界）

以下两项**不测**，报告里明确保留为"未验证"：
- 真实妙手采集（会消耗配额）
- 扩展"登陆妙手"完整流程（需云端装扩展）