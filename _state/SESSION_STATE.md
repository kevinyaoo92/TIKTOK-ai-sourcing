# AI 职责清单（每次新会话必读，必须遵守）

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
  0. 【先确认 VPN 已连接】——推 GitHub 必须先连 VPN，否则 push 会失败
  1. 跑 python _state\make_session_report.py 生成最新快照
  2. 更新 _state/CURRENT_TASK.md（本次进度）
  3. 更新 _state/OPEN_ISSUES.md（新问题/已解决问题）
  4. git add -A && git commit -m "..." && git push
  （如 push 失败：先确认 VPN 是否连接，再 git config http.version HTTP/1.1 重试）

如果用户说"结束了"、"收尾"、"下次继续"、"先这样"等类似结束信号，
AI 必须立刻主动提出上述四步，不能等用户问。

## 四、开场提示词模板（用户下次直接复制）
读 D:\tiktok-ai-sourcing\_state\SESSION_STATE.md
读完直接：复述进度 → 列下一步 → 从下一步开始执行。
规则：反方 → 支持方 → 中立裁判；不猜、不装懂、要证据；
改代码前先确认锚点；每关键节点更新 CURRENT_TASK.md 并提醒我 commit + push。

---

# SESSION STATE — 2026-10-08 21:55
## 1. Git

```
8b4091f 生产部署阻塞修复：task_id 熵增强到 128bit + 扩展 BACKEND 切生产地址
2f4c64a 修复：找货失败提示改中文，兜底 '找货失败，请稍后再试'
ebc804f 新增扩展安装说明 README
31ab5fd 埋点扩展：补 6 个找货漏斗事件（created/success/failed/timeout/cancelled/1688_click）
d85f5d1 修复：移除 visibilitychange → checkMiaoshouLogin 监听（误触发弹空白 Chrome）
405d20f 上线前审计修复：并发防护 + 关闭清 timer + F5 恢复 + 删测试页 + 扩展改名
9347e3d 接入正式闭环：找优质货源 → 扩展执行 → 弹窗展示 1~3 个货源（带图 + 起订量）
c5de660 收尾：POC 全阶段完成记录 + 快照更新
908eeec POC-4：完整找货闭环（GBK URL + 卡片层过滤 + 逐个详情 + 四档降级），连续 3 次成功
a3f7021 POC-3B：单商品判断器（四档降级 + 硬性淘汰），9 项边界全部通过

---
(clean)
---
* main 8b4091f [origin/main] 生产部署阻塞修复：task_id 熵增强到 128bit + 扩展 BACKEND 切生产地址

```
## 2. 数据库

```
active_period = 2026-09
  - ai_analysis_log: 2581 行
  - app_config: 1 行
  - category: 182 行
  - collection_batch: 182 行
  - keyword_metric: 28014 行
  - opportunity_analysis: 2493 行
  - sqlite_sequence: 5 行
  - user_events: 138 行
  - user_usage: 3 行
```
## 3. _state 文件
### CURRENT_TASK.md

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


## 服务器同步 + 缓存修复上线 2026-10-07 10:32

### 本次完成
- 修复缓存判据：161 个 L2 从"每次重跑 1-3 分钟"改为"9ms 秒开"
  - 旧判据 len(rows) < expected 恒成立（过滤前 vs 过滤后口径不同）
  - 新判据：rows 非空 + 至少一行 ai_summary 有内容
- related_keywords 退出正式逻辑：前端不展示、搜索不匹配、AI prompt 不消费
- 删除"建议定价"和"内容营销建议"两个前端模块
- 服务器同步 3 个文件（deepseek.py / analyze_service.py / opportunity.html）
- 服务器验证：缓存命中 9ms

### 提交
- c04474f（本地 + GitHub）

### 下一步
- 按大纲进 Phase 3（Chrome Extension POC），或按用户决定跳过 Phase 2


## POC 全阶段完成 2026-10-07 23:42

### 完成内容
- 阶段 0：supplier_finder.py 代码盘点（675 行，9 函数，10 常量）
- 阶段 1A：扩展↔后端通信层（4 个 API + 测试页 + 扩展骨架）
- 阶段 2：搜索结果页读取（标题/链接/回头率），3 次成功
- 阶段 3A：详情页读取（MAIN world 读 window.context + DOM 兜底），7 次成功
- 阶段 3B：单商品判断器（四档降级 + 硬淘汰），9 项边界通过
- 阶段 4：完整找货闭环（GBK URL + 卡片过滤 + 逐个详情 + 四档降级），3 次连续成功

