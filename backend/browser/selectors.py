"""Every DoorDash selector/URL/pattern in one place.

Harvested from the proven old app (ddtr/config.py + ddtr/app.py) and extended
for DashManager's new flows. DoorDash's hashed styled-component classes churn
every deploy — selectors here key on data-testids, placeholders, visible text,
and hrefs only. Re-verify against the live site at each milestone and record
dates in HARVEST_NOTES.md.
"""

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

CHROMIUM_ARGS = ["--disable-blink-features=AutomationControlled"]

# ── URLs ─────────────────────────────────────────────────────────────────────
LOGIN_URL = "https://www.doordash.com/consumer/login"
ORDERS_URL = "https://www.doordash.com/orders"
HELP_ORDERS_URL = "https://www.doordash.com/orders/help"
EDIT_PROFILE_URL = "https://www.doordash.com/consumer/edit_profile"

# URL fragments that mean "we are NOT logged in" (harvest: _scrape_orders).
LOGIN_URL_MARKERS = ["login", "signin", "identity.doordash"]

# URL fragments that mean login is still mid-flight (2FA/OTP/verification/
# challenge). The manual-login wait must keep waiting on these — they contain
# "doordash.com" but are NOT a completed session, so capturing here would save
# a half-authenticated, useless session.
LOGIN_PENDING_MARKERS = [
    "login", "identity", "verification", "verify", "2fa", "otp",
    "challenge", "captcha",
]

# ── Cloudflare / bot gate (harvest: _handle_bot + 2026-06-12 variant B) ─────
# Variant A (handled since the old app): the soft "Verifying you are human"
# interstitial — a wait + reload clears it.
CLOUDFLARE_TEXT = "Verifying you are human"
CLOUDFLARE_WAIT_S = 30
# Variant B (NEW, harder Turnstile gate seen live on Heidi's session): does NOT
# clear on a reload or a short wait. Any of these phrases marks it. Handling:
# wait longer (Turnstile can take 30-60s) polling until the challenge text is
# gone, then ONE fresh navigation, else surface to the caller (relogin/manual).
CLOUDFLARE_B_TEXTS = [
    "performing security verification",
    "protect against malicious bots",
    "needs to review the security of your connection",
    "checking if the site connection is secure",
]
# Marker that we're still ON a challenge page (used while polling variant B).
CLOUDFLARE_RAY_ID_TEXT = "ray id"
CLOUDFLARE_B_MAX_WAIT_S = 60   # Turnstile can take this long to auto-solve
CLOUDFLARE_B_POLL_S = 3        # how often to re-check the challenge text

# ── Order list page (harvest: _scrape_orders + 2026-06 inspector findings) ──
# Old testid first; the live page now shows OrdersV2/OrdersCompletedSection
# wrappers, so fallbacks are ready. Tried in order; first that matches >0 wins.
ORDER_CARD_SELECTORS = [
    '[data-testid="OrderHistoryOrderItem"]',
    '[data-testid="OrdersCompletedSection"] [role="link"]',
    '[data-testid="OrdersV2"] [role="link"]',
]
ORDER_LINK_SELECTOR = 'a[href*="/orders/"]'
CANCELLED_BADGE_TEXTS = ["order cancelled", "order canceled"]
# Broader set for CLASSIFYING a list card as cancelled. The orders-list card can
# show a shorter badge ("Cancelled") than the receipt heading ("Order
# Cancelled"), which caused cancelled orders to be mislabelled `completed`.
# Kept separate from CANCELLED_BADGE_TEXTS (used for description-stripping) so a
# bare "cancelled" token doesn't accidentally swallow a description line.
CANCELLED_STATUS_TEXTS = ["order cancelled", "order canceled",
                          "cancelled", "canceled"]
# A remade/redelivered order's card may carry a remake badge — a remake
# usually doesn't auto-refund, so flag it for the chat to call out.
REMAKE_BADGE_TEXTS = ["remade", "remake", "reordered for you"]
# A self-claimable order: cancelled with a refund available to claim. The card
# shows a "Resolution" button + "Pending Refund"/"Pending Resolution" text and
# CRUCIALLY has NO /orders/<uuid> receipt link yet (verified live 2026-06-13 on
# Traci). The scrape must keep these (as pending_claim) instead of dropping them
# for lacking a UUID — they self-claim to the original card, no chat needed.
PENDING_CLAIM_BADGE_TEXTS = ["pending refund", "pending resolution"]
SCROLL_MAX_ITERS = 40       # scroll-until-stable loop bounds (harvest)
SCROLL_STABLE_ITERS = 2

