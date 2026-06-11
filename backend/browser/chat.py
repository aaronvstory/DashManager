"""Support-chat driver: navigation, AGENT escalation, and the strategy loop.

Owns ALL Playwright interaction for chats; strategies (chat_strategy.py) only
ever see the transcript. Chat machinery (input cascade, contenteditable send
sequence, Received-counting wait loop, Got-it popup handling, silent-block
detection, End-chat confirm) is ported from the proven ddtr app
(_open_chat / _send_messages / _wait_response / _count_received); navigation
is adapted to the 2026-06 help-page flow.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from typing import TYPE_CHECKING, Any, Callable

from playwright.async_api import Locator, Page

if TYPE_CHECKING:  # runtime import is lazy — see run_chat
    from backend.browser.chat_strategy import ChatContext, ChatStrategy

from backend.browser.selectors import (
    CHAT_SELS,
    CONTACT_SUPPORT_TEXT,
    END_BUTTON_SELECTORS,
    END_CHAT_CONFIRM,
    GOT_IT_TEXT,
    HELP_ORDERS_URL,
    RECEIVED_RE,
    REPLY_WAIT_S,
    SILENT_BLOCK_MIN_CHARS,
)
from backend.models import ChatOutcome

Emit = Callable[[str, dict[str, Any]], None]

_RECEIVED_RE = re.compile(RECEIVED_RE, re.IGNORECASE)

# navigate_to_chat results map 1:1 onto ChatOutcome values.
_NAV_TO_OUTCOME = {
    "blocked": ChatOutcome.blocked.value,
    "review_blocked": ChatOutcome.review_blocked.value,
    "failed": ChatOutcome.failed.value,
}

NAV_POLL_S = 25.0  # post Contact-support click: how long to wait for an input


def _notify(emit: Emit | None, type: str, data: dict[str, Any]) -> None:
    if emit is not None:
        emit(type, data)


# ── Pure helpers (no Playwright — unit-tested) ──────────────────────────────

def is_bot_reply(text: str, bot_patterns: list[str]) -> bool:
    """Case-insensitive substring match against known bot phrases."""
    lo = text.lower()
    return any(p.lower() in lo for p in bot_patterns)


# Success means a refund to the ORIGINAL payment method. Any mention of
# credits in the same reply disqualifies the driver-level short-circuit —
# "I've processed a refund as credits" must NOT end the chat as success;
# the strategy (or a human) keeps pushing instead.
CREDIT_GUARD_TERMS = ("credit",)


def detect_success(text: str, phrases: list[str],
                   guard_terms: tuple[str, ...] = CREDIT_GUARD_TERMS) -> bool:
    """Case-insensitive phrase match, vetoed by credit-guard terms."""
    lo = text.lower()
    if any(g in lo for g in guard_terms):
        return False
    return any(p.lower() in lo for p in phrases)


def _clean_lines(text: str) -> list[str]:
    """Strip blanks and 'Received just now/…' timestamp lines."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not _RECEIVED_RE.search(line):
            out.append(line)
    return out


def extract_diff(prev_text: str, curr_text: str, exclude: str = "") -> str:
    """New agent text: lines in curr but not prev, as a line MULTISET diff.

    Multiset (not set) so a repeated line ("Thank you" sent twice) still
    surfaces; curr order is preserved. Timestamp/blank noise is stripped first
    so a 'Received just now' -> 'Received 1 minute ago' rewrite is not a diff.

    `exclude` is our own just-sent message: its lines are seeded into the
    baseline budget so our outgoing bubble never shows up as agent text. This
    lets the caller snapshot `prev_text` BEFORE sending (closing the timing
    window where a sub-second agent reply would otherwise land in the baseline
    and be missed).
    """
    budget = Counter(_clean_lines(prev_text))
    for line in _clean_lines(exclude):
        budget[line] += 1
    new: list[str] = []
    for line in _clean_lines(curr_text):
        if budget[line] > 0:
            budget[line] -= 1
        else:
            new.append(line)
    return "\n".join(new)


# ── IO machinery (harvest-ported) ───────────────────────────────────────────

async def _body_text(page: Page) -> str:
    try:
        return await page.evaluate("() => document.body.innerText") or ""
    except Exception:
        return ""


async def find_chat_input(page: Page, wait_s: float = 0.0) -> Locator | None:
    """First visible match in the CHAT_SELS cascade, else None.

    With wait_s > 0 the cascade is re-polled until the deadline — the sendbird
    widget re-renders its input, so a single zero-wait pass can miss it
    (legacy waited ~5s per send).
    """
    deadline = time.monotonic() + wait_s
    while True:
        for sel in CHAT_SELS:
            loc = page.locator(sel).first
            try:
                if await loc.is_visible():
                    return loc
            except Exception:
                continue
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.5)


