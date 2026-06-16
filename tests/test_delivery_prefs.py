"""Delivery-prefs module: instruction set + selectors (pure parts).

The live Playwright flow (open address popover, pick suggestion, hand-it-to-me,
fill instructions) is exercised manually.
"""
from __future__ import annotations

import random

import backend.browser.delivery_prefs as dp


def test_seven_dasher_instructions():
    # The user provided exactly 7 "hand it to me directly" messages.
    assert len(dp.DASHER_INSTRUCTIONS) == 7
    assert all(isinstance(s, str) and s.strip() for s in dp.DASHER_INSTRUCTIONS)


def test_every_instruction_says_hand_it_or_no_dropoff():
    # Every message must convey hand-to-me / do-not-drop-off intent.
    for s in dp.DASHER_INSTRUCTIONS:
        low = s.lower()
        assert ("hand" in low) or ("drop" in low)
        assert "message me in the app" in low


def test_pick_instruction_is_from_the_set():
    for seed in range(20):
        got = dp.pick_instruction(random.Random(seed))
        assert got in dp.DASHER_INSTRUCTIONS


def test_pick_instruction_varies_with_seed():
    # Different seeds should not all collapse to one message (randomization works).
    picks = {dp.pick_instruction(random.Random(s)) for s in range(20)}
    assert len(picks) > 1


def test_address_input_selectors_keyed_on_stable_anchors():
    # id / placeholder / role — never hashed styled-component classes.
    sels = " ".join(dp.ADDR_INPUT_SELECTORS)
    assert "#HomeAddressAutocomplete" in sels
    assert "delivery address" in sels
    # no hashed class fragments
    assert "sc-" not in sels


def test_hand_it_selectors_target_hand_it_to_me():
    assert any("hand it to me" in s.lower() for s in dp.HAND_IT_SELECTORS)
