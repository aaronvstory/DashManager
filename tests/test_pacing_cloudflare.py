"""Pure helpers for human pacing + Cloudflare gate classification."""
from __future__ import annotations

import random

from backend.browser.driver import classify_cloudflare
from backend.browser.pacing import (DEFAULT_MAX_S, DEFAULT_MIN_S,
                                    KEY_DELAY_MAX_MS, KEY_DELAY_MIN_MS,
                                    key_delay_ms, pause_seconds)


# ── Pacing ───────────────────────────────────────────────────────────────────

def test_pause_seconds_within_range():
    rng = random.Random(0)
    for _ in range(200):
        v = pause_seconds(0.8, 2.5, rng=rng)
        assert 0.8 <= v <= 2.5


def test_pause_seconds_defaults():
    v = pause_seconds(rng=random.Random(1))
    assert DEFAULT_MIN_S <= v <= DEFAULT_MAX_S


def test_pause_seconds_degenerate_inputs():
    # max < min -> floored to min; negative min -> floored to 0.
    assert pause_seconds(5.0, 1.0, rng=random.Random(2)) == 5.0
    v = pause_seconds(-3.0, -1.0, rng=random.Random(3))
    assert v == 0.0


def test_key_delay_within_range():
    rng = random.Random(0)
    for _ in range(200):
        v = key_delay_ms(rng=rng)
        assert KEY_DELAY_MIN_MS <= v <= KEY_DELAY_MAX_MS


def test_key_delay_is_humanlike_not_instant():
    # the whole point: never 0 (instant fill is the bot tell)
    assert key_delay_ms(rng=random.Random(7)) > 0


# ── Cloudflare classification ────────────────────────────────────────────────

def test_classify_none():
    assert classify_cloudflare("Your orders\n$12.34\nTotal") == ""
    assert classify_cloudflare("") == ""


def test_classify_variant_a():
    assert classify_cloudflare("Verifying you are human. Please wait.") == "a"
    assert classify_cloudflare("VERIFYING YOU ARE HUMAN") == "a"


def test_classify_variant_b():
    assert classify_cloudflare(
        "Performing security verification...\nRay ID: 8f2c") == "b"
    assert classify_cloudflare(
        "This helps protect against malicious bots.") == "b"
    assert classify_cloudflare(
        "doordash.com needs to review the security of your connection") == "b"


def test_variant_b_wins_over_a():
    # A variant-B page can also contain generic verifying copy — B must win so
    # the caller uses the wait-it-out path, not a reload.
    text = "Verifying you are human\nPerforming security verification\nRay ID"
    assert classify_cloudflare(text) == "b"
