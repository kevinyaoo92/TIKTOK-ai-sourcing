# 月度数据更新 SOP

本文件由 
2026-10-04 13:02
 生成。
下次月度更新时，AI 助手先读本文件，按流程执行，不重复摸索。

## 一、更新前准备（5 分钟）

1. 确认 TikTok Seller Center 数据已更新（手动打开看月份）
2. 确认 .chrome_1688 / .run/profile 里 TikTok 已登录：
   python scripts\collect\prepare_login.py
   看到 [OK] 已登录 才能继续
3. 备份当前库：
   Copy-Item "D:\tiktok-ai-sourcing\data\tiktok_market.db" "D:\tiktok-ai-sourcing\data\tiktok_market_YYYYMM_backup.db"
4. 停后端：
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*frontend*server.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

## 二、采集（3-26 小时）

命令：
  cd D:\tiktok-ai-sourcing
  python scripts\collect\run_all_collection.py --mode fullrow --page-size 30 --source hot+rising --overwrite-all --period YYYY-MM

关键点：
- --period 填新月，如 2026-10
- --overwrite-all 会清空旧数据。用户已确认用覆盖模式
- 中断后重跑同命令会断点续跑（不能加 --reset）
- 中途不要关 Chrome，不要手动操作 Chrome

采集完成后必须验证（不能跳过）：
  python scripts\collect\report_collection.py

预期：
- 成功 215（或接近）
- 失败 0
- keyword_metric 有 YYYY-MM 数据
- collection_batch 行数 = L2 数

## 三、分析（1-2 小时）

命令：
  cd D:\tiktok-ai-sourcing
  python scripts\analyze\run_all_analysis.py --period YYYY-MM --reset

关键点：
- --period 填新月
- --reset 首次跑加，重跑断点不加
- 预期输出：成功 ~161 / 空 ~54 / 失败 0

分析完成后验证：
  查 opportunity_analysis 行数（~2500）
  查 period 分布（只有 YYYY-MM）
  查等级分布（S+A+B+C）

## 四、切换前端周期（1 分钟）

$py = @'
import sqlite3
con = sqlite3.connect(r"D:\tiktok-ai-sourcing\data\tiktok_market.db")
con.execute("UPDATE app_config SET value=? WHERE key='active_period'", ("YYYY-MM",))
con.commit()
print("active_period 已切到 YYYY-MM")
con.close()
'@
$tmp = Join-Path $env:TEMP "switch.py"
[System.IO.File]::WriteAllText($tmp, $py, [System.Text.UTF8Encoding]::new($false))
python $tmp

关键点：
- 前端会动态读 active_period，不需要改前端任何代码
- 不要在前端硬编码月份

## 五、重启前端 + 验证（5 分钟）

重启：
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*frontend*server.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  Start-Sleep -Seconds 2
  Start-Process -FilePath "python" -ArgumentList "frontend\server.py","--port","8123" -WorkingDirectory "D:\tiktok-ai-sourcing" -RedirectStandardOutput "D:\tiktok-ai-sourcing\frontend_server.log" -RedirectStandardError "D:\tiktok-ai-sourcing\frontend_server.err.log" -WindowStyle Hidden
  Start-Sleep -Seconds 3

验证：
1. 浏览器 Ctrl+Shift+R 强刷
2. 检查顶栏周期 = 新月
3. 抽测 3 个不同 L1 下的 L2，每个显示 20 条机会：
   - 女装和内衣 > 女士上装
   - 美妆个护 > 美妆
   - 宠物用品 > 猫狗食品
4. 每个都正常 → 通过

## 六、冻结（1 分钟）

$root = "D:\tiktok-ai-sourcing"
$state = "$root\_state"
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item "$root\data\tiktok_market.db" "$state\tiktok_market_YYYYMM.STABLE_$ts.db" -Force
Copy-Item "$root\frontend\static\opportunity.html" "$state\opportunity.STABLE_$ts.html" -Force
Copy-Item "$root\frontend\server.py" "$state\server.STABLE_$ts.py" -Force

## 七、已知坑（不要重犯）

### 坑 1：前端 period 硬编码
症状：切了 active_period，前端还显示旧月份
正确做法：前端已改成动态读 /api/active_period，不要改回硬编码

### 坑 2：fetch 竞态
症状：前端传 period=旧月给后端，返回 0 条
正确做法：前端已用 periodReady.then 包裹分析逻辑，不要拆开

### 坑 3：collection_batch 只记一条
症状：采集了 215 个 L2，collection_batch 只有 1 条
正确做法：normalize_fullrow.py 已修复（batch_id 加后缀），不要改回

### 坑 4：前端显示旧周期但实际数据是新周期
症状：数据来自新月，但页面顶部写旧月
正确做法：/api/active_period 接口已就绪，前端动态读取

## 八、排查问题的正确顺序（不猜）

任何"数据不对"的问题，按这个顺序：

1. 先看后端日志：Get-Content "D:\tiktok-ai-sourcing\frontend_server.log" -Tail 20
2. 看关键参数：period / level1 / level2 传的是什么
3. 再查数据库确认数据是否真的存在
4. 最后改代码

不要：先猜、先改、先查库、先让用户测
要：先看日志，参数没错再往下查

## 九、脚本位置速查

- 采集：scripts/collect/run_all_collection.py
- 归一入库：scripts/collect/normalize_fullrow.py
- 采集报告：scripts/collect/report_collection.py
- 登录准备：scripts/collect/prepare_login.py
- 批量分析：scripts/analyze/run_all_analysis.py
- 后端服务：frontend/server.py
- 前端页面：frontend/static/opportunity.html
- 主库：data/tiktok_market.db
- 任务清单：data/_tasks/final_collection_tasks.json

## 十、时间预算

采集：3-26 小时（视 TikTok 反爬）
分析：1-2 小时
切换 + 验证：10 分钟

一次完整月度更新：4-28 小时
