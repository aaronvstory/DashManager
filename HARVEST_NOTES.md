# Harvest Notes

What DashManager ported from the proven old app
(`F:\claude\claudedasher\claudedasher\ddtr\`, read-only quarry), what is new,
and when each selector was last verified against the live site.

## Ported (battle-tested in the old app)

| Logic | Old source (`ddtr/app.py`) | New home |
|---|---|---|
| Stealth launch args + UA | run flow ~1608 | `browser/driver.py`, `selectors.py` |
| Session replay: storage_state first, cookies fallback | 1612–1653 | `driver.new_customer_context` (plain JSON — encryption layer dropped on purpose) |
| Login wait predicate (`doordash.com` ∧ ¬`login` ∧ ¬`identity`, 300 s) | `_login_async` 977–1031 | `session.login_and_capture` |
| Cloudflare "Verifying you are human" → wait 30 s + reload | `_handle_bot` 2092–2097 | `driver.handle_cloudflare` |
| Not-logged-in URL markers (`login`/`signin`/`identity.doordash`) | `_scrape_orders` 1839–1841 | `orders.scrape_orders` → `SessionExpiredError` |
| Scroll-until-stable order loading (max 40 iters, stable ×2) | 1849–1861 | `orders.scrape_orders` |
| Chat input cascade `CHAT_SELS` (verbatim) | `ddtr/config.py` 28–37 | `selectors.CHAT_SELS` |
| contenteditable send sequence (click → clear → type → Enter; `fill()` breaks sendbird) | `_send_messages` 2256–2336 | `chat.send_message` |
| Reply counting regex `\bReceived\s+(just now\|a \|an \|\d\|moment)` | `_count_received` 2202–2213 | `selectors.RECEIVED_RE`, `chat.count_received` |
| "Got it" popup retry; double "Got it" → review_blocked | `_open_chat` 2147–2181 | `chat.navigate_to_chat` |
| Silent rate-limit block (text unchanged / <200 chars) | `_open_chat` 2183–2189 | `chat.navigate_to_chat` |
| End chat (`End` → `End Chat` confirm) | 2321–2326 | `chat.end_chat` |
| Auto-throttle after blocked chats (+8 s → 45 s cap, 120 s decay) | 1915–1926, 2023–2031 | `runner.py` |
| Screenshots on error paths | `_ss` 2338–2344 | `driver.screenshot` |

## New in DashManager (not in the old app)

- **Identity capture** from `/consumer/edit_profile` (first/last/email/phone
  via form labels) — old app never knew who was logged in.
- **Refund detection** from the receipt breakdown (`Total $X` / `Refund -$Y`
  text parsing; cancellation text alone counts for nothing). Old app only
  detected tip text.
- **Chat navigation path**: old app used order page → `helpButton` →
  "Something else" → "Contact support". The user-verified 2026-06 flow is
  `/orders/help` → click `a[href*="<order-uuid>"]` → "Contact support" →
  bottom-right widget. `helpButton` kept only as a legacy constant.
- **AGENT escalation loop** past the virtual-assistant bot, then re-send the
  opening message to the human.
- **One chat per customer** bundling all not-properly-refunded orders.
- **LLM strategy** (OpenRouter, strict-JSON action protocol, never-credits
  rules) alongside the scripted strategy.
- Dropped: CustomTkinter UI, Fernet/password layer, `min_order` filter, tip
  messages, URL-keyed customers.json schema, worker-thread/queue model
  (replaced by FastAPI async + SSE).

## Selector verification log

| Selector / flow | Status | Date | Notes |
|---|---|---|---|
| `[data-testid="OrderHistoryOrderItem"]` | ⚠️ SUSPECT | 2026-06-11 | User's inspector shows `OrdersV2` / `OrdersCompletedSection` wrappers; fallback cascade in `ORDER_CARD_SELECTORS` ready. **Verify on first live run.** |
| `"Type message"` chat input placeholder | ✅ seen live | 2026-06-11 | Visible in user's screenshot of the support widget. |
| `/orders/help` → order link → "Contact support" flow | ✅ seen live | 2026-06-11 | From user's click-through screenshots. |
| Receipt `Total` / `Refund -$X` breakdown text | ✅ seen live | 2026-06-11 | User's receipt screenshot ($112.34 case) is a test fixture. |
| `/consumer/edit_profile` labels (First Name…) | ✅ seen live | 2026-06-11 | From user's screenshot. Input-attr fallbacks coded. |
| Login URL predicate | ⏳ code-ported | 2026-06-11 | Verify at first real login. |
| Cloudflare gate handling | ⏳ code-ported | 2026-06-11 | Verify when encountered. |
| "Got it" popups / silent-block / End chat | ⏳ code-ported | 2026-06-11 | Old flow; verify during first supervised chat. |
| Bot-vs-human detection patterns | ⏳ untested | 2026-06-11 | `bot_patterns` setting; expect live tuning during supervised chats. |

Update this table whenever a selector is re-verified or fixed.

## Account creation (CustomerDaisy signup) — verified live 2026-06-12

Full end-to-end account creation succeeded (account: a real DoorDash consumer,
Edenton NC identity from CustomerDaisy). Confirmed working:

| Step | Selector / detail | Status |
|---|---|---|
| Signup form | direct URL `identity.doordash.com/auth/user/signup`; fields by `get_by_role("textbox", name=...)` First/Last/Email/Mobile/Password; submit `button "Sign Up"` | ✅ verified |
| Phone field | type 10 bare digits; DoorDash auto-formats to `(NPA) NXX-XXXX` | ✅ verified |
| OTP modal | `div[role='dialog'] input` aria-label "Enter your 6-digit code", `type=number`, single box (NOT split); submit button labelled "Submit" | ✅ verified |
| api.cc live OTP | `fetch_code_once` extracted the code from the real SMS first poll | ✅ verified |
| OTP expiry | a code can EXPIRE between arrival and submit → modal stays, no redirect. Resend (free) + submit next code works. `signup.py` now auto-resends on a non-success submit | ✅ verified + handled |
| Success | redirect to `doordash.com/home` (then `?newUser=true` after address) | ✅ verified |
| Address modal | post-OTP "Unlock $0 delivery" — `combobox`/input placeholder "Enter delivery address"; type full address → click matching autocomplete row | ✅ verified |
| Cloudflare on signup | same "Verifying you are human" gate; existing `handle_cloudflare` clears it | ✅ verified |

Bridge (subprocess under CustomerDaisy venv): balance/locations/generate_identity/
rent_number/fetch_otp/save_customer all verified live. api.cc balance read $12.74.

## Refund states + claim/chat flow — verified live 2026-06-12 (with user)

Three-way refund classification (detector now implements this):

| State | Signal on receipt | Action |
|---|---|---|
| **refunded** | breakdown has `Refund -$X` line (>= total); or banner "We've issued $X refund ... to your original payment method" | done, skip |
| **pending_claim** | order under "Pending Resolution" w/ "Pending Refund" badge + "Resolution" button; opening shows "Choose your refund method" | SELF-CLAIM (no chat) |
| **not_refunded** | cancelled, NO refund line, NO resolution option — breakdown ends at Total -> Payment | CHAT with agent |

**pending_claim self-claim flow (verified — Wendy $112.24):**
1. Orders page -> "Resolution" button (`get_by_role("button", name="Resolution")`)
2. -> `/orders/<uuid>` "Choose your refund method": two options, **credits is DEFAULT-selected (BAD)**
3. Click text "to original payment method" (`get_by_text("to original payment method")`) — selects that radio
4. VERIFY correct radio selected (screenshot/DOM) BEFORE confirming
5. Click "Confirm" (`get_by_role("button", name="Confirm")`)
6. -> "We've issued $X refund ... to your original payment method" + breakdown gains `Refund -$X`

**Chat flow (verified — Wendy $106.71):**
1. `/orders/help` -> click the order's $amount text (cards have NO href) -> `/help/orders/<uuid>?deliveryUUID=...`
2. Click "Contact support" -> chat widget opens (quick-reply buttons + "Type message" input)
3. Type the opening message (store + amount + "refund to original card, not credits"), Enter
4. Bot replies (often confirms no refund on record + offers to escalate)
5. Type "AGENT" -> bot: "I'm unable to resolve ... connect you with a human" -> "Connecting you with an agent..." (loading)
6. Human joins: **"You are now connected to our support agent"** — agent echoes the amount + "original payment card"
7. Answer any clarifying Qs (e.g. "store canceled due to items unavailable"), ask to confirm BOTH today's orders refunded to card + amounts; thank + goodbye. Decline DashPass.

**Cloudflare — TWO variants (2026-06-12):**
- Variant A (handled): "Verifying you are human" → wait 30s + reload clears it.
- Variant B (NEW, NOT yet handled): "Performing security verification" /
  "protect against malicious bots" + a Ray ID — a harder Turnstile gate that
  did NOT clear with reload or a 20s+ wait on Heidi's session. `handle_cloudflare`
  must also detect this text; handling likely needs a longer wait, a fresh
  navigation, or surfacing to the user (per the early VPN/connection note).
  This is a real robustness gap to design for.

**Detection signals for the automation:**
- bot vs human: bot says "virtual assistant" / shows quick-reply buttons; human handoff = "Connecting you with an agent" then "connected to our support agent".
- ⚠️ **Agents time out if you're slow** — "Since you are unresponsive let me call you" after a delay. The automation must reply promptly once a human is engaged (LLM/scripted = near-instant; this is where the speed matters more than the cleverness).
