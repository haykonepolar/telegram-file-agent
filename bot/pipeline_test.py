# pipeline_test.py - exercise bot.py voice functions end-to-end
import sys, time
sys.path.insert(0, r"C:\private-ai\bot")
sys.stdout.reconfigure(encoding="utf-8")
import bot
from pathlib import Path

vc = bot.load_voice_config()
tmp = bot.TMP_DIR
tmp.mkdir(parents=True, exist_ok=True)

# Simulate incoming voice: make an ogg (like Telegram sends) from selftest wav
src_wav = tmp / "selftest_16k.wav"
if not src_wav.exists():
    # regenerate via piper
    text = "Привет, это проверка голосового цикла, скажи что-то важное"
    bot.synth_voice(text, src_wav.with_suffix(".ogg"), vc)

in_ogg = tmp / "in_test.ogg"
bot.run_tool([vc["ffmpeg"], "-y", "-i", str(src_wav), "-c:a", "libopus", "-b:a", "24k", str(in_ogg)], 30)

t0 = time.monotonic()
text = bot.transcribe_ogg(in_ogg, vc)
print("TRANSCRIBED (%.1fs): %s" % (time.monotonic() - t0, text))

out_ogg = tmp / "out_test.ogg"
t0 = time.monotonic()
bot.synth_voice("Ответ получен. Голосовой цикл работает правильно.", out_ogg, vc)
print("SYNTH+CONVERT (%.1fs): %d bytes ogg" % (time.monotonic() - t0, out_ogg.stat().st_size))

# round-trip the synthesized ogg back through whisper to prove it is valid speech
check = bot.transcribe_ogg(out_ogg, vc)
print("ROUND-TRIP ASR OF TTS OUTPUT: %s" % check)
