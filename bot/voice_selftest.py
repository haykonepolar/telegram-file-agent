# voice_selftest.py - byte-exact TTS->ASR loop, no PowerShell encoding issues
import json, subprocess, sys, time
from pathlib import Path

ROOT = Path(r"C:\private-ai")
vc = json.loads((ROOT / "config" / "voice.json").read_text(encoding="utf-8-sig"))
tmp = ROOT / "data" / "tmp"
tmp.mkdir(parents=True, exist_ok=True)

sentence = "Привет, это проверка голосового цикла, скажи что-то важное"
wav1 = tmp / "selftest_tts.wav"
wav2 = tmp / "selftest_16k.wav"
txt_out = tmp / "selftest.txt"

def run(cmd, timeout, **kw):
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW, **kw)
    if proc.returncode != 0:
        print("FAILED:", cmd[0], proc.stderr.decode("utf-8", "replace")[-300:])
        sys.exit(1)
    return proc

print("[1/3] Piper TTS")
run([vc["piper_exe"], "-m", vc["piper_voice"], "-c", vc["piper_config"],
     "-f", str(wav1)], 60, input=sentence.encode("utf-8"))
print("  wav:", wav1.stat().st_size, "bytes")

print("[2/3] ffmpeg -> 16k mono")
run([vc["ffmpeg"], "-y", "-i", str(wav1), "-ar", "16000", "-ac", "1", str(wav2)], 30)
print("  wav16k:", wav2.stat().st_size, "bytes")

print("[3/3] whisper-cli ASR (ru)")
t0 = time.monotonic()
run([vc["whisper_cli"], "-m", vc["whisper_model"], "-f", str(wav2),
     "-l", "ru", "-nt", "-t", "4", "-otxt", "-of", str(txt_out.with_suffix(""))], 120)
print("  asr time: %.1fs" % (time.monotonic() - t0))

text = txt_out.read_text(encoding="utf-8").strip()
sys.stdout.reconfigure(encoding="utf-8")
print("INPUT:         " + sentence)
print("TRANSCRIPTION: " + text)
