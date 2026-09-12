# Security model

The agent is designed for untrusted operators — a business owner, an employee, a family member — not just for an admin.

## Risk classes (tool permissioning)
- AUTO — read-only, safe operations run without asking.
- CONFIRM — destructive or external actions require inline approval in Telegram.
- FORBIDDEN — blocked at the system level (network access, dangerous paths). Even a hallucinating model cannot execute them.

## Guardrails
- Sandboxed working directory: the agent can only touch files inside its sandbox.
- Read-only by default; writes only inside the sandbox.

## Audit
- Every action is logged (file path, action, result) and shown in chat in plain language.

## Failure-mode analysis (kill-test protocol)
- We test the agent by trying to break it. A fatal failure injected mid-task must be caught and rolled back before any release. A documented caught incident is part of this repo.
