"""Unit tests for ScriptedStrategy and LlmStrategy — no browser, no network."""
from __future__ import annotations

from typing import Any

from backend.browser.chat_strategy import (
    ChatContext,
    ChatTurn,
    LlmStrategy,
    ProblemOrder,
    ScriptedStrategy,
    get_strategy,
)
from backend.llm.openrouter import LlmError


def make_ctx(*, config: dict[str, Any] | None = None,
             **overrides: Any) -> ChatContext:
    cfg: dict[str, Any] = {
        "scripted_followups": [
            "Please refund my {order_count} order(s) for {amounts} to my "
            "original card.",
            "Thank you for confirming.",
        ],
        "success_phrases": ["refund has been processed",
                            "refunded to your original"],
        "llm_model": "test/model",
        "llm_system_prompt": "",
        "openrouter_api_key": "sk-test",
    }
    cfg.update(config or {})
    fields: dict[str, Any] = dict(
        customer_name="Alex Smith",
        orders=[
            ProblemOrder(1, "uuid-1", "Burger Barn", 23.45, "not_refunded"),
            ProblemOrder(2, "uuid-2", "Pizza Place", 10.00, "partial"),
        ],
        opening_message="opening",
        config=cfg,
    )
    fields.update(overrides)
    return ChatContext(**fields)


# ── ScriptedStrategy ─────────────────────────────────────────────────────────

async def test_scripted_sequencing_send_reply_send_exhausted():
    strategy = ScriptedStrategy()
    strategy.start(make_ctx())
    transcript: list[ChatTurn] = []

    first = await strategy.next_action(transcript)
    assert first.kind == "send"
    assert "2 order(s)" in first.message
    assert "$23.45, $10.00" in first.message

    transcript += [ChatTurn("out", first.message),
                   ChatTurn("in", "Let me look into that for you.")]
    second = await strategy.next_action(transcript)
    assert second.kind == "send"
    assert second.message == "Thank you for confirming."

    transcript += [ChatTurn("out", second.message),
                   ChatTurn("in", "Is there anything else?")]
    final = await strategy.next_action(transcript)
    assert final.kind != "send"  # exhausted


async def test_scripted_success_phrase_ends_success():
    strategy = ScriptedStrategy()
    strategy.start(make_ctx(config={"scripted_followups": []}))
    transcript = [
        ChatTurn("out", "opening"),
        ChatTurn("in", "Good news — your REFUND HAS BEEN PROCESSED back to "
                       "your card."),
    ]
    action = await strategy.next_action(transcript)
    assert action.kind == "end_success"


async def test_scripted_no_phrase_flags_manual():
    strategy = ScriptedStrategy()
    strategy.start(make_ctx(config={"scripted_followups": []}))
    transcript = [ChatTurn("out", "opening"),
                  ChatTurn("in", "We can offer you DoorDash credits.")]
    action = await strategy.next_action(transcript)
    assert action.kind == "flag_manual"
    assert action.reason


async def test_scripted_missing_format_key_never_raises():
    strategy = ScriptedStrategy()
    strategy.start(make_ctx(
        config={"scripted_followups": ["Hi {nonexistent} #{order_count}"]}))
    action = await strategy.next_action([])
    assert action.kind == "send"
    assert action.message == "Hi  #2"


def test_registry_has_both_strategies():
    assert isinstance(get_strategy("scripted"), ScriptedStrategy)
    assert isinstance(get_strategy("llm"), LlmStrategy)


# ── LlmStrategy (fake client) ────────────────────────────────────────────────

class FakeClient:
    """Same async .complete signature as OpenRouterClient."""

    def __init__(self, *replies: str | Exception) -> None:
        self._replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, model: str, messages: list[dict[str, str]],
                       max_tokens: int = 400, timeout: float = 60) -> str:
        self.calls.append(messages)
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_llm(*replies: str | Exception) -> tuple[LlmStrategy, FakeClient]:
    fake = FakeClient(*replies)
    strategy = LlmStrategy(client=fake)
    strategy.start(make_ctx())
    return strategy, fake


