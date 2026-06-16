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


def test_export_storage_handles_enum_same_site():
    # CDP cookies carry same_site as a CookieSameSite ENUM — str(enum) is
    # 'CookieSameSite.NONE', NOT 'None'. _norm_same_site must read .value so a
    # sameSite=None cookie isn't silently collapsed to Lax.
    import enum

    class CookieSameSite(enum.Enum):
        STRICT = "Strict"
        LAX = "Lax"
        NONE = "None"

    class Cookie:
        name = "x"
        value = "1"
        domain = ".doordash.com"
        path = "/"
        same_site = CookieSameSite.NONE

    class FakeSB:
        @property
        def cdp(self):
            class _Cdp:
                def get_all_cookies(self_inner):
                    return [Cookie()]

            return _Cdp()

    ss = c._export_storage(FakeSB())
    assert ss["cookies"][0]["sameSite"] == "None"   # NOT collapsed to Lax


def test_norm_same_site_variants():
    import enum

    class CSS(enum.Enum):
        LAX = "Lax"
        NONE = "None"

    assert c._norm_same_site(CSS.NONE) == "None"
    assert c._norm_same_site(CSS.LAX) == "Lax"
    assert c._norm_same_site("strict") == "Strict"
    assert c._norm_same_site("") == "Lax"            # empty -> safe default
    assert c._norm_same_site("garbage") == "Lax"     # unknown -> safe default


def test_resolve_proxy_returns_none_or_user_pass_form():
    # Never raises; returns None or a 'user:pass@host:port' string. (On this
    # machine working-proxies.txt exists, so it returns the gateway string.)
    out = c.resolve_proxy()
    assert out is None or ("@" in out and ":" in out.split("@", 1)[0])


def test_resolve_proxy_uses_proxy_pool_parser(tmp_path):
    # Goes through proxy_pool.load_proxies/format_sb_proxy (path arg), so it
    # handles a colon-in-password line the old hand-parser would have split
    # wrong. Returns the SB inline-auth form (no scheme prefix).
    f = tmp_path / "working-proxies.txt"
    f.write_text("http://host.example:8080:user-flags:pa:ss:word\n",
                 encoding="utf-8")
    out = c.resolve_proxy(str(f))
    assert out == "user-flags:pa:ss:word@host.example:8080"


def test_resolve_proxy_missing_file_is_none(tmp_path):
    assert c.resolve_proxy(str(tmp_path / "nope.txt")) is None


def test_focus_signup_window_forces_front_and_rect():
    # Before OS input, the window must be brought to front AND restored to the
    # known 1200x720 rect — else PyAutoGUI's element-center clicks miss the form
    # (the live bug: a shrunk/unfocused window typed into the address bar).
    calls = []

    class _Cdp:
        def bring_active_window_to_front(self):
            calls.append("front")

        def set_window_rect(self, x, y, w, h):
            calls.append(("rect", x, y, w, h))

        def get_window_rect(self):
            return {"x": c._WIN_X, "y": c._WIN_Y,
                    "width": c._WIN_W, "height": c._WIN_H}

    class FakeSB:
        cdp = _Cdp()

    events = []
    ok = c.focus_signup_window(
        FakeSB(), emit=lambda t, d: events.append((t, d)))

    assert ok is True
    assert "front" in calls
    assert ("rect", c._WIN_X, c._WIN_Y, c._WIN_W, c._WIN_H) in calls
    # It self-reports the resulting rect so a recurrence is visible in the log.
    assert events and events[0][0] == "signup_window_focus"
    assert events[0][1]["rect"]["width"] == 1200


def test_focus_signup_window_survives_cdp_failure():
    # Best-effort: if every CDP window call raises, it must NOT propagate (a
    # focus hiccup must never abort a signup mid-flight) and still emit.
    class _Cdp:
        def bring_active_window_to_front(self):
            raise RuntimeError("no window")

        def set_window_rect(self, *a):
            raise RuntimeError("no window")

        def get_window_rect(self):
            raise RuntimeError("no window")

    class FakeSB:
        cdp = _Cdp()

    events = []
    # Must not raise; ok may be False (pygetwindow may or may not find a window
    # on the test box), but the event is always emitted.
    c.focus_signup_window(FakeSB(), emit=lambda t, d: events.append((t, d)))
    assert events and events[0][0] == "signup_window_focus"