### 关键技术结论
- window.context 在扩展 MAIN world 可读，最高风险解除
- [data-offer-grid-cell="true"] 选择器有效，旧 EXTRACT_CARDS_JS 可复用
- 搜索 URL 必须用 GBK 编码（1688 特性，UTF-8 会乱码）
- 三次完整跑都返回 1 个结果，命中"宽松"档

### 提交
- bce2786 POC-2
- 2a91e8e POC-3A
- a3f7021 POC-3B
- 908eeec POC-4

### 新增文件
- app/analysis/poc_judge.py（单商品判断器 + 卡片过滤）
- frontend/static/poc_test.html（测试页）
- frontharbor-extension/（扩展 4 文件）

### 未做（按专家方案）
- AI 排序 / 价格排序 / 妙手 / 服务器 Playwright 生产化

### 下一步
- 提交专家验收
- 等专家反馈决定是否进入 Phase 5 或正式上线扩展

### OPEN_ISSUES.md

# OPEN ISSUES — tiktok-ai-sourcing
最后更新: 2026-10-05 20:30
项目根: D:\tiktok-ai-sourcing

## P0 — 当前阻塞
1. ICP 备案审核中：腾讯云初审 + 陕西管局审核（7-10 工作日），等 010 电话 + 幕布拍照。
2. 域名解析 + HTTPS：等备案通过后做。
3. 1688 数据合规：TikTok 数据转卖第三方是否允许，未确认。

## P1 — 待办
1. 撤除 Basic Auth（域名 + HTTPS 上线后）。
2. 妙手集成方式确认：当前用 Playwright 走链接采集，Phase 3 才拆到 Chrome 扩展。
3. 项目卫生：C 盘空壳 tiktok-audit、云盘副本、opportunity.html.bak_* 备份清理。

## P2 — 长期
1. 评分体系回测，无成交数据。
2. featurePair 类目映射表（解决"女士连衣裙搜出童装"类问题）。
3. 多国家扩展（TH / VN / MY / PH / ID）。

## 历史已解决（归档，仅留痕）
- 9月数据采集 SOP：已全量上线（28014 关键词 / 2493 机会 / 161 L2）。
- 19 个缺失 L2：已确认数据源本身少（cohort < 3）。
- supplier_finder.py：已完整实现，弹窗返回货源。
- SKU 深度库存检查 `_check_sku_stock_deep`：20261001_114414 SKU 闭环完成。
- opportunity.html bak_finder / bak_skudims：端到端已通过。
- "不要点去找货按钮"：已放行，Phase 1 端到端验收通过。
- 用户系统 + 免费额度：已完成，find 10 / collect 5，UUID 计数。
- 部署上云：已完成（CVM 118.89.85.175，systemd，Nginx 反代）。

## 工作规则
21. Python 走文件，不用 python -c。
22. 每次对话：反方 -> 支持方 -> 中立裁判。
23. 不猜、不装懂、要证据、批量前单跑、UI 逐条核对。
24. 同一类错误不犯第二次。

## 已确认事实（防止重复排查）
- 唯一活跃项目: D:\tiktok-ai-sourcing
- 主库: data\tiktok_market.db, 45.86MB, integrity ok
- 7 张表齐全, category 182 行, keyword_metric 28014 行
- 10 个历史备份在 data\, 9/22-9/28 时间线完整
- 前端端口 8123, 已跑通
- Chrome profile: .chrome_1688, CDP 9222 已验证
- API key 和类目 TXT 用户手上有
- 前端 -> 主库 -> AI 分析链路已实测可用

## SKU 规则锁定（V1）
- SKU_FIELD_MAX_ITEMS = 8
- STOCK_BAD_WORDS = ["库存不足", "售罄", "无货", "缺货"]
- 维度顺序 = DOM 顺序
- 1 字段：读全部项库存，命中禁用词 pass
- 2 字段：第一维度逐项点击，看下级库存文本，命中即 pass
- 3+ 字段：按 2 字段处理，标记 dimension_count>=3，忽略第三维度
- 不考虑误杀，命中即停
- 评分体系 V1 冻结: Score = 100*(0.40*Demand+0.35*Conversion+0.25*Competition)

## SKU 闭环完成（20261001_114414）

