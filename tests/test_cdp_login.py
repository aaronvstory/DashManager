"""CDP phone-login: pure guards + selector hygiene.

The live SeleniumBase CDP flow is exercised manually.
"""
from __future__ import annotations

import backend.browser.cdp_login as cl


class _FakeSB:
    def __init__(self, url: str):
        self._url = url

        class _Cdp:
            def get_current_url(self_inner):
                return url

        self.cdp = _Cdp()

    def get_current_url(self):
        return self._url


def test_is_logged_in_requires_doordash_domain():
    # A DoorDash home URL = logged in.
    assert cl._is_logged_in(_FakeSB("https://www.doordash.com/home")) is True
    # A Google OAuth URL that happens to contain a success substring must NOT
    # read as logged in (the live false-positive: it matched and bailed early).
    assert cl._is_logged_in(
        _FakeSB("https://accounts.google.com/o/oauth2/v2/auth?...consumer")
    ) is False
    # The login screen itself = not logged in.
    assert cl._is_logged_in(
        _FakeSB("https://www.doordash.com/consumer/login/")) is False


def test_continue_selectors_exclude_social_buttons():
    # EXACT DoorDash submit text only — never "Continue with Google/Apple/FB",
    # which a broad contains("Continue") wrongly matched (the live derail).
    joined = " ".join(cl.CONTINUE_SELECTORS)
    assert "Continue to Sign In" in joined
    assert "Google" not in joined
    assert "Apple" not in joined
    assert "Facebook" not in joined


def test_phone_input_selectors_present():
    assert any("tel" in s for s in cl.PHONE_INPUT_SELECTORS)


def test_hand_it_selectors_target_hand_it_to_me():
    assert any("hand it to me" in s.lower() for s in cl.HAND_IT_SELECTORS)
