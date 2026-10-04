@echo off
rem TikTok Seller Center 生产定时采集启动器（供 Windows 任务计划程序调用）
rem 使用 pythonw 后台运行（无控制台窗口），日志写入 .runtime\logs\scheduled_<时间戳>.log
rem 不使用 start，等待 pythonw 退出并把退出码回传给任务计划程序（0=全部成功，1=有失败）
cd /d D:\tiktok-ai-sourcing
set PYTHONIOENCODING=utf-8
pythonw D:\tiktok-ai-sourcing\scripts\collect\run_scheduled_collection.py
exit /b %errorlevel%
