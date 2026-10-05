from pathlib import Path

p = Path(r"D:\tiktok-ai-sourcing\frontend\static\opportunity.html")
s = p.read_text(encoding="utf-8")
changed = []

# 1. header 里加额度条
if 'id="quota-bar"' not in s:
    anchor = "  </header>"
    ins = ('      <div class="max-w-7xl mx-auto px-4 pb-2">\n'
           '        <div id="quota-bar" class="text-xs text-white/70"></div>\n'
           '      </div>\n')
    s = s.replace(anchor, ins + anchor, 1)
    changed.append("header")

# 2. getUUID 后加 refreshQuota + 页面加载时调用
if "function refreshQuota" not in s:
    anchor = "    var MS_COLLECTED = {};"
    fn = (
        '    function refreshQuota() {\n'
        '      fetch("/api/usage?uuid=" + encodeURIComponent(getUUID()))\n'
        '        .then(function (r) { return r.json(); })\n'
        '        .then(function (d) {\n'
        '          if (!d || !d.ok) return;\n'
        '          var el = document.getElementById("quota-bar");\n'
        '          if (el) {\n'
        '            el.textContent = "找货剩余 " + d.find.remaining + "/" + d.find.limit +\n'
        '                             "  ·  采集剩余 " + d.collect.remaining + "/" + d.collect.limit;\n'
        '          }\n'
        '        });\n'
        '    }\n'
        '    if (document.readyState === "loading") {\n'
        '      document.addEventListener("DOMContentLoaded", refreshQuota);\n'
        '    } else {\n'
        '      refreshQuota();\n'
        '    }\n'
    )
    s = s.replace(anchor, fn + anchor, 1)
    changed.append("refreshQuota")

p.write_text(s, encoding="utf-8")
print("changed:", changed if changed else "no change")
