"""Human-like pacing — jittered settle delays between browser actions.

Cloudflare's bot detection reacts partly to too-fast/too-mechanical activity:
back-to-back rapid navigations and reloads can trip the harder variant-B gate
(observed live 2026-06-12). Sprinkling small *jittered* pauses between clicks
and navigations makes the automation read more like a person and also reduces
flaky "page didn't render in time" reads from too-tight waits.

The delay calculation is pure (``pause_seconds``) so it is unit-testable; the
async ``human_pause`` just sleeps for that long.
"""
from __future__ import annotations

import asyncio
import random

# Defaults tuned for "between UI actions" — short enough not to crawl, long
# enough to look unhurried. Page-load settles pass a larger range explicitly.
DEFAULT_MIN_S = 0.8
DEFAULT_MAX_S = 2.5


def pause_seconds(min_s: float = DEFAULT_MIN_S,
                  max_s: float = DEFAULT_MAX_S,
                  rng: random.Random | None = None) -> float:
    """A jittered delay in [min_s, max_s]. Pure — inject ``rng`` for tests.

    Guards the degenerate inputs so a misconfigured range can never sleep
    forever or negatively: min is floored at 0, and max is floored at min.
    """
    lo = max(0.0, float(min_s))
    hi = max(lo, float(max_s))
    r = rng or random
    return r.uniform(lo, hi)


async def human_pause(min_s: float = DEFAULT_MIN_S,
                      max_s: float = DEFAULT_MAX_S) -> None:
    """Sleep a jittered ``pause_seconds`` — call between clicks/navigations."""
    await asyncio.sleep(pause_seconds(min_s, max_s))


# Per-keystroke delay (ms) for typing form fields like a human. Playwright's
# ``locator.fill()`` sets a field's value INSTANTLY (0ms) — a dead giveaway to
# DoorDash's signup anti-bot ("Something went wrong, please refresh and retry").
# Typing each char with a jittered ~60-140ms delay reads like a real person.
KEY_DELAY_MIN_MS = 60.0
KEY_DELAY_MAX_MS = 140.0


def key_delay_ms(rng: random.Random | None = None) -> float:
    """A jittered per-keystroke delay in ms for human-like typing."""
    r = rng or random
    return r.uniform(KEY_DELAY_MIN_MS, KEY_DELAY_MAX_MS)