async def test_llm_well_formed_send():
    strategy, fake = make_llm('{"action":"send","message":"Hello support"}')
    action = await strategy.next_action([])
    assert action.kind == "send"
    assert action.message == "Hello support"
    messages = fake.calls[0]
    assert messages[0]["role"] == "system"
    assert "Alex" in messages[0]["content"]      # customer first name
    assert "Burger Barn" in messages[0]["content"]
    assert messages[-1]["role"] == "user"        # empty-transcript nudge


async def test_llm_transcript_maps_out_assistant_in_user():
    strategy, fake = make_llm('{"action":"send","message":"ok"}')
    await strategy.next_action([ChatTurn("out", "I sent this"),
                                ChatTurn("in", "support said this")])
    messages = fake.calls[0]
    assert messages[1] == {"role": "assistant", "content": "I sent this"}
    assert messages[2] == {"role": "user", "content": "support said this"}


async def test_llm_end_success():
    strategy, _ = make_llm(
        '{"action":"end","outcome":"success","reason":"refund confirmed"}')
    action = await strategy.next_action([ChatTurn("in", "Refund issued.")])
    assert action.kind == "end_success"
    assert action.reason == "refund confirmed"


async def test_llm_end_failure():
    strategy, _ = make_llm(
        '{"action":"end","outcome":"failure","reason":"only offered credits"}')
    action = await strategy.next_action([ChatTurn("in", "Credits only.")])
    assert action.kind == "end_failure"
    assert action.reason == "only offered credits"


async def test_llm_fenced_json_is_stripped():
    strategy, _ = make_llm(
        '```json\n{"action":"send","message":"Fenced hello"}\n```')
    action = await strategy.next_action([])
    assert action.kind == "send"
    assert action.message == "Fenced hello"


async def test_llm_garbage_corrective_then_garbage_flags_manual():
    strategy, fake = make_llm("I think you should...", "still not json")
    action = await strategy.next_action([])
    assert action.kind == "flag_manual"
    assert len(fake.calls) == 2
    retry = fake.calls[1]
    assert retry[-2] == {"role": "assistant",
                         "content": "I think you should..."}
    assert retry[-1]["role"] == "user"
    assert "bare JSON" in retry[-1]["content"]


async def test_llm_garbage_then_valid_recovers():
    strategy, fake = make_llm("garbage",
                              '{"action":"send","message":"recovered"}')
    action = await strategy.next_action([])
    assert action.kind == "send"
    assert action.message == "recovered"
    assert len(fake.calls) == 2


async def test_llm_error_flags_manual():
    strategy, _ = make_llm(LlmError("rate limited hard"))
    action = await strategy.next_action([])
    assert action.kind == "flag_manual"
    assert "rate limited hard" in action.reason


async def test_llm_truncates_message_to_300_chars():
    strategy, _ = make_llm(
        '{"action":"send","message":"' + "x" * 400 + '"}')
    action = await strategy.next_action([])
    assert action.kind == "send"
    assert len(action.message) == 300


async def test_llm_default_client_missing_key_flags_manual(monkeypatch):
    # No injected client: start() builds one from ctx.config; with no key
    # anywhere, complete() raises LlmError before any HTTP happens.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    strategy = LlmStrategy()
    strategy.start(make_ctx(config={"openrouter_api_key": ""}))
    action = await strategy.next_action([])
    assert action.kind == "flag_manual"
    assert "key" in action.reason.lower()


def test_parse_llm_reply_recovers_json_from_prose():
    """LLM wrapping JSON in conversational text must still parse."""
    from backend.browser.chat_strategy import _parse_llm_reply
    a = _parse_llm_reply('Sure, here is the action: {"action":"send","message":"hi"}')
    assert a is not None and a.kind == "send" and a.message == "hi"
    b = _parse_llm_reply('```json\n{"action":"end","outcome":"success"}\n```')
    assert b is not None and b.kind == "end_success"
    assert _parse_llm_reply("no json here at all") is None
