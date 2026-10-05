# 当前任务（进度存根）

最后更新: 2026-10-05 17:20

## 这次会话完成

- 服务器 Phase 1 部署完成
- 后端服务通过 systemd 稳定运行（frontharbor.service）
- 监听 0.0.0.0:8123，外网可访问
- 类目 TXT 通过环境变量 TIKTOK_CATEGORY_FILE 指向正确路径
- 浏览器访问 http://118.89.85.175:8123 全部功能正常

## 下一步

- Nginx 反代（80 → 8123），去掉端口号访问
- 加基础访问限制（防止数据被公开抓取）
- 开发用户系统（匿名 UUID + 额度计数）
- 开发免费额度逻辑（找货 10 次 / 采集 5 次）
- 开发找货按钮占位

## 服务器状态

- IP: 118.89.85.175
- 实例 ID: ins-2zuo8hib
- 服务: frontharbor.service (systemd, 自动重启)
- 监听: 0.0.0.0:8123
- 类目文件: /home/ubuntu/frontharbor/app/app/data/泰国TK官方类目.txt
- systemd override: /etc/systemd/system/frontharbor.service.d/override.conf
- 前端访问: http://118.89.85.175:8123

## 未完成任务清单

- [x] SSH 登录服务器
- [x] 服务器初始化
- [x] 数据库上传
- [x] 后端部署
- [x] 前端部署（静态文件可访问）
- [ ] Nginx 反代
- [ ] 访问控制（用户系统上线前先挡外网）
- [ ] 域名解析（等备案）
- [ ] HTTPS（等备案）
- [ ] 用户系统
- [ ] 免费额度
- [ ] 找货占位
- [ ] 端到端测试

## 新会话开场白

读以下文件，然后继续 Phase 1 开发：
- _state/CURRENT_TASK.md
- _state/OPEN_ISSUES.md
- _state/PRODUCT_ROADMAP.md
- _state/PHASE1_SPEC.md

读完直接：复述进度 → 列下一步 → 开始执行。