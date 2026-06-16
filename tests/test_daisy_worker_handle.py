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
    with pytest.raises(ValueError, match="generate_address needs origin"):
        w.handle(NO_MGR, "generate_address", {})


def test_unknown_command_rejected():
    with pytest.raises(ValueError, match="unknown command: bogus"):
        w.handle(NO_MGR, "bogus", {})
