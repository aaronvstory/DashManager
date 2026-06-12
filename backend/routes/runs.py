"""Run lifecycle endpoints: start, stop, history, transcripts."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import db
from backend.runner import manager

router = APIRouter()


class StartRunBody(BaseModel):
    scope: dict[str, Any] = Field(default_factory=dict)
    chat_strategy: str = "scripted"
    headless: bool | None = None  # per-action override of the setting


@router.post("")
async def start_run(body: StartRunBody) -> dict:
    if body.chat_strategy not in ("scripted", "llm", "none"):
        raise HTTPException(400, "chat_strategy must be scripted|llm|none")
    if not (body.scope.get("bucket_date") or body.scope.get("customer_ids")):
        raise HTTPException(400, "scope needs bucket_date or customer_ids")
    try:
        run_id = await manager.start(body.scope, body.chat_strategy,
                                     headless=body.headless)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"run_id": run_id}


@router.post("/stop")
async def stop_run() -> dict:
    manager.stop()
    return {"stopping": manager.is_running}


@router.get("/active")
async def active_run() -> dict:
    return {"run_id": manager.current_run_id if manager.is_running else None}


def _parse_run(row: dict) -> dict:
    row = dict(row)
    row["scope"] = json.loads(row.pop("scope_json") or "{}")
    row["stats"] = json.loads(row.pop("stats_json") or "{}")
    return row


@router.get("")
async def list_runs() -> dict:
    return {"runs": [_parse_run(r) for r in await db.list_runs()]}


@router.get("/{run_id}")
async def run_detail(run_id: int) -> dict:
    run = await db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if run is None:
        raise HTTPException(404, "run not found")
    chats = []
    for chat in await db.list_chats(run_id):
        chat = dict(chat)
        chat["order_ids"] = json.loads(chat.pop("order_ids_json") or "[]")
        chat["messages"] = await db.list_chat_messages(chat["id"])
        chats.append(chat)
    return {"run": _parse_run(run),
            "orders": await db.list_run_orders(run_id),
            "chats": chats,
            "claims": await db.list_claims(run_id)}
