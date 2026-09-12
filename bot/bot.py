# bot.py - Private AI Telegram bot (M2, text only)
# Talks to local llama-server (OpenAI-compatible API). No voice.

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import sys

# ---------------------------------------------------------------- paths
ROOT = Path(r"C:\private-ai")
ENV_FILE = ROOT / ".env"
SELECTED_FILE = ROOT / "config" / "selected.json"
PERSONA_FILE = ROOT / "config" / "persona.md"
DB_DIR = ROOT / "data"
DB_FILE = DB_DIR / "bot.db"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "bot.log"

API_BASE = "http://127.0.0.1:8080"
CHAT_COMPLETIONS_URL = API_BASE + "/v1/chat/completions"
MODELS_URL = API_BASE + "/v1/models"

HISTORY_CONTEXT_ENTRIES = 20
RATE_LIMIT_SECONDS = 3
LLM_TIMEOUT_SECONDS = 120
DEFAULT_PERSONA = "Ты — приватный ассистент. Отвечай на русском кратко и по делу."
SERVER_DOWN_REPLY = "Модель сейчас не отвечает — запусти 03-start-server.ps1"
LLM_TIMEOUT_REPLY = "Модель думает слишком долго, попробуй ещё раз"
BUSY_REPLY = "Обрабатываю предыдущее сообщение, секунду..."

# ---------------------------------------------------------------- agent mode (M6)
sys.path.insert(0, str(ROOT / "bot"))
import agent_runner  # noqa: E402
import i18n as i18n_mod  # noqa: E402
from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # noqa: E402

def read_env_var(key: str) -> str | None:
    """Read one var from C:\\private-ai\\.env (utf-8-sig)."""
    if not ENV_FILE.exists():
        return None
    for raw in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip()
    return None

SANDBOX_DIR = Path(read_env_var("SANDBOX_DIR") or str(ROOT / "sandbox"))

def allowed_ids() -> set[int]:
    raw = read_env_var("TELEGRAM_ALLOWED_IDS") or ""
    return {int(x) for x in raw.split(",") if x.strip().isdigit()}

# one agent task globally
_agent_state = {
    "running": False,       # agent busy flag
    "chat_id": None,
    "task": None,
    "step": 0,
    "steps_total": 0,
    "started": 0.0,         # monotonic
    "stop_requested": False,
    "proc_holder": {},      # holds current hermes Popen for /stop kill
    "progress_msg_id": None,
    "plan": [],             # last confirmed plan steps
}
_main_loop: dict = {"loop": None}  # set in main() for cross-thread progress
_agent_confirm_q: dict = {"event": None}  # set -> asyncio.Event for CONFIRM gate
_agent_confirm_answer: dict = {"ok": False}

# ---------------------------------------------------------------- logging
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("private-ai-bot")

# ---------------------------------------------------------------- .env
def read_bot_token() -> str:
    """Parse C:\\private-ai\\.env manually. utf-8-sig strips the BOM
    that PowerShell 5.1 Set-Content -Encoding UTF8 writes.
    NEVER log or echo the token value."""
    if not ENV_FILE.exists():
        raise RuntimeError(f".env not found: {ENV_FILE}")
    for raw in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "BOT_TOKEN":
            token = value.strip()
            if token and token != "PASTE_TOKEN_HERE":
                log.info(".env parsed: key=BOT_TOKEN token_len=%d", len(token))
                return token
    raise RuntimeError(
        "BOT_TOKEN not set in .env (missing, empty, or still PASTE_TOKEN_HERE)"
    )

# ---------------------------------------------------------------- config
def load_selected() -> dict:
    with open(SELECTED_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)

def load_persona() -> str:
    if PERSONA_FILE.exists():
        try:
            return PERSONA_FILE.read_text(encoding="utf-8-sig").strip()
        except Exception as e:
            log.warning("persona.md read failed (%s), using default", e)
    return DEFAULT_PERSONA

# ---------------------------------------------------------------- db
def db() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history ("
        " chat_id INTEGER, role TEXT, text TEXT, ts TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_log ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts TEXT, chat_id INTEGER, task_text TEXT, plan_json TEXT,"
        " step_action TEXT, risk_class TEXT, result TEXT, duration_s REAL)"
    )
    return conn

def audit_log(chat_id: int, task_text: str, plan_json: str,
              step_action: str, risk_class: str, result: str, duration_s: float):
    with db() as conn:
        conn.execute(
            "INSERT INTO agent_log (ts, chat_id, task_text, plan_json,"
            " step_action, risk_class, result, duration_s)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), chat_id, task_text[:500],
             plan_json[:2000], step_action[:400], risk_class, result[:400], duration_s),
        )

