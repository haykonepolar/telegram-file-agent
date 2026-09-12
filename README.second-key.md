# Second zai API key (recovery / backup)

## What it is
`.env.second` holds a backup zai API key. If the primary key is rejected
(HTTP 401/403), agent_runner.py loads `ZAI_API_KEY_SECOND` from this file
and retries automatically, logging `switched to second key`.

## Setup
1. Paste the real key after `ZAI_API_KEY_SECOND=` below (replace
   `PASTE_KEY_HERE`).
2. Restart the bot (05-start-bot.ps1).

## Recovery procedure (key rejected mid-run)
1. Paste the working key into `ZAI_API_KEY_SECOND=PASTE_KEY_HERE`.
2. Restart the bot — the runner picks it up on the next agent call.

## Notes
- The placeholder value `PASTE_KEY_HERE` is treated as "no key".
- Never commit this file or print its contents.