async def send_message(page: Page, text: str) -> bool:
    el = await find_chat_input(page, wait_s=5.0)
    if el is None:
        return False
    try:
        ce = await el.get_attribute("contenteditable")
        if ce is not None and ce.lower() != "false":
            # Sendbird contenteditable: .fill() does NOT work — must
            # click -> clear -> keyboard.type -> Enter (harvest-proven).
            await el.click()
            await page.keyboard.press("ControlOrMeta+A")
            await page.keyboard.press("Delete")
            await page.keyboard.type(text)
            await page.keyboard.press("Enter")
        else:
            await el.click()
            await el.fill(text)
            await page.keyboard.press("Enter")
        return True
    except Exception:
        return False


async def count_received(page: Page) -> int:
    """Agent replies are counted via 'Received …' labels in body text."""
    text = await _body_text(page)
    return len(_RECEIVED_RE.findall(text))


async def wait_for_reply(page: Page, before_count: int,
                         max_wait: float = REPLY_WAIT_S) -> bool:
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        if await count_received(page) > before_count:
            await asyncio.sleep(0.5)  # small settle after the reply lands
            return True
        await asyncio.sleep(1.0)
    return False


async def end_chat(page: Page) -> None:
    """Click End, then the 'End Chat' confirm; swallow all failures."""
    clicked = False
    for sel in END_BUTTON_SELECTORS:
        try:
            await page.locator(sel).first.click(timeout=3_000)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        return
    await asyncio.sleep(0.6)
    try:
        await page.locator(END_CHAT_CONFIRM).first.click(timeout=2_000)
        await asyncio.sleep(0.4)
    except Exception:
        pass


# ── Navigation (2026-06 user-verified flow) ─────────────────────────────────

async def _click_order_link(page: Page, order_uuid: str) -> bool:
    link = page.locator(f'a[href*="{order_uuid}"]').first
    try:
        await link.click(timeout=10_000)
        return True
    except Exception:
        pass
    try:
        await page.reload(wait_until="domcontentloaded")
        await asyncio.sleep(2.0)
        await link.click(timeout=10_000)
        return True
    except Exception:
        pass
    try:  # fallback: any element whose href contains the uuid
        await page.locator(f'[href*="{order_uuid}"]').first.click(timeout=5_000)
        return True
    except Exception:
        return False


async def _click_contact_support(page: Page) -> str | None:
    """Click Contact support; returns the body-text snapshot taken just
    before the successful click (silent-block baseline), or None."""
    for attempt in range(2):
        snapshot = await _body_text(page)
        try:
            await page.locator(CONTACT_SUPPORT_TEXT).first.click(timeout=8_000)
            return snapshot
        except Exception:
            if attempt == 0:
                try:
                    await page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
                await asyncio.sleep(2.0)
    return None


async def navigate_to_chat(page: Page, order_uuid: str,
                           emit: Emit | None = None) -> str:
    """Open the support-chat widget for one order.

    Returns 'ok' | 'blocked' (silent rate-limit) | 'review_blocked'
    (double 'Got it' popup) | 'failed'.
    """
    # Lazy import: driver.py is a sibling module built in parallel; this keeps
    # the pure helpers importable/testable without it.
    from backend.browser.driver import handle_cloudflare

    _notify(emit, "log", {"msg": f"chat: opening help page for {order_uuid}"})
    await page.goto(HELP_ORDERS_URL, wait_until="domcontentloaded")
    await handle_cloudflare(page)

    if not await _click_order_link(page, order_uuid):
        return "failed"
    await asyncio.sleep(1.5)  # lands on /help/orders/<uuid>?deliveryUUID=…

    text_before = await _click_contact_support(page)
    if text_before is None:
        return "failed"

    await asyncio.sleep(2.0)  # widget settle (harvest)
    got_it_clicks = 0
    deadline = time.monotonic() + NAV_POLL_S
    while time.monotonic() < deadline:
        if await find_chat_input(page) is not None:
            # The runner emits the canonical chat_opened (with chat_id);
            # this is only a diagnostic breadcrumb.
            _notify(emit, "log",
                    {"msg": f"chat: input found for {order_uuid}"})
            return "ok"
        try:
            got = page.locator(GOT_IT_TEXT).first
            if await got.is_visible():
                got_it_clicks += 1
                if got_it_clicks >= 2:
                    # Second popup = DoorDash refund-review block (harvest).
                    await got.click()
                    return "review_blocked"
                await got.click()
                await asyncio.sleep(2.0)
                try:
                    await page.locator(CONTACT_SUPPORT_TEXT).first.click(
                        timeout=5_000)
                except Exception:
                    pass
                await asyncio.sleep(1.5)
        except Exception:
            pass
        await asyncio.sleep(0.8)

    text_after = await _body_text(page)
    if (text_after.strip() == text_before.strip()
            or len(text_after) < SILENT_BLOCK_MIN_CHARS):
        _notify(emit, "log",
                {"msg": f"chat: silent block (rate-limit?) for {order_uuid}"})
        return "blocked"
    return "failed"


# ── Driver loop ──────────────────────────────────────────────────────────────