def add_message(chat_id: int, role: str, text: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO history (chat_id, role, text, ts) VALUES (?, ?, ?, ?)",
            (chat_id, role, text, time.strftime("%Y-%m-%d %H:%M:%S")),
        )

def get_history(chat_id: int, limit: int = HISTORY_CONTEXT_ENTRIES):
    with db() as conn:
        rows = conn.execute(
            "SELECT role, text FROM ("
            "  SELECT role, text, ts, rowid AS rid FROM history"
            "  WHERE chat_id = ? ORDER BY ts DESC, rid DESC LIMIT ?"
            ") ORDER BY ts ASC, rid ASC",
            (chat_id, limit),
        ).fetchall()
    return rows

def reset_history(chat_id: int) -> int:
    with db() as conn:
        cur = conn.execute("DELETE FROM history WHERE chat_id = ?", (chat_id,))
        return cur.rowcount

def count_messages(chat_id: int) -> int:
    with db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM history WHERE chat_id = ?", (chat_id,)
        ).fetchone()[0]

# ------------------------------------------------- M8 FIX 2: agent quota
AGENT_QUOTA_TABLE_READY = {"flag": False}

def _ensure_quota_tables(conn):
    """Create usage + arrivals tables once per connection."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS usage ("
        " chat_id INTEGER, date TEXT, count INTEGER, "
        " PRIMARY KEY (chat_id, date))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS arrivals ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT)"
    )

def agent_quota_used(chat_id: int) -> int:
    """Tasks used today for this chat."""
    with db() as conn:
        _ensure_quota_tables(conn)
        row = conn.execute(
            "SELECT count FROM usage WHERE chat_id = ? AND date = ?",
            (chat_id, time.strftime("%Y-%m-%d")),
        ).fetchone()
    return row[0] if row else 0

def agent_quota_increment(chat_id: int) -> int:
    """Increment today's counter, return new count."""
    with db() as conn:
        _ensure_quota_tables(conn)
        conn.execute(
            "INSERT INTO usage (chat_id, date, count) VALUES (?, ?, 1) "
            "ON CONFLICT(chat_id, date) DO UPDATE SET count = count + 1",
            (chat_id, time.strftime("%Y-%m-%d")),
        )
        row = conn.execute(
            "SELECT count FROM usage WHERE chat_id = ? AND date = ?",
            (chat_id, time.strftime("%Y-%m-%d")),
        ).fetchone()
    return row[0] if row else 1

def quota_limit() -> int:
    try:
        with open(ROOT / "config" / "agent.json", encoding="utf-8-sig") as f:
            return int(json.load(f).get("agent_quota_per_day", 20))
    except Exception:
        return 20

# ------------------------------------------------- M8 FIX 5: arrivals
def record_arrival(source: str):
    with db() as conn:
        _ensure_quota_tables(conn)
        conn.execute(
            "INSERT INTO arrivals (ts, source) VALUES (?, ?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), source[:100]),
        )

