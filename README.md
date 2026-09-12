# Telegram File Agent — agentic system for untrusted operators

Task from phone (Telegram) → plan → risk-class approval → sandboxed execution on local files → report + audit trail.

**Safety model:** tool permissioning (AUTO / CONFIRM / FORBIDDEN risk classes) · guardrails (sandbox, read-only default, no network) · memory · failure-mode analysis (kill-test protocol — a documented caught incident).

**Numbers:** ~1 minute per task · hybrid brain: local llama.cpp + API fallback · full test night: $1.27

**Demo (2 min):** https://youtube.com/shorts/ZKAa5s3XIuE · Example session: /docs

**Related work:** Hermes Agent (NousResearch, MIT) ships multi-platform agent gateways; this project targets operator-safety defaults and business-vertical workflows that horizontal tools leave open.

---

## How it works

```
Telegram chat  ->  bot.py (Telegram dispatcher + audit log)
                   ->  agent_runner.py (LLM loop + tool runner)
                       ->  classifier (AUTO / CONFIRM / FORBIDDEN)
                       ->  sandboxed working directory
```

- `bot.py` — Telegram bot front-end: parses messages, runs the agent, prints a
  human-readable audit of every step, and reads the bot token from `.env`.
- `agent_runner.py` — the agent loop: loads config, classifies each step, runs tools
  inside a sandbox, optionally loads a second API key for a fallback model.
- `test_classifier.py` — unit tests proving the classifier blocks user-home paths and
  dangerous disk operations (intentional FORBIDDEN test fixtures).
- `check_env.py` — sanity check that `.env` keys are populated (not placeholder values).

## Security model

The agent is designed for untrusted operators — a business owner, an employee, a family member — not just for an admin. In short:

- **AUTO** — read-only, safe operations run without asking.
- **CONFIRM** — destructive or external actions require inline approval in Telegram.
- **FORBIDDEN** — blocked at the system level (network access, dangerous paths). Even a hallucinating model cannot execute them.
- Sandboxed working directory: the agent can only touch files inside its sandbox.
- Every action is logged (file path, action, result) and shown in chat in plain language.

See [docs/security-model.md](docs/security-model.md) for the full risk classes, guardrails, audit, and kill-test protocol, and [docs/example-session.md](docs/example-session.md) for an annotated example run.

## Getting started

1. Open the project at `C:\private-ai`.
2. Put your credentials in `.env` (and `.env.second` for the fallback model). Use
   `python bot\check_env.py` to confirm no keys are still placeholders.
3. Run the classifier tests:  `python bot\test_classifier.py`
4. Start the bot:        `python bot\bot.py`

The bot token must be pasted into `.env` as `BOT_TOKEN=...` (never hardcoded in code).

## Repository layout

| Path | What it is |
|------|------------|
| `bot\` | Python source for the Telegram agent |
| `config\` | Runtime config (persona, model selection, hardware profile) |
| `sandbox\demo\` | Demo seed data used by tests and examples |
| `docs\` | Security model + example session write-ups |
| `.env`, `.env.second` | Local secrets — **never committed** |

> This repo intentionally contains only source, config, demo seed, and docs. The large
> model / voice / tool binaries, the chat database (`data\bot.db`), and logs are excluded
> via `.gitignore`.

## Docs

- [docs/security-model.md](docs/security-model.md) — risk classes, guardrails, audit, kill-test protocol.
- [docs/example-session.md](docs/example-session.md) — a full annotated example agent session.
