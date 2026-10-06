# 当前任务（进度存根）

最后更新: 2026-10-06 11:45

## 当前状态

- 第 4 个纠偏任务已完成（找货入口改为正式产品形态）
- 当前"找优质货源"已按正式产品形态展示
- 真实找货暂未执行（点"立即找货"仅提示"正在准备中"）
- Playwright 继续冻结（内部验证资产，不继续投资）
- Phase 2 进入真实用户行为验证阶段

## 已完成能力速览

- Web V1 类目路径已修复（app/data/泰国TK官方类目.txt，不再依赖 Desktop）
- 服务器部署：CVM 118.89.85.175，systemd frontharbor，Nginx 反代 + Basic Auth
- 9 月数据上线：keyword_metric 28014 / opportunity_analysis 2493 / 161 L2
- 用户系统：匿名 UUID + user_usage 计数（find 10 / collect 5）
- 用户行为埋点：user_events 表 + POST /api/track
  - category_selected / opportunity_view / opportunity_detail
  - search_1688_click / find_supplier_click / apply_experience（历史）
  - confirm_find_supplier
- 找货入口正式 UI：找优质货源 + 会员专享弹窗 + 立即找货（toast 准备中）

## 下一步

- 等真实用户数据验证 Phase 2 商业价值
- ICP 备案通过后：域名解析 + HTTPS + 撤 Basic Auth
- 观察漏斗，决定是否进入 Phase 3（最小 Extension POC）

## 服务器状态

- IP: 118.89.85.175
- 实例 ID: ins-2zuo8hib
- 服务: frontharbor.service (systemd, 自动重启)
- 后端监听: 0.0.0.0:8123
- Nginx: 80 端口反代 + Basic Auth
- Basic Auth 用户名: front
- 类目文件: /home/ubuntu/frontharbor/app/app/data/泰国TK官方类目.txt
- systemd override: /etc/systemd/system/frontharbor.service.d/override.conf
- Nginx 配置: /etc/nginx/sites-available/frontharbor
- 前端访问: http://118.89.85.175（带 Basic Auth）

## 未完成任务清单（Phase 1 + Phase 2）

- [x] SSH 登录服务器
- [x] 服务器初始化
- [x] 数据库上传
- [x] 后端部署
- [x] 前端部署
- [x] Nginx 反代
- [x] 访问控制（Basic Auth 临时挡外网）
- [x] 用户系统（匿名 UUID）
- [x] 免费额度（find 10 / collect 5）
- [x] 找货入口（正式产品形态）
- [x] 端到端测试
- [x] 用户行为埋点（Phase 2）
- [x] 导航返回逐层修复
- [ ] 域名解析（等备案）
- [ ] HTTPS（等备案）
- [ ] 真实用户数据观察（Phase 2 进行中）
- [ ] 已知未修 bug：切标签偶尔弹空白 Chrome（搁置）

## 新会话开场白

读以下文件，然后继续当前阶段开发：
- _state/CURRENT_TASK.md
- _state/OPEN_ISSUES.md
- _state/PRODUCT_ROADMAP.md
- _state/PHASE1_SPEC.md

读完直接：复述进度 → 列下一步 → 开始执行。

## 自动快照 2026-10-05 18:10

### 最近 10 次提交
47de16d Phase 1: 鐢ㄦ埛棰濆害鍚庣锛坒ind 10 / collect 5锛孶UID 璁℃暟锛?d5e9b5f init_user_usage 鏀寔 --db 鍙傛暟
1f77418 Phase 1: 鍔?user_usage 寤鸿〃鑴氭湰
6a6d725 Phase 1: Nginx 鍙嶄唬 + Basic Auth 瀹屾垚 2026-10-05 17:29
11c465f Phase 1: 鏈嶅姟鍣ㄩ儴缃插畬鎴?2026-10-05 17:20
30e6781 杩涘害瀛樻牴鏇存柊 2026-10-05 15:17
20777d7 鏀跺熬锛氭洿鏂?OPEN_ISSUES.md 璁板綍浠婃棩鏈€缁堢姸鎬?bd1442c 棣栨鎻愪氦锛?鏈堟暟鎹笂绾匡紝濡欐墜閲囬泦闂幆锛屽妗堝凡鎻愪氦

### 当前未提交的修改
 M frontend/static/opportunity.html

### 恢复指引
新会话开场读以下文件接上：
- _state/CURRENT_TASK.md
- _state/OPEN_ISSUES.md
- _state/PRODUCT_ROADMAP.md
- _state/PHASE1_SPEC.md


## 额度链路闭环 2026-10-05 18:31

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


## Phase 1 端到端验收通过 2026-10-05 18:59

