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

## Operating playbook (how Claude runs this — read before a refund session)

This task is run **manually + intelligently by Claude**, headed, user watching.
The `/dash-refunds` skill is the full playbook; the `/dash-create` skill is
account prep. Key durable facts so a fresh session is fast:

- **Account CREATION is bot-blocked** (DoorDash 403s automated browsers,
  `user_assessment_bot`). The user creates accounts in **CustomerDaisy** (a real
  browser). Claude only does everything AFTER: adopt → login (no bot gate) →
  refund audit → report. Numbers from a failed signup are NOT burned.
- **FOUR refund states:** refunded · pending_claim (self-claim Resolution button,
  never pick credits) · not_refunded (chat) · credits-issued (chat → agent →
  convert credits to the original card). Pending-claim cards often have NO
  `/orders/<uuid>` link — kept by "Pending Refund" text (`claimable_from_card`).
- **Refunds are lost forever after ~3 days** — resolve promptly.
- **Live support-chat rules:** poll ≤30s (agents disconnect if you're slower);
  reply briskly (short, blunt-but-polite); do ALL a customer's orders in one
  chat; nudge if quiet; SUCCESS = the agent confirms EACH amount VERBATIM to the
  CARD (a singular "the refund was issued" is NOT enough for multiple orders);
  use the red "Reconnect with an Agent" button (carries context).
- **Browser window is 1200x720** (fits the user's screen when resized). Never
  kill chrome by name — kill the PID holding the profile's `--user-data-dir`,
  and `rm data/profiles/<id>/SingletonLock` to clear a stale lock.
- **REPL hygiene:** desktop-commander REPL mangles multi-line `async def` +
  inline-JS regex — write single-line statements, or a real `.py` file (never a
  /tmp heredoc; venv resolves /tmp as F:\tmp). Don't dump a DoorDash page's full
  innerText (200KB+ GraphQL).
- **Proof:** every run captures full-page screenshots (orders page per customer
  + receipt per order + chat confirmation) into `data/screenshots/<date>/`,
  linked as thumbnails in the report. The report shows a per-customer breakdown
  table (amount · checked · refunded · method · confirmation + totals).
- A card-based self-claim can succeed even when the runner's auto-verify flags
  it "manual" — always VERIFY ground truth (reopen the receipt) before trusting
  a self-reported outcome.
