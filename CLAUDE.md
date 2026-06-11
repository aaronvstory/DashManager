# DashManager — Project Instructions

Local web app auditing DoorDash refunds per customer: login once per customer
(headed Chromium) → capture session + profile identity → organize customers
into user-assigned date buckets → run checks that scrape ALL orders, open each
receipt, detect refunded-or-not, and pursue missing refunds via support chat
(scripted or LLM via OpenRouter), with full audit transcripts.

## Run

```
.venv\Scripts\python.exe -m backend     # backend + built frontend on :8765
cd frontend && npm run dev              # dev UI on :5173 (proxies /api)
.venv\Scripts\python.exe -m pytest -q   # tests (no browser needed)
```

Always start the backend via `python -m backend` — it installs the Windows
Proactor event-loop policy before uvicorn; the `uvicorn` CLI breaks Playwright.

## Hard rules

- **Never commit data.** `data/` (SQLite DB, per-customer sessions/cookies,
  screenshots) is gitignored from commit #1. Real customer names/emails stay
  out of code, tests, and docs.
- **The old app is a read-only quarry**: `F:\claude\claudedasher\claudedasher\`
  (`ddtr/app.py`, `ddtr/config.py`). NEVER read its data files
  (`customers.json`, `*cookies*`, `*storage*`).
- **Selectors live in `backend/browser/selectors.py` + the settings table** —
  never inline DoorDash selectors in logic. Key on testids/text/labels/hrefs,
  never hashed styled-component classes.
- Refund truth = receipt breakdown `Refund -$X` line vs `Total`. Cancellation
  text alone proves nothing; no Refund line ⇒ pursue.
- Chat success = refund confirmed **to original payment method**. Credits ≠
  success.
- LF line endings everywhere (pinned via .gitattributes); `.bat` files CRLF.

## Map

- `backend/runner.py` — RunManager: the per-customer orchestration loop.
- `backend/browser/` — Playwright: driver, session+identity, orders scrape,
  refund_detector (pure), chat driver (AGENT escalation), chat_strategy
  (Scripted/Llm state machines, browser-free).
- `backend/llm/` — OpenRouter client + system prompt.
- `backend/events.py` → SSE at `/api/events`; event types in `models.py`
  mirrored by `frontend/src/lib/types.ts`.
- `HARVEST_NOTES.md` — which old-app logic was ported + selector verification
  dates. Update when re-verifying selectors live.

## Verification gates (live, user present)

Real login + identity capture; detect-only bucket run vs manual receipt
inspection; one supervised scripted chat reaching a human; one supervised LLM
chat. Record selector re-verification dates in HARVEST_NOTES.md.