# ---------------------------------------------------------------- llama api
def server_up() -> bool:
    try:
        with urllib.request.urlopen(MODELS_URL, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False

def chat_completion(messages: list) -> str:
    """POST to llama-server. Raises on any failure - caller catches.
    urllib timeouts: connect 10s, read LLM_TIMEOUT_SECONDS."""
    selected = load_selected()
    payload = json.dumps({
        "model": selected["model_path"],
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 512,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        CHAT_COMPLETIONS_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]

# ---------------------------------------------------------------- rate limit
_chat_lang: dict[int, str] = {}   # last detected language per chat
_last_request: dict[int, float] = {}
_chat_locks: dict[int, asyncio.Lock] = {}
context_bot_holder: dict = {"bot": None}  # set in main() after app is built

def chat_lang(chat_id: int, text: str | None = None) -> str:
    """Detect/remember the chat language. Called with user text on every
    user message; without text returns the remembered language (default en)."""
    if text is not None:
        _chat_lang[chat_id] = i18n_mod.detect_lang(text)
    return _chat_lang.get(chat_id, "en")


def rate_limited(chat_id: int) -> bool:
    """Check AND stamp. Returns True if a new request is not allowed yet."""
    now = time.monotonic()
    last = _last_request.get(chat_id, 0.0)
    if now - last < RATE_LIMIT_SECONDS:
        return False  # not allowed; do NOT update the timestamp
    _last_request[chat_id] = now
    return True

# ---------------------------------------------------------------- voice (M3)
VOICE_CONFIG_FILE = ROOT / "config" / "voice.json"
TMP_DIR = ROOT / "data" / "tmp"
VOICE_TIMINGS = {}  # per-chat timings logged at the end of a voice turn

def load_voice_config() -> dict:
    with open(VOICE_CONFIG_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)

def run_tool(cmd: list, timeout: int, input: bytes | None = None) -> subprocess.CompletedProcess:
    """Run an external tool, raising with stderr on failure/timeout."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, input=input,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{cmd[0]} timed out after {timeout}s")
    if proc.returncode != 0:
        raise RuntimeError(
            f"{cmd[0]} failed rc={proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace')[-400:]}"
        )
    return proc

def cleanup_tmp(max_age_hours: float = 1.0):
    """Delete tmp wav/ogg files older than max_age_hours."""
    if not TMP_DIR.exists():
        return
    cutoff = time.time() - max_age_hours * 3600
    for p in TMP_DIR.glob("*"):
        if p.suffix.lower() in (".wav", ".ogg", ".txt") and p.stat().st_mtime < cutoff:
            try:
                p.unlink()
            except OSError:
                pass

def voice_replies_enabled(chat_id: int) -> bool:
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS voice_pref (chat_id INTEGER PRIMARY KEY, voice INTEGER)")
        row = conn.execute("SELECT voice FROM voice_pref WHERE chat_id = ?", (chat_id,)).fetchone()
    return bool(row and row[0])

def set_voice_replies(chat_id: int, enabled: bool):
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS voice_pref (chat_id INTEGER PRIMARY KEY, voice INTEGER)")
        conn.execute(
            "INSERT INTO voice_pref (chat_id, voice) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET voice = excluded.voice",
            (chat_id, 1 if enabled else 0),
        )

def transcribe_ogg(ogg_path: Path, vc: dict) -> str:
    """ogg -> 16k mono wav -> whisper-cli -> text."""
    wav_path = ogg_path.with_suffix(".wav")
    t0 = time.monotonic()
    run_tool([vc["ffmpeg"], "-y", "-i", str(ogg_path), "-ar", "16000", "-ac", "1", str(wav_path)], 30)
    VOICE_TIMINGS["convert_in_s"] = round(time.monotonic() - t0, 2)

    t0 = time.monotonic()
    txt_path = wav_path.with_suffix(".txt")
    run_tool([
        vc["whisper_cli"], "-m", vc["whisper_model"], "-f", str(wav_path),
        "-nt", "-t", "4", "-otxt", "-of", str(txt_path.with_suffix("")),
    ], 120)
    VOICE_TIMINGS["transcribe_s"] = round(time.monotonic() - t0, 2)

    text = txt_path.read_text(encoding="utf-8", errors="replace").strip()
    for p in (wav_path, txt_path):
        try:
            p.unlink()
        except OSError:
            pass
    return text

def synth_voice(text: str, ogg_path: Path, vc: dict):
    """text -> piper wav -> ffmpeg opus ogg."""
    wav_path = ogg_path.with_suffix(".wav")
    t0 = time.monotonic()
    voice = vc.get("voice") or vc["piper_voice"]
    config = vc["piper_config"] if voice == vc["piper_voice"] else voice + ".json"
    run_tool(
        [vc["piper_exe"], "--model", voice, "--config", config,
         "--output_file", str(wav_path)],
        60, input=text.encode("utf-8"),
    )
    VOICE_TIMINGS["tts_s"] = round(time.monotonic() - t0, 2)

    t0 = time.monotonic()
    run_tool([vc["ffmpeg"], "-y", "-i", str(wav_path), "-c:a", "libopus", "-b:a", "24k", str(ogg_path)], 30)
    VOICE_TIMINGS["convert_out_s"] = round(time.monotonic() - t0, 2)
    try:
        wav_path.unlink()
    except OSError:
        pass

# ---------------------------------------------------------------- handlers
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # M8 FIX 5: deep-link analytics  /start=habr -> source=habr
    first_arg = context.args[0] if context.args else ""
    if first_arg.startswith("="):
        source = first_arg[1:].strip() or "direct"
    elif first_arg:
        source = first_arg.strip()
    else:
        source = "direct"
    record_arrival(source)
    log.info("Arrival recorded: source=%s chat=%s", source,
             update.effective_chat.id)

    # M8 FIX 4: channel link from .env
    link = read_env_var("TELEGRAM_CHANNEL_LINK") or ""
    if "PASTE_LATER" in link or not link:
        link = ""   # placeholder not yet replaced -> omit the line
    L = chat_lang(chat_id)
    feedback_line = i18n_mod.tr(L, "feedback_line", link=link) + "\n" if link else ""

    await update.message.reply_text(
        i18n_mod.tr(L, "start_welcome") + "\n" + feedback_line.rstrip("\n")
    )

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    n = reset_history(chat_id)
    log.info("History reset for chat %s (%d rows deleted)", chat_id, n)
    await update.message.reply_text(
        i18n_mod.tr(chat_lang(chat_id), "reset_done", n=n))

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        selected = load_selected()
        model_id = selected.get("model_id", "?")
    except Exception:
        model_id = "read error"
    up = server_up()
    L = chat_lang(chat_id)
    await update.message.reply_text(i18n_mod.tr(
        L, "status_body", model_id=model_id,
        server=i18n_mod.tr(L, "server_up" if up else "server_down"),
        count=count_messages(chat_id)))

async def generate_reply(chat_id: int, user_text: str, reply_func) -> str | None:
    """Shared M2/M3 pipeline: per-chat lock, rate limit, save user msg,
    LLM call with typing indicator, save reply. Always sends SOME reply.
    Returns the reply text, or None if no LLM answer was produced
    (a wait/fallback reply was already sent via reply_func)."""
    lock = _chat_locks.setdefault(chat_id, asyncio.Lock())

    if lock.locked():
        log.info("Chat %s busy - previous message still processing", chat_id)
        await reply_func(BUSY_REPLY)
        return None

    async with lock:
        if not rate_limited(chat_id):
            wait = int(RATE_LIMIT_SECONDS - (time.monotonic() - _last_request.get(chat_id, 0.0)) + 0.9)
            log.info("Rate limited chat %s (wait ~%ds)", chat_id, wait)
            await reply_func(i18n_mod.tr(chat_lang(chat_id), "rate_limited",
                                         wait=max(wait, 1)))
            return None

        add_message(chat_id, "user", user_text)
        L = chat_lang(chat_id, user_text)   # auto-detect from this message

        messages = [{"role": "system",
                     "content": load_persona()
                     + "\n\nLANGUAGE RULE (overrides the persona above): "
                       "the user's message is in " + L.upper()
                     + ". You MUST reply in " + L + "."}]
        for role, text in get_history(chat_id):
            messages.append({"role": role, "content": text})

        # keep 'typing' visible while the LLM works
        typing_task = asyncio.create_task(_typing_loop(chat_id, context_bot_holder["bot"]))
        try:
            t0 = time.monotonic()
            reply = await asyncio.to_thread(chat_completion, messages)
            VOICE_TIMINGS["llm_s"] = round(time.monotonic() - t0, 2)
        except TimeoutError:
            log.error("LLM timeout after %ds (chat %s)", LLM_TIMEOUT_SECONDS, chat_id)
            await reply_func(LLM_TIMEOUT_REPLY)
            return None
        except Exception as e:
            log.error("LLM request failed: %s", e)
            await reply_func(SERVER_DOWN_REPLY)
            return None
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except (asyncio.CancelledError, Exception):
                pass

        add_message(chat_id, "assistant", reply)
        return reply

async def _typing_loop(chat_id: int, bot_obj):
    """Send 'typing' chat action every 4 seconds until cancelled."""
    try:
        while True:
            await bot_obj.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        raise

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text
    log.info("Message from chat %s: %d chars", chat_id, len(user_text))

    reply = await generate_reply(chat_id, user_text, update.message.reply_text)
    if reply is None:
        return
    # Telegram hard limit is 4096 chars per message
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i:i + 4000])

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """M3: voice in -> whisper -> LLM -> (optional) piper voice out."""
    chat_id = update.effective_chat.id
    voice = update.message.voice
    t_start = time.monotonic()
    VOICE_TIMINGS.clear()
    log.info("Voice message from chat %s, duration %ds", chat_id, voice.duration)

    cleanup_tmp()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    vc = load_voice_config()

    ogg_in = TMP_DIR / f"in_{chat_id}_{int(time.time())}.ogg"
    ogg_out = TMP_DIR / f"out_{chat_id}_{int(time.time())}.ogg"

    try:
        tg_file = await voice.get_file()
        await tg_file.download_to_drive(str(ogg_in))
    except Exception as e:
        log.error("Voice download failed: %s", e)
        await update.message.reply_text(
            i18n_mod.tr(chat_lang(chat_id), "voice_not_recognized"))
        return

    # 1) transcribe (ffmpeg + whisper). On failure -> text fallback, no answer text
    try:
        text = transcribe_ogg(ogg_in, vc)
    except Exception as e:
        log.error("Transcription failed: %s", e)
        await update.message.reply_text(
            i18n_mod.tr(chat_lang(chat_id), "voice_not_recognized"))
        try:
            ogg_in.unlink()
        except OSError:
            pass
        return
    finally:
        try:
            ogg_in.unlink()
        except OSError:
            pass

    if not text:
        log.warning("Transcription empty")
        await update.message.reply_text(
            i18n_mod.tr(chat_lang(chat_id), "voice_not_recognized"))
        return
    log.info("Voice transcribed (%d chars): %.40s", len(text), text)
    chat_lang(chat_id, text)   # detect language from the transcript

    # 2) LLM via shared pipeline; transcription succeeded -> text reply on failure
    reply = await generate_reply(chat_id, text, update.message.reply_text)
    if reply is None:
        return

    # 3) voice out if enabled; on any TTS failure fall back to text reply
    if voice_replies_enabled(chat_id):
        try:
            synth_voice(reply, ogg_out, vc)
            with open(ogg_out, "rb") as f:
                await update.message.reply_voice(f)
            log.info(
                "Voice turn done in %.1fs (convert_in=%s transcribe=%s llm=%s tts=%s convert_out=%s)",
                time.monotonic() - t_start,
                VOICE_TIMINGS.get("convert_in_s"), VOICE_TIMINGS.get("transcribe_s"),
                VOICE_TIMINGS.get("llm_s"), VOICE_TIMINGS.get("tts_s"),
                VOICE_TIMINGS.get("convert_out_s"),
            )
        except Exception as e:
            log.error("TTS failed, falling back to text: %s", e)
            for i in range(0, len(reply), 4000):
                await update.message.reply_text(reply[i:i + 4000])
        finally:
            try:
                ogg_out.unlink()
            except OSError:
                pass
    else:
        log.info(
            "Voice-in/text-out turn in %.1fs (convert_in=%s transcribe=%s llm=%s)",
            time.monotonic() - t_start,
            VOICE_TIMINGS.get("convert_in_s"), VOICE_TIMINGS.get("transcribe_s"),
            VOICE_TIMINGS.get("llm_s"),
        )
        for i in range(0, len(reply), 4000):
            await update.message.reply_text(reply[i:i + 4000])

# ================================================================ agent (M6)
async def _agent_progress(chat_id: int, one_line: str):
    """Progress updater: edit the progress message (throttled by caller)."""
    st = _agent_state
    try:
        bot = context_bot_holder["bot"]
        if st.get("progress_msg_id"):
            await bot.edit_message_text(
                chat_id=chat_id, message_id=st["progress_msg_id"],
                text=f"🤖 {one_line}")
        else:
            m = await bot.send_message(chat_id, f"🤖 {one_line}")
            st["progress_msg_id"] = m.message_id
    except Exception as e:
        log.warning("agent progress edit failed: %s", e)

def _agent_audit_rows(chat_id: int, task_text: str, plan):
    plan_json = json.dumps(plan, ensure_ascii=False)
    def cb(row: dict):
        audit_log(chat_id, task_text, plan_json,
                  row.get("step_action") or row.get("action", ""),
                  row.get("risk_class", ""), row.get("result", ""),
                  row.get("duration_s", 0))
    return cb

async def cmd_agent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/agent <task>: allowlist-gated, plan -> confirm -> execute."""
    chat_id = update.effective_chat.id
    task_text = " ".join(context.args).strip() if context.args else ""

    if chat_id not in allowed_ids():
        audit_log(chat_id, task_text or "(empty)", "[]", "access",
                  "DENIED", "chat_id not in TELEGRAM_ALLOWED_IDS", 0)
        log.info("Agent access DENIED for chat %s", chat_id)
        return  # silent ignore

    L = chat_lang(chat_id, task_text or None)
    if not task_text:
        await update.message.reply_text(i18n_mod.tr(L, "agent_usage"))
        return

    if _agent_state["running"]:
        await update.message.reply_text(i18n_mod.tr(L, "agent_busy"))
        return

    # M8 FIX 2: per-user daily quota
    limit = quota_limit()
    used = agent_quota_used(chat_id)
    if used >= limit:
        audit_log(chat_id, task_text, "[]", "quota", "BLOCKED",
                  f"quota exceeded ({used}/{limit})", 0)
        log.info("Agent quota BLOCKED for chat %s (%d/%d)", chat_id, used, limit)
        await update.message.reply_text(
            i18n_mod.tr(L, "agent_quota_exceeded", limit=limit))
        return
    agent_quota_increment(chat_id)

    _agent_state.update(running=True, chat_id=chat_id, task=task_text,
                        step=0, steps_total=0, started=time.monotonic(),
                        stop_requested=False, proc_holder={}, progress_msg_id=None,
                        plan=[])
    typing = asyncio.create_task(_typing_loop(chat_id, context_bot_holder["bot"]))
    try:
        # ---- Step A: plan
        await update.message.reply_text(i18n_mod.tr(L, "agent_planning"))
        try:
            plan_res = await asyncio.to_thread(
                agent_runner.make_plan, task_text, _agent_state["proc_holder"])
        except Exception as e:
            log.error("agent plan failed: %s", e)
            audit_log(chat_id, task_text, "[]", "plan", "ERROR", str(e)[:400], 0)
            _agent_state["running"] = False
            await update.message.reply_text(
                i18n_mod.tr(L, "agent_plan_failed", err=e))
            return
        steps = plan_res["plan"]
        _agent_state["steps_total"] = len(steps)
        _agent_state["step"] = 0
        _agent_state["plan"] = steps
        audit_log(chat_id, task_text,
                  json.dumps(steps, ensure_ascii=False)[:2000],
                  "plan", "AUTO", f"{len(steps)} steps", 0)
        if not steps:
            _agent_state["running"] = False
            await update.message.reply_text(
                i18n_mod.tr(L, "agent_no_plan"))
            return

        # ---- Step B: confirmation via inline keyboard.
        # IMPORTANT: do NOT block here waiting for the callback. PTB processes
        # updates sequentially by default, so a blocking wait would stall the
        # queue and the button press would arrive after Telegram's query
        # timeout ("Query is too old"). The callback handler owns the rest.
        risk_view = "\n".join(
            f"{i+1}. [{agent_runner.classify_step(s)}] {s[:80]}"
            for i, s in enumerate(steps))
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(i18n_mod.tr(L, "agent_btn_run"),
                                 callback_data="agent_run"),
            InlineKeyboardButton(i18n_mod.tr(L, "agent_btn_cancel"),
                                 callback_data="agent_cancel"),
        ]])
        await update.message.reply_text(
            i18n_mod.tr(L, "agent_plan_header", n=len(steps),
                        risk_view=risk_view),
            reply_markup=kb)
        log.info("Agent plan shown for chat %s, %d steps, awaiting button", chat_id, len(steps))
    finally:
        typing.cancel()

