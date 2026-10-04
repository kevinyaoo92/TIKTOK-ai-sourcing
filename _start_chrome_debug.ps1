$chromePath = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $chromePath) {
    Write-Host "[ERR] Chrome not found"
    exit 1
}
Write-Host "[OK] Chrome: $chromePath"

$existing = Get-NetTCPConnection -LocalPort 9222 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[WARN] Port 9222 already in use"
    exit 0
}

Start-Process -FilePath $chromePath -ArgumentList "--remote-debugging-port=9222"
Start-Sleep 3

try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/version" -UseBasicParsing -TimeoutSec 5
    Write-Host "[OK] Port 9222 ready"
    $json = $r.Content | ConvertFrom-Json
    Write-Host "  Browser: $($json.Browser)"
} catch {
    Write-Host "[ERR] Port 9222 not ready"
    Write-Host "Reason: Chrome still running in background. Kill all chrome.exe in Task Manager then retry"
}