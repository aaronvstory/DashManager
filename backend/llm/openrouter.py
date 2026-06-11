"""Minimal async OpenRouter client used by LlmStrategy and Settings.

One POST per completion (no shared connection pool — chat volume is tiny),
with bounded retries on rate limits and server errors.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx


class LlmError(Exception):
    """Raised when an OpenRouter completion cannot be obtained."""


# Sleeps before each EXTRA attempt; module-level so tests can zero them out.
_RETRY_DELAYS: tuple[float, ...] = (1.0, 3.0)


class OpenRouterClient:
    def __init__(self, api_key: str | None,
                 base_url: str = "https://openrouter.ai/api/v1") -> None:
        self.api_key = api_key or None
        self.base_url = base_url.rstrip("/")

    async def complete(self, model: str, messages: list[dict[str, Any]],
                       max_tokens: int = 400, timeout: float = 60) -> str:
        """Return choices[0].message.content, or raise LlmError."""
        if not self.api_key:
            raise LlmError(
                "OpenRouter API key missing — set OPENROUTER_API_KEY or add "
                "a key in Settings.")
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"model": model, "messages": messages,
                   "max_tokens": max_tokens}
        attempts = 1 + len(_RETRY_DELAYS)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(attempts):
                try:
                    resp = await client.post(url, headers=headers,
                                             json=payload)
                except httpx.HTTPError as exc:
                    raise LlmError(
                        f"OpenRouter request failed: {exc}") from exc
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < len(_RETRY_DELAYS):
                        await asyncio.sleep(_RETRY_DELAYS[attempt])
                        continue
                    raise LlmError(
                        f"OpenRouter HTTP {resp.status_code} after "
                        f"{attempts} attempts: {resp.text[:200]}")
                if resp.status_code != 200:
                    raise LlmError(
                        f"OpenRouter HTTP {resp.status_code}: "
                        f"{resp.text[:200]}")
                try:
                    content = resp.json()["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    raise LlmError(
                        "OpenRouter response missing "
                        f"choices[0].message.content: {resp.text[:200]}"
                    ) from exc
                if not isinstance(content, str):
                    raise LlmError("OpenRouter returned non-text content")
                return content
        raise LlmError("unreachable")  # pragma: no cover

    async def test_key(self, model: str) -> tuple[bool, str]:
        """1-token smoke completion for the Settings 'test key' button."""
        try:
            await self.complete(
                model, [{"role": "user", "content": "Reply with OK."}],
                max_tokens=1, timeout=30)
        except LlmError as exc:
            return False, str(exc)
        return True, "API key OK"