async def on_agent_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """✅/❌ on the plan message. Owns the whole execution phase."""
    query = update.callback_query
    chat_id = query.message.chat.id
    try:
        await query.answer()
    except Exception:
        pass  # query may already be answered/old — not fatal
    L = chat_lang(chat_id)
    if query.data == "agent_cancel":
        _agent_state["running"] = False
        audit_log(chat_id, _agent_state.get("task") or "", "[]",
                  "confirm", "CANCELLED", "user cancelled plan", 0)
        await query.edit_message_text(i18n_mod.tr(L, "agent_cancelled"))
        return
    if not _agent_state["running"]:
        await query.edit_message_text(
            i18n_mod.tr(L, "agent_plan_inactive"))
        return

    # ✅ Исполнить: run execution as a background task so the update
    # queue (typing, /stop, /status, new messages) stays responsive
    await query.edit_message_reply_markup(reply_markup=None)
    await query.edit_message_text(
        query.message.text + i18n_mod.tr(L, "agent_executing"))
    _agent_state["progress_msg_id"] = None
    asyncio.create_task(_run_agent_task(chat_id))

def _sandbox_snapshot() -> dict:
    """name -> (size, mtime) of every file in the sandbox."""
    snap = {}
    for e in os.scandir(SANDBOX_DIR):
        if e.is_file():
            st = e.stat()
            snap[e.name] = (st.st_size, st.st_mtime)
    return snap

