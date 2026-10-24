# -*- coding: utf-8 -*-
# 生成带探测的 index.html 调试副本，用于定位 secAll null 问题
s = open("index.html", encoding="utf-8").read()

old = "  $('#secAll').textContent = '共 ' + arts.length + ' 条';"
probe = (
    "  console.log('[PROBE] secAll?', !!document.getElementById('secAll'), "
    "'qsa:', document.querySelectorAll('#sectionsNav .sc').length);\n"
)
s2 = s.replace(old, probe + old, 1)
open("_debug.html", "w", encoding="utf-8").write(s2)
print("覆盖成功:", "PROBE" in s2)
