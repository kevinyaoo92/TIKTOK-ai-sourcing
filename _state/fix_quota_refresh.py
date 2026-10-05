from pathlib import Path

p = Path(r"D:\tiktok-ai-sourcing\frontend\static\opportunity.html")
s = p.read_text(encoding="utf-8")
changed = []

# 1. find_suppliers 成功后刷新额度
old1 = '''        FINDER.taskId = data.task_id;
        FINDER.timer = setInterval(pollFinderTask, 800);'''
new1 = '''        refreshQuota();
        FINDER.taskId = data.task_id;
        FINDER.timer = setInterval(pollFinderTask, 800);'''
if "refreshQuota();" not in s.split("function openDetail")[0]:
    if old1 in s:
        s = s.replace(old1, new1, 1)
        changed.append("find_refresh")
    else:
        print("WARN: find_suppliers 锚点未找到")

# 2. miaoshou 采集成功后刷新额度
old2 = '''          if (data.status === "success") {
            MS_COLLECTED[oid] = true;'''
new2 = '''          if (data.status === "success") {
            refreshQuota();
            MS_COLLECTED[oid] = true;'''
if old2 in s and new2 not in s:
    s = s.replace(old2, new2, 1)
    changed.append("collect_refresh")
else:
    print("WARN: miaoshou 锚点未找到或已改")

p.write_text(s, encoding="utf-8")
print("changed:", changed if changed else "no change")
