# 01-detect-hardware.ps1
# Detects hardware and writes to hardware.json

$logDate = Get-Date -Format "yyyyMMdd"
$logFile = "C:\private-ai\logs\launcher-$logDate.log"
$hardwareFile = "C:\private-ai\config\hardware.json"

# Function for logging
function Write-Log {
    param([string]$message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $message"
    Add-Content -Path $logFile -Value $logMessage
    Write-Host $logMessage
}

Write-Log "Starting hardware detection"

# GPU detection
$gpuInfo = @{}
Write-Log "Searching for GPU information..."

# First, get basic GPU info
try {
    $gpus = Get-CimInstance -ClassName Win32_VideoController
    if ($gpus) {
        $maxVram = 0
        $gpuName = "Unknown"
        
        foreach ($gpu in $gpus) {
            if ($gpu.Name -and $gpu.AdapterRAM) {
                Write-Log "Found GPU: $($gpu.Name), VRAM (AdapterRAM): $([math]::Round($gpu.AdapterRAM / 1GB, 2)) GB"
                
                if ($gpu.Name.Length -gt $gpuName.Length) {
                    $gpuName = $gpu.Name
                }
            }
        }
        
        Write-Log "Selected GPU: $gpuName"
        
        # VRAM detection with priority order
        $vramGb = 0
        $vramSource = "UNKNOWN"
        
        # Method 1: Registry
        try {
            Write-Log "Attempting VRAM detection via registry..."
            $registryPath = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\*"
            $registryKeys = Get-ChildItem -Path $registryPath -ErrorAction SilentlyContinue
            
            foreach ($key in $registryKeys) {
                try {
                    $driverDesc = Get-ItemProperty -Path $key.PSPath -Name "DriverDesc" -ErrorAction SilentlyContinue
                    $hwInfo = Get-ItemProperty -Path $key.PSPath -Name "HardwareInformation.qwMemorySize" -ErrorAction SilentlyContinue
                    
                    if ($driverDesc -and $driverDesc.DriverDesc -and $hwInfo -and $hwInfo.'HardwareInformation.qwMemorySize') {
                        if ($driverDesc.DriverDesc -match $gpuName -or $gpuName -match $driverDesc.DriverDesc) {
                            $registryVram = $hwInfo.'HardwareInformation.qwMemorySize'
                            $vramGb = [math]::Round($registryVram / 1GB, 2)
                            $vramSource = "REGISTRY"
                            Write-Log "VRAM via registry: $vramGb GB"
                            break
                        }
                    }
                } catch {
                    # Continue to next key
                }
            }
        } catch {
            Write-Log "Registry method failed: $($_.Exception.Message)"
        }
        
        # Method 2: nvidia-smi (if NVIDIA and registry failed)
        if ($vramGb -eq 0 -and $gpuName -match "NVIDIA") {
            try {
                Write-Log "Attempting VRAM detection via nvidia-smi..."
                $nvidiaOutput = nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
                if ($nvidiaOutput) {
                    $nvidiaVram = [int]$nvidiaOutput.Trim()
                    $vramGb = $nvidiaVram
                    $vramSource = "NVIDIA_SMI"
                    Write-Log "VRAM via nvidia-smi: $vramGb GB"
                }
            } catch {
                Write-Log "nvidia-smi method failed: $($_.Exception.Message)"
            }
        }
        
        # Method 3: Fallback to AdapterRAM
        if ($vramGb -eq 0) {
            try {
                $adapterVram = [math]::Round(($gpus | Where-Object { $_.Name -eq $gpuName } | Select-Object -First 1).AdapterRAM / 1GB, 2)
                $vramGb = $adapterVram
                $vramSource = "FALLBACK_UNRELIABLE"
                Write-Log "VRAM fallback (unreliable): $vramGb GB"
            } catch {
                $vramGb = 0
                $vramSource = "FALLBACK_UNRELIABLE"
                Write-Log "VRAM fallback failed: $($_.Exception.Message)"
            }
        }
        
        $gpuInfo = @{
            gpu_name = $gpuName
            vram_gb = $vramGb
            vram_source = $vramSource
        }
        Write-Log "Selected GPU: $gpuName, VRAM: $vramGb GB (Source: $vramSource)"
    } else {
        $gpuInfo = @{
            gpu_name = "None"
            vram_gb = 0
            vram_source = "NO_GPU"
        }
        Write-Log "GPU not found, vram_gb set to 0"
    }
} catch {
    $gpuInfo = @{
        gpu_name = "Unknown"
        vram_gb = 0
        vram_source = "ERROR"
    }
    Write-Log "ERROR reading GPU info: $($_.Exception.Message)"
    Write-Log "Fallback: vram_gb = 0"
}

# RAM detection
try {
    $ramInfo = Get-CimInstance -ClassName Win32_ComputerSystem
    $totalRam = [math]::Round($ramInfo.TotalPhysicalMemory / 1GB, 2)
    Write-Log "Total RAM: $totalRam GB"
} catch {
    $totalRam = 16 # Fallback
    Write-Log "ERROR reading RAM info, fallback: 16 GB"
}

# CPU core detection
try {
    $cpuInfo = Get-CimInstance -ClassName Win32_Processor
    $physicalCores = ($cpuInfo.NumberOfCores | Measure-Object -Sum).Sum
    Write-Log "Physical CPU cores: $physicalCores"
} catch {
    $physicalCores = 8 # Fallback
    Write-Log "ERROR reading CPU info, fallback: 8 cores"
}

# Create final object
$hardware = @{
    gpu_name = $gpuInfo.gpu_name
    vram_gb = $gpuInfo.vram_gb
    vram_source = $gpuInfo.vram_source
    total_ram_gb = $totalRam
    cpu_physical_cores = $physicalCores
}

# Save to JSON
$hardwareJson = $hardware | ConvertTo-Json -Depth 10
$hardwareJson | Out-File -FilePath $hardwareFile -Encoding UTF8
Write-Log "Hardware detection completed. Result saved to $hardwareFile"

Write-Log "Values: GPU=$(if($gpuInfo.vram_gb -gt 0) {$gpuInfo.gpu_name} else {"None"}), VRAM=$($gpuInfo.vram_gb) GB (Source: $($gpuInfo.vram_source)), RAM=$totalRam GB, CPU=$physicalCores cores"