# ── Order lifecycle signals (verified live 2026-06-12) ──────────────────────
# Empty account: the orders page literally says this and nothing else.
ORDERS_EMPTY_TEXT = "no previous deliveries"
# Active/in-progress orders live under an "In Progress" section with status
# text like these; they show "View Order" (no receipt UUID yet).
ORDERS_IN_PROGRESS_HEADER = "in progress"
# Every pre-delivery / pre-cancel status phrase seen live (2026-06-12). Order
# matters loosely; classify_orders_page / in_progress_status do substring match.
IN_PROGRESS_STATUS_TEXTS = [
    "dasher waiting for order",        # dasher assigned, at restaurant
    "picking up your doubledash order",
    "picking up your order",
    "heading to you",                  # dasher en route
    "preparing",                       # no dasher yet
    "being prepared",
    "order in progress",               # DoubleDash group header
    "arrives between",
    "estimated delivery",
    "confirming your order",
    "finding a dasher",
    "finding you a dasher",
    "dasher is heading",
    "dasher is on the way",
    "almost there",
    "on the way",
    "picked up",
    "order received",
    "waiting for order",
]
# Friendly display labels for the common statuses (used in the UI/live view).
STATUS_DISPLAY = {
    "dasher waiting for order": "Dasher waiting for order",
    "picking up your doubledash order": "Picking up order",
    "picking up your order": "Picking up order",
    "heading to you": "Heading to you",
    "preparing": "Preparing",
    "being prepared": "Preparing",
    "order in progress": "Order in progress",
    "confirming your order": "Confirming order",
    "finding a dasher": "Finding a dasher",
    "finding you a dasher": "Finding a dasher",
    "almost there": "Almost there",
    "on the way": "On the way",
}
# In-progress orders live under this section and have NO /orders/<uuid> link
# on the card (verified live 2026-06-12) — each row is store + status text +
# a "View Order" button (data-anchor-id=ViewOrderButton). Scraped by text.
IN_PROGRESS_SECTION = '[data-testid="OrdersInProgressSection"]'
VIEW_ORDER_BUTTON = '[data-anchor-id="ViewOrderButton"]'

# ── Pending-claim self-claim (verified live 2026-06-12, Wendy $112.24) ──────
# Orders page -> "Resolution" button opens "Choose your refund method", where
# CREDITS is default-selected (BAD). Pick the original-payment radio by its
# visible text, VERIFY it, then Confirm. The receipt then gains a Refund line
# + a "we've issued ... to your original payment method" banner.
RESOLUTION_BUTTON = "Resolution"            # get_by_role("button", name=...)
REFUND_METHOD_ORIGINAL_TEXT = "to original payment method"
REFUND_METHOD_CREDITS_TEXT = "credits"
CLAIM_CONFIRM_BUTTON = "Confirm"            # get_by_role("button", name=...)
# pending_claim has TWO variants (verified live 2026-06-12):
#   • DIRECT (Wendy): Resolution -> straight to the credits-vs-card screen.
#   • REMAKE-OFFER (Heidi): Resolution -> a remake offer page first ("<store>
#     can remake your order", a red "Review new order" button + a "Get refund"
#     link). Click "Get refund" to reach the SAME credits-vs-card screen.
# NEVER click "Review new order" — that accepts the remake instead of refunding.
CLAIM_GET_REFUND_TEXT = "Get refund"
CLAIM_REMAKE_OFFER_TEXTS = ["can remake your order", "review new order",
                            "remake your order"]
CLAIM_AVOID_REMAKE_TEXT = "Review new order"  # must NOT be clicked
# Banner confirming a refund went to the ORIGINAL CARD (claim success signal).
# Only original-payment phrasing counts — a generic "we've issued a refund"
# banner also appears for a CREDITS refund, so it must NOT be a success signal
# here (claim_succeeded checks credits separately).
CLAIM_SUCCESS_TEXTS = [
    "to your original payment method",
    "to your original payment",
    "back to your original",
    "to your original card",
]
CLAIM_NAV_SETTLE_S = 2.0

# ── Support chat (user-specified 2026-06 flow + harvested chat machinery) ───
# Navigation: HELP_ORDERS_URL -> click a[href*="<order-uuid>"] ->
# /help/orders/<uuid>?deliveryUUID=... -> "Contact support" -> widget.
CONTACT_SUPPORT_TEXT = "text=Contact support"
HELP_BUTTON = "[data-testid='helpButton']"  # legacy entry, kept as fallback
GOT_IT_TEXT = "text=Got it"

# Chat input cascade, tried in order (harvest: CHAT_SELS, verbatim —
# the widget's "Type message" placeholder confirmed live 2026-06).
CHAT_SELS = [
    "[placeholder='Type message']",
    "[placeholder*='message' i]",
    "[placeholder*='Type' i]",
    "div.sendbird-message-input--text-field[contenteditable='true']",
    "div[contenteditable='true'][placeholder]",
    "div[contenteditable='true']",
    "textarea[placeholder*='message' i]",
    "textarea",
]

# Counting agent replies on body text (harvest: _count_received).
RECEIVED_RE = r"\bReceived\s+(just now|a |an |\d|moment)"

# Ending a chat (harvest: _send_messages tail).
END_BUTTON_SELECTORS = ["button:has-text('End'):not(:has-text('chat'))",
                        "text=/^End$/"]
END_CHAT_CONFIRM = "text=End Chat"

# After an agent times out / ends the chat, a reconnect affordance usually
# appears. Click it to resume the SAME session before reopening a fresh chat.
RECONNECT_SELECTORS = [
    "button:has-text('Reconnect')",
    "text=Reconnect",
    "button:has-text('Chat again')",
    "button:has-text('Start a new chat')",
    "text=Resume chat",
]

# Silent rate-limit block: page text unchanged or shorter than this after the
# Contact-support click (harvest: _open_chat).
SILENT_BLOCK_MIN_CHARS = 200

REPLY_WAIT_S = 35           # per-reply wait (harvest: _wait_response)
