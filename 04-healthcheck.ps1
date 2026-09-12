# 04-healthcheck.ps1
# Health check for llama-server with automatic restart

param(
    [string]$ConfigFile = "C:\private-ai\config\selected.json"
)

$logDate = Get-Date -Format "yyyyMMdd"
$logFile = "C:\private-ai\logs\launcher-$logDate.log"

# Function for logging
function Write-Log {
    param([string]$message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $message"
    Add-Content -Path $logFile -Value $logMessage
    Write-Host $logMessage
}

Write-Log "Starting health check"

# Check if server responds
try {
    $response = Invoke-RestMethod -Uri "http://127.0.0.1:8080/v1/models" -TimeoutSec 10
    Write-Log "Server is OK"
    Write-Host "Status: OK"
    exit 0
}
catch {
    Write-Log "Server is not responding, attempting restart..."
    
    # Try to restart server
    try {
        # Check if config file exists
        if (-not (Test-Path $ConfigFile)) {
            Write-Log "ERROR: Config file not found: $ConfigFile"
            exit 1
        }
        
        # Read configuration
        $config = Get-Content $ConfigFile | ConvertFrom-Json
        
        # Check if model file exists
        if (-not (Test-Path $config.model_path)) {
            Write-Log "ERROR: Model file not found: $($config.model_path)"
            exit 1
        }
        
        # Build arguments for llama-server.exe
        $serverPath = "C:\llama-cpp\llama-server.exe"
        $arguments = @(
            "--model `"$($config.model_path)`"",
            "--n-gpu-layers $($config.gpu_layers)",
            "--ctx-size $($config.ctx_size)",
            "--parallel 1",
            "--cache-type-k q4_0",
            "--cache-type-v q4_0",
            "--flash-attn on",
            "--jinja",
            "--host 0.0.0.0",
            "--port $($config.port)",
            "--temp 1.0",
            "--top-k 20",
            "--top-p 0.95",
            "--min-p 0.0",
            "--presence-penalty 1.5",
            "--load-mode mmap",
            "--threads $($config.threads)"
        )
        
        Write-Log "Starting server with arguments: $arguments"
        
        # Start server in background
        $process = Start-Process -FilePath $serverPath -ArgumentList $arguments -RedirectStandardOutput "C:\private-ai\logs\server-restart.log" -RedirectStandardError "C:\private-ai\logs\server-error.log" -PassThru
        
        # Wait for server to start
        $timeout = 120
        $startTime = Get-Date
        
        while ((Get-Date) -lt $startTime.AddSeconds($timeout)) {
            try {
                $response = Invoke-RestMethod -Uri "http://127.0.0.1:8080/v1/models" -TimeoutSec 5
                Write-Log "Server restarted successfully"
                Write-Host "Status: OK (restarted)"
                exit 0
            }
            catch {
                Write-Log "Waiting for server to start..."
                Start-Sleep -Seconds 2
            }
        }
        
        # If we get here, server didn't start
        Write-Log "ERROR: Server restart failed after $timeout seconds"
        
        # Kill the process
        if ($process -and -not $process.HasExited) {
            Write-Log "Killing server process..."
            Stop-Process -Id $process.Id -Force
        }
        
        # Create diagnostic bundle
        $diagTime = Get-Date -Format "yyyyMMdd-HHmm"
        $diagZip = "C:\private-ai\diag\diag-$diagTime.zip"
        
        Write-Log "Creating diagnostic bundle: $diagZip"
        
        # Create zip with logs and configs
        Compress-Archive -Path "C:\private-ai\logs\*.log", "C:\private-ai\config\hardware.json", "C:\private-ai\config\selected.json" -DestinationPath $diagZip -Force
        
        Write-Log "Diagnostic bundle created: $diagZip"
        
        # Show contents of diagnostic bundle
        Write-Log "Diagnostic bundle contents:"
        $items = Get-Item $diagZip
        Write-Log "  - $diagZip ($($items.Length) bytes)"
        
        $logFiles = Get-ChildItem "C:\private-ai\logs\*.log"
        foreach ($logFile in $logFiles) {
            Write-Log "  - $($logFile.Name) ($($logFile.Length) bytes)"
        }
        
        Write-Host "Status: FAILED - Diagnostic bundle created: $diagZip"
        exit 1
    }
    catch {
        Write-Log "ERROR: Failed to restart server: $($_.Exception.Message)"
        Write-Host "Status: FAILED"
        exit 1
    }
}