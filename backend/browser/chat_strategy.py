"""Pluggable chat strategies — browser-free state machines.

The driver in backend/browser/chat.py owns ALL Playwright interaction; a
strategy only ever sees the transcript and returns the next ChatAction. That
keeps ScriptedStrategy and LlmStrategy interchangeable and unit-testable
without a browser.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from backend import config as app_config
from backend.llm import prompts
from backend.llm.openrouter import LlmError, OpenRouterClient


@dataclass
class ProblemOrder:
    """One not-properly-refunded order included in a chat."""
    order_id: int
    order_uuid: str
    store_name: str
    price: float | None
    refund_status: str


@dataclass
class ChatContext:
    customer_name: str
    orders: list[ProblemOrder]
    opening_message: str
    config: dict[str, Any]  # merged "chat" settings (+ "llm" for LlmStrategy)


@dataclass
class ChatTurn:
    direction: Literal["out", "in"]
    content: str


@dataclass
class ChatAction:
    kind: Literal["send", "end_success", "end_failure", "flag_manual"]
    message: str | None = None  # required when kind == "send"
    reason: str | None = None


class ChatStrategy(ABC):
    name: str = "base"

    @abstractmethod
    def start(self, ctx: ChatContext) -> None:
        """Reset per-chat state. Called once before the first next_action."""

    @abstractmethod
    async def next_action(self, transcript: list[ChatTurn]) -> ChatAction:
        """Decide the next step given the full transcript so far."""


_REGISTRY: dict[str, type[ChatStrategy]] = {}


def register(cls: type[ChatStrategy]) -> type[ChatStrategy]:
    _REGISTRY[cls.name] = cls
    return cls


def get_strategy(name: str) -> ChatStrategy:
    if name not in _REGISTRY:
        raise ValueError(f"unknown chat strategy: {name!r} "
                         f"(available: {sorted(_REGISTRY)})")
    return _REGISTRY[name]()


# ── Shared helpers ───────────────────────────────────────────────────────────

MAX_MESSAGE_CHARS = 300


def _format_fields(ctx: ChatContext) -> dict[str, str]:
    """{order_count}/{amounts} values for template interpolation."""
    prices = [o.price for o in ctx.orders if o.price is not None]
    amounts = ", ".join(f"${p:.2f}" for p in prices)
    return {
        "order_count": str(len(ctx.orders)),
        "amounts": amounts or "the order amounts",
    }


def _safe_format(template: str, fields: dict[str, str]) -> str:
    """str.format that never raises: missing keys become '', and a malformed
    template (stray braces / positional fields) falls back to the raw text."""
    try:
        return template.format_map(defaultdict(str, fields))
    except (ValueError, IndexError, KeyError):
        return template


@register
class ScriptedStrategy(ChatStrategy):
    """Fixed follow-up sequence from settings, then phrase-based verdict.

    Sends ctx.config["scripted_followups"] one at a time — the driver waits
    for a support reply between sends; this strategy only sequences. When the
    list is exhausted: end_success if any ctx.config["success_phrases"]
    (case-insensitive substring) appears in an incoming turn, else
    flag_manual.
    """

    name = "scripted"

    def start(self, ctx: ChatContext) -> None:
        self._ctx = ctx
        fields = _format_fields(ctx)
        self._followups: list[str] = [
            _safe_format(t, fields)
            for t in ctx.config.get("scripted_followups", [])
        ]
        self._next_idx = 0

    async def next_action(self, transcript: list[ChatTurn]) -> ChatAction:
        if self._next_idx < len(self._followups):
            message = self._followups[self._next_idx]
            self._next_idx += 1
            return ChatAction(kind="send", message=message)

        phrases = [str(p).lower()
                   for p in self._ctx.config.get("success_phrases", [])]
        incoming = "\n".join(t.content.lower() for t in transcript
                             if t.direction == "in")
        if any(p in incoming for p in phrases):
            return ChatAction(kind="end_success",
                              reason="success phrase detected in support reply")
        return ChatAction(
            kind="flag_manual",
            reason="scripted follow-ups exhausted without a success phrase")


_CORRECTIVE_PROMPT = (
    "Your previous reply was not valid. Respond with bare JSON only — "
    "no code fences, no prose. " + prompts.JSON_PROTOCOL
)


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_object(text: str) -> str:
    """Slice from the first '{' to the last '}' — recovers JSON wrapped in
    conversational prose ("Sure, here is the action: {...}") that fence
    stripping alone leaves unparseable."""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start:end + 1]
    return text


def _parse_llm_reply(raw: str) -> ChatAction | None:
    """Strict-JSON protocol -> ChatAction, or None on any deviation."""
    fenced = _strip_code_fences(raw)
    data: Any = None
    for candidate in (fenced, _extract_json_object(fenced)):
        try:
            data = json.loads(candidate)
            break
        except (json.JSONDecodeError, TypeError):
            continue
    if not isinstance(data, dict):
        return None
    action = data.get("action")
    reason = data.get("reason")
    reason = reason if isinstance(reason, str) and reason else None
    if action == "send":
        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            return None
        return ChatAction(kind="send",
                          message=message.strip()[:MAX_MESSAGE_CHARS])
    if action == "end":
        outcome = data.get("outcome")
        if outcome == "success":
            return ChatAction(kind="end_success", reason=reason)
        if outcome == "failure":
            return ChatAction(kind="end_failure", reason=reason)
    return None


@register
class LlmStrategy(ChatStrategy):
    """OpenRouter-driven strategy: the model writes each next message.

    Transcript maps out->assistant and in->user on top of a system prompt
    (ctx.config["llm_system_prompt"] override, else
    backend.llm.prompts.default_system_prompt). The model must answer the
    strict JSON protocol; one corrective re-prompt is allowed before the chat
    is flagged for manual follow-up. LlmError never escapes next_action — it
    becomes flag_manual.

    The runner flattens these keys into ctx.config before start():
      - "llm_system_prompt": optional system-prompt override (empty -> default)
      - "llm_model": OpenRouter model id (else DEFAULT_SETTINGS["llm"]["model"])
      - "openrouter_api_key": UI override, resolved through
        backend.config.openrouter_api_key() with env-var fallback

    A pre-built client can be injected for tests: LlmStrategy(client=fake);
    the fake only needs the same async .complete(model, messages, ...) -> str.
    """

    name = "llm"

    def __init__(self, client: OpenRouterClient | None = None) -> None:
        self._injected_client = client

    def start(self, ctx: ChatContext) -> None:
        self._ctx = ctx
        self._system_prompt = (ctx.config.get("llm_system_prompt")
                               or prompts.default_system_prompt(ctx))
        self._model = (ctx.config.get("llm_model")
                       or str(app_config.DEFAULT_SETTINGS["llm"]["model"]))
        # A missing API key surfaces as LlmError at .complete() time (and thus
        # flag_manual), so constructing the client here can never raise.
        self._client = self._injected_client or OpenRouterClient(
            app_config.openrouter_api_key(ctx.config.get("openrouter_api_key")))

    def _build_messages(
            self, transcript: list[ChatTurn]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt}]
        for turn in transcript:
            role = "assistant" if turn.direction == "out" else "user"
            messages.append({"role": role, "content": turn.content})
        if not transcript:
            messages.append({
                "role": "user",
                "content": ("The support chat is open but nothing has been "
                            "said yet. Produce your opening follow-up "
                            "message now."),
            })
        return messages

    async def next_action(self, transcript: list[ChatTurn]) -> ChatAction:
        messages = self._build_messages(transcript)
        try:
            raw = await self._client.complete(self._model, messages)
        except LlmError as exc:
            return ChatAction(kind="flag_manual", reason=f"LLM error: {exc}")

        action = _parse_llm_reply(raw)
        if action is not None:
            return action

        # One corrective re-prompt: show the bad reply, demand bare JSON.
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": _CORRECTIVE_PROMPT},
        ]
        try:
            raw = await self._client.complete(self._model, messages)
        except LlmError as exc:
            return ChatAction(kind="flag_manual", reason=f"LLM error: {exc}")

        action = _parse_llm_reply(raw)
        if action is not None:
            return action
        return ChatAction(
            kind="flag_manual",
            reason="LLM returned unparseable output twice")