async def run_chat(page: Page, strategy: ChatStrategy, ctx: ChatContext, *,
                   chat_cfg: dict[str, Any], emit: Emit | None = None,
                   record: Callable[..., Any] | None = None
                   ) -> tuple[str, bool]:
    """Run one full support chat. Returns (ChatOutcome value, agent_reached).

    ``record`` is an ASYNC callable(direction, content); ``emit`` is sync;
    both may be None.
    """
    # Lazy: chat_strategy pulls in backend.llm (sibling modules built in
    # parallel); pure helpers here must stay importable without them.
    from backend.browser.chat_strategy import ChatTurn

    order_uuid = ctx.orders[0].order_uuid
    nav = await navigate_to_chat(page, order_uuid, emit)
    if nav != "ok":
        return _NAV_TO_OUTCOME.get(nav, ChatOutcome.failed.value), False

    agent_reached = False
    transcript: list[ChatTurn] = []

    async def _rec(direction: str, content: str) -> None:
        transcript.append(ChatTurn(direction=direction, content=content))
        # The runner's record() both persists AND emits chat_message with the
        # chat_id; emitting here too would double every message in the UI.
        if record is not None:
            await record(direction, content)

    async def _exchange(text: str, max_wait: float = REPLY_WAIT_S
                        ) -> str | None:
        """Send `text`, wait for the next reply; record both sides.

        Returns the new agent text; '' when the send worked but nothing (new)
        arrived within max_wait — silence is NOT fatal (legacy proceeded to
        its next message); None ONLY when the send itself failed.
        """
        # Flush window: let any in-flight reply to the PREVIOUS message land
        # before the baseline, so wait_for_reply only fires on the reply to
        # THIS message (harvest-proven race fix).
        await asyncio.sleep(1.2)
        # Baseline captured BEFORE sending. Our own outgoing bubble is then
        # excluded from the diff explicitly (we know its exact text), so a
        # very fast agent reply can never slip into the baseline and get
        # missed — no reliance on a post-send sleep window.
        prev_text = await _body_text(page)
        before = await count_received(page)
        if not await send_message(page, text):
            return None
        await _rec("out", text)
        if not await wait_for_reply(page, before, max_wait):
            return ""
        reply = extract_diff(prev_text, await _body_text(page), exclude=text)
        if reply:
            await _rec("in", reply)
        return reply

    try:
        # ── Escalation phase: punch through the bot to a human agent ──
        reply = await _exchange(ctx.opening_message)
        if reply is None:
            return ChatOutcome.failed.value, False

        # Humans take far longer to connect than the bot takes to answer —
        # after sending AGENT, wait on a human-scale clock.
        human_wait = float(chat_cfg.get("human_wait_seconds", 90))
        escalations = 0
        # Silence counts as "still the bot" (legacy proceeded on timeout);
        # the escalation cap bounds the loop either way.
        while not reply or is_bot_reply(reply, chat_cfg["bot_patterns"]):
            escalations += 1
            if escalations > chat_cfg["max_escalations"]:
                await end_chat(page)
                return ChatOutcome.blocked.value, False
            _notify(emit, "chat_escalation", {"attempt": escalations})
            reply = await _exchange(chat_cfg["agent_word"],
                                    max_wait=human_wait)
            if reply is None:
                return ChatOutcome.failed.value, False

        agent_reached = True
        # Re-send the request so the human sees it directly (user-specified);
        # a missing reply here is fine — the strategy loop keeps waiting.
        reply = await _exchange(ctx.opening_message)
        if reply and detect_success(reply, chat_cfg["success_phrases"]):
            await end_chat(page)
            return ChatOutcome.success.value, True

        # ── Strategy phase ──
        strategy.start(ctx)
        started = time.monotonic()
        turns = 0
        while (turns < chat_cfg["max_turns"]
               and time.monotonic() - started < chat_cfg["max_chat_seconds"]):
            action = await strategy.next_action(transcript)
            if action.kind == "send":
                reply = await _exchange(action.message or "")
                if reply is None:  # chat input is gone — the widget died
                    await end_chat(page)
                    return ChatOutcome.manual_flag.value, agent_reached
                # Driver-level short-circuit on every new agent text.
                if reply and detect_success(reply, chat_cfg["success_phrases"]):
                    await end_chat(page)
                    return ChatOutcome.success.value, True
            elif action.kind == "end_success":
                await end_chat(page)
                return ChatOutcome.success.value, True
            elif action.kind == "end_failure":
                await end_chat(page)
                return ChatOutcome.failed.value, agent_reached
            else:  # flag_manual
                await end_chat(page)
                return ChatOutcome.manual_flag.value, agent_reached
            turns += 1

        await end_chat(page)
        return ChatOutcome.manual_flag.value, agent_reached

    except Exception:
        try:
            from backend.browser.driver import screenshot
            await screenshot(page, f"chat_error_{order_uuid}")
        except Exception:
            pass
        try:
            await end_chat(page)
        except Exception:
            pass
        return ChatOutcome.manual_flag.value, agent_reached
