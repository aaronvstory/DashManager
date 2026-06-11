"""Paths, environment, and default settings for DashManager."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")

DATA_DIR = BASE / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
DB_PATH = DATA_DIR / "dashmanager.db"
FRONTEND_DIST = BASE / "frontend" / "dist"

for _d in (DATA_DIR, SESSIONS_DIR, SCREENSHOTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

PORT = int(os.getenv("DASH_PORT", "8765"))


def openrouter_api_key(settings_value: str | None = None) -> str | None:
    """Resolution order: settings-row override, then environment."""
    return settings_value or os.getenv("OPENROUTER_API_KEY") or None


# Defaults for every settings key; rows in the `settings` table override these
# (dict values are shallow-merged so new default keys appear after upgrades).
DEFAULT_SETTINGS: dict[str, object] = {
    "identity_capture": {
        "url": "https://www.doordash.com/consumer/edit_profile",
        # Inputs are located by visible form labels — stable across DoorDash
        # deploys, unlike hashed styled-component classes.
        "labels": {
            "first_name": "First Name",
            "last_name": "Last Name",
            "email": "Email",
            "phone": "Phone Number",
        },
    },
    "refund_signal": {
        # Receipt-breakdown parsing keys on text + dollar amounts, never CSS.
        "total_label": "Total",
        "refund_label": "Refund",
        "cancelled_texts": [
            "order cancelled",
            "order was canceled",
            "order was cancelled",
        ],
    },
    "chat": {
        "opening_template": (
            "Hi, I placed {order_count} order(s) for {amounts} and they are not "
            "showing as refunded. Please ensure they are REFUNDED back to my "
            "original payment card (not credits)."
        ),
        "agent_word": "AGENT",
        "scripted_followups": [
            "Can you please confirm the refund has been processed back to my "
            "original payment card?",
            "Thank you for confirming.",
        ],
        "bot_patterns": [
            "virtual assistant",
            "i'm your doordash",
            "select one of the options",
        ],
        "max_escalations": 6,
        "success_phrases": [
            "refunded to your original",
            "refunded back to your",
            "refund has been processed",
            "refund has been issued",
            "processing your refund",
            "issued a refund",
        ],
        "max_turns": 12,
        "max_chat_seconds": 300,
        # How long to wait for a reply after sending the AGENT word — humans
        # take far longer to connect than the bot takes to answer.
        "human_wait_seconds": 90,
    },
    "llm": {
        "model": "anthropic/claude-sonnet-4.5",
        "system_prompt": "",  # empty -> backend.llm.prompts.default_system_prompt()
        "max_turns": 12,
    },
    "browser": {"headless": False, "viewport": [1400, 900]},
    "openrouter_api_key": "",  # UI-set override; env var preferred
    "daisy": {
        # CustomerDaisy install — DashManager shells out to its venv.
        "root": r"C:\claude\CustomerDaisy",
        # Default account-creation location + radius (Edenton, NC = index 0).
        "location_origin": "706 N Broad St, Edenton, NC 27932",
        "radius_miles": 5.0,
    },
}
