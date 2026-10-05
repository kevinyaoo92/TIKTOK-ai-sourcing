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
