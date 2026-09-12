# 02-select-model.ps1
# Selects model based on hardware and configuration

param(
    [string]$HardwareFile = "C:\private-ai\config\hardware.json",
    [string]$ModelsFile = "C:\private-ai\config\models.json",
    [string]$OutputFile = "C:\private-ai\config\selected.json"
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

Write-Log "Starting model selection"

# Check if files exist
if (-not (Test-Path $HardwareFile)) {
    Write-Log "ERROR: Hardware file not found: $HardwareFile"
    exit 1
}

if (-not (Test-Path $ModelsFile)) {
    Write-Log "ERROR: Models file not found: $ModelsFile"
    exit 1
}

# Read configuration
try {
    $hardware = Get-Content $HardwareFile | ConvertFrom-Json
    Write-Log "Hardware info loaded: GPU=$(if($hardware.vram_gb -gt 0) {$hardware.gpu_name} else {"None"}), VRAM=$($hardware.vram_gb) GB"
} catch {
    Write-Log "ERROR reading hardware file: $($_.Exception.Message)"
    exit 1
}

try {
    $models = Get-Content $ModelsFile | ConvertFrom-Json
    Write-Log "Models catalog loaded: $($models.models.Count) models"
} catch {
    Write-Log "ERROR reading models file: $($_.Exception.Message)"
    exit 1
}

# Model selection rules
$selectedModel = $null
$modelId = ""
$modelPath = ""
$gpuLayers = 0
$cpuMode = $false

if ($hardware.vram_gb -ge 5) {
    # VRAM >= 5 GB - select Ornith 9B
    $selectedModel = $models.models | Where-Object { $_.id -eq "ornith-9b-q4_k_m" } | Select-Object -First 1
    if ($selectedModel -and $selectedModel.availability) {
        $modelId = "ornith-9b-q4_k_m"
        $modelPath = $selectedModel.file_path
        $gpuLayers = 99
        $cpuMode = $false
        Write-Log "Selected ornith-9b-q4_k_m (VRAM >= 5 GB)"
    } else {
        Write-Log "ERROR: ornith-9b-q4_k_m model not available"
        exit 1
    }
} else {
    # VRAM < 5 GB or no GPU - select CPU model
    $selectedModel = $models.models | Where-Object { $_.id -eq "qwen3-4b-instruct-q4" } | Select-Object -First 1
    if ($selectedModel -and $selectedModel.availability) {
        $modelId = "qwen3-4b-instruct-q4"
        $modelPath = $selectedModel.file_path
        $gpuLayers = 0
        $cpuMode = $true
        Write-Log "Selected qwen3-4b-instruct-q4 (VRAM < 5 GB or no GPU)"
    } else {
        Write-Log "ERROR: NO_SUITABLE_MODEL - CPU model not available"
        # Create error result
        $selection = @{
            model_id = "error"
            model_path = "null"
            gpu_layers = 0
            threads = 8
            ctx_size = 8192
            port = 8080
            cpu_mode = $false
            error = "NO_SUITABLE_MODEL"
        }
        $selectionJson = $selection | ConvertTo-Json -Depth 10
        $selectionJson | Out-File -FilePath $OutputFile -Encoding UTF8
        Write-Log "Error result saved to $OutputFile"
        exit 1
    }
}

# Check if model file exists
if ($modelPath -and -not (Test-Path $modelPath)) {
    Write-Log "ERROR: Model file not found: $modelPath"
    exit 1
}

# Create result
$selection = @{
    model_id = $modelId
    model_path = $modelPath
    gpu_layers = $gpuLayers
    threads = 8
    ctx_size = 8192
    port = 8080
    cpu_mode = $cpuMode
}

# Save to JSON
$selectionJson = $selection | ConvertTo-Json -Depth 10
$selectionJson | Out-File -FilePath $OutputFile -Encoding UTF8
Write-Log "Model selection completed. Result saved to $OutputFile"
Write-Log "Selected model: $modelId, CPU mode: $cpuMode, GPU layers: $gpuLayers"