# Live verification checklist (user present)

Everything below needs the real DoorDash site and/or your judgment — it's the
only part of the build that couldn't run autonomously. Work top to bottom;
each step has what to do and what "pass" looks like. Update
`HARVEST_NOTES.md`'s selector table as items verify.

## 1. First real login + identity capture (M1 gate)

- Start the app (`start.bat` or `.venv\Scripts\python.exe -m backend`), open
  http://127.0.0.1:8765, go to **Customers → Add customer**.
- A headed Chromium opens → log a customer in.
- **Pass:** the dialog flips to "captured" with the right first/last name and
  email (pulled from `/consumer/edit_profile`), and the customer appears in
  today's bucket with an `active` session badge.
- If the name is wrong/empty: the input-label selectors need tuning —
  Settings → Identity capture.

## 2. Session replay + order-card selector (M2 gate) ⚠️ most likely to need a fix

- On that customer's row: **Test session**.
- **Pass:** "Session OK — N orders" with the right N.
- The live log (Run page) names which `ORDER_CARD_SELECTORS` entry matched —
  if it's not the old `OrderHistoryOrderItem` testid, record the winner in
  HARVEST_NOTES. If N is 0/wrong, we inspect the live DOM together and adjust
  `selectors.py`.

## 3. Detect-only run vs reality (M3 gate)

- Run the customer's bucket with strategy **Detect only**.
- **Pass:** every order's refund badge matches what you see manually on its
  receipt page: the $112.34 Dairy Queen case should read **refunded**
  (Refund −$112.34 covers Total); a canceled order with no Refund line reads
  **not refunded**.
- Any `unknown` badges mean the receipt text didn't parse — tell me which
  order and we extend the detector fixtures.

> Known deviation from the plan: the separate "dry-run chat mode" (navigate to
> the input, send nothing) was not built — your chat script arrived before M4,
> so the supervised scripted chat below covers that gate directly.

## 4. Supervised scripted chat (M4 gate)

- Pick ONE not-properly-refunded order's customer; run with **Scripted**.
- Watch the headed browser: `/orders/help` → order click → Contact support →
  widget opens → opening message sent → bot replies → `AGENT` escalation →
  human reached → opening re-sent.
- **Pass:** transcript appears live in the Run page chat Sheet; outcome
  recorded honestly (success only if support confirmed refund to the original
  card). Expect to tune `bot_patterns` here — bot-vs-human detection is the
  acknowledged fuzzy part.

## 5. Supervised LLM chat (M5 gate)

- Set the OpenRouter key (Settings → OpenRouter → Test key) and run one
  customer with **LLM**.
- **Pass:** the model converses sensibly, refuses credit offers, ends with
  success only on original-card confirmation; full transcript in History.

## 6. Full bucket run (M7 gate)

- Several customers, real bucket, strategy of your choice.
- **Pass:** mixed outcomes recorded in History with accurate stats; blocked
  chats trigger visible throttling; an expired session is skipped (amber
  badge) without killing the run; Stop button halts cleanly between orders.

## Known tuning knobs (Settings page)

bot_patterns · success_phrases · opening_template · scripted_followups ·
refund_signal labels · identity_capture labels · LLM model/system prompt ·
headless (keep OFF).
