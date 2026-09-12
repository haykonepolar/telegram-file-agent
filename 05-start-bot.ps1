# 05-start-bot.ps1
# Ensures llama-server is up, then starts bot.py in the foreground.

$ErrorActionPreference = "Stop"
$root = "C:\private-ai"
$botScript = Join-Path $root "bot\bot.py"
$serverScript = Join-Path $root "03-start-server.ps1"
$python = "$env:LOCALAPPDATA\Microsoft\WindowsApps\python.exe"

Write-Host "[05-start-bot] Checking llama-server at http://127.0.0.1:8080/v1/models ..."

$serverUp = $false
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8080/v1/models" -UseBasicParsing -TimeoutSec 5
    if ($resp.StatusCode -eq 200) { $serverUp = $true }
} catch {
    $serverUp = $false
}

if ($serverUp) {
    Write-Host "[05-start-bot] Server is up."
} else {
    Write-Host "[05-start-bot] Server is down - running 03-start-server.ps1 ..."
    & $serverScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[05-start-bot] Server failed to start - aborting. See logs\server.log"
        exit 1
    }
}

if (-not (Test-Path $botScript)) {
    Write-Host "[05-start-bot] bot.py not found: $botScript"
    exit 1
}

if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Host "[05-start-bot] .env not found in $root - create it with BOT_TOKEN first."
    exit 1
}

Write-Host "[05-start-bot] Starting bot (Ctrl+C to stop) ..."
& $python $botScript