def _fs_diff(before: dict, after: dict, lang: str = "en") -> list[str]:
    """Markdown-ish diff lines: + new, ~ changed, - deleted."""
    lines = []
    for name in sorted(set(before) | set(after)):
        b, a = before.get(name), after.get(name)
        if b is None:
            lines.append(i18n_mod.tr(lang, "file_new",
                         path=f"C:\\private-ai\\sandbox\\{name}", size=a[0]))
        elif a is None:
            lines.append(i18n_mod.tr(lang, "file_deleted",
                         path=f"C:\\private-ai\\sandbox\\{name}"))
        elif b[0] != a[0] or b[1] != a[1]:
            lines.append(i18n_mod.tr(lang, "file_changed",
                         path=f"C:\\private-ai\\sandbox\\{name}", size=a[0]))
    return lines

def _preview_new_files(before: dict, after: dict, max_bytes: int = 2048) -> str:
    """First 20 lines of each NEW text file <= 2KB."""
    blocks = []
    for name in sorted(set(after) - set(before)):
        path = SANDBOX_DIR / name
        try:
            if after[name][0] <= max_bytes:
                text = path.read_text(encoding="utf-8", errors="replace")
                lines = text.splitlines()[:20]
                blocks.append(f"```\n" + "\n".join(lines) + "\n```")
        except (OSError, UnicodeError):
            continue
    return "\n".join(blocks)

