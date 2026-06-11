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
from pathlib import Path
from typing import Any

DEFAULT_DAISY_ROOT = r"C:\claude\CustomerDaisy"


def _default_python(root: str) -> str:
    p = Path(root) / ".venv" / "Scripts" / "python.exe"
    return str(p) if p.exists() else "python"


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
            env={"DAISY_ROOT": self.root, "PYTHONUNBUFFERED": "1",
                 **_os_environ()},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        ready = await self._read_line()
        if not ready.get("ok"):
            raise DaisyError(ready.get("error", "worker failed to start"))

    async def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                self._proc.kill()
            except ProcessLookupError:
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
        return json.loads(raw.decode("utf-8"))

    async def _call(self, cmd: str, args: dict | None = None,
                    timeout: float = 90) -> dict[str, Any]:
        async with self._lock:
            if self._proc is None:
                await self.start()
            assert self._proc and self._proc.stdin
            req = json.dumps({"cmd": cmd, "args": args or {}}) + "\n"
            self._proc.stdin.write(req.encode("utf-8"))
            await self._proc.stdin.drain()
            resp = await asyncio.wait_for(self._read_line(), timeout=timeout)
        if not resp.get("ok"):
            raise DaisyError(resp.get("error", f"{cmd} failed"))
        return resp["result"]

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


def _os_environ() -> dict[str, str]:
    import os
    return dict(os.environ)
