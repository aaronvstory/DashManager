"""HTTP-level tests for the OpenRouter client (respx-mocked)."""
from __future__ import annotations

import httpx
import pytest
import respx

from backend.llm import openrouter
from backend.llm.openrouter import LlmError, OpenRouterClient

URL = "https://openrouter.ai/api/v1/chat/completions"
MESSAGES = [{"role": "user", "content": "hi"}]


def _ok(content: str = "hello") -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"content": content}}]})


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(openrouter, "_RETRY_DELAYS", (0.0, 0.0))


@respx.mock
async def test_success_returns_content_and_sends_bearer():
    route = respx.post(URL).mock(return_value=_ok("the reply"))
    client = OpenRouterClient("sk-test")
    assert await client.complete("test/model", MESSAGES) == "the reply"
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-test"


@respx.mock
async def test_429_then_success_retries():
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(429, text="slow down"),
                     _ok("after retry")])
    client = OpenRouterClient("sk-test")
    assert await client.complete("test/model", MESSAGES) == "after retry"
    assert route.call_count == 2


@respx.mock
async def test_persistent_500_raises_after_three_attempts():
    route = respx.post(URL).mock(
        return_value=httpx.Response(500, text="boom"))
    client = OpenRouterClient("sk-test")
    with pytest.raises(LlmError, match="500"):
        await client.complete("test/model", MESSAGES)
    assert route.call_count == 3


async def test_missing_key_raises_without_http():
    client = OpenRouterClient(None)
    with pytest.raises(LlmError, match="key"):
        await client.complete("test/model", MESSAGES)


@respx.mock
async def test_4xx_raises_without_retry():
    route = respx.post(URL).mock(
        return_value=httpx.Response(401, text="bad key"))
    client = OpenRouterClient("sk-bad")
    with pytest.raises(LlmError, match="401"):
        await client.complete("test/model", MESSAGES)
    assert route.call_count == 1


@respx.mock
async def test_malformed_body_raises():
    respx.post(URL).mock(return_value=httpx.Response(200, json={"nope": 1}))
    client = OpenRouterClient("sk-test")
    with pytest.raises(LlmError, match="choices"):
        await client.complete("test/model", MESSAGES)


@respx.mock
async def test_test_key_success():
    respx.post(URL).mock(return_value=_ok("OK"))
    ok, message = await OpenRouterClient("sk-test").test_key("test/model")
    assert ok is True
    assert message


async def test_test_key_missing_key():
    ok, message = await OpenRouterClient("").test_key("test/model")
    assert ok is False
    assert "key" in message.lower()
