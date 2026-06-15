"""Tests for the browser-free parts of cdp_signup (selectors, markers, cookie
normalization, proxy resolution). The live CDP flow is exercised manually.
"""
from backend.browser import cdp_signup as c


def test_selectors_use_autocomplete():
    assert c.SEL_FIRST == 'input[autocomplete="given-name"]'
    assert c.SEL_PHONE == 'input[autocomplete="tel"]'
    assert c.SEL_PASSWORD == 'input[autocomplete="new-password"]'


def test_cf_markers_cover_turnstile_interstitials():
    # The Phase-0 smoke saw exactly these CF interstitial strings.
    assert "just a moment" in c.CF_MARKERS
    assert "verify you are human" in c.CF_MARKERS


def test_normalize_phone_reused_from_uc_signup():
    assert c.normalize_phone("+1 (484) 210-5769") == "4842105769"


def test_export_storage_normalizes_dict_cookies():
    class FakeSB:
        def __init__(self, cookies):
            self._c = cookies

        @property
        def cdp(self):
            outer = self

            class _Cdp:
                def get_all_cookies(self_inner):
                    return outer._c

            return _Cdp()

    sb = FakeSB([
        {"name": "a", "value": "1", "domain": ".doordash.com", "path": "/",
         "httpOnly": True, "secure": True, "sameSite": "lax", "expires": 999},
        {"name": "b", "value": "2", "domain": "x", "same_site": "weird"},
    ])
    ss = c._export_storage(sb)
    assert ss["origins"] == []
    a, b = ss["cookies"]
    assert a["name"] == "a" and a["httpOnly"] is True and a["secure"] is True
    assert a["sameSite"] == "Lax"        # 'lax' -> 'Lax'
    assert a["expires"] == 999
    assert b["sameSite"] == "Lax"        # unknown -> safe default
    assert b["path"] == "/"               # missing path -> '/'


def test_export_storage_handles_attr_style_cookies():
    class Cookie:
        def __init__(self):
            self.name = "sess"
            self.value = "v"
            self.domain = ".doordash.com"
            self.path = "/"
            self.http_only = False
            self.secure = True
            self.same_site = "None"

    class FakeSB:
        @property
        def cdp(self):
            class _Cdp:
                def get_all_cookies(self_inner):
                    return [Cookie()]

            return _Cdp()

    ss = c._export_storage(FakeSB())
    assert ss["cookies"][0]["name"] == "sess"
    assert ss["cookies"][0]["sameSite"] == "None"


def test_resolve_proxy_returns_none_or_user_pass_form():
    # Never raises; returns None or a 'user:pass@host:port' string. (On this
    # machine working-proxies.txt exists, so it returns the gateway string.)
    out = c.resolve_proxy()
    assert out is None or ("@" in out and ":" in out.split("@", 1)[0])
