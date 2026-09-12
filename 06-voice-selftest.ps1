# 06-voice-selftest.ps1 - Piper TTS -> ffmpeg 16k -> whisper-cli ASR loop
$ErrorActionPreference = "Continue"
$vc = Get-Content "C:\private-ai\config\voice.json" -Raw | ConvertFrom-Json
$tmp = "C:\private-ai\data\tmp"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

$sentence = "Привет, это проверка голосового цикла, скажи что-то важное"
$txtIn = "$tmp\selftest_in.txt"
$wav1 = "$tmp\selftest_tts.wav"
$wav2 = "$tmp\selftest_16k.wav"

# Write the sentence as UTF-8 WITHOUT BOM so piper reads it correctly
[System.IO.File]::WriteAllText($txtIn, $sentence, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "[1/3] Piper TTS"
cmd /c "type `"$txtIn`" | `"$($vc.piper_exe)`" -m `"$($vc.piper_voice)`" -c `"$($vc.piper_config)`" -f `"$wav1`" 2>nul"
if (-not (Test-Path $wav1)) { Write-Host "PIPER FAILED"; exit 1 }
Write-Host ("  wav: " + (Get-Item $wav1).Length + " bytes")

Write-Host "[2/3] ffmpeg -> 16k mono"
cmd /c "`"$($vc.ffmpeg)`" -y -i `"$wav1`" -ar 16000 -ac 1 `"$wav2`" 2>nul"
if (-not (Test-Path $wav2)) { Write-Host "FFMPEG FAILED"; exit 1 }
Write-Host ("  wav16k: " + (Get-Item $wav2).Length + " bytes")

Write-Host "[3/3] whisper-cli ASR (ru)"
cmd /c "`"$($vc.whisper_cli)`" -m `"$($vc.whisper_model)`" -f `"$wav2`" -l ru -nt -t 4 -otxt -of `"$tmp\selftest`" 2>nul"
$txt = [System.IO.File]::ReadAllText("$tmp\selftest.txt", [System.Text.Encoding]::UTF8)
Write-Host "INPUT:         $sentence"
Write-Host "TRANSCRIPTION: $txt"

Remove-Item $wav1, $wav2, $txtIn, "$tmp\selftest.txt" -ErrorAction SilentlyContinue
