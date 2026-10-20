# 網頁截圖（開發用）：啟動示範伺服器（自動結束）→ 無頭 Edge 截圖 → 關閉所有程序。
# 用法：pwsh scripts/web_shot.ps1 [-Hash "#tac=C3"] [-Wait 20] [-Out shot.png]
param([string]$Hash = "", [string]$Size = "1600,1000", [int]$Wait = 20, [string]$Out = "$env:TEMP\ac_shot.png", [int]$Port = 8091)
$py = "C:\Users\kcfan\anaconda3\envs\kuro\python.exe"
$root = Split-Path $PSScriptRoot -Parent
$log = "$env:TEMP\ac_demo_out.txt"
$srv = Start-Process -FilePath $py -ArgumentList "-B", "-u", "scripts\web_demo.py", "$Port", "--exit-after=$($Wait + 60)" `
    -WorkingDirectory $root -RedirectStandardOutput $log -PassThru -WindowStyle Hidden -Environment @{PYTHONIOENCODING = "utf-8" }
try {
    $tok = $null
    for ($i = 0; $i -lt 30 -and -not $tok; $i++) {
        Start-Sleep 1
        $line = Get-Content -Encoding utf8 $log -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($line -match "token=(\S+)") { $tok = $Matches[1] }
    }
    if (-not $tok) { throw "demo server did not start" }
    Start-Sleep $Wait
    $edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    $prof = Join-Path $env:TEMP ("ac_edge_" + [guid]::NewGuid())
    $e = Start-Process -FilePath $edge -ArgumentList "--headless=new", "--disable-gpu", "--user-data-dir=$prof", `
        "--window-size=$Size", "--virtual-time-budget=8000", "--screenshot=$Out", "http://localhost:$Port/?token=$tok$Hash" `
        -PassThru -WindowStyle Hidden
    if (-not $e.WaitForExit(40000)) { $e.Kill() }
    "screenshot: $Out"
}
finally {
    if (-not $srv.HasExited) { Stop-Process -Id $srv.Id -Force }
}
