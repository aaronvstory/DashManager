# DashManager

Local web app that audits DoorDash refunds per customer — and goes after the
missing ones.

**Pipeline:** log a customer in once (headed Chromium) → the app captures the
session *and* the account profile → customers are organized into date buckets
you assign → a run replays each session, scrapes **all** orders, opens every
receipt, and checks the breakdown for a real `Refund −$X` line → orders without
a proper refund get a support chat (scripted, or LLM-driven via OpenRouter)
that escalates past the bot with `AGENT` and insists on a refund **to the
original payment card — credits don't count**. Every chat is transcripted for
audit.

## Quickstart

```powershell
uv venv .venv
uv pip install --python .venv -e ".[dev]"
.venv\Scripts\playwright.exe install chromium
cd frontend && npm install && npm run build && cd ..
.venv\Scripts\python.exe -m backend        # http://127.0.0.1:8765
```

Dev UI with hot reload: `cd frontend && npm run dev` (port 5173, proxies
`/api`). Tests (no browser needed): `.venv\Scripts\python.exe -m pytest -q`.

> Always start the backend with `python -m backend`, never the `uvicorn` CLI —
> the entrypoint installs the Windows Proactor event-loop policy Playwright
> requires.

## Architecture

```
backend/
  runner.py            RunManager — per-customer orchestration loop
  events.py  main.py   EventBus → SSE /api/events (live UI progress)
  db.py                SQLite (data/dashmanager.db): customers, orders, runs,
                       chats, transcripts, settings
  browser/
    selectors.py       every DoorDash selector/URL/pattern, one place
    driver.py          launch, session replay, Cloudflare gate, screenshots
    session.py         manual-login capture + profile identity scrape
    orders.py          order-list scrape (scroll-until-stable)
    refund_detector.py pure receipt parser (the correctness core)
    chat.py            chat navigation, AGENT escalation, send/reply loop
    chat_strategy.py   ScriptedStrategy | LlmStrategy (pluggable)
  llm/                 OpenRouter client + system prompt
frontend/              Vite + React + Tailwind v4 + shadcn, dark-default
                       DoorDash theme, SSE-driven live run view
```

- **Refund truth:** the receipt breakdown only. `Refund` line covering the
  total ⇒ refunded; smaller ⇒ partial; missing ⇒ **not refunded**, no matter
  what the cancellation banner says.
- **Chat strategies** are browser-free state machines; the Playwright driver
  feeds them the transcript and executes their actions, with hard rails (turn
  cap, timeouts, API failure ⇒ flag-for-manual + screenshot).
- **Settings** (refund signal, chat script, bot patterns, OpenRouter key/model,
  identity labels, headless) live in SQLite and are editable in the UI.

## Data safety

`data/` — the SQLite DB, per-customer sessions/cookies, screenshots — is
gitignored from commit #1 and must stay that way. The old app this harvests
from is read-only reference; its customer data files are never read.

## Status

Built autonomously per the approved plan; all pure logic is test-covered
(98+ tests). Pending live verification with the user present: first real
login + identity capture, order-card selector check (`HARVEST_NOTES.md`),
supervised scripted + LLM chats.
