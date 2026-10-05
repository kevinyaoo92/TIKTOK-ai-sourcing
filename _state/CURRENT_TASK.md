# 当前任务（进度存根）

最后更新: 2026-10-05 15:17

## 这次会话完成

- 服务器已购并 SSH 登录成功（Ubuntu 22.04.5，IP 118.89.85.175）
- 服务器已装：python3-pip / python3-venv / git / nginx
- Git 已配置 + 首次提交推 GitHub（bd1442c）
- 9 月数据完整上线（28014 行关键词，2493 条机会）
- 妙手采集端到端验证 3/3 成功
- 搜索词优化（name 全称 + 地区词过滤）
- ICP 备案已提交，等腾讯云初审电话
- 产品路线 + Phase 1 规格文档化

## 下一步

- 服务器上装 Python 依赖（从 requirements.txt）
- 上传 tiktok_market.db（45.86MB）到服务器
- 部署后端服务（systemd 守护）
- 部署前端服务
- Nginx 反代配置
- 开发用户系统（匿名 UUID + 额度计数）

## 未完成任务清单（从 PHASE1_SPEC.md 同步）

- [x] SSH 登录服务器
- [x] 服务器初始化
- [ ] 数据库上传
- [ ] 后端部署
- [ ] 前端部署
- [ ] Nginx 反代
- [ ] 域名解析（等备案）
- [ ] HTTPS（等备案）
- [ ] 用户系统
- [ ] 免费额度
- [ ] 找货占位
- [ ] 端到端测试

## 服务器信息

- IP: 118.89.85.175
- 实例 ID: ins-2zuo8hib
- 系统: Ubuntu 22.04.5 LTS
- Python: 3.10.12
- 磁盘: 20G，剩 15G
- 内存: 3.6G

## 新会话开场白

读以下文件，然后继续 Phase 1 开发：
- _state/CURRENT_TASK.md
- _state/OPEN_ISSUES.md
- _state/PRODUCT_ROADMAP.md
- _state/PHASE1_SPEC.md

读完直接：复述进度  列下一步  开始执行。