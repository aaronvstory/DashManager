"""Tests for the pure helpers in uc_signup (no browser)."""
from backend.browser.uc_signup import (BOT_BLOCK_MARKERS, SEL_FIRST, SEL_PHONE,
                                       normalize_phone)


def test_normalize_phone_strips_to_last_10():
    assert normalize_phone("+1 (484) 210-5769") == "4842105769"
    assert normalize_phone("18507877395") == "8507877395"
    assert normalize_phone("850-787-7395") == "8507877395"


def test_normalize_phone_short_returns_what_it_has():
    assert normalize_phone("12345") == "12345"
    assert normalize_phone("") == ""


def test_selectors_use_autocomplete():
    # the name fields have no id/name/aria-label, only autocomplete
    assert SEL_FIRST == 'input[autocomplete="given-name"]'
    assert SEL_PHONE == 'input[autocomplete="tel"]'


def test_bot_block_markers_include_the_signal():
    # the live 403 body carries this exact statusCode
    assert "user_assessment_bot" in BOT_BLOCK_MARKERS
