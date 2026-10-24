# DUT News Startup Popup
# 1) python deadline_check.py computes hits -> data\hits.json
# 2) if hits exist, show popup (我不需要 = never remind again; 我要看看 = open aggregator and locate it)
param([int]$Days = 3)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = 'C:\Users\赵朔\.workbuddy\binaries\python\versions\3.13.12\python.exe'
$pyw = $py -replace 'python\.exe$', 'pythonw.exe'
if (Test-Path $pyw) { $py = $pyw }

& $py (Join-Path $root 'collector.py') | Out-Null
& $py (Join-Path $root 'deadline_check.py') --days $Days | Out-Null

$hitsPath = Join-Path $root 'data\hits.json'
if (-not (Test-Path $hitsPath)) { exit 0 }
$hits = @(Get-Content $hitsPath -Raw -Encoding UTF8 | ConvertFrom-Json)
if ($hits.Count -eq 0) { exit 0 }

$disPath = Join-Path $root 'data\dismissed.json'
$dis = @()
if (Test-Path $disPath) {
    try { $dis = @(Get-Content $disPath -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { $dis = @() }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

function Save-Dismiss([string]$t) {
    $script:dis += [pscustomobject]@{ t = $t; d = (Get-Date -Format 'yyyy-MM-dd') }
    if ($script:dis.Count -gt 300) { $script:dis = $script:dis[-300..-1] }
    ($script:dis | ConvertTo-Json -Depth 2) | Set-Content -Path $script:disPath -Encoding UTF8
}

$form = New-Object System.Windows.Forms.Form
$form.Text = '大工资讯提醒'
$form.Width = 660
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false

$head = New-Object System.Windows.Forms.Label
$head.Text = "有 $($hits.Count) 条信息将在 $Days 天内截止，请及时关注："
$head.Font = New-Object System.Drawing.Font('Microsoft YaHei', 11, [System.Drawing.FontStyle]::Bold)
$head.AutoSize = $true
$head.Location = New-Object System.Drawing.Point(14, 12)
$form.Controls.Add($head)

$closeBtn = New-Object System.Windows.Forms.Button
$closeBtn.Text = '关闭'
$closeBtn.Size = New-Object System.Drawing.Size(70, 28)
$closeBtn.Location = New-Object System.Drawing.Point(548, 10)
$closeBtn.Add_Click({ $script:form.Close() })
$form.Controls.Add($closeBtn)

$panel = New-Object System.Windows.Forms.FlowLayoutPanel
$panel.FlowDirection = 'TopDown'
$panel.WrapContents = $false
$panel.AutoSize = $true
$panel.Location = New-Object System.Drawing.Point(10, 50)
$panel.Width = 620
$form.Controls.Add($panel)

foreach ($a in $hits) {
    $row = New-Object System.Windows.Forms.Panel
    $row.Width = 616
    $row.Height = 96
    $row.BackColor = [System.Drawing.Color]::FromArgb(245, 247, 250)
    $row.Margin = New-Object System.Windows.Forms.Padding(0, 0, 0, 8)

    $imp = [int]$a.importance
    $stars = [string]::Join('', (@(1..5) | ForEach-Object { if ($_ -le $imp) { '★' } else { '☆' } }))
    $dlTxt = ([string]$a.deadline).Substring(5) + ' 截止'
    $tag = ''
    if ($a.hy) { $tag = '【本学院】' }
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = "⏰ $dlTxt   $stars   $tag`r`n$($a.title)"
    $lbl.Font = New-Object System.Drawing.Font('Microsoft YaHei', 10)
    $lbl.Location = New-Object System.Drawing.Point(8, 8)
    $lbl.Size = New-Object System.Drawing.Size(430, 80)
    $row.Controls.Add($lbl)

    $bNo = New-Object System.Windows.Forms.Button
    $bNo.Text = '我不需要'
    $bNo.Size = New-Object System.Drawing.Size(80, 32)
    $bNo.Location = New-Object System.Drawing.Point(446, 32)
    $bNo.Tag = @{ row = $row; title = [string]$a.title }
    $bNo.Add_Click({
        Save-Dismiss $this.Tag.title
        $script:panel.Controls.Remove($this.Tag.row)
        if ($script:panel.Controls.Count -eq 0) { $script:form.Close() }
    })
    $row.Controls.Add($bNo)

    $bYes = New-Object System.Windows.Forms.Button
    $bYes.Text = '我要看看'
    $bYes.Size = New-Object System.Drawing.Size(80, 32)
    $bYes.Location = New-Object System.Drawing.Point(532, 32)
    $bYes.Tag = @{ title = [string]$a.title }
    $bYes.Add_Click({
        Save-Dismiss $this.Tag.title
        $idx = Join-Path $script:root 'index.html'
        $u = New-Object System.Uri($idx)
        Start-Process ($u.AbsoluteUri + '?focus=' + [uri]::EscapeDataString($this.Tag.title))
        $script:form.Close()
    })
    $row.Controls.Add($bYes)

    $panel.Controls.Add($row)
}

$form.Height = 50 + $panel.Height + 70
[void]$form.ShowDialog()
