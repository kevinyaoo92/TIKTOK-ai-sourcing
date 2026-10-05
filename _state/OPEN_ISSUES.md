# OPEN ISSUES — tiktok-ai-sourcing
最后更新: 20260929_170123
项目根: D:\tiktok-ai-sourcing

## P0 数据与月度更新
1. 9月数据采集 SOP 未实战：脚本参数、period 切换、中间文件路径、备份时机、失败回退。
2. keyword_metric 里 19 个缺失 L2 是否有数据，未查。
3. 2522 vs 3568 口径未对齐。
4. ai_analysis_log 7310 行 vs opportunity_analysis 2522 行，差异原因未查。

## P0 代码真实状态
5. supplier_finder.py 是完整实现还是 stub，未查。
6. SKU 深度库存检查后端是否存在 _check_sku_stock_deep，未查。
7. opportunity.html 的 bak_finder / bak_skudims 对应功能状态，未查。
8. 查清前，不要点"去找货"按钮。

## P1 商业化阻塞
9. 营业执照经营范围变更 -> ICP 备案。
10. 1688 数据合规：TikTok 数据转卖第三方是否允许，未确认。
11. 妙手集成：插件 vs API，未定。
12. 用户系统 + 免费 5 次，未开发。
13. 部署上云、域名解析、HTTPS，未开始。

## P1 项目卫生
14. C 盘空壳 tiktok-audit 和云盘副本如何处理。
15. opportunity.html.bak_* 20+ 个备份文件清理。
16. Git 配置、临时脚本清理。
17. 评分体系回测，无成交数据。

## 待用户提供
18. DeepSeek API key 位置。
19. 官方类目 TXT 位置。
20. 1688 五项指标位置（找货功能前置）。

## 工作规则
21. Python 走文件，不用 python -c。
22. 每次对话：反方 -> 支持方 -> 中立裁判。
23. 不猜、不装懂、要证据、批量前单跑、UI 逐条核对。
24. 同一类错误不犯第二次。

## 已确认事实（防止重复排查）
- 唯一活跃项目: D:\tiktok-ai-sourcing
- 主库: data\tiktok_market.db, 45.86MB, integrity ok
- 7 张表齐全, category 181 行, keyword_metric 27954 行
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

## 未解决问题汇总（按优先级）
P0: 9月采集 SOP / 19 缺失 L2 / supplier_finder 状态 / SKU 代码状态
P1: ICP 备案 / 数据合规 / 妙手 / 用户系统 / 部署
P2: 项目卫生 / 备份清理 / Git

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
