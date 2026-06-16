"""Phone-OTP login path: phone-entry helper + orchestration plumbing.

The live Playwright flow is exercised manually; here we cover the pure bits and
the field-finding/entry logic with a fake Page.
"""
from __future__ import annotations

import backend.browser.login_flow as lf


def test_phone_constants_present():
    # The phone-login path needs a way TO the phone screen and a tel-field
    # selector cascade.
    assert "tel" in " ".join(lf.PHONE_INPUT_SELECTORS)
    assert lf.USE_PHONE_LINK  # the "use phone instead" link selector
    assert "Continue" in lf.CONTINUE_PHONE_BUTTONS


class _FakeLoc:
    def __init__(self, *, visible: bool):
        self._visible = visible
        self.clicked = False
        self.filled: str | None = None

    @property
    def first(self):
        return self

    async def is_visible(self):
        return self._visible

    async def click(self):
        self.clicked = True

    async def fill(self, v):
        self.filled = v


class _FakePage:
    def __init__(self, visible_selectors: set[str]):
        self._visible = visible_selectors
        self.locs: dict[str, _FakeLoc] = {}

    def locator(self, sel):
        loc = _FakeLoc(visible=sel in self._visible)
        self.locs[sel] = loc
        return loc


async def test_enter_phone_fills_first_visible_tel_field():
    # The first tel selector that is visible gets the 10-digit number.
    page = _FakePage({"input[type='tel']"})
    ok = await lf._enter_phone(page, "6803462490")
    assert ok is True
    assert page.locs["input[type='tel']"].filled == "6803462490"


async def test_enter_phone_returns_false_when_no_field():
    page = _FakePage(set())  # nothing visible
    assert await lf._enter_phone(page, "6803462490") is False


def test_normalize_phone_used_for_entry():
    # phone_login_and_capture normalizes to 10 digits before entry.
    from backend.browser.signup import normalize_phone
    assert normalize_phone("+1 (680) 346-2490") == "6803462490"
    assert normalize_phone("16803462490") == "6803462490"
