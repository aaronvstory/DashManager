"""Shared Pydantic models, enums, and the SSE event envelope.

This file is the cross-module contract: scraping, refund detection, chat,
runner, routes, and the frontend's types.ts all mirror what is defined here.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionStatus(StrEnum):
    active = "active"
    expired = "expired"
    invalid = "invalid"


class OrderStatus(StrEnum):
    active = "active"
    cancelled = "cancelled"


class RefundStatus(StrEnum):
    unchecked = "unchecked"
    refunded = "refunded"        # Refund line present, amount >= total
    partial = "partial"          # Refund line present, 0 < amount < total
    not_refunded = "not_refunded"  # no Refund line — pursue, even if "canceled"
    unknown = "unknown"          # unparseable — never silently pass


class RunStatus(StrEnum):
    running = "running"
    completed = "completed"
    stopped = "stopped"
    error = "error"


class ChatOutcome(StrEnum):
    success = "success"          # agent confirmed refund to ORIGINAL payment method
    failed = "failed"
    blocked = "blocked"          # silent rate-limit block
    review_blocked = "review_blocked"  # double "Got it" popup
    manual_flag = "manual_flag"  # needs a human follow-up; transcript saved


StrategyName = Literal["scripted", "llm", "none"]


class Customer(BaseModel):
    id: int
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    bucket_date: str  # 'YYYY-MM-DD', user-assigned (defaults to date added)
    storage_state_path: str = ""
    cookies_path: str | None = None
    session_status: SessionStatus = SessionStatus.active
    created_at: str = ""
    notes: str = ""
    # Credentials for re-login / on-demand OTP (see db SCHEMA_V2). password is
    # the account password; number_token/api_url/mirror_hosts address the
    # rented api.cc number so a fresh verification code can be fetched.
    password: str = ""
    number_token: str = ""
    api_url: str = ""
    mirror_hosts: str = "[]"  # JSON-encoded list[str]

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or f"Customer {self.id}"


class ScrapedOrder(BaseModel):
    """Raw result of scraping one card on https://www.doordash.com/orders."""
    order_uuid: str
    receipt_url: str
    store_name: str = ""
    description: str = ""
    items_count: int | None = None
    price: float | None = None
    order_status: OrderStatus = OrderStatus.active


class Order(BaseModel):
    id: int
    customer_id: int
    order_uuid: str
    receipt_url: str
    store_name: str = ""
    description: str = ""
    items_count: int | None = None
    price: float | None = None
    order_status: OrderStatus = OrderStatus.active
    refund_status: RefundStatus = RefundStatus.unchecked
    total_amount: float | None = None
    refund_amount: float | None = None
    last_checked_at: str | None = None


class RefundResult(BaseModel):
    """Output of the pure refund detector run on receipt-page text."""
    status: RefundStatus
    total_amount: float | None = None
    refund_amount: float | None = None
    cancelled_text_seen: bool = False


class IdentityProfile(BaseModel):
    """Values read from /consumer/edit_profile after login."""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""


class Run(BaseModel):
    id: int
    started_at: str = ""
    finished_at: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    chat_strategy: StrategyName = "scripted"
    status: RunStatus = RunStatus.running
    stats: dict[str, Any] = Field(default_factory=dict)


class Chat(BaseModel):
    id: int
    run_id: int
    customer_id: int
    order_ids: list[int] = Field(default_factory=list)
    opening_message: str = ""
    outcome: ChatOutcome | None = None
    agent_reached: bool = False
    started_at: str = ""
    finished_at: str | None = None


class ChatMessageRow(BaseModel):
    id: int
    chat_id: int
    ts: str
    direction: Literal["out", "in", "system"]
    content: str


# Every SSE event type the backend can emit; frontend types.ts mirrors this.
EVENT_TYPES = (
    "login_waiting",
    "login_captured",
    "login_failed",
    "account_balance",
    "identity_generating",
    "identity_generated",
    "number_renting",
    "number_rented",
    "signup_submitting",
    "signup_outcome",
    "otp_waiting",
    "otp_received",
    "otp_resent",
    "address_set",
    "account_created",
    "account_failed",
    "relogin_started",
    "relogin_outcome",
    "relogin_done",
    "relogin_failed",
    "run_started",
    "customer_started",
    "session_invalid",
    "orders_found",
    "order_checking",
    "order_checked",
    "chat_opened",
    "chat_escalation",
    "chat_message",
    "chat_outcome",
    "customer_done",
    "run_done",
    "run_error",
    "log",
    "heartbeat",
)


class Event(BaseModel):
    id: int
    ts: str
    run_id: int | None = None
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