### 已解决
- skuInfoMap 全量库存抓取（替代 DOM 逐个读），验证 18色×6码=108 组合全抓
- SKU 每维 <=8，任一超限 pass
- 库存 min >=2000/1000 判定
- 性别过滤 _title_matches_keyword（卡片层 + 详情层）
- AI 只做"剔除明显错的"，不排序、不截断
- 结果不排序，原顺序返回
- 保证金：不做（用户明确）
- 商家类型：保持硬编码图片签名

### 冻结版本
- supplier_finder.py: _state\supplier_finder.STABLE_20261001_114414.py
- opportunity.html:   _state\opportunity.STABLE_20261001_114414.html
- tiktok_market.db:   _state	iktok_market.STABLE_20261001_114414.db
- 规则文件:           _state\FINDER_RULES.md

### 遗留
- 1688 搜索池对"女士连衣裙"等词返回大量童装，AI 只能剔除无法纠正
- 修复路径: featurePair 硬编码映射表（未做，用户可选）

### 关键教训
1. 改 .py 后必须重启后端
2. AI 只剔除不排序，保留全量候选池
3. 规则执行不能擅自改动：整页遍历、翻页、降级、命中 3 个停
4. 回退备份要确认版本内容
5. 搜索池错的类目，AI 无法纠正，只能从搜索 URL 用 featurePair 限类目


## AI 剔除逻辑已知边界（20261001_122632）

### 已解决的问题
- 主商品判定（prompt 加规则）：AI 可识别"标题末尾的商品名为主商品"
- 赠品剔除：布套、垫、毛、附赠品 能剔
- 性别过滤：女士/男士/童装能判

### 已知无法解决的问题
- 陷阱标题：标题堆砌 N 个同义词 + 主商品是另一件东西
  例: "严选跨境手持烫衣板家用熨衣板迷你熨烫板熨衣服防烫手套隔热垫" (实物是手套)
- 原因: AI 只能读标题，读不到图片。看标题人也会判错
- 用户决定: 接受现状，不修（选 C）

### 未来可选方案（如需修复）
- A. 多模态 AI 看图片（需额外 API key）
- B. 进详情页抓规格再判（耗时翻倍）
- C. 接受现状（当前选择）


## 妙手采集闭环完成（20261001_183834）

### 已实现
- 后端 miaoshou_collector.py：open_login_page / check_login / collect_to_miaoshou
- 后端 server.py 路由：/api/miaoshou_collect, /api/miaoshou_check_login, /api/miaoshou_open_login
- 前端弹窗标题右侧：登陆妙手按钮 + 提示小字
- 前端商品卡：未登录时"妙手采集"禁用；已登录时可用
- 未登录 → 点"登陆妙手"打开登录页 → 用户登录 → 切回产品页自动检测 → 状态更新
- 采集成功 → 按钮变绿"已采集" + 下方小字提示

### check_login 判定逻辑
打开 https://erp.91miaoshou.com/common_collect_box/index?fetchType=linkCopy
读 textarea.jx-textarea__inner 是否存在。存在=已登录，不存在=未登录。
不用 Cookie 判断（登录流程中 Cookie 会提前写入 mserp，误判）。

### 冻结版本（20261001_183834）
- supplier_finder.STABLE_20261001_183834.py
- miaoshou_collector.STABLE_20261001_183834.py
- server.STABLE_20261001_183834.py
- opportunity.STABLE_20261001_183834.html
- tiktok_market.STABLE_20261001_183834.db

### 回退命令（把文件名替换成需要的版本）
Copy-Item "_state\supplier_finder.STABLE_XXX.py" "app\analysis\supplier_finder.py" -Force
Copy-Item "_state\miaoshou_collector.STABLE_XXX.py" "app\analysis\miaoshou_collector.py" -Force
Copy-Item "_state\server.STABLE_XXX.py" "frontend\server.py" -Force
Copy-Item "_state\opportunity.STABLE_XXX.html" "frontend\static\opportunity.html" -Force
重启后端即可。

### 下一步待办（未做）
- 用户系统 + 免费 5 次
- 部署上云 + ICP 备案（营业执照已改好，包含"互联网信息服务"）
- 9月数据采集 SOP 实战
- 妙手集成方式确认（当前用 Playwright 走链接采集）
- featurePair 类目映射表（解决"女士连衣裙搜出童装"类问题）