async def _run_agent_task(chat_id: int):
    """Step C: execute the confirmed plan (background task)."""
    st = _agent_state
    lang = chat_lang(chat_id)
    st["lang"] = lang
    task_text = st.get("task") or ""
    t0 = time.monotonic()
    plan = st.get("plan") or []
    plan_json = json.dumps(plan, ensure_ascii=False)
    audit_rows: list[dict] = []      # in-memory copy for the final report
    last_progress = {"t": 0.0}

    def progress_cb(line: str):
        """Called from the worker thread at EVERY unit start: immediate."""
        st["step"] += 1
        try:
            fut = asyncio.run_coroutine_threadsafe(
                _agent_progress(chat_id, line), _main_loop["loop"])
            fut.result(timeout=10)
        except Exception as e:
            log.warning("progress send failed: %s", e)

    def audit_cb(row: dict):
        audit_rows.append(dict(row))
        audit_log(chat_id, task_text, plan_json,
                  row.get("step_action") or row.get("action", ""),
                  row.get("risk_class", ""), row.get("result", ""),
                  row.get("duration_s", 0))

    # M8 FIX 1: user-visible events from 429-grace / escalation / key switch
    _429_start = {"t": None}
    def event_cb(kind: str, detail: str):
        log.info("agent event: %s: %s", kind, detail)
        try:
            fut = asyncio.run_coroutine_threadsafe(
                _agent_progress(chat_id, detail), _main_loop["loop"])
            fut.result(timeout=10)
        except Exception as e:
            log.warning("event notify failed: %s", e)
        # user-visible delay guard: first 429 starts the clock
        if kind == "429":
            if _429_start["t"] is None:
                _429_start["t"] = time.monotonic()
            elapsed = time.monotonic() - _429_start["t"]
            if elapsed + 60 > 90 and not event_cb._over90_sent:
                event_cb._over90_sent = True
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        context_bot_holder["bot"].send_message(
                            chat_id,
                            i18n_mod.tr(lang, "agent_overload_notice")),
                        _main_loop["loop"])
                    fut.result(timeout=10)
                except Exception as e:
                    log.warning("overload notice failed: %s", e)
        elif kind in ("escalation", "key_switch"):
            _429_start["t"] = None
            event_cb._over90_sent = False
    event_cb._over90_sent = False

    before_snap = _sandbox_snapshot()
    try:
        result = await asyncio.to_thread(
            agent_runner.run_agent,
            task_text, chat_id,
            None,              # confirm_callback: classes already shown on plan
            progress_cb,
            audit_cb,
            lambda: st["stop_requested"],
            st["proc_holder"],
            event_cb,
            user_lang=st.get("lang", "en"),
        )
    except Exception as e:
        log.error("agent run failed: %s", e)
        audit_log(chat_id, task_text, plan_json, "run", "ERROR", str(e)[:400], 0)
        result = {"status": "error", "plan": [],
                  "output": i18n_mod.tr(lang, "run_error", err=e),
                  "answer": "", "audit": []}

    dur = round(time.monotonic() - t0, 1)
    status = result.get("status", "error")
    status_view = i18n_mod.tr(lang, "status_" + (
        "done" if status == "done" else
        "timeout" if status == "timeout" else
        "stopped" if status == "stopped" else "error"))

    # ---- M7 transparent final report
    after_snap = _sandbox_snapshot()
    diff = _fs_diff(before_snap, after_snap, lang)

    parts = [i18n_mod.tr(lang, "report_header", status=status_view,
                         dur=dur, n=len(plan))]
    # one line per audit action
    for row in audit_rows:
        action = (row.get("action") or "")[:70]
        parts.append(f"✅ {action}" if row.get("status_ok", True) else f"❌ {action}")
    parts.append("")
    answer = (result.get("answer") or "").strip()
    if answer:
        # strip hermes boilerplate lines for brevity
        clean = "\n".join(l for l in answer.splitlines()
                          if "hermes update" not in l and "session_id:" not in l
                          and "Gateways may" not in l and "Run `hermes" not in l).strip()
        parts.append(i18n_mod.tr(lang, "report_answer"))
        parts.append(clean[:1200])
    else:
        parts.append(i18n_mod.tr(lang, "report_answer_none"))
    parts.append("")
    parts.append(i18n_mod.tr(lang, "report_files"))
    parts.extend(diff if diff else [i18n_mod.tr(lang, "files_unchanged")])
    preview = _preview_new_files(before_snap, after_snap)
    if preview:
        parts.append(preview)
    parts.append(i18n_mod.tr(lang, "report_journal"))

    msg = "\n".join(parts)
    # Telegram 4096 limit
    for i in range(0, len(msg), 4000):
        await context_bot_holder["bot"].send_message(chat_id, msg[i:i + 4000])
    st["running"] = False
    st["progress_msg_id"] = None
    log.info("Agent task finished in %.1fs status=%s", dur, status)

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stop: kill the running hermes process tree immediately."""
    chat_id = update.effective_chat.id
    st = _agent_state
    L = chat_lang(chat_id)
    if not st["running"]:
        await update.message.reply_text(i18n_mod.tr(L, "stop_not_running"))
        return
    if chat_id not in allowed_ids():
        return  # silent
    st["stop_requested"] = True
    proc = st["proc_holder"].get("proc")
    if proc:
        try:
            agent_runner._kill_tree(proc)
        except Exception as e:
            log.warning("stop: kill failed: %s", e)
    audit_log(chat_id, st.get("task") or "", "[]", "stop",
              "STOPPED", "process tree killed via /stop", 0)
    st["running"] = False
    st["progress_msg_id"] = None
    await update.message.reply_text(i18n_mod.tr(L, "stop_done"))

async def cmd_agent_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status extension: if agent is running, show its state."""
    st = _agent_state
    if st["running"]:
        elapsed = int(time.monotonic() - st["started"])
        await update.message.reply_text(i18n_mod.tr(
            chat_lang(chat_id), "agent_status_running",
            task=st['task'][:200], step=st['step'],
            total=st['steps_total'], elapsed=elapsed))
        return
    # fall through to normal /status
    await _cmd_status_impl(update)

