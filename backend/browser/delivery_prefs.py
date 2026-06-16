"""Set a logged-in DoorDash account's delivery address + dasher preferences.

Post-login onboarding the account flow doesn't do: open the "Your Address"
control on /home, enter a specific address, pick the first autocomplete
suggestion, then on the address-detail step choose "Hand it to me" (not "Leave
at door") and fill the dasher delivery instructions.

Pure Playwright (login + onboarding have no bot gate, unlike signup), so this is
headless-capable and never fights the OS cursor. Best-effort + self-reporting:
each step is wrapped so a layout change degrades to a logged warning, not a
crash, and returns a dict of which steps landed.

Selectors key on stable text / role / placeholder, never hashed classes. The
"$0 delivery fee" home modal's address box is id=HomeAddressAutocomplete
(verified live 2026-06-17).
"""
from __future__ import annotations

import asyncio
import random
from typing import Any, Callable

from playwright.async_api import Page

# Randomized "hand it to me directly" dasher instructions (user-provided set).
DASHER_INSTRUCTIONS = (
    "HAND IT TO ME IN PERSON ONLY!! DO NOT DROP IT OFF, DO NOT LEAVE IT "
    "ANYWHERE, DO NOT HAND IT TO ANYONE ELSE, JUST ME, DIRECTLY, IN MY HAND. "
    "MESSAGE ME IN THE APP.",
    "DO NOT DROP OFF UNDER ANY CIRCUMSTANCE!! HAND IT DIRECTLY TO ME ONLY. "
    "MESSAGE ME IN THE APP, AND IF YOU CANNOT REACH ME, WAIT, DO NOT LEAVE IT.",
    "MESSAGE ME IN THE APP!! HAND IT TO ME IN PERSON, DO NOT LEAVE IT, DO NOT "
    "DROP IT OFF.",
    "MUST BE HANDED TO ME PERSONALLY!! IT GOES INTO MY HANDS AND ONLY MY "
    "HANDS. MESSAGE ME IN THE APP.",
    "DO NOT DROP IT OFF!!! I NEED IT HANDED TO ME DIRECTLY, IN PERSON, EVERY "
    "SINGLE TIME. MESSAGE ME IN THE APP.",
    "HANDED TO ME ONLY, IN PERSON, NO EXCEPTIONS!! DO NOT DROP OFF, DO NOT "
    "LEAVE UNATTENDED. MESSAGE ME IN THE APP AND I WILL RESPOND.",
    "I REPEAT: DO NOT DROP OFF!! IT HAS TO GO STRAIGHT INTO MY HANDS. MESSAGE "
    "ME IN THE APP, AND WAIT FOR ME IF YOU HAVE TO.",
)

HOME_URL = "https://www.doordash.com/home"

# The address-entry box (home modal OR the "Your Address" popover both use it).
ADDR_INPUT_SELECTORS = (
    "#HomeAddressAutocomplete",
    "input[placeholder*='delivery address' i]",
    "input[aria-controls*='AddressSearchAutocomplete' i]",
    "input[role='combobox']",
)
# The "Your Address" / "+ Your Address" control in the top bar (opens the popover
# when no home modal is showing).
YOUR_ADDRESS_BUTTON = (
    "button:has-text('Your Address')",
    "[aria-label*='address' i]",
    "button:has-text('Address')",
)
# First autocomplete suggestion row.
ADDR_SUGGESTION_SELECTORS = (
    "[id*='AddressAutocomplete'] li",
    "ul[role='listbox'] li",
    "[role='option']",
)
# "Hand it to me" vs "Leave at door" on the address-detail / dropoff step.
HAND_IT_SELECTORS = (
    "text=/hand it to me/i",
    "[aria-label*='hand it to me' i]",
    "label:has-text('Hand it to me')",
)
# The dasher delivery-instructions textarea.
INSTRUCTIONS_SELECTORS = (
    "textarea[placeholder*='instruction' i]",
    "textarea[aria-label*='instruction' i]",
    "textarea[name*='instruction' i]",
    "textarea",
)
SAVE_BUTTONS = ("Save", "Save Address", "Continue", "Done")