## 9月批量分析准备完成（20261002_083906）

### 已就绪
- 分析脚本: scripts/analyze/run_all_analysis.py（dry-run 通过）
- 采集脚本: scripts/collect/run_all_collection.py（已有）
- 硬编码 bug 已修: analyze_pipeline.py L380

### 关键文件
- _state/9月全量跑_操作手册.md（完整 4 步命令 + 验证 + 回退）
- _state/run_all_analysis.STABLE_20261002_083906.py
- _state/run_all_collection.STABLE_20261002_083906.py
- _state/analyze_pipeline.STABLE_20261002_083906.py
- _state/ANALYSIS_SOP.md（分析逻辑详细）

### 9月跑 4 步
1. 备份 8 月库
2. run_all_collection.py --overwrite-all --period 2026-09
3. run_all_analysis.py --period 2026-09 --limit 3 --reset（试跑验证）
4. run_all_analysis.py --period 2026-09 --reset（全量）

### 已知会空的 53 个 L2（不算失败）
- 19 个：cohort < 3（数据源不足）
- 34 个：category 表里不存在（L1 级无数据）

### 下次跑前必须确认
- force_refresh=True（脚本已硬编码）
- 后端先停
- 磁盘 >= 200MB


## 9月数据完整上线（{ts}）

### 全量采集完成
- 215 个任务，成功 215，失败 0
- keyword_metric: 28014 行（period=2026-09）
- category: 182 个 L2
- collection_batch: 182 条（每 L2 一条，bug 已修生效）

### 全量分析完成
- 215 个任务，成功 161 / 空 54 / 失败 0
- opportunity_analysis: 2493 条（period=2026-09）
- 覆盖 161 个 L2
- 等级分布：S 42 / A 170 / B 669 / C 1612
- AI 语义失败 0

### 54 个空的 L2
- 19 个：keyword_metric 有数据但清洗后 < 3 条（数据源本身少）
- 34 个：category 表里没有此 L2（TikTok 无此分类）
- 1 个新增（8月有9月无，数据波动）

### 前端 9 月切换完成
- active_period = 2026-09
- 前端顶栏"数据周期"动态读后端 active_period
- 修复：前端在 fetch active_period 回来前使用 fallback 导致传错 period
- 修复后：所有类目正常显示 9 月数据

### 本次修复的问题
1. 前端 fetch 位置错误（插进了 STATE 对象内部，导致 JS 语法错误）
2. 前端 period 硬编码 2026-08 → 改成从后端动态读
3. 前端 fetch 竞态（分析逻辑在 fetch 完成前就跑）→ 用 Promise 包裹
4. 前端 target.level2 等字段未 trim → 加 trim
5. collection_batch 覆盖 bug（batch_id 加 level1+level2 后缀）
6. analyze_pipeline.py 硬编码 "2026-08" → 改成 {period}

### 本次冻结（20261004_125556）
- _state/supplier_finder.STABLE_*.py
- _state/miaoshou_collector.STABLE_*.py
- _state/server.STABLE_*.py
- _state/opportunity.STABLE_*.html
- _state/run_all_analysis.STABLE_*.py
- _state/normalize_fullrow.STABLE_*.py
- _state/tiktok_market_202609.STABLE_*.db（45.86 MB）

### 备案进度
- 腾讯云 CVM 已购：ins-2zuo8hib，上海五区，2核4G/5Mbps，188元/1年
- 公网 IP：118.89.85.175
- ICP 备案已提交：订单号 30179108639935234
- 状态：腾讯云审核中（1-2 工作日）
- 待办：接听 010 电话、收幕布拍照、管局审核

### 8月数据
- 已被 9 月覆盖（决定），备份在 data/tiktok_market_202608_backup.db

### 待办（下一次）
- 部署产品到腾讯云 CVM（备案通过后）
- 用户系统 + 免费 5 次
- 域名解析 + HTTPS
- 妙手采集稳定性验证
- Git 首次提交
- 清理项目根目录临时脚本


## Git 提醒规则（2026-10-04 15:52 建立）

Git 已配置完成。仓库：https://github.com/kevinyaoo92/TIKTOK-ai-sourcing

### 关键节点必须提醒用户 commit + push

以下节点完成后，我主动提醒用户执行：
```
cd D:\tiktok-ai-sourcing
git add -A
git commit -m "描述"
git push
```

