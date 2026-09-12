# make_voice_samples.py - synthesize the same sentence with all 3 RU voices
import json, subprocess, sys
from pathlib import Path

ROOT = Path(r"C:\private-ai")
vc = json.loads((ROOT / "config" / "voice.json").read_text(encoding="utf-8-sig"))
samples = ROOT / "voices" / "samples"
samples.mkdir(parents=True, exist_ok=True)

sentence = "Привет, это проверка голосового цикла, скажи что-то важное"
voices = {
    "denis":  ROOT / "voices" / "ru_RU-denis-medium.onnx",
    "ruslan": ROOT / "voices" / "ru_RU-ruslan-medium.onnx",
    "dmitri": ROOT / "voices" / "ru_RU-dmitri-medium.onnx",
}

for name, onnx in voices.items():
    wav = samples / f"sample_{name}.wav"
    cfg = Path(str(onnx) + ".json")
    proc = subprocess.run(
        [vc["piper_exe"], "--model", str(onnx), "--config", str(cfg),
         "--output_file", str(wav)],
        input=sentence.encode("utf-8"), capture_output=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if proc.returncode != 0 or not wav.exists():
        print(f"{name}: FAILED rc={proc.returncode} "
              f"{proc.stderr.decode('utf-8','replace')[-200:]}")
        continue
    print(f"{name}: {wav.name} {wav.stat().st_size} bytes")
