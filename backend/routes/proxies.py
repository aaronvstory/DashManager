"""Residential-proxy liveness endpoints for the Proxy Manager page.

Lists the configured proxies (NON-secret handle only) and runs liveness checks
THROUGH each one against an IP-echo, reporting exit IP / geo / latency. The
proxy credentials live in ``working-proxies.txt`` (gitignored) and are NEVER
returned to the client — only the derived ``id`` (host:port#user-prefix) and the
exit IP it produces.

Liveness checks do blocking network I/O (the ``requests`` library through a
proxy), so they're off-loaded to a thread to keep the event loop responsive.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.browser import proxy_pool

logger = logging.getLogger(__name__)
router = APIRouter()


class AddProxyBody(BaseModel):
    # One or more proxy lines (any supported format). A single quick-add sends
    # one line; bulk-paste sends many (newline-joined or a list).
    text: str = ""
    lines: list[str] | None = None


@router.get("")
async def list_proxies() -> dict[str, Any]:
    """The configured proxies — id + scheme/host/port only, NO credentials.

    ``count`` is the distinct-credential count (the gateway file repeats one
    line). Returns ``configured: False`` when ``working-proxies.txt`` is absent
    so the UI can show a clear "no proxies file" state instead of an empty grid.
    """
    proxies = proxy_pool.dedup_proxies(proxy_pool.load_proxies())
    return {
        "configured": bool(proxies),
        "count": len(proxies),
        "proxies": [
            {
                "id": proxy_pool.proxy_id(px),
                "scheme": px.get("scheme", "http"),
                "host": px["host"],
                "port": px["port"],
                # username carries the geo/rotation flags — safe to show (it is
                # NOT a secret; the password is what's withheld). Truncated.
                "label": px.get("username", "")[:48],
            }
            for px in proxies
        ],
    }


@router.post("")
async def add_proxies(body: AddProxyBody) -> dict[str, Any]:
    """Add one or many proxies (any supported format). Parses each line, skips
    blanks/dupes, appends to the file. Returns counts + per-line parse errors so
    the UI can show which pasted lines were bad.

    Handles quick-add (one line in ``text``) and bulk paste (many lines in
    ``text`` newline-joined, or a ``lines`` list).
    """
    raw_lines: list[str] = []
    if body.lines:
        raw_lines.extend(body.lines)
    if body.text:
        raw_lines.extend(body.text.splitlines())

    parsed: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for line in raw_lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        px = proxy_pool.parse_proxy_line(s)
        if px is None:
            errors.append({"line": s, "error": "unparseable"})
        else:
            parsed.append(px)

    added = await asyncio.to_thread(proxy_pool.add_proxies, parsed)
    return {"added": added, "parsed": len(parsed), "errors": errors}


@router.delete("/{proxy_id}")
async def delete_proxy(proxy_id: str) -> dict[str, Any]:
    """Delete every line matching this proxy id (host:port~username). 404 if none."""
    removed = await asyncio.to_thread(proxy_pool.delete_proxy, proxy_id)
    if removed == 0:
        raise HTTPException(status_code=404, detail="proxy not found")
    return {"removed": removed}


@router.get("/{proxy_id}/line")
async def proxy_line(proxy_id: str) -> dict[str, Any]:
    """The full proxy line (incl. password) for ONE proxy — for a copy button.

    The list endpoint withholds the password by design; this returns it only on
    an explicit per-proxy request, so the user can copy their own credential to
    use elsewhere. Local single-user app; the password is the user's own.
    """
    proxies = proxy_pool.dedup_proxies(proxy_pool.load_proxies())
    target = next(
        (px for px in proxies if proxy_pool.proxy_id(px) == proxy_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="proxy not found")
    return {
        "scheme": target.get("scheme", "http"),
        "host": target["host"],
        "port": target["port"],
        "username": target.get("username", ""),
        "password": target.get("password", ""),
        "line": proxy_pool.serialize_proxy_line(target),
    }


@router.post("/test")
async def test_all_proxies() -> dict[str, Any]:
    """Liveness-test every distinct proxy. Returns exit IP/geo/latency per proxy.

    Off-loaded to a thread (blocking HTTP through each proxy). Creds withheld.
    """
    try:
        return await asyncio.to_thread(proxy_pool.check_all)
    except Exception as exc:  # noqa: BLE001 — a probe must never 500
        # Do NOT echo the raw exception: it could embed the proxy URL (creds).
        # Log it server-side; return a generic message to the client.
        logger.exception("proxy test-all failed")
        err = HTTPException(status_code=500, detail="proxy test failed")
        raise err from exc


@router.post("/test/{proxy_id}")
async def test_one_proxy(proxy_id: str) -> dict[str, Any]:
    """Liveness-test the single proxy whose ``id`` matches. 404 if unknown."""
    proxies = proxy_pool.dedup_proxies(proxy_pool.load_proxies())
    target = next(
        (px for px in proxies if proxy_pool.proxy_id(px) == proxy_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="proxy not found")
    try:
        mine = await asyncio.to_thread(proxy_pool.local_ip)
        result = await asyncio.to_thread(
            proxy_pool.check_proxy, target, local_ip=mine)
    except Exception as exc:  # noqa: BLE001 — a probe must never 500
        # Mirror test_all_proxies: an unexpected raise from local_ip/check_proxy
        # could embed the proxy URL (creds) in its message. Log server-side;
        # return a generic message so the client never sees the secret.
        logger.exception("proxy test-one failed")
        err = HTTPException(status_code=500, detail="proxy test failed")
        raise err from exc
    return {"local_ip": mine or "", **result}
