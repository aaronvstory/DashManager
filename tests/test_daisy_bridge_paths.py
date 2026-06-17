"""Cross-platform path resolution for DaisyBridge (macOS/Linux compat).

Windows venvs use .venv/Scripts/python.exe; POSIX venvs use .venv/bin/python.
_default_python must find whichever exists and never return a non-existent
.exe path on macOS (which would make the worker un-spawnable).
"""
import sys

from backend.daisy import bridge


def _make_venv(root, layout: str):
    """Create a fake venv interpreter under root for the given layout."""
    if layout == "win":
        interp = root / ".venv" / "Scripts" / "python.exe"
    else:
        interp = root / ".venv" / "bin" / "python"
    interp.parent.mkdir(parents=True, exist_ok=True)
    interp.write_text("#!fake interpreter\n", encoding="utf-8")
    return interp


def test_default_python_finds_windows_layout(tmp_path):
    interp = _make_venv(tmp_path, "win")
    assert bridge._default_python(str(tmp_path)) == str(interp)


def test_default_python_finds_posix_layout(tmp_path):
    interp = _make_venv(tmp_path, "posix")
    assert bridge._default_python(str(tmp_path)) == str(interp)


def test_default_python_prefers_windows_when_both_exist(tmp_path):
    # If somehow both exist, Windows .exe wins (it's checked first) — harmless,
    # since a given machine only has one layout.
    win = _make_venv(tmp_path, "win")
    _make_venv(tmp_path, "posix")
    assert bridge._default_python(str(tmp_path)) == str(win)


def test_default_python_falls_back_to_current_interpreter(tmp_path):
    # No venv in the (empty) root -> must return a REAL interpreter, never a
    # bogus .../Scripts/python.exe that doesn't exist on macOS.
    out = bridge._default_python(str(tmp_path))
    assert out == (sys.executable or "python")
    # never silently returns a non-existent Windows path on a POSIX box
    if sys.platform != "win32":
        assert not out.endswith("python.exe")


def test_default_daisy_root_is_a_string():
    # Never raises; returns a path string (existence not guaranteed — it's a
    # default the user can override in settings).
    assert isinstance(bridge.DEFAULT_DAISY_ROOT, str)
    assert bridge.DEFAULT_DAISY_ROOT


async def test_start_raises_stream_limit_for_big_list_responses(monkeypatch):
    """The worker speaks one-JSON-line-per-response; a big list (list_customers
    over a 200+ pool) is a single line past asyncio's default 64 KiB readline
    limit. start() must spawn the subprocess with a raised `limit` or that whole
    endpoint 500s ('chunk is longer than limit'). Regression guard."""
    captured: dict = {}

    class _FakeStdout:
        async def readline(self) -> bytes:
            return b'{"ok": true}\n'  # the worker's ready line

    class _FakeProc:
        stdin = stdout = None
        def __init__(self) -> None:
            self.stdout = _FakeStdout()

    async def fake_exec(*args, **kwargs):
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(bridge.asyncio, "create_subprocess_exec", fake_exec)
    b = bridge.DaisyBridge(root="/fake", python="/fake/python")
    await b.start()
    # Must be well above 64 KiB (the default that broke the list endpoint).
    assert captured.get("limit", 0) >= 1_000_000
    assert captured["limit"] == bridge._STREAM_LIMIT
