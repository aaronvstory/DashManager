"""DaisyBridge._read_line protocol robustness — no subprocess needed.

The worker speaks newline-delimited JSON on real stdout (CustomerDaisy's own
prints are routed to stderr). _read_line must turn a stray NON-JSON line into a
clean DaisyError and drop the worker handle (so the next _call restarts it),
rather than leaking a bare JSONDecodeError that _call's narrow except misses.
"""
import pytest

from backend.daisy.bridge import DaisyBridge, DaisyError


class _FakeStdout:
    def __init__(self, line: bytes):
        self._line = line

    async def readline(self) -> bytes:
        return self._line


class _FakeProc:
    def __init__(self, line: bytes):
        self.stdout = _FakeStdout(line)


def _bridge_with_line(line: bytes) -> DaisyBridge:
    b = DaisyBridge(root="/fake", python="/fake/python")
    b._proc = _FakeProc(line)  # type: ignore[assignment]
    return b


async def test_read_line_parses_json():
    b = _bridge_with_line(b'{"ok": true, "result": {"pong": true}}\n')
    assert await b._read_line() == {"ok": True, "result": {"pong": True}}


async def test_eof_is_clean_dead_worker_error():
    b = _bridge_with_line(b"")            # readline() == b"" -> EOF
    with pytest.raises(DaisyError, match="exited unexpectedly"):
        await b._read_line()
    assert b._proc is None                # handle dropped so next call restarts


async def test_non_json_line_becomes_daisy_error_and_drops_handle():
    # a stray library warning on real stdout — NOT valid JSON
    b = _bridge_with_line(b"UserWarning: something noisy happened\n")
    with pytest.raises(DaisyError, match="non-JSON line"):
        await b._read_line()
    assert b._proc is None                # desynced handle dropped, not left alive


async def test_invalid_utf8_line_becomes_daisy_error():
    b = _bridge_with_line(b"\xff\xfe not utf-8 \x80\n")
    with pytest.raises(DaisyError, match="non-JSON line"):
        await b._read_line()
    assert b._proc is None
