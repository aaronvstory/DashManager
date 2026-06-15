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

from backend.browser import proxy_pool

logger = logging.getLogger(__name__)
router = APIRouter()


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
        raise HTTPException(status_code=500, detail="proxy test failed") \
            from exc


@router.post("/test/{proxy_id}")
async def test_one_proxy(proxy_id: str) -> dict[str, Any]:
    """Liveness-test the single proxy whose ``id`` matches. 404 if unknown."""
    proxies = proxy_pool.dedup_proxies(proxy_pool.load_proxies())
    target = next(
        (px for px in proxies if proxy_pool.proxy_id(px) == proxy_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="proxy not found")
    mine = await asyncio.to_thread(proxy_pool.local_ip)
    result = await asyncio.to_thread(
        proxy_pool.check_proxy, target, local_ip=mine)
    return {"local_ip": mine or "", **result}
