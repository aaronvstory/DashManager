"""Tests for the browser-free parts of camoufox_signup (selectors, markers,
cookie sameSite normalization, sticky-proxy resolution). The live Camoufox
(Firefox stealth) flow is exercised manually — see SIGNUP_RESULTS.md.
"""
from backend.browser import camoufox_signup as c


def test_selectors_use_autocomplete():
    # Key on stable autocomplete attrs, never hashed styled-component classes.
    assert c.SEL_FIRST == 'input[autocomplete="given-name"]'
    assert c.SEL_LAST == 'input[autocomplete="family-name"]'
    assert c.SEL_EMAIL == 'input[autocomplete="email"]'
    assert c.SEL_PHONE == 'input[autocomplete="tel"]'
    assert c.SEL_PASSWORD == 'input[autocomplete="new-password"]'


def test_markers_reused_from_uc_signup():
    # Shared with the other drivers so a verdict is comparable across engines.
    assert "user_assessment_bot" in c.BOT_BLOCK_MARKERS
    assert c.SIGNUP_URL.startswith("https://")
    assert c.SUCCESS_URL_MARKERS  # non-empty
    assert c.VERIFY_MARKERS       # non-empty


def test_normalize_phone_reused_from_uc_signup():
    assert c.normalize_phone("+1 (484) 210-5769") == "4842105769"


def test_norm_same_site_variants():
    import enum

    class CSS(enum.Enum):
        LAX = "Lax"
        NONE = "None"

    # Playwright can hand back a CookieSameSite enum; .value must be read so a
    # sameSite=None cookie isn't silently collapsed to Lax.
    assert c._norm_same_site(CSS.NONE) == "None"
    assert c._norm_same_site(CSS.LAX) == "Lax"
    assert c._norm_same_site("strict") == "Strict"
    assert c._norm_same_site("") == "Lax"            # empty -> safe default
    assert c._norm_same_site("garbage") == "Lax"     # unknown -> safe default
    assert c._norm_same_site(None) == "Lax"


def test_resolve_sticky_proxy_shape(tmp_path, monkeypatch):
    # Returns a Playwright proxy DICT (server/username/password), NOT the inline
    # user:pass@host form — Camoufox/Playwright wants the dict shape. We point
    # the resolver at a temp file so the test is hermetic (no real creds).
    import backend.browser.camoufox_signup as mod
    from pathlib import Path

    fake_root = tmp_path
    (fake_root / "working-proxies.txt").write_text(
        "# a comment line\n"
        "http://gw.example.net:8080:user123:pass456\n"
        "http://second.example.net:8080:u2:p2\n",
        encoding="utf-8",
    )

    real_resolve = Path.resolve

    def fake_resolve(self):
        # camoufox_signup computes root = Path(__file__).resolve().parents[2]
        if str(self).endswith("camoufox_signup.py"):
            class _P:
                parents = {2: fake_root}
            return _P()
        return real_resolve(self)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    out = mod.resolve_sticky_proxy()
    assert out == {
        "server": "http://gw.example.net:8080",
        "username": "user123",
        "password": "pass456",
    }


def test_resolve_sticky_proxy_takes_first_line_only(tmp_path, monkeypatch):
    # STICKY: one dedicated IP per account (PerimeterX gotcha #E1) — must pick
    # the FIRST non-comment line, never rotate.
    import backend.browser.camoufox_signup as mod
    from pathlib import Path

    (tmp_path / "working-proxies.txt").write_text(
        "host1.net:1111:a:b\nhost2.net:2222:c:d\n", encoding="utf-8")

    real_resolve = Path.resolve

    def fake_resolve(self):
        if str(self).endswith("camoufox_signup.py"):
            class _P:
                parents = {2: tmp_path}
            return _P()
        return real_resolve(self)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    out = mod.resolve_sticky_proxy()
    assert out is not None
    assert "host1.net:1111" in out["server"]


def test_resolve_sticky_proxy_missing_file_returns_none(tmp_path, monkeypatch):
    import backend.browser.camoufox_signup as mod
    from pathlib import Path

    real_resolve = Path.resolve

    def fake_resolve(self):
        if str(self).endswith("camoufox_signup.py"):
            class _P:
                parents = {2: tmp_path}   # empty dir, no working-proxies.txt
            return _P()
        return real_resolve(self)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    assert mod.resolve_sticky_proxy() is None


def test_resolve_sticky_proxy_never_raises():
    # On any machine state it returns None or a well-formed dict, never throws.
    out = c.resolve_sticky_proxy()
    assert out is None or (
        out.get("server", "").startswith(("http://", "https://", "socks5://"))
        and "username" in out and "password" in out)