### 已验收
- 打开站点 → 选类目 → 看机会列表 → 点详情 → 看 1688 搜索词：通过
- 点「去找1688货源」→ 弹窗返回货源：通过
- 关闭弹窗 → 额度自动 10→9：通过
- 额度耗尽 → 显示"免费找货次数已用完（10/10）。升级付费后可继续使用"：通过
- 后端扣减逻辑单测：find 第 11 次拒绝，collect 第 6 次拒绝
- get_usage 返回 used/limit/remaining 正确

### 本次提交
- 3428828 额度查询接口 + 前端额度显示
- 590cc99 清理临时文件 + PHASE1_SPEC 勾选进度
- 61c1a59 修复 find_suppliers 成功后未自动刷新额度 + WORK_ERRORS

### Phase 1 剩余
- [ ] 域名解析（等备案）
- [ ] HTTPS（等备案）
- [ ] 备案通过后部署到正式域名

### 下一次会话开场
读以下文件接上：
- _state/CURRENT_TASK.md
- _state/OPEN_ISSUES.md
- _state/PRODUCT_ROADMAP.md
- _state/PHASE1_SPEC.md


## 冻结服务器 Playwright 正式开发 2026-10-05 21:45

### 本次完成
- 新建 _state/ARCHITECTURE_FREEZE.md：冻结声明
- app/analysis/supplier_finder.py 头部加 [FROZEN 2026-10-05] 注释
- app/analysis/miaoshou_collector.py 头部加 [FROZEN 2026-10-05] 注释

### 冻结定位
- supplier_finder.py：保留，冻结，不继续扩展
- miaoshou_collector.py：保留，冻结，不继续扩展
- 服务器 Chrome（.chrome_1688 + CDP 9222）：保留测试用途
- 以上定位为 Internal Validation / Reference Implementation
- 正式执行层方向：Chrome Extension + 用户自己的 Chrome

### 未修改
- 现有 Playwright 执行逻辑未动
- 未做 Chrome Extension、未删代码、未改前端、未碰配额/支付/AI


## Phase 2 埋点 + 导航修复 2026-10-06 09:52

### 本次完成
- 新建 app/user_events.py：user_events 表 + record_event()
- server.py 加 POST /api/track 路由
- opportunity.html 加 5 个埋点：category_selected / opportunity_view / opportunity_detail / search_1688_click / find_supplier_click
- 修复导航：showView + goBack 统一管理，所有返回按钮只回上一界面，不再直接跳首页

### 数据表
- user_events(id, anonymous_id, event_name, event_time, category, opportunity_id)
- 存在 data/tiktok_market.db

### 验证
- 接口测试：POST /api/track 返回 ok=true
- 浏览器全流程：category_selected + opportunity_view + find_supplier_click 已记录
- 导航 5 条测试通过

### 未做（按任务要求不做）
- contact_submit 预留未做（当前无留资 UI）
- 未做支付/会员/统计后台
- 未碰 1688 / 妙手 / Playwright / Extension


## 找货入口改回占位 + apply_experience 埋点 2026-10-06 10:53

### 本次完成
- 列表卡片 + 详情页按钮：去找货 → 申请体验
- 新增 showApplyExperienceModal：弹"找货功能正在内测"提示
- 点击处理：find_supplier_click + apply_experience 双埋点
- user_events 白名单加 apply_experience
- openFinderModal 保留为死代码（无调用点）
- 提交：ca95954

## 找货入口正式产品形态（找优质货源） 2026-10-06 11:15

### 本次完成
- 按钮文案：申请体验 → 找优质货源（列表卡片 + 详情页）
- 弹窗重写为 showFindSourceModal：
  找优质货源 / 会员专享功能 / 你当前有 10 次免费体验机会 /
  使用后系统自动寻找 1688 货源 / [稍后再说] [立即找货]
- 点"立即找货"：track(confirm_find_supplier) + toast "找货功能正在准备中，敬请期待。"
- 预留 TODO：正式上线替换为 Chrome Extension 通道或启用已冻结的服务器 Playwright
- user_events 白名单加 confirm_find_supplier
- 未启动 Playwright / Chrome / 1688 自动找货
- 提交：0ff15a1

## 已知未修 bug（搁置） 2026-10-06 11:30

### 现象
切浏览器标签时偶尔弹空白 Chrome。

### 根因
opportunity.html 620 行 visibilitychange 监听 → checkMiaoshouLogin()
→ POST /api/miaoshou_check_login → miaoshou_collector.check_login()
→ _ensure_chrome_debug() → subprocess.Popen 启动 Chrome

### 修复方案（未执行）
删 opportunity.html 620-625 行 visibilitychange 监听，加注释说明。
只改前端 1 处，不动后端冻结代码。

### 搁置原因
不频繁，且妙手正式执行层会搬到 Chrome Extension，这段逻辑会整体重写。