def pick_instruction(rng: random.Random | None = None) -> str:
    """Pick one dasher instruction at random (injectable RNG for tests)."""
    r = rng or random
    return r.choice(DASHER_INSTRUCTIONS)


async def _first_visible(page: Page, selectors: tuple[str, ...]):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible():
                return loc
        except Exception:
            continue
    return None


async def _click_text(page: Page, selectors: tuple[str, ...]) -> bool:
    loc = await _first_visible(page, selectors)
    if loc is None:
        return False
    try:
        await loc.click()
        return True
    except Exception:
        return False


async def _click_button(page: Page, names: tuple[str, ...],
                        timeout: float = 4000) -> bool:
    for name in names:
        try:
            await page.get_by_role("button", name=name, exact=False
                                   ).first.click(timeout=timeout)
            return True
        except Exception:
            continue
    return False


async def set_address_and_prefs(page: Page, full_address: str, *,
                                instruction: str | None = None,
                                emit: Callable[[str, dict], None] | None = None,
                                rng: random.Random | None = None
                                ) -> dict[str, Any]:
    """Set the delivery address, choose "Hand it to me", fill a dasher note.

    Returns {address_set, hand_it_to_me, instruction_set, instruction}. Each
    step is best-effort; the account is already usable, these refine it.
    """
    def _e(t: str, d: dict | None = None) -> None:
        if emit:
            try:
                emit(t, d or {})
            except Exception:
                pass

    note = instruction or pick_instruction(rng)
    out: dict[str, Any] = {"address_set": False, "hand_it_to_me": False,
                           "instruction_set": False, "instruction": note}

    # Make sure we're on /home (the address controls live there).
    try:
        if "doordash.com" not in page.url:
            await page.goto(HOME_URL, wait_until="domcontentloaded")
            await asyncio.sleep(2.0)
    except Exception:
        pass

    # 1. Find the address box. If it isn't already showing (home modal), open the
    #    "Your Address" popover.
    box = await _first_visible(page, ADDR_INPUT_SELECTORS)
    if box is None:
        await _click_text(page, YOUR_ADDRESS_BUTTON)
        await asyncio.sleep(1.5)
        box = await _first_visible(page, ADDR_INPUT_SELECTORS)

    # 2. Type the address + pick the first suggestion.
    if box is not None:
        try:
            await box.click()
            await box.fill("")
            await box.type(full_address, delay=20)
            await asyncio.sleep(2.0)  # let suggestions populate
            sug = await _first_visible(page, ADDR_SUGGESTION_SELECTORS)
            if sug is not None:
                await sug.click()
            else:
                await page.keyboard.press("Enter")
            await asyncio.sleep(2.0)
            out["address_set"] = True
            _e("delivery_address_set", {"address": full_address[:60]})
        except Exception as exc:
            _e("delivery_address_warn",
               {"error": f"{type(exc).__name__}: {exc}"[:120]})

    # 3. Choose "Hand it to me" on the dropoff/detail step (may need a beat to
    #    render after picking the address).
    await asyncio.sleep(1.0)
    if await _click_text(page, HAND_IT_SELECTORS):
        out["hand_it_to_me"] = True
        _e("delivery_hand_it_to_me", {})

    # 4. Fill the dasher instructions textarea.
    instr = await _first_visible(page, INSTRUCTIONS_SELECTORS)
    if instr is not None:
        try:
            await instr.click()
            await instr.fill(note)
            out["instruction_set"] = True
            _e("delivery_instruction_set", {"instruction": note[:60]})
        except Exception as exc:
            _e("delivery_instruction_warn",
               {"error": f"{type(exc).__name__}: {exc}"[:120]})

    # 5. Save.
    await _click_button(page, SAVE_BUTTONS)
    await asyncio.sleep(1.5)
    _e("delivery_prefs_done", out)
    return out
