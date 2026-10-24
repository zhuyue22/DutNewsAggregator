' 大连理工资讯采集 - 开机静默自启（纯程序，零token）
' 用 WScript 隐藏窗口运行 collector，避免弹黑窗
Set sh = CreateObject("WScript.Shell")
sh.Run """C:\Users\赵朔\.workbuddy\binaries\python\versions\3.13.12\python.exe"" ""C:\Users\赵朔\LearnBuddy\2026-10-01-18-43-49\dut-news-site\collector.py""", 0, False
