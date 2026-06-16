"""Unit tests for check_proxy's merge / error / differs logic — mocked network.

check_proxy does the genuinely tricky part: it MERGES two complementary IP-echoes
(lumtest = geo but no ip; ipify = ip but no geo) into one liveness result, scrubs
proxy creds out of any error string, and flags differs_from_local. That merge had
a live bug before (the two echoes were treated as complete individually). These
tests pin it by monkeypatching requests.get — no real network.
"""
import requests

from backend.browser import proxy_pool as pp

PROXY = {"scheme": "http", "host": "gw.example.com", "port": "8080",
         "username": "user-flags", "password": "secretpass123"}

# Live-shape echo payloads (creds are fake). lumtest = geo, ipify = bare ip.
LUMTEST = {"country": "US", "geo": {"city": "Reno", "region_name": "Nevada"}}
IPIFY = {"ip": "203.0.113.7"}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise requests.HTTPError(f"{self._status} error")

    def json(self):
        return self._payload


def _fake_get(mapping):
    """Build a requests.get stand-in dispatching by which echo URL is hit."""
    def get(url, proxies=None, timeout=None):
        for needle, val in mapping.items():
            if needle in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"unexpected url {url}")
    return get


def test_merges_geo_and_ip_from_two_echoes(monkeypatch):
    # lumtest gives country+city+region (no ip); ipify gives the ip. The result
    # must carry BOTH halves — the whole point of the merge.
    monkeypatch.setattr(requests, "get", _fake_get({
        pp.IP_ECHO_GEO: _Resp(LUMTEST),
        pp.IP_ECHO_BARE: _Resp(IPIFY),
    }))
    r = pp.check_proxy(PROXY)
    assert r["alive"] is True
    assert r["exit_ip"] == "203.0.113.7"      # from ipify
    assert r["country"] == "US"               # from lumtest
    assert r["city"] == "Reno"
    assert r["region"] == "Nevada"
    assert r["latency_ms"] is not None
    assert r["error"] == ""


def test_early_break_when_first_echo_has_ip_and_country(monkeypatch):
    # If the geo echo already returns BOTH ip and country, check_proxy stops
    # early and never calls the second echo. Record the URLs hit and assert on
    # them AFTER the call — an assert INSIDE get() would be swallowed by
    # check_proxy's `except Exception`, giving false assurance.
    seen: list[str] = []
    both = {"ip": "198.51.100.9", "country": "CA",
            "geo": {"city": "Toronto", "region_name": "Ontario"}}

    def get(url, proxies=None, timeout=None):
        seen.append(url)
        return _Resp(both)

    monkeypatch.setattr(requests, "get", get)
    r = pp.check_proxy(PROXY)
    assert len(seen) == 1                       # short-circuited after echo 1
    assert pp.IP_ECHO_BARE not in seen          # bare echo was NOT hit
    assert r["exit_ip"] == "198.51.100.9" and r["country"] == "CA"


def test_error_string_scrubs_creds(monkeypatch):
    # A requests exception can embed the full proxy URL (user:pass). The error
    # surfaced in the result must NOT contain the password.
    boom = requests.ConnectionError(
        f"failed to connect to http://user-flags:secretpass123@gw.example.com:8080")
    monkeypatch.setattr(requests, "get", _fake_get({
        pp.IP_ECHO_GEO: boom, pp.IP_ECHO_BARE: boom,
    }))
    r = pp.check_proxy(PROXY)
    assert r["alive"] is False
    assert "secretpass123" not in r["error"]   # password scrubbed
    assert r["error"]                          # but an error IS reported
    # prove the scrub REPLACEMENT actually ran (not just that the password
    # happened to be absent) — the redacted URL token must be present.
    assert "<proxy>" in r["error"]


def test_both_echoes_fail_is_not_alive_never_raises(monkeypatch):
    monkeypatch.setattr(requests, "get", _fake_get({
        pp.IP_ECHO_GEO: requests.Timeout("timeout"),
        pp.IP_ECHO_BARE: requests.Timeout("timeout"),
    }))
    r = pp.check_proxy(PROXY)               # must not raise
    assert r["alive"] is False
    assert r["exit_ip"] == ""
    assert r["latency_ms"] is None
    assert r["error"]                       # the failure IS reported, not dropped


def test_differs_from_local_true_when_exit_ip_differs(monkeypatch):
    monkeypatch.setattr(requests, "get", _fake_get({
        pp.IP_ECHO_GEO: _Resp(LUMTEST), pp.IP_ECHO_BARE: _Resp(IPIFY),
    }))
    r = pp.check_proxy(PROXY, local_ip="10.0.0.1")
    assert r["differs_from_local"] is True     # exit 203.0.113.7 != local


def test_differs_from_local_false_when_exit_ip_equals_local(monkeypatch):
    # exit IP == the PC's real IP => the proxy isn't routing (red flag).
    monkeypatch.setattr(requests, "get", _fake_get({
        pp.IP_ECHO_GEO: _Resp(LUMTEST), pp.IP_ECHO_BARE: _Resp(IPIFY),
    }))
    r = pp.check_proxy(PROXY, local_ip="203.0.113.7")
    assert r["differs_from_local"] is False
