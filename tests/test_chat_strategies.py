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


# ── ScriptedStrategy (re-push model) ─────────────────────────────────────────

# A config close to DEFAULT_SETTINGS["chat"] so the re-push model has the
# offer-decline + question patterns it keys on.
REPUSH_CFG = {
    "repush_template": "Please refund {amounts} to my original card.",
    "remake_note": " This was an automatic remake I never asked for.",
    "success_phrases": ["refund has been processed", "back to your card"],
    "dashpass_patterns": ["dashpass"],
    "dashpass_decline": "No thank you, I don't want DashPass.",
    "call_offer_patterns": ["give you a call", "call you"],
    "call_decline": "I can't take a call right now, refund to my card please.",
    "offscript_answer": "The store canceled it because items were unavailable.",
}


async def test_scripted_repushes_on_neutral_reply():
    strategy = ScriptedStrategy()
    strategy.start(make_ctx(config=REPUSH_CFG))
    action = await strategy.next_action(
        [ChatTurn("out", "opening"),
         ChatTurn("in", "Let me look into that for you.")])
    assert action.kind == "send"
    assert "$23.45, $10.00" in action.message
    assert "original card" in action.message


async def test_scripted_success_phrase_ends_success():
    strategy = ScriptedStrategy()
    strategy.start(make_ctx(config=REPUSH_CFG))
    action = await strategy.next_action(
        [ChatTurn("out", "opening"),
         ChatTurn("in", "Good news — your REFUND HAS BEEN PROCESSED.")])
    assert action.kind == "end_success"


async def test_scripted_credits_is_not_success():
    # A refund phrase paired with "credits" must NOT end the chat as success.
    strategy = ScriptedStrategy()
    strategy.start(make_ctx(config=REPUSH_CFG))
    action = await strategy.next_action(
        [ChatTurn("in", "Your refund has been processed as DoorDash credits.")])
    assert action.kind == "send"  # keeps pushing, not success


async def test_scripted_declines_dashpass_then_repushes():
    strategy = ScriptedStrategy()
    strategy.start(make_ctx(config=REPUSH_CFG))
    action = await strategy.next_action(
        [ChatTurn("in", "Would you like to try DashPass free for a month?")])
    assert action.kind == "send"
    assert "DashPass" in action.message
    assert "original card" in action.message  # re-push paired with decline


async def test_scripted_declines_phone_call():
    strategy = ScriptedStrategy()
    strategy.start(make_ctx(config=REPUSH_CFG))
    action = await strategy.next_action(
        [ChatTurn("in", "Since you're unresponsive, can I give you a call?")])
    assert action.kind == "send"
    assert "can't take a call" in action.message.lower()
    # The decline must be paired with the re-push so the agent stays on task.
    assert "original card" in action.message


async def test_scripted_answers_offscript_question_then_repushes():
    strategy = ScriptedStrategy()
    strategy.start(make_ctx(config=REPUSH_CFG))
    action = await strategy.next_action(
        [ChatTurn("in", "Who canceled the order — the dasher or the store?")])
    assert action.kind == "send"
    assert "store canceled" in action.message.lower()
    assert "original card" in action.message


async def test_scripted_remake_note_appended():
    strategy = ScriptedStrategy()
    ctx = make_ctx(config=REPUSH_CFG)
    ctx.orders[0].remake = True
    strategy.start(ctx)
    action = await strategy.next_action([ChatTurn("in", "How can I help?")])
    assert "remake I never asked for" in action.message


def test_classify_agent_turn_pure():
    from backend.browser.chat_strategy import classify_agent_turn
    assert classify_agent_turn(
        "your refund has been processed", REPUSH_CFG) == "success"
    assert classify_agent_turn("Try DashPass!", REPUSH_CFG) == "dashpass"
    assert classify_agent_turn("Can I call you?", REPUSH_CFG) == "call"
    assert classify_agent_turn("Who canceled it?", REPUSH_CFG) == "question"
    assert classify_agent_turn("One moment please.", REPUSH_CFG) == "repush"
    # credits veto: refund phrase + credits is NOT success
    assert classify_agent_turn(
        "refund has been processed as credits", REPUSH_CFG) == "repush"


def test_question_requires_trailing_question_mark():
    from backend.browser.chat_strategy import classify_agent_turn
    # A "?" buried mid-sentence (not ending a line) must NOT be a question —
    # only a line that ENDS with "?" counts, so pleasantries don't derail us.
    assert classify_agent_turn(
        "I'll check that? for you and process the refund now.",
        REPUSH_CFG) == "repush"
    # A real trailing question is detected.
    assert classify_agent_turn(
        "Sure.\nWhich card did you use?", REPUSH_CFG) == "question"


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


async def test_llm_skips_system_turns():
    # "system" turns are driver status notes (escalation/agent-reached), NOT
    # conversation — they must never be fed to the LLM as user/assistant msgs.
    strategy, fake = make_llm('{"action":"send","message":"ok"}')
    await strategy.next_action([ChatTurn("out", "I sent this"),
                                ChatTurn("system", "Reached a human agent."),
                                ChatTurn("in", "support said this")])
    messages = fake.calls[0]
    contents = [m["content"] for m in messages]
    assert "Reached a human agent." not in contents
    # only the system prompt + the two real turns survive
    assert messages[1] == {"role": "assistant", "content": "I sent this"}
    assert messages[2] == {"role": "user", "content": "support said this"}
    assert len(messages) == 3


async def test_llm_system_only_transcript_still_gets_opener():
    # A transcript of ONLY system turns (e.g. "Reached a human agent." written
    # before the first real exchange) has no conversational content, so the
    # synthetic opener prompt must still be injected — the empty guard tests the
    # FILTERED turns, not the raw list.
    strategy, fake = make_llm('{"action":"send","message":"ok"}')
    await strategy.next_action([ChatTurn("system", "Reached a human agent.")])
    messages = fake.calls[0]
    assert len(messages) == 2  # system prompt + the synthetic opener
    assert messages[1]["role"] == "user"
    assert "nothing has been" in messages[1]["content"]


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
