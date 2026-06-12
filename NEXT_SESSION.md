# Next Session — Start Here

Paste-as-first-prompt context for a FRESH session. Everything below is on disk;
you do NOT need the prior conversation.

## What DashManager is

Local FastAPI + Playwright (Chromium, headed) + React/shadcn app that audits
DoorDash refunds per customer. Accounts are created/logged-in via CustomerDaisy
(api.cc numbers, Mail.tm email). Each customer = its own persistent Chromium
profile in `data/profiles/{id}/`. Read `CLAUDE.md` and `AGENTS.md` first.

## State (as of 2026-06-12)

- 7 customers in DB. 5 with orders: Heidi(7) Wanda(6) Renee(5) Wendy(4)
  Courtney(3) — all logged into DashManager profiles. Renee/Courtney/Wendy
  refunds fully resolved (verified live). Heidi/Wanda/Renee may have more orders
  to handle.
- Detector DONE + tested (three-way: refunded / pending_claim / not_refunded;
  121 tests green). `backend/browser/refund_detector.py`.
- PR #2 (`feat/account-creation-and-isolation` -> base `review-base-2`) OPEN at
  the merge gate — bot + code-reviewer + codex findings all fixed across several
  commits. A few MEDIUM bot findings remain (account_creator temp-leak full
  try/finally wrap; orders.py selector-drift log when page_state=has_completed
  but chosen is None; include cancelled rows in completed_count). Do NOT merge
  without the user's explicit go.

## THE BUILD TARGET → read `REFUND_AUTOMATION_SPEC.md`

That file is the source of truth (distilled from a live walkthrough). Build
order:
1. DB migration: `chats.order_id` FK (chats are currently customer-keyed;
   make them order-keyed) — REQUIRED for the user's per-order transcript audit.
2. `claim.py` — pending_claim self-claim driver: Resolution button -> select
   "to original payment method" radio (default is CREDITS — never pick it) ->
   Confirm -> verify `Refund -$X`. Record a claim audit row per order.
3. Upgrade `chat.py`: 1 chat per unrefunded order, AGENT escalation, then
   RE-SEND the refund request on EVERY agent turn until a confirmation phrase
   (amount + "refunded"/"back to your card") — this is the win condition.
   Decline DashPass. Retry up to 3 attempts (reconnect button or reopen a fresh
   chat). Record EVERY message to chat_messages.
4. Wire into `runner.py`: completed->detect; pending_claim->claim;
   not_refunded/remake->chat. Detect-only mode unchanged.
5. "remake" order detection in orders.py (usually doesn't auto-refund).
6. Frontend: per-order transcript view (all chats per order, all messages as
   bubbles, chronological).

SCRIPTED strategy — NOT LLM. OpenRouter stays an optional toggle, not wired
hot. Selectors + signals are all in `HARVEST_NOTES.md` (verified live).

## Design goal: MINIMAL human-in-the-loop

The app should run a customer end-to-end autonomously — detect every order,
self-claim pending ones, chat+confirm not-refunded ones — and only surface to
the user when it genuinely GIVES UP: 3 failed chat attempts on an order, a
Cloudflare gate it can't pass, a login it can't complete. Everything else
(off-script agent questions, DashPass offers, "who canceled?") it handles on
its own and keeps pushing. The user reviews the per-order transcripts AFTER,
not during.

## Pacing — human-like delays (likely helps Cloudflare + reliability)

Hypothesis worth building in: Cloudflare's bot detection partly reacts to
too-fast/too-mechanical actions, so back-to-back rapid navigations/reloads can
TRIGGER the harder variant-B gate. Make interactions more human-paced:
- Add small randomized settle delays between clicks/navigations (e.g.
  0.8-2.5s jitter, not fixed sleeps), and a longer settle after page loads
  before reading/acting.
- Avoid rapid repeated reloads on a challenge page — wait it out, then ONE
  fresh navigation, rather than hammering reload.
- This also reduces flaky "page didn't render in time" reads (the user noted
  earlier our headless breakdown failed from too-tight waits, not blocking).
Centralize a `human_pause()` helper (jittered sleep) and use it in the drivers.

## FIRST THING TO FIX — Cloudflare variant B

Heidi's session hit a harder gate: "Performing security verification" /
"protect against malicious bots" + a Ray ID. It did NOT clear on reload or a
20s+ wait (unlike variant A "Verifying you are human" which clears on
30s+reload). `backend/browser/driver.py:handle_cloudflare` only detects
variant A. Get past variant B — options, try in order:
1. Detect the variant-B text too; wait longer (Turnstile can take 30-60s),
   poll the URL/title until it leaves the challenge page, then proceed.
2. If it persists, a FRESH navigation (new page / goto again) often gets a
   clean pass where a reload doesn't.
3. Re-login the account (its session may be stale-flagged) — relogin grabs a
   fresh OTP and re-establishes the profile; the user is fine spending api.cc
   numbers (resends free). `backend/relogin.py: relogin_customer(cid)`.
4. Last resort: surface it to the user (the user noted early that a
   blocked/VPN state is something they can fix on their end).
Add variant-B handling to `handle_cloudflare` either way.

## Process (user's rules — non-negotiable)

- Work on a feature BRANCH -> PR -> bot reviews + a `code-reviewer` subagent on
  the full diff + `/codex` for big chunks -> address findings -> WAIT for the
  user's explicit merge approval. Never commit to main; never merge unsolicited.
- TDD: cover pure logic with pytest. `.venv\Scripts\python.exe -m pytest -q`.
- Run via `python -m backend` (Proactor loop). Windows: LF endings, no `nul`
  files, EOL-guard after edits.

## Memory / docs index
- `REFUND_AUTOMATION_SPEC.md` — the build plan (READ FIRST for the build)
- `HARVEST_NOTES.md` — every verified selector/signal + both Cloudflare variants
- `CLAUDE.md` / `AGENTS.md` — project rules
- `.claude/session-log.md` — running history
- Memory files: dashmanager-project-state, dashmanager-refund-flow
