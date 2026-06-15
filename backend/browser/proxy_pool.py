"""Residential-proxy pool + liveness checker for the signup browser.

Signup (and only signup) needs a US **residential** egress IP — DoorDash's
``user_assessment_bot`` gate flags datacenter ranges. The user has a
LightningProxies HTTP gateway; the working lines live in ``working-proxies.txt``
(repo root, gitignored — NEVER commit creds). This module:

  * parses those lines into a normalized proxy dict,
  * formats them as the ``user:pass@host:port`` string SeleniumBase's
    ``SB(proxy=...)`` wants (which sets Chromium's ``--proxy-server`` flag on
    THAT browser process only — the PC's normal IP is untouched), and
  * routes a request THROUGH a proxy to an IP-echo to confirm it's alive and
    returns a US residential exit IP ≠ the PC's real IP.

The parse/format halves are pure (unit-tested, browser-free). ``check_proxy``
does real network I/O and is the only part that needs the proxy to be live.

Line format in ``working-proxies.txt`` (colon-separated, from LightningProxies):

    http://HOST:PORT:USERNAME:PASSWORD

The HOST is a hostname (``resident.lightningproxies.net``) so it never contains
a colon; PORT is numeric; USERNAME carries the geo/rotation flags
(``...-country-us-filter-medium-speed-fast``). The password is whatever's left
after the 4th field — split with a bounded ``maxsplit`` so a ``:`` in the
password (rare, but possible) stays intact.

Creds NEVER leave this layer: ``check_proxy`` / the API route return only the
exit IP + geo + latency, never the username/password.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable

# Repo root (…/backend/browser/proxy_pool.py -> parents[2]).
DEFAULT_PROXY_FILE = Path(__file__).resolve().parents[2] / "working-proxies.txt"

# IP-echo endpoints hit THROUGH the proxy. lumtest returns geo too; ipify is the
# bare-IP fallback. Both are tiny + fast + automation-friendly.
IP_ECHO_GEO = "https://lumtest.com/myip.json"
IP_ECHO_BARE = "https://api.ipify.org?format=json"

# Network timeout for a single liveness probe (residential exits are slow-ish).
CHECK_TIMEOUT_S = 20.0


def parse_proxy_line(line: str) -> dict[str, str] | None:
    """Parse one ``working-proxies.txt`` line into a normalized proxy dict.

    Accepts both the colon-separated LightningProxies form
    (``http://host:port:user:pass`` or ``host:port:user:pass``) and an already
    URL-shaped ``http://user:pass@host:port``. Returns
    ``{scheme, host, port, username, password}`` or ``None`` for a blank/comment
    /unparseable line (so callers can skip junk without raising).
    """
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None

    scheme = "http"
    if "://" in raw:
        scheme, raw = raw.split("://", 1)
        scheme = scheme.lower() or "http"

    # URL-shaped: user:pass@host:port
    if "@" in raw:
        creds, _, hostport = raw.partition("@")
        username, _, password = creds.partition(":")
        host, _, port = hostport.partition(":")
        if not (host and port):
            return None
        return _norm(scheme, host, port, username, password)

    # Colon-separated: host:port:user:pass  (password may itself contain ':')
    parts = raw.split(":", 3)
    if len(parts) < 4:
        return None
    host, port, username, password = parts[0], parts[1], parts[2], parts[3]
    if not (host and port):
        return None
    return _norm(scheme, host, port, username, password)


def _norm(scheme: str, host: str, port: str, username: str,
          password: str) -> dict[str, str] | None:
    if not port.isdigit():
        return None
    return {
        "scheme": scheme,
        "host": host.strip(),
        "port": port.strip(),
        "username": username.strip(),
        "password": password.strip(),
    }


def format_sb_proxy(proxy: dict[str, str]) -> str:
    """Format a proxy dict as SeleniumBase's ``user:pass@host:port`` string.

    SeleniumBase's ``SB(proxy=...)`` takes the auth WITHOUT the scheme prefix
    for inline auth (it adds ``--proxy-server`` itself). When there's no auth,
    returns bare ``host:port``.
    """
    host = proxy["host"]
    port = proxy["port"]
    user = proxy.get("username") or ""
    pwd = proxy.get("password") or ""
    if user and pwd:
        return f"{user}:{pwd}@{host}:{port}"
    return f"{host}:{port}"


def format_requests_proxy(proxy: dict[str, str]) -> str:
    """Full proxy URL for the ``requests`` library: ``http://user:pass@host:port``."""
    scheme = proxy.get("scheme") or "http"
    return f"{scheme}://{format_sb_proxy(proxy)}"


def proxy_id(proxy: dict[str, str]) -> str:
    """Stable, NON-SECRET handle for a proxy (host:port + FULL username).

    Used as the per-proxy identifier in API responses / UI and as the lookup
    key in ``/api/proxies/test/{id}``. The password is deliberately excluded so
    it never reaches the client. The username (which carries the geo/rotation
    flags) is NOT a secret — and it's included IN FULL so two proxies that share
    a host:port but differ in their flags get distinct ids (a truncated id would
    collide and make per-proxy testing probe the wrong line).
    """
    # Separator is '~' (a URL-unreserved char) NOT '#': the id is used as a
    # path segment in /api/proxies/test/{id}, and a '#' would be parsed as a URL
    # fragment and truncate the path.
    return f"{proxy['host']}:{proxy['port']}~{proxy.get('username','')}"


def load_proxies(path: str | Path | None = None) -> list[dict[str, str]]:
    """Read + parse every proxy line in the file. Missing file → empty list.

    Does NOT dedup — the gateway file legitimately repeats one line N times
    (each re-request through the gateway can yield a different exit IP).
    """
    p = Path(path) if path else DEFAULT_PROXY_FILE
    if not p.exists():
        return []
    out: list[dict[str, str]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        parsed = parse_proxy_line(line)
        if parsed:
            out.append(parsed)
    return out


def dedup_proxies(proxies: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """Collapse identical (host,port,user,pass) entries, keeping first order.

    The gateway file repeats one line; for a "test each distinct credential"
    view we usually want one row, not ten. ``check_all`` uses this.
    """
    seen: set[tuple] = set()
    out: list[dict[str, str]] = []
    for px in proxies:
        key = (px["host"], px["port"], px.get("username"), px.get("password"))
        if key in seen:
            continue
        seen.add(key)
        out.append(px)
    return out


def _classify_geo(payload: dict[str, Any]) -> dict[str, str]:
    """Pull {exit_ip, country, city, region} out of an IP-echo JSON payload.

    Handles lumtest's live shape — ``{country, geo:{city, region, region_name,
    ...}}`` (note: lumtest returns NO top-level ``ip``, only ``ip_version``) —
    and the bare ipify shape ``{ip}``. Because the two echoes give complementary
    halves (ipify=ip, lumtest=geo), ``check_proxy`` MERGES both rather than
    treating either as complete. Unknown fields → "".
    """
    # str() guards against an IP-echo returning a non-string (int/null/nested)
    # — calling .strip() on that would raise AttributeError mid-probe.
    ip = str(payload.get("ip") or payload.get("query") or "").strip()
    country = (payload.get("country") or payload.get("country_code") or "")
    raw_geo = payload.get("geo")
    geo: dict[str, Any] = raw_geo if isinstance(raw_geo, dict) else {}
    city = (payload.get("city") or geo.get("city") or "")
    region = (geo.get("region_name") or geo.get("region")
              or payload.get("region") or "")
    if not country:
        country = geo.get("country") or geo.get("country_code") or ""
    return {"exit_ip": ip, "country": str(country), "city": str(city),
            "region": str(region)}


def _scrub_creds(text: str, proxy: dict[str, str]) -> str:
    """Redact a proxy's credentials from a string before it leaves the backend.

    ``requests`` exceptions can embed the full proxy URL (incl. ``user:pass``)
    in their ``str()`` — that string would otherwise reach the UI via
    ``result["error"]``. Replace every form the URL/creds can appear in:
    the full requests URL, the SB inline-auth form, and the bare password.
    """
    out = text
    for needle in (format_requests_proxy(proxy), format_sb_proxy(proxy)):
        if needle:
            out = out.replace(needle, "<proxy>")
    pwd = proxy.get("password") or ""
    if pwd:
        out = out.replace(pwd, "<redacted>")
    return out


def check_proxy(proxy: dict[str, str], *, timeout: float = CHECK_TIMEOUT_S,
                local_ip: str | None = None) -> dict[str, Any]:
    """Route a request THROUGH ``proxy`` to an IP-echo; report liveness + geo.

    Returns ``{id, alive, exit_ip, country, city, region, latency_ms, error,
    differs_from_local}`` and NEVER the credentials.

    The two echoes give COMPLEMENTARY halves (verified live 2026-06-15):
    lumtest returns country + geo.{city,region} but NO ``ip`` field; ipify
    returns the bare ``ip`` but no geo. So we MERGE: take geo from the geo echo
    and the IP from whichever echo provides one. ``alive`` requires a real exit
    IP (proof the route works). Any network error → recorded in ``error``; both
    echoes failing → ``alive=False`` (never raises — one dead line must not
    abort a "test all").

    ``local_ip``, when given, flags a proxy whose exit IP equals the PC's real
    IP (the proxy isn't routing) — the "only the browser, not the whole PC"
    proof.
    """
    import requests  # lazy: keeps module import browser/network-free for tests

    url = format_requests_proxy(proxy)
    proxies = {"http": url, "https": url}
    result: dict[str, Any] = {
        "id": proxy_id(proxy), "alive": False, "exit_ip": "",
        "country": "", "city": "", "region": "", "latency_ms": None,
        "error": "", "differs_from_local": None,
    }
    errors: list[str] = []
    best_latency: float | None = None
    for echo in (IP_ECHO_GEO, IP_ECHO_BARE):
        start = time.monotonic()
        try:
            resp = requests.get(echo, proxies=proxies, timeout=timeout)
            latency = (time.monotonic() - start) * 1000.0
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 — surfaced as error string
            errors.append(_scrub_creds(
                f"{type(exc).__name__}: {exc}".strip(), proxy))
            continue
        if best_latency is None:
            best_latency = latency
        geo = _classify_geo(payload if isinstance(payload, dict) else {})
        # Merge: only fill a field we don't already have (first echo wins per
        # field, but each echo contributes its half).
        for key in ("exit_ip", "country", "city", "region"):
            if not result[key] and geo.get(key):
                result[key] = geo[key]
        # Stop early once we have BOTH an IP and a country (full picture).
        if result["exit_ip"] and result["country"]:
            break

    if best_latency is not None:
        result["latency_ms"] = round(best_latency, 1)
    result["alive"] = bool(result["exit_ip"])
    if result["alive"]:
        if local_ip:
            result["differs_from_local"] = result["exit_ip"] != local_ip
    elif errors:
        result["error"] = errors[-1]
    return result


def local_ip(timeout: float = 8.0) -> str:
    """The PC's OWN public IP (no proxy) — to prove a proxy actually differs."""
    import requests
    try:
        resp = requests.get(IP_ECHO_BARE, timeout=timeout)
        resp.raise_for_status()
        return str((resp.json() or {}).get("ip") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def check_all(path: str | Path | None = None, *, dedup: bool = True,
              with_local: bool = True) -> dict[str, Any]:
    """Liveness-test every (distinct) proxy line. Returns a UI-ready summary.

    ``{local_ip, count, alive_count, proxies:[{id,alive,exit_ip,country,city,
    latency_ms,error,differs_from_local}, ...]}``. Creds never included.
    """
    proxies = load_proxies(path)
    if dedup:
        proxies = dedup_proxies(proxies)
    mine = local_ip() if with_local else None
    # Check proxies CONCURRENTLY — each probe blocks up to `timeout` seconds, so
    # a serial loop would be N×timeout wall-time and the HTTP client would give
    # up long before. One thread per proxy (capped) keeps "Test all" snappy.
    if proxies:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(len(proxies), 8)) as pool:
            rows = list(pool.map(lambda px: check_proxy(px, local_ip=mine),
                                 proxies))
    else:
        rows = []
    return {
        "local_ip": mine or "",
        "count": len(rows),
        "alive_count": sum(1 for r in rows if r["alive"]),
        "proxies": rows,
    }


def pick_live_proxy(path: str | Path | None = None) -> dict[str, str] | None:
    """Return the first proxy whose liveness check passes, or None.

    For the signup driver: ``px = pick_live_proxy(); SB(proxy=format_sb_proxy(px))``.
    Healing-friendly — callers can re-call to retry through the gateway for a
    fresh exit IP.
    """
    for px in load_proxies(path):
        if check_proxy(px)["alive"]:
            return px
    return None
