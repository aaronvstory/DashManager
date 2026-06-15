"""Tests for the hidden-desktop scaffold's importable parts. The actual
desktop create/spawn is exercised manually (it touches the Win32 API and is
Windows-only) — see backend/browser/hidden_desktop.py module docstring.
"""
import sys

import pytest

from backend.browser import hidden_desktop as hd


def test_random_desktop_name_is_valid():
    # Desktop names must not contain backslashes and should be unique-ish.
    n1 = hd.random_desktop_name()
    n2 = hd.random_desktop_name()
    assert "\\" not in n1
    assert n1.startswith("dm_")
    assert n1 != n2  # random suffix


def test_random_desktop_name_prefix():
    assert hd.random_desktop_name("sx").startswith("sx_")


def test_is_available_returns_bool():
    assert isinstance(hd.is_available(), bool)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_available_on_windows_with_pywin32():
    # On this dev box pywin32 is installed, so it should be available.
    # (If pywin32 is ever missing, is_available() returns False, not raises.)
    assert hd.is_available() is True
