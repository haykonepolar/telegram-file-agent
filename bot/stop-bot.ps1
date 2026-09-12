# stop-bot.ps1 - stop ONLY the python process running bot.py
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -match 'bot\.py' } |
    ForEach-Object {
        Write-Host "Stopping PID $($_.ProcessId): $($_.CommandLine)"
        Stop-Process -Id $_.ProcessId -Force
    }
