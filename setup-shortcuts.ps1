# Create desktop shortcut + startup entry (integrated deadline reminder via exe --autostart)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$wsh = New-Object -ComObject WScript.Shell
$icon = Join-Path $root 'assets\app2.ico'

# Find packaged exe: prefer dist/, fallback release/
$exe = Join-Path $root 'dist\DutNewsAggregator.exe'
if(-not (Test-Path $exe)){ $exe = Join-Path $root 'release\DutNewsAggregator.exe' }
$hasExe = Test-Path $exe

# 1) Desktop shortcut
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk1 = $wsh.CreateShortcut((Join-Path $desktop '大工资讯聚合.lnk'))
if($hasExe){
  $lnk1.TargetPath = $exe
  $lnk1.WorkingDirectory = $root
}else{
  $lnk1.TargetPath = Join-Path $root 'index.html'
}
$lnk1.IconLocation = "$icon,0"
$lnk1.Description = '大工资讯聚合（盘锦校区新生助手）'
$lnk1.Save()

# 2) Startup entry: exe --autostart (hidden window, popup on deadline) or fallback to ps1
$startup = [Environment]::GetFolderPath('Startup')
$lnk2 = $wsh.CreateShortcut((Join-Path $startup '大工资讯提醒.lnk'))
if($hasExe){
  $lnk2.TargetPath = $exe
  $lnk2.Arguments = '--autostart'
  $lnk2.WorkingDirectory = $root
}else{
  $check = Join-Path $root 'startup-check.ps1'
  $lnk2.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
  $lnk2.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$check`""
}
$lnk2.IconLocation = "$icon,0"
$lnk2.Description = '开机检查即将截止的大工资讯'
$lnk2.Save()

"OK (desktop + startup shortcuts, exe=$hasExe)"
