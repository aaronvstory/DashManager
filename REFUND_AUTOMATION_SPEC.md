# Refund Automation Spec

Distilled from the live walkthrough (2026-06-12) — the requirements that make
the refund pipeline robust for real life. This is the build target.

## Strategy decision: SCRIPTED, not LLM

The chat is simple enough to script. The win condition is just: **keep
restating the request until the agent writes a confirmation containing the
amount + a refund/card phrase.** No comprehension needed. The LLM strategy
stays available as an optional toggle for accounts where scripted stalls, but
OpenRouter is NOT wired hot by default. (Scripted is also faster — and speed
matters: agents time out if you're slow.)

## Per-customer run flow

For each customer:
1. Scrape `/orders` — collect ALL orders (1 to ~4; usually 2, rarely 3-4).
2. Classify each (open its receipt, run the detector):
   - **refunded** (receipt has `Refund -$X`) → skip, done.
   - **pending_claim** ("Pending Refund" badge / "Choose your refund method")
     → SELF-CLAIM (no chat): Resolution → select "original payment method"
     (NEVER credits) → Confirm. Re-check it shows `Refund -$X`.
   - **not_refunded** (cancelled, no refund line, no claim) → needs a CHAT.
   - **remake** → an order marked "remake" usually does NOT auto-refund.
     Treat like not_refunded but flag it; the chat message calls it out
     ("including the remake I never asked for").
3. For every order needing a chat, open support and confirm the refund.

## Chat handling — robust version

**Simplest reliable model: 1 chat per order** (the app opens a separate
support chat for each not-refunded order and confirms it individually). This
is cleaner to automate than bundling. Bundling (one chat, multiple amounts)
also works — we did it live — but per-order is simpler and each confirmation
is unambiguous. Default to **one chat per unrefunded order**.

Per chat:
1. Navigate: `/orders/help` → click the order's `$amount` text (cards have no
   href) → `/help/orders/<uuid>?deliveryUUID=...` → "Contact support" → widget.
2. Send the opening message: "Hi, my order from {store} for ${amount} was
   canceled but I have not received a refund. Please refund ${amount} back to
   my original payment card (not credits)." (For a remake: append "— this was
   an automatic remake I never asked for.")
3. **Escalate:** on each bot reply, send `AGENT`. Bot → "Connecting you with
   an agent" → human: "you are now connected to our support agent".
4. **Confirmation loop (the key robustness):** once a human is connected,
   on EVERY agent turn, RE-SEND the request ("Please make sure ${amount} is
   refunded back to my original card, and confirm the amount"). The user
   confirmed this is fine — re-sending every turn never annoys them and beats
   the timeout. Keep going until the agent writes a confirmation phrase.
5. **Success signal:** agent text contains the amount AND a refund-confirmed
   phrase ("has been refunded", "refund of $X ... processed", "refunded ...
   original payment method"). Then: thank + goodbye. Decline DashPass if
   offered ("No thank you, I don't want DashPass.").

## Robustness rules (real-life)

- **Retry up to 3 chat attempts** before giving up on an order. If the agent
  times out / ends the chat, a "reconnect" button usually appears — click it;
  if missed, REOPEN a fresh chat. Loop until confirmation or 3 attempts, then
  flag for manual.
- **Answer off-script questions cheaply, then re-push.** Rare questions (e.g.
  "who canceled — dasher or store?" — seen once in hundreds) don't need smart
  answers; reply plausibly ("the store canceled it due to items unavailable")
  and re-send the request. It doesn't have to be perfect.
- **Reply promptly.** Agents send "Since you are unresponsive..." then offer
  to call. Scripted replies are near-instant, which avoids this. Decline call
  offers: "I can't take a call right now, please ensure it's refunded to my
  card."
- **Variable order counts:** 1 order → mention one amount; 2-4 → handle each.
  Per-order chats make this trivial (N orders → up to N chats).

## Detector (DONE — verified live)

`refund_detector.detect()` implements the three-way model. `pending_texts`
config drives pending_claim. Real `Refund -$X` line always wins.

## Selectors (verified live 2026-06-12) — see HARVEST_NOTES.md

- Resolution button: `get_by_role("button", name="Resolution")`
- Refund-method radio: `get_by_text("to original payment method")` (default is
  credits — MUST switch); Confirm: `get_by_role("button", name="Confirm")`
- Help order pick: click the `$amount` text on `/orders/help`
- Contact support: `text=Contact support`; chat input: CHAT_SELS ("Type message")
- Human-connected signal: "connected to our support agent"
- Success phrases: amount + ("has been refunded" | "refund of $X" | "refunded
  ... original payment method")

## Transcripts — per-order, always viewable, audit trail (REQUIRED)

Every order gets its own chat section in the app showing ALL chats for that
order — including MULTIPLE chats per order (retries, reopened sessions). The
user reviews these manually to verify what actually happened with each refund.

Data-model change (current `chats` is keyed by customer — make it order-aware):
- `chats` gains `order_id` (FK). A chat belongs to ONE order. An order can
  have MANY chats (attempt 1, attempt 2, reopened after timeout…).
- Each chat: attempt number, outcome (success/failed/blocked/manual/timeout),
  agent_reached, started/finished, the full opening message.
- `chat_messages` already stores every turn (direction out/in/system,
  content, ts) — keep recording EVERY message both ways, plus system markers
  (escalation attempts, "human connected", "reopened", DashPass-declined).
- Self-claim (pending_claim) actions also get a lightweight record per order
  (claimed amount, original-payment-method, confirmed) so the audit covers
  non-chat resolutions too.

UI: the Database Viewer / order detail shows, per order: its refund status +
a list of its chats (each expandable to the full transcript as bubbles), in
chronological order. Multiple chats stack under the order.

## What to build next

1. DB migration: `chats.order_id` (FK) + per-order chat queries. Backfill
   existing chats best-effort (they were customer-keyed).
2. `claim.py` — pending_claim self-claim driver (Resolution → original-payment
   radio → Confirm → verify). Records a claim audit row per order. Supervised.
3. Extend `chat.py` driver: per-order chat, AGENT escalation, re-push-every-
   turn confirmation loop, success-phrase detect, decline-DashPass, 3-attempt
   retry with reconnect/reopen. Records EVERY turn to chat_messages.
4. Wire both into `runner.py`: completed→detect; pending_claim→claim;
   not_refunded/remake→chat. Detect-only mode unchanged.
5. "remake" detection in orders.py (status text / badge).
6. Frontend: per-order transcript view (all chats, all messages, bubbles).
