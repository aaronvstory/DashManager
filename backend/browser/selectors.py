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

# ── Cloudflare / bot gate (harvest: _handle_bot) ─────────────────────────────
CLOUDFLARE_TEXT = "Verifying you are human"
CLOUDFLARE_WAIT_S = 30

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
SCROLL_MAX_ITERS = 40       # scroll-until-stable loop bounds (harvest)
SCROLL_STABLE_ITERS = 2

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

# Silent rate-limit block: page text unchanged or shorter than this after the
# Contact-support click (harvest: _open_chat).
SILENT_BLOCK_MIN_CHARS = 200

REPLY_WAIT_S = 35           # per-reply wait (harvest: _wait_response)
