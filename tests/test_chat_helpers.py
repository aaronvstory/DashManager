"""Pure-helper tests for backend.browser.chat (no Playwright/browser)."""
import pytest

from backend.browser.chat import detect_success, extract_diff, is_bot_reply

BOT_PATTERNS = [
    "virtual assistant",
    "i'm your doordash",
    "select one of the options",
]

SUCCESS_PHRASES = [
    "refunded to your original",
    "refunded back to your",
    "refund has been processed",
    "refund has been issued",
    "processing your refund",
    "issued a refund",
]


# ── extract_diff ─────────────────────────────────────────────────────────────

class TestExtractDiff:
    def test_new_lines_appended(self):
        prev = "Hello\nHow can I help you today?"
        curr = "Hello\nHow can I help you today?\nYour refund is on its way."
        assert extract_diff(prev, curr) == "Your refund is on its way."

    def test_multiple_new_lines_preserve_order(self):
        prev = "Welcome"
        curr = "Welcome\nLine one\nLine two\nLine three"
        assert extract_diff(prev, curr) == "Line one\nLine two\nLine three"

    def test_identical_texts(self):
        text = "Hello\nHow can I help?"
        assert extract_diff(text, text) == ""

    def test_both_empty(self):
        assert extract_diff("", "") == ""

    def test_received_timestamp_lines_stripped(self):
        prev = "Hello\nReceived just now"
        curr = "Hello\nReceived 1 minute ago\nHere is your refund update"
        # The 'Received …' rewrite is noise, not new agent text.
        assert extract_diff(prev, curr) == "Here is your refund update"

    @pytest.mark.parametrize("stamp", [
        "Received just now",
        "Received a moment ago",
        "Received an hour ago",
        "Received 2 minutes ago",
        "Received moments ago",
    ])
    def test_received_variants_stripped(self, stamp):
        assert extract_diff("Hello", f"Hello\n{stamp}") == ""

    def test_blank_lines_ignored(self):
        prev = "A\n\nB"
        curr = "A\nB\n\n\nC\n"
        assert extract_diff(prev, curr) == "C"

    def test_reordered_noise_is_not_a_diff(self):
        prev = "A\nB\nC"
        curr = "C\nA\nB"
        assert extract_diff(prev, curr) == ""

    def test_repeated_line_surfaces_once_more(self):
        # Multiset semantics: a second "Thank you" IS new text.
        prev = "Thank you\nAnything else?"
        curr = "Thank you\nAnything else?\nThank you"
        assert extract_diff(prev, curr) == "Thank you"

    def test_whitespace_only_indentation_changes_ignored(self):
        prev = "  Hello  \nAgent joined"
        curr = "Hello\nAgent joined\nNew message"
        assert extract_diff(prev, curr) == "New message"


# ── is_bot_reply ─────────────────────────────────────────────────────────────

class TestIsBotReply:
    @pytest.mark.parametrize("text", [
        "I'm your DoorDash Virtual Assistant, how can I help?",
        "VIRTUAL ASSISTANT here to help",
        "Please select one of the options below",
        "Hi! I'm your DoorDash support bot.",
    ])
    def test_bot_text_hits(self, text):
        assert is_bot_reply(text, BOT_PATTERNS) is True

    @pytest.mark.parametrize("text", [
        "Hi, this is Raj from DoorDash support. Let me check that for you.",
        "I have processed the refund to your card.",
        "",
    ])
    def test_human_text_misses(self, text):
        assert is_bot_reply(text, BOT_PATTERNS) is False

    def test_empty_patterns_never_match(self):
        assert is_bot_reply("virtual assistant", []) is False


# ── detect_success ───────────────────────────────────────────────────────────

class TestDetectSuccess:
    @pytest.mark.parametrize("text", [
        "Your refund has been processed back to your card.",
        "I have issued a refund for both orders.",
        "We are processing your refund now.",
        "The amount was REFUNDED TO YOUR ORIGINAL payment method.",
        "A refund has been issued; allow 5-7 business days.",
    ])
    def test_success_text_hits(self, text):
        assert detect_success(text, SUCCESS_PHRASES) is True

    @pytest.mark.parametrize("text", [
        "I cannot help with that request.",
        "Unfortunately this order is not eligible for a refund.",
        "Let me transfer you to a specialist.",
        "",
    ])
    def test_non_success_text_misses(self, text):
        assert detect_success(text, SUCCESS_PHRASES) is False

    def test_empty_phrases_never_match(self):
        assert detect_success("refund has been processed", []) is False


class TestCreditGuard:
    """Success requires the ORIGINAL payment method — credits never count."""

    def test_credit_mention_vetoes_success(self):
        from backend.browser.chat import detect_success
        phrases = ["refund has been processed"]
        assert detect_success(
            "Good news, your refund has been processed!", phrases)
        assert not detect_success(
            "Your refund has been processed as DoorDash credits.", phrases)
        assert not detect_success(
            "I have added credits and your refund has been processed.",
            phrases)


class TestExtractDiffExclude:
    """Baseline captured before send: our own message must not be agent text."""

    def test_own_message_excluded_even_if_in_curr(self):
        from backend.browser.chat import extract_diff
        prev = "Hi Colleen\nWhat can we help with?"
        # Our outgoing message AND a fast agent reply both appear in curr.
        curr = ("Hi Colleen\nWhat can we help with?\nremove tip please\n"
                "Sure, one moment")
        out = extract_diff(prev, curr, exclude="remove tip please")
        assert out == "Sure, one moment"

    def test_no_exclude_keeps_prior_behavior(self):
        from backend.browser.chat import extract_diff
        assert extract_diff("a\nb", "a\nb\nc") == "c"
