"""Hidden Win32 desktop isolation — run the signup browser on an INVISIBLE
desktop so PyAutoGUI/OS-input doesn't hijack the user's real mouse/keyboard,
letting them keep using the PC while a signup runs.

WHY: the signup gate is beaten by REAL OS-level input (PyAutoGUI gui_click/
gui_write — see memory doordash-signup-bot-detection). But PyAutoGUI drives the
ONE shared physical cursor, so the user can't touch the PC during a run (the
live batch once typed an OTP into the chat window on focus steal). A separate
Win32 desktop (CreateDesktop) hosts the browser invisibly; the user's default
desktop is untouched.

⚠️ OPEN QUESTION (needs a live cursor test): does PerimeterX still PASS when the
browser is on a hidden desktop? The win came from genuine hardware-level input
telemetry. Two input strategies on a hidden desktop:
  (A) Switch the WORKER THREAD's desktop to the hidden one (SetThreadDesktop)
      then call SendInput there — closest to genuine input, but SendInput goes
      to the desktop's input-focus, which is reliable only on the *input*
      desktop; on a non-input hidden desktop SendInput may not deliver.
  (B) PostMessage/SendMessage WM_* directly to the target HWND — works on a
      hidden desktop, but is SYNTHETIC window-message injection, which
      PerimeterX may flag (the very thing real OS input avoided).
So this is a SCAFFOLD: it creates the desktop + launches the browser there. The
input-delivery layer + the live PerimeterX validation are the remaining step —
do NOT assume it passes until tested live. Until then, the working path is the
foreground os_input run (user must not touch the PC during it).

This module is best-effort and Windows-only; importing it on non-Windows or
without pywin32 raises at call time, not import time.
"""
from __future__ import annotations

import random
import string
from typing import Any


def _require_win32() -> tuple[Any, Any, Any]:
    import sys
    if sys.platform != "win32":
        raise RuntimeError("hidden_desktop is Windows-only")
    try:
        import win32con
        import win32process
        import win32service
    except Exception as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(
            f"pywin32 required for hidden desktop: {exc}") from exc
    return win32service, win32process, win32con


def random_desktop_name(prefix: str = "dm") -> str:
    """A random, valid desktop name (no backslashes; case-insensitive)."""
    suffix = "".join(random.sample(string.ascii_lowercase, 10))
    return f"{prefix}_{suffix}"


class HiddenDesktop:
    """Create/destroy a hidden Win32 desktop and launch processes onto it.

    Usage:
        with HiddenDesktop() as hd:
            pid = hd.spawn(r'C:\\path\\chrome.exe --remote-debugging-port=...')
            # drive that browser via CDP (debugging port) — NOT global PyAutoGUI,
            # which targets the visible desktop. See module docstring.

    The desktop is created on the current window station and torn down on exit.
    """

    def __init__(self, name: str | None = None) -> None:
        self._svc, self._proc, self._con = _require_win32()
        self.name = name or random_desktop_name()
        self._hdesk: Any = None

    def __enter__(self) -> "HiddenDesktop":
        import pywintypes
        sa = pywintypes.SECURITY_ATTRIBUTES()
        sa.bInheritHandle = 1
        # GENERIC_ALL so spawned processes can create windows on it.
        self._hdesk = self._svc.CreateDesktop(
            self.name, 0, self._con.MAXIMUM_ALLOWED, sa)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._hdesk is not None:
            try:
                self._hdesk.CloseDesktop()
            except Exception:
                pass
            self._hdesk = None

    def spawn(self, command_line: str) -> int:
        """Launch a process whose windows appear ON this hidden desktop.

        Returns the new process PID. The process (and any windows it opens) is
        bound to this desktop via STARTUPINFO.lpDesktop, so nothing shows on the
        user's visible desktop.
        """
        si = self._proc.STARTUPINFO()
        si.lpDesktop = self.name
        # CREATE_NEW_CONSOLE keeps the child's console off the visible desktop.
        flags = self._con.CREATE_NEW_CONSOLE
        proc_info = self._proc.CreateProcess(
            None, command_line, None, None, True, flags, None, None, si)
        # proc_info = (hProcess, hThread, dwProcessId, dwThreadId). The process
        # and thread HANDLES must be closed or they leak per spawn() — we only
        # need the PID, so close both before returning.
        h_process, h_thread = proc_info[0], proc_info[1]
        try:
            return int(proc_info[2])
        finally:
            for h in (h_process, h_thread):
                try:
                    h.Close()
                except Exception:
                    pass


def is_available() -> bool:
    """True if a hidden desktop can be created on this machine (Win + pywin32)."""
    try:
        _require_win32()
        return True
    except Exception:
        return False
