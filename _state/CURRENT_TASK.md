# 当前任务（进度存根）

最后更新: 2026-10-05 17:29

## 这次会话完成

- Nginx 反代配置完成（80 端口 → 8123）
- HTTP Basic Auth 已启用（用户 front，密码用户自己设的）
- http://118.89.85.175 外网可访问，带密码保护

## 下一步

- 开发用户系统（匿名 UUID + 额度计数）
- 开发免费额度逻辑（找货 10 次 / 采集 5 次）
- 开发找货按钮占位
- 域名解析（等备案通过）
- HTTPS（等备案通过）

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

## 未完成任务清单

- [x] SSH 登录服务器
- [x] 服务器初始化
- [x] 数据库上传
- [x] 后端部署
- [x] 前端部署
- [x] Nginx 反代
- [x] 访问控制（Basic Auth 临时挡外网）
- [ ] 用户系统
- [ ] 免费额度
- [ ] 找货占位
- [ ] 域名解析（等备案）
- [ ] HTTPS（等备案）
- [ ] 端到端测试

## 新会话开场白

读以下文件，然后继续 Phase 1 开发：
- _state/CURRENT_TASK.md
- _state/OPEN_ISSUES.md
- _state/PRODUCT_ROADMAP.md
- _state/PHASE1_SPEC.md

读完直接：复述进度 → 列下一步 → 开始执行。
---

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
