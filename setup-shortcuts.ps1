# Create desktop shortcut (custom anime icon) + startup entry (silent deadline popup)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$wsh = New-Object -ComObject WScript.Shell
$icon = Join-Path $root 'assets\app2.ico'
$index = Join-Path $root 'index.html'
$check = Join-Path $root 'startup-check.ps1'

# 1) Desktop shortcut
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk1 = $wsh.CreateShortcut((Join-Path $desktop '大工资讯聚合.lnk'))
$lnk1.TargetPath = $index
$lnk1.IconLocation = "$icon,0"
$lnk1.Description = '大工资讯聚合（盘锦校区）'
$lnk1.Save()

# 2) Startup entry (runs hidden; popup only when deadlines are near)
$startup = [Environment]::GetFolderPath('Startup')
$lnk2 = $wsh.CreateShortcut((Join-Path $startup '大工资讯提醒.lnk'))
$lnk2.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$lnk2.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$check`""
$lnk2.IconLocation = "$icon,0"
$lnk2.Description = '开机检查即将截止的大工资讯'
$lnk2.Save()

"OK"
