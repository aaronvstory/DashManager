"""Default system prompt for LlmStrategy.

Imported by backend.browser.chat_strategy at module load; the ChatContext
import stays under TYPE_CHECKING to avoid a runtime import cycle.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.browser.chat_strategy import ChatContext

# Shared with the corrective re-prompt in chat_strategy so the model sees the
# identical protocol both times.
JSON_PROTOCOL = (
    "Output protocol — reply with bare JSON only (no code fences, no text "
    "outside the JSON), exactly one of:\n"
    '{"action":"send","message":"<your next chat message>"}\n'
    '{"action":"end","outcome":"success","reason":"<why>"}\n'
    '{"action":"end","outcome":"failure","reason":"<why>"}'
)


def default_system_prompt(ctx: ChatContext) -> str:
    name_parts = ctx.customer_name.split()
    first_name = name_parts[0] if name_parts else "the customer"
    order_lines = "\n".join(
        f"- {order.store_name or 'Unknown store'}: "
        + (f"${order.price:.2f}" if order.price is not None
           else "amount unknown")
        for order in ctx.orders
    ) or "- (order details unavailable)"
    return (
        f"You are {first_name}, a DoorDash customer chatting with DoorDash "
        "support. These orders of yours were never properly refunded:\n"
        f"{order_lines}\n\n"
        "Your single goal: get every order above refunded to the ORIGINAL "
        "payment card.\n\n"
        "Rules:\n"
        "- NEVER accept DoorDash credits. If support offers credits, "
        "politely refuse and insist on a refund to the original payment "
        "method.\n"
        "- Stay strictly on the topic of these refunds; do not discuss "
        "anything else.\n"
        "- Never invent order details — mention only the stores and amounts "
        "listed above.\n"
        "- Keep every message at or under 300 characters.\n"
        "- Be polite but persistent; do not give up after a first refusal.\n"
        "- End with outcome \"success\" ONLY when support explicitly "
        "confirms the refund(s) will go to the original payment method. "
        "Credits or vague promises are not success.\n\n"
        f"{JSON_PROTOCOL}"
    )