触发条件：
1. 每次改完代码、测试通过后
2. 月度数据更新完成后
3. 大功能模块完成时
4. 部署上云前
5. 备案通过后
6. 数据库冻结前后（数据库单独备份，代码进 Git）

不提醒：只改临时脚本、只改数据文件、只做诊断。


## 今日收尾（2026-10-04 15:58）

### 完成事项
1. 9月数据全量上线：keyword_metric 28014 行 / opportunity_analysis 2493 条 / 161 个 L2 有数据
2. active_period 已切到 2026-09，前端验证 3 个类目正常
3. 妙手采集端到端验证 3/3 成功（女装/美妆/宠粮）
4. 搜索词优化：前端改用 name 全称（不截断斜杠）；加地域词过滤
5. 妙手登录检测改为只读 Cookie，不打开页面
6. 妙手采集页删除所有主动检测逻辑（不点采集不弹妙手页）
7. ICP 备案已提交（订单号 30179108639935234），等腾讯云初审电话
8. Git 配置完成 + 首次提交 + 推 GitHub（bd1442c）

### 待用户处理
- 保持两个手机号开机，接听 010 开头电话（腾讯云初审，1-2 工作日）
- 收到腾讯云幕布后，法人本人拍照上传
- 管局审核 7-10 工作日（陕西）

### 下次会话开场
读 `D:\tiktok-ai-sourcing\_state\OPEN_ISSUES.md`


## 路线与规格已冻结（2026-10-05 14:58）

- 产品路线：_state/PRODUCT_ROADMAP.md
- Phase 1 规格：_state/PHASE1_SPEC.md
- 当前阶段：Phase 1 开工，从服务器初始化开始


## 状态文件维护规则（2026-10-05 15:02 建立）

AI 助手必须主动维护以下文件，不等用户提醒：

### 每次关键节点后主动更新
1. CURRENT_TASK.md —— 当前在哪一步、下一步做什么、未完成任务
2. OPEN_ISSUES.md —— 已完成、新发现的问题、待办
3. PRODUCT_ROADMAP.md —— 路线是否变化
4. PHASE1_SPEC.md —— Phase 1 参数是否变化

### 关键节点定义
- 每完成一个可交付动作（部署完成、功能开发完成、测试通过）
- 每次会话即将结束
- 每次用户确认新的决策
- 每次发现新的风险

### 主动提醒方式
在关键节点后，AI 主动说：
「[状态更新] 刚才完成了 XXX，已更新 CURRENT_TASK.md，下一步是 YYY。」
不需要用户问。

### 跨会话接续标准
新会话开场，用户只需说：
「读 D:\tiktok-ai-sourcing\_state\CURRENT_TASK.md」
AI 读完后应能：
1. 复述当前进度
2. 列出下一步动作
3. 直接继续，不问废话

### 文件清单（新会话必须能一次读全）
- OPEN_ISSUES.md（总入口）
- PRODUCT_ROADMAP.md
- PHASE1_SPEC.md
- CURRENT_TASK.md（最常更新）
- MONTHLY_UPDATE_SOP.md
- ANALYSIS_SOP.md
- FINDER_RULES.md
- WORK_ERRORS.md

### PRODUCT_ROADMAP.md

# 战略决策 2026-10-05（专家研判结论）

## 结论

技术可行性已验证（真实找货 + 妙手 + 额度已跑通）。
现在停止证明技术，开始证明"用户真的需要，且愿意付钱"。

## 具体决策

1. 服务器 Playwright 找货 + 妙手采集
   - 保留为内部验证资产
   - 不再作为正式架构继续投资
   - 不删除、不优化、不扩展

2. 下一阶段：补 Phase 2（商业验证）
   - 用户侧"找货"改回占位 + 申请体验
   - 补埋点：机会点击 / 找货点击 / 留资 / 付费意愿
   - 观察漏斗，决定是否进 Phase 3

3. 通过后：最小 Extension POC
   - 只验证：网站 → 扩展 → 用户 1688 → 读一个商品三字段 → 返回
   - POC PASS 标准：连续成功 3 次
   - 不做妙手、不做批量、不做 AI、不做支付

## 不做

- 服务器并发找货优化
- Playwright 架构化
- 妙手架构化
- 双轨执行（服务器 + 扩展）

---

（以下是原 PRODUCT_ROADMAP 内容）
# 产品路线图

最后更新: 2026-10-05 14:58

## 六阶段串行，不并行，不越阶段

