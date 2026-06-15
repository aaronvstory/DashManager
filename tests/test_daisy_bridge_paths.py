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
