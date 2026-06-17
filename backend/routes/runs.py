"""Run lifecycle endpoints: start, stop, history, transcripts."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import db
from backend.keep_open_manager import manager as keep_open_manager
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
    # Reject a concurrent run BEFORE touching keep-open state — otherwise a
    # rejected start would needlessly close the user's kept-open windows.
    if manager.is_running:
        raise HTTPException(409, "a run is already active")
    # Yield any kept-open windows for the customers this run will touch: the run
    # acquires each profile_lock keep-open is holding, so it would block forever
    # otherwise. Closing releases the locks; the run re-opens the same on-disk
    # (already-logged-in) profile. Resolve via the runner's own scope→ids logic
    # so "bucket_date" and "customer_ids" map identically.
    run_customers = await manager.resolve_customers(body.scope)
    if run_customers:
        await keep_open_manager.close([int(c["id"]) for c in run_customers])
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