### Phase 1：Web SaaS 上线
目标：真实用户能完成核心 AI 选品流程。
阻塞：无。当前阶段。

### Phase 2：验证产品价值
目标：观察用户使用 AI 选品、点「找1688货源」、留联系方式、愿付费。
判据：无需求则停止后续插件开发。

### Phase 3：Chrome Extension POC
目标：验证扩展能否在用户自己 1688 环境读一个商品数据回传。
PASS 标准（6 条，全过才算）：
1. 进入 1688 搜索结果页
2. 找到至少 1 个真实商品
3. 读取标题
4. 读取链接
5. 读取价格
6. 三个字段有效非空，数据发送到后端，连续成功 3 次
POC 不做：妙手、SKU、AI、评分、批量、完整用户系统、支付、上架、UI。
FAIL：不投完整插件。

### Phase 4：完整 1688 找货
云端管逻辑、任务、权限、配额。
插件管页面操作、数据读取。
核心逻辑不进插件。

### Phase 5：妙手采集
1688 找货稳定后再做。

### Phase 6：商业化
免费额度、订阅、支付、权限、服务器端额度控制、插件权限校验、Chrome Web Store 发布。

## 执行纪律
1. 不并行
2. 不提前优化
3. 不越阶段
4. 新需求进待办

### PHASE1_SPEC.md

# Phase 1 规格（Web SaaS）

最后更新: 2026-10-05 14:58

## 参数（已确认）

- 免费额度：找货 10 次，采集 5 次，独立计数
- 用户识别：匿名 UUID，localStorage + Cookie 双存
- 清缓存可绕过：接受，不防
- 换设备重置：接受
- 额度用完：显示付费提示（实际付费后续开发）
- 付费时注册：用户点付费入口时引导注册手机号/邮箱
- 注册请求携带匿名 UUID，服务器尝试挂账
- 匿名数据迁移：点击记录、收藏功能未开发，先预留 UUID 传递接口

## Phase 1 任务

1. 云端部署（服务器 118.89.85.175，Ubuntu 22.04）
2. 用户系统（匿名 UUID + user_usage 计数表）
3. 数据库上传（tiktok_market.db）
4. 前端流程（类目  机会  详情  1688 搜索词  点「找1688货源」）
5. 找货占位（扣额度 + 显示"功能开发中/申请体验"）
6. 采集占位（同）
7. Nginx 反代 + HTTPS（备案后）
8. 端到端测试

## Phase 1 不做

Chrome 扩展、实时找货、妙手采集、支付、多国家、品牌词过滤、featurePair、点击记录收藏迁移。

## Phase 1 验收标准

真实用户操作：
打开站点  选类目  看机会列表  点详情  看 1688 搜索词  点「找1688货源」 看到占位提示。
额度从 10 递减到 0。0 时点找货显示付费提示。

## 任务状态

- [x] 服务器初始化
- [x] 数据库上传
- [x] 后端部署
- [x] 前端部署
- [x] Nginx 反代
- [ ] 域名解析（等备案）
- [ ] HTTPS（等备案）
- [x] 用户系统
- [x] 免费额度
- [x] 找货占位
- [x] 端到端测试

### WORK_ERRORS.md

# WORK ERRORS


## 2026-10-05 18:57 插入代码用全文 in 判断导致跳过

### 现象
- find_suppliers 成功后没调用 refreshQuota()，额度条不会自动减。
- 原因是插入脚本用了 `if "refreshQuota();" not in s.split("function openDetail")[0]:`，而 DOMContentLoaded 回调里已有一句 refreshQuota()，落在 split 前，判断为已存在，跳过插入。

### 教训
- 插入代码时，判断“是否已插入”必须用**锚点上下文**判断，如 `anchor + 新代码` 是否同时出现。
- 不能用全文 `in` 判断，尤其当插入的代码在别处已出现时。
- 锚点选唯一、紧邻、可控的那一行。

### 修复
- 用 `old = "FINDER.taskId = data.task_id;\n        FINDER.timer = setInterval(pollFinderTask, 800);"` 作为锚点，前后判断 refreshQuota 是否已插入。

## 4. 关键路径

```
server.py     : frontend/server.py
user_usage.py : app/user_usage.py
前端          : frontend/static/opportunity.html
DB            : data/tiktok_market.db
```

## 5. 本次快照生成时间

2026-10-08 21:55:06
