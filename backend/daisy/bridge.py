"""Async bridge to CustomerDaisy via a subprocess worker.

DashManager stays dependency-clean: it spawns `daisy_worker.py` under
CustomerDaisy's own python + root dir and exchanges newline-delimited JSON.
A single DaisyBridge instance keeps one worker process alive for the duration
of an account-creation flow (so api.cc sessions / caches persist across the
generate -> rent -> poll-otp -> save sequence).

Configure the CustomerDaisy location once in DashManager settings
('daisy' -> {root, python}); defaults point at the standard install.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any


def _default_daisy_root() -> str:
    """Best-effort default CustomerDaisy checkout, cross-platform.

    Windows dev box uses C:\\claude\\CustomerDaisy; on macOS/Linux fall back to a
    sibling 'CustomerDaisy' next to this repo, then ~/CustomerDaisy. The user can
    always override via DashManager settings ('daisy' -> root), so this is just a
    sensible default, never a hard requirement.
    """
    candidates = []
    if sys.platform == "win32":
        candidates.append(Path(r"C:\claude\CustomerDaisy"))
    # sibling of the DashManager repo root (…/<parent>/CustomerDaisy)
    repo_root = Path(__file__).resolve().parents[2]
    candidates.append(repo_root.parent / "CustomerDaisy")
    candidates.append(Path.home() / "CustomerDaisy")
    for c in candidates:
        if c.exists():
            return str(c)
    return str(candidates[0])  # first as the documented default


DEFAULT_DAISY_ROOT = _default_daisy_root()

# asyncio's StreamReader defaults to a 64 KiB line limit; one JSON response for a
# large list (list_customers over a big pool) exceeds it. 16 MiB leaves ample
# headroom for thousands of records.
_STREAM_LIMIT = 16 * 1024 * 1024


def _default_python(root: str) -> str:
    """The CustomerDaisy venv interpreter, cross-platform.

    Windows venvs put the interpreter in Scripts/python.exe; POSIX venvs use
    bin/python. Try both, then fall back to the current interpreter, then PATH.
    """
    win = Path(root) / ".venv" / "Scripts" / "python.exe"
    posix = Path(root) / ".venv" / "bin" / "python"
    if win.exists():
        return str(win)
    if posix.exists():
        return str(posix)
    # fall back to the interpreter running DashManager, else bare 'python'
    return sys.executable or "python"


class DaisyError(RuntimeError):
    """A CustomerDaisy worker command failed."""


class DaisyBridge:
    def __init__(self, root: str | None = None,
                 python: str | None = None) -> None:
        self.root = root or DEFAULT_DAISY_ROOT
        self.python = python or _default_python(self.root)
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()  # one in-flight command at a time

    async def start(self) -> None:
        if self._proc is not None:
            return
        worker = Path(__file__).with_name("daisy_worker.py")
        self._proc = await asyncio.create_subprocess_exec(
            self.python, str(worker),
            cwd=self.root,
            # OUR vars LAST so a stray parent DAISY_ROOT / PYTHONUNBUFFERED
            # can't point the worker at the wrong checkout or buffer stdout
            # (which would corrupt the JSON protocol).
            env={**_os_environ(), "DAISY_ROOT": self.root,
                 "PYTHONUNBUFFERED": "1"},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            # The protocol is one JSON object per LINE. A big response (e.g.
            # list_customers over a 200+ pool) is a single line that blows past
            # asyncio's default 64 KiB StreamReader limit — readline() then
            # raises "chunk is longer than limit" (the whole list endpoint 500s
            # while small calls like analytics still work). Give the reader
            # plenty of room so the pool can grow.
            limit=_STREAM_LIMIT,
        )
        ready = await self._read_line()
        if not ready.get("ok"):
            await self._kill_proc()  # reap — don't leak the failed worker
            raise DaisyError(ready.get("error", "worker failed to start"))

    async def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                # A crashed worker makes close() raise BrokenPipeError, which
                # would skip the wait/kill below — guard it so we still reap.
                try:
                    self._proc.stdin.close()
                except Exception:
                    pass
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                self._proc.kill()
                await self._proc.wait()  # reap to avoid a zombie
            except (ProcessLookupError, Exception):
                pass
        finally:
            self._proc = None

    async def __aenter__(self) -> "DaisyBridge":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _read_line(self) -> dict[str, Any]:
        assert self._proc and self._proc.stdout
        raw = await self._proc.stdout.readline()
        if not raw:
            # EOF = the worker died. Drop the handle so the next _call()
            # restarts it instead of writing to a closed stdin (BrokenPipe).
            self._proc = None
            raise DaisyError("CustomerDaisy worker exited unexpectedly")
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # The worker swaps stdout->stderr so CustomerDaisy's own prints don't
            # corrupt the protocol, but a stray non-JSON line on real stdout (a
            # C-ext warning, output before _bootstrap) would otherwise raise a
            # bare JSONDecodeError that _call's except doesn't catch — leaking a
            # confusing error AND leaving the now-desynced handle alive. Unlike
            # EOF (where the worker already exited), the worker is still ALIVE
            # here — it sent bytes — so KILL it (not just drop the ref), else it
            # orphans a live proc blocked on a now-half-closed pipe.
            snippet = raw.decode("utf-8", "replace").strip()[:120]
            await self._kill_proc()
            raise DaisyError(
                f"worker sent non-JSON line: {snippet!r}") from exc

    async def _call(self, cmd: str, args: dict | None = None,
                    timeout: float = 90) -> dict[str, Any]:
        async with self._lock:
            if self._proc is None:
                await self.start()
            assert self._proc and self._proc.stdin
            req = json.dumps({"cmd": cmd, "args": args or {}}) + "\n"
            try:
                self._proc.stdin.write(req.encode("utf-8"))
                await self._proc.stdin.drain()
                resp = await asyncio.wait_for(self._read_line(), timeout=timeout)
            except (BrokenPipeError, ConnectionResetError,
                    asyncio.TimeoutError) as exc:
                # Worker died/stalled around the write or hung on read — drop
                # the dead handle so the next call restarts it cleanly.
                await self._kill_proc()
                raise DaisyError(f"{cmd} failed: {exc}") from exc
        if not resp.get("ok"):
            raise DaisyError(resp.get("error", f"{cmd} failed"))
        return resp["result"]

    async def _kill_proc(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()  # reap so we don't leak a zombie
        except Exception:
            pass

    # ── Commands ─────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        return (await self._call("ping")).get("pong", False)

    async def balance(self) -> float:
        return float((await self._call("balance"))["balance"])

    async def locations(self) -> list[dict[str, Any]]:
        return (await self._call("locations"))["locations"]

    async def generate_identity(self, origin_address: str | None,
                                radius_miles: float = 5.0) -> dict[str, Any]:
        # MapQuest + Mail.tm calls can be slow; give it room.
        return (await self._call(
            "generate_identity",
            {"origin_address": origin_address, "radius_miles": radius_miles},
            timeout=120))["identity"]

    async def rent_number(self) -> dict[str, Any]:
        return (await self._call("rent_number", timeout=60))["number"]

    async def fetch_otp(self, token: str, api_url: str = "",
                        mirror_hosts: list[str] | None = None) -> dict[str, Any]:
        return await self._call(
            "fetch_otp",
            {"token": token, "api_url": api_url,
             "mirror_hosts": mirror_hosts or []},
            timeout=45)

    async def save_customer(self, customer: dict[str, Any]) -> str:
        return (await self._call(
            "save_customer", {"customer": customer}))["customer_id"]

    async def list_recent_customers(self, limit: int = 20
                                    ) -> list[dict[str, Any]]:
        return (await self._call(
            "list_recent_customers", {"limit": limit}))["customers"]

    # ── Slice 1: full CustomerDaisy surface ──────────────────────────────────
    async def list_customers(self, limit: int = 200
                             ) -> list[dict[str, Any]]:
        """All CustomerDaisy customers (newest first), normalized + token-bearing."""
        return (await self._call(
            "list_customers", {"limit": limit}))["customers"]

    async def customer_count(self) -> int:
        return int((await self._call("customer_count"))["count"])

    async def analytics(self, limit: int = -1) -> dict[str, Any]:
        """Coverage analytics over the pool: totals + by-state/by-city counts.
        ``limit=-1`` (default) covers the whole pool (SQLite no-limit)."""
        return await self._call("analytics", {"limit": limit})

    async def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        return (await self._call(
            "get_customer", {"customer_id": customer_id}))["customer"]

    async def update_customer(self, customer_id: str, fields: dict[str, Any]
                              ) -> dict[str, Any] | None:
        """Update whitelisted identity/address fields on a CustomerDaisy row."""
        return (await self._call(
            "update_customer",
            {"customer_id": customer_id, "fields": fields}))["customer"]

    async def delete_customer(self, customer_id: str) -> bool:
        return bool((await self._call(
            "delete_customer", {"customer_id": customer_id}))["deleted"])

    async def export(self, fmt: str = "json", limit: int = 1000
                     ) -> dict[str, Any]:
        """Export customers as csv|json|txt TEXT (caller decides where to save)."""
        return await self._call("export", {"format": fmt, "limit": limit})

    async def list_addresses(self) -> list[dict[str, Any]]:
        """The user's anchor-address pool (my_addresses.json)."""
        return (await self._call("list_addresses"))["addresses"]

    async def add_address(self, address: dict[str, Any]
                          ) -> list[dict[str, Any]]:
        """Append an address to the pool; returns the new full list."""
        return (await self._call(
            "add_address", {"address": address}))["addresses"]

    async def update_address(self, index: int, address: dict[str, Any]
                             ) -> list[dict[str, Any]]:
        """Replace the address at ``index`` (0-based); returns the new list."""
        return (await self._call(
            "update_address",
            {"index": index, "address": address}))["addresses"]

    async def delete_address(self, index: int) -> list[dict[str, Any]]:
        """Remove the address at ``index`` (0-based); returns the new list."""
        return (await self._call(
            "delete_address", {"index": index}))["addresses"]

    async def generate_address(self, origin_address: str,
                               radius_miles: float = 5.0
                               ) -> dict[str, Any] | None:
        """A radius-scoped real address near an origin (no customer created)."""
        return (await self._call(
            "generate_address",
            {"origin_address": origin_address,
             "radius_miles": radius_miles}))["address"]


def _os_environ() -> dict[str, str]:
    import os
    return dict(os.environ)
