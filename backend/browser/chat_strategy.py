"""Pluggable chat strategies — browser-free state machines.

The driver in backend/browser/chat.py owns ALL Playwright interaction; a
strategy only ever sees the transcript and returns the next ChatAction. That
keeps ScriptedStrategy and LlmStrategy interchangeable and unit-testable
without a browser.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal


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
