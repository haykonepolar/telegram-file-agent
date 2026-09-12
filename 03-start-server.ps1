# 03-start-server.ps1
# Starts llama-server and checks its functionality

param(
    [string]$ConfigFile = "C:\private-ai\config\selected.json"
)

$logDate = Get-Date -Format "yyyyMMdd"
$logFile = "C:\private-ai\logs\launcher-$logDate.log"
$serverLogFile = "C:\private-ai\logs\server.log"

# Function for logging
function Write-Log {
    param([string]$message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $message"
    Add-Content -Path $logFile -Value $logMessage
    Write-Host $logMessage
}

Write-Log "Starting llama-server"

# Check if config file exists
if (-not (Test-Path $ConfigFile)) {
    Write-Log "ERROR: Config file not found: $ConfigFile"
    exit 1
}

# Read configuration
try {
    $config = Get-Content $ConfigFile | ConvertFrom-Json
    Write-Log "Configuration loaded: model $($config.model_id), port $($config.port)"
} catch {
    Write-Log "ERROR reading config file: $($_.Exception.Message)"
    exit 1
}

# Check if model file exists
if (-not (Test-Path $config.model_path)) {
    Write-Log "ERROR: Model file not found: $($config.model_path)"
    exit 1
}

# Build arguments for llama-server.exe based on working ornith15-agent.ps1
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
    "--host 127.0.0.1",
    "--port $($config.port)",
    "--temp 1.0",
    "--top-k 20",
    "--top-p 0.95",
    "--min-p 0.0",
    "--presence-penalty 1.5",
    "--load-mode mmap",
    "--threads $($config.threads)"
)

Write-Log "Arguments for llama-server.exe: $arguments"

# Start server in background
try {
    Write-Log "Starting llama-server.exe..."
    $process = Start-Process -FilePath $serverPath -ArgumentList $arguments -RedirectStandardOutput $serverLogFile -RedirectStandardError "C:\private-ai\logs\server-error.log" -PassThru -NoNewWindow
    Write-Log "Server started with PID $($process.Id)"
} catch {
    Write-Log "ERROR starting server: $($_.Exception.Message)"
    exit 1
}

# Check server availability
$timeout = 300 # seconds
$interval = 2 # seconds
$elapsed = 0
$serverReady = $false

Write-Log "Waiting for server to be ready (timeout: $timeout seconds)..."

while ($elapsed -lt $timeout) {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$($config.port)/v1/models" -Method GET -TimeoutSec 5
        $serverReady = $true
        break
    } catch {
        if ($_.Exception.Response.StatusCode -eq 200) {
            $serverReady = $true
            break
        }
        Start-Sleep -Seconds $interval
        $elapsed += $interval
        Write-Log "Connection attempt ($elapsed/$timeout)..."
    }
}

if ($serverReady) {
    Write-Log "SERVER_READY: Server is ready in $elapsed seconds"
    Write-Log "Models available on server:"
    $response | ConvertTo-Json -Depth 10
} else {
    Write-Log "SERVER_FAILED: Server did not start in $timeout seconds"
    
    # Kill process
    if ($process -and -not $process.HasExited) {
        Write-Log "Killing process with PID $($process.Id)..."
        Stop-Process -Id $process.Id -Force
    }
    
    # Show last 30 lines of server log
    Write-Log "Last 30 lines of server log:"
    if (Test-Path $serverLogFile) {
        $lastLines = Get-Content $serverLogFile | Select-Object -Last 30
        $lastLines | ForEach-Object { Write-Log "SERVER_LOG: $_" }
    } else {
        Write-Log "Server log not found: $serverLogFile"
    }
    
    # Show last 30 lines of error log
    Write-Log "Last 30 lines of error log:"
    $errorLogFile = "C:\private-ai\logs\server-error.log"
    if (Test-Path $errorLogFile) {
        $lastLines = Get-Content $errorLogFile | Select-Object -Last 30
        $lastLines | ForEach-Object { Write-Log "ERROR_LOG: $_" }
    } else {
        Write-Log "Error log not found: $errorLogFile"
    }
    
    exit 1
}