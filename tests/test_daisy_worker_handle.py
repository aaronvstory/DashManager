"""handle() command dispatch + arg validation (no Managers / no CustomerDaisy).

The DB helpers are covered in test_daisy_worker_db.py; this covers the dispatch
layer: that a missing required arg yields a DESCRIPTIVE error (not a bare
KeyError), and that an unknown command is rejected. Commands that need a missing
arg raise before ever touching ``mgr``, so these pass mgr=None.
"""
from typing import Any, cast

import pytest

from backend.daisy import daisy_worker as w

# These commands raise before touching mgr; a typed None stand-in keeps the
# type-checker happy without constructing a real Managers (which needs CustomerDaisy).
NO_MGR = cast(Any, None)


def test_req_present_and_missing():
    assert w._req({"customer_id": "cd-1"}, "customer_id", "get_customer") == "cd-1"
    for bad in ({}, {"customer_id": None}, {"customer_id": ""}):
        with pytest.raises(ValueError, match="get_customer needs customer_id"):
            w._req(bad, "customer_id", "get_customer")


def test_ping_needs_no_mgr():
    assert w.handle(NO_MGR, "ping", {}) == {"pong": True}


@pytest.mark.parametrize("cmd", ["get_customer", "update_customer",
                                 "delete_customer"])
def test_customer_cmds_missing_id_descriptive(cmd):
    # bare args[...] would raise KeyError('customer_id') serialized as "'customer_id'";
    # we want a message naming the command + the arg.
    with pytest.raises(ValueError, match=f"{cmd} needs customer_id"):
        w.handle(NO_MGR, cmd, {})


def test_fetch_otp_missing_token_descriptive():
    with pytest.raises(ValueError, match="fetch_otp needs token"):
        w.handle(NO_MGR, "fetch_otp", {})


def test_save_customer_missing_customer_descriptive():
    # the only other required-arg command; validates before touching mgr.db.
    with pytest.raises(ValueError, match="save_customer needs customer"):
        w.handle(NO_MGR, "save_customer", {})


def test_generate_address_missing_origin_descriptive():
    with pytest.raises(ValueError,
                       match=r"generate_address needs origin_address"):
        w.handle(NO_MGR, "generate_address", {})


def test_unknown_command_rejected():
    with pytest.raises(ValueError, match="unknown command: bogus"):
        w.handle(NO_MGR, "bogus", {})


# ── DoH getaddrinfo fallback (no real network — DoH resolver injected) ────────
def test_doh_fallback_resolves_when_os_resolver_fails(monkeypatch):
    """When the OS resolver raises gaierror, the patched getaddrinfo resolves the
    host via the injected DoH resolver and retries with the numeric IP. A host
    the OS CAN resolve passes straight through (no DoH call)."""
    import socket

    real = socket.getaddrinfo
    calls = {"doh": 0}

    def fake_real(host, port, *a, **k):
        if host == "api.mail.tm":          # simulate the OS resolver failing
            raise socket.gaierror(11001, "getaddrinfo failed")
        if host == "49.12.20.211":         # numeric retry succeeds
            return [(2, 1, 6, "", ("49.12.20.211", port))]
        return [(2, 1, 6, "", ("1.2.3.4", port))]  # any OS-resolvable host

    def fake_doh(host):
        calls["doh"] += 1
        return "49.12.20.211" if host == "api.mail.tm" else None

    monkeypatch.setattr(socket, "getaddrinfo", fake_real)
    try:
        w._install_doh_fallback(doh_resolve=fake_doh)
        # Failing host -> DoH -> numeric retry succeeds:
        info = socket.getaddrinfo("api.mail.tm", 443)
        assert info and info[0][4][0] == "49.12.20.211"
        assert calls["doh"] == 1
        # OS-resolvable host -> passes through, DoH NOT called again:
        socket.getaddrinfo("ok.host", 80)
        assert calls["doh"] == 1
    finally:
        socket.getaddrinfo = real


def test_doh_fallback_reraises_when_doh_also_fails(monkeypatch):
    """If DoH can't resolve either, the original gaierror propagates (DoH being
    down never makes resolution worse)."""
    import socket

    real = socket.getaddrinfo

    def fake_real(host, port, *a, **k):
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(socket, "getaddrinfo", fake_real)
    try:
        w._install_doh_fallback(doh_resolve=lambda h: None)
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("nope.example", 443)
    finally:
        socket.getaddrinfo = real
