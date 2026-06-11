"""Settings get/put endpoints plus an OpenRouter key probe."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import config, db

router = APIRouter()


@router.get("")
async def get_settings() -> dict[str, Any]:
    return await db.get_all_settings()


@router.put("/{key}")
async def put_setting(key: str, request: Request) -> dict[str, Any]:
    # The body IS the raw JSON value (scalar, list, or object) — no envelope.
    try:
        value = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be valid JSON")
    try:
        await db.set_setting(key, value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"key": key, "value": await db.get_setting(key)}


@router.post("/test-llm-key")
async def test_llm_key() -> dict[str, Any]:
    key = config.openrouter_api_key(
        (await db.get_setting("openrouter_api_key")) or None)
    if not key:
        return {
            "ok": False,
            "message": ("No OpenRouter API key configured. Set one in "
                        "Settings or via the OPENROUTER_API_KEY env var."),
        }

    # Lazy: the LLM client (and its HTTP machinery) loads only on demand.
    from backend.llm.openrouter import OpenRouterClient

    model = (await db.get_setting("llm"))["model"]
    try:
        ok, msg = await OpenRouterClient(key).test_key(model)
    except Exception as e:  # noqa: BLE001 — probe must never 500
        return {"ok": False, "message": str(e)}
    return {"ok": ok, "message": msg}