async def _cmd_status_impl(update: Update):
    chat_id = update.effective_chat.id
    try:
        selected = load_selected()
        model_id = selected.get("model_id", "?")
    except Exception:
        model_id = "read error"
    up = server_up()
    L = chat_lang(chat_id)
    await update.message.reply_text(i18n_mod.tr(
        L, "status_body", model_id=model_id,
        server=i18n_mod.tr(L, "server_up" if up else "server_down"),
        count=count_messages(chat_id)))

async def cmd_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle voice replies for this chat."""
    chat_id = update.effective_chat.id
    enabled = not voice_replies_enabled(chat_id)
    set_voice_replies(chat_id, enabled)
    log.info("Voice replies for chat %s -> %s", chat_id, enabled)
    await update.message.reply_text(i18n_mod.tr(
        chat_lang(chat_id), "voice_on" if enabled else "voice_off"))

def acquire_pid_lock() -> None:
    """M7: bot.lock PID-lockfile - two instances must be physically impossible.
    Exits with a message if the lock is held by a live process."""
    lock_file = DB_DIR / "bot.lock"
    if lock_file.exists():
        old_pid = lock_file.read_text(encoding="utf-8", errors="replace").strip()
        if old_pid.isdigit():
            r = subprocess.run(
                ["powershell", "-Command",
                 f"(Get-Process -Id {old_pid} -ErrorAction SilentlyContinue) -ne $null"],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if r.stdout.strip().lower() == "true":
                print(f"BOT ALREADY RUNNING (pid {old_pid}). "
                      f"Stop it first or delete {lock_file}.", flush=True)
                raise SystemExit(1)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(str(os.getpid()), encoding="utf-8")


def release_pid_lock():
    lock_file = DB_DIR / "bot.lock"
    try:
        if lock_file.exists():
            lock_file.unlink()
    except OSError:
        pass


def main():
    acquire_pid_lock()
    try:
        _main_impl()
    finally:
        release_pid_lock()


def _main_impl():
    token = read_bot_token()  # exits here if token missing/placeholder
    _main_loop["loop"] = asyncio.new_event_loop()
    asyncio.set_event_loop(_main_loop["loop"])
    app: Application = ApplicationBuilder().token(token).build()
    context_bot_holder["bot"] = app.bot
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("status", cmd_agent_status))
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(CommandHandler("agent", cmd_agent))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CallbackQueryHandler(on_agent_button, pattern="^agent_(run|cancel)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    log.info("Starting long polling (no webhook, no open ports)...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
