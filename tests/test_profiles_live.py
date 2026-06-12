"""Tests for the live-state file in profiles_live (pure, no browser)."""
import json

import pytest

from backend import profiles_live


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch):
    """Redirect the state file into a temp dir so tests don't touch real data."""
    monkeypatch.setattr(profiles_live, "STATE_PATH", tmp_path / "open.json")
    yield


def test_empty_when_missing():
    assert profiles_live.read_open_ids() == []


def test_write_then_read_roundtrip():
    profiles_live.write_open_ids([3, 1, 2, 2])
    assert profiles_live.read_open_ids() == [1, 2, 3]  # deduped + sorted


def test_mark_open_unions():
    profiles_live.write_open_ids([1, 2])
    assert profiles_live.mark_open([2, 3]) == [1, 2, 3]
    assert profiles_live.read_open_ids() == [1, 2, 3]


def test_mark_closed_subtracts():
    profiles_live.write_open_ids([1, 2, 3])
    assert profiles_live.mark_closed([2]) == [1, 3]
    assert profiles_live.read_open_ids() == [1, 3]


def test_corrupt_state_reads_as_empty():
    profiles_live.STATE_PATH.write_text("{not json", encoding="utf-8")
    assert profiles_live.read_open_ids() == []


def test_coerces_string_ids():
    profiles_live.STATE_PATH.write_text(
        json.dumps({"open_ids": ["4", "5"]}), encoding="utf-8")
    assert profiles_live.read_open_ids() == [4, 5]
