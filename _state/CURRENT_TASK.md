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