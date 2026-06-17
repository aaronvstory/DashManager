"""Keep-open endpoints: hold customer browser windows open between runs.

POST /api/keep-open          open windows for {ids} or all in {bucket_date}
POST /api/keep-open/close    close {ids} (or all kept-open if omitted)
GET  /api/keep-open          {open_ids, recorded_ids}

Scope is by DashManager customer id or bucket_date. (CustomerDaisy's batch_id
isn't a column on the customers table, so it can't resolve to ids here — use the
bucket the batch landed in, or explicit ids.)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import db
from backend.keep_open_manager import manager

router = APIRouter()


class OpenBody(BaseModel):
    ids: list[int] = Field(default_factory=list)
    bucket_date: str | None = None
    headless: bool = False
    landing_url: str | None = None


class CloseBody(BaseModel):
    # Omit ids to close every kept-open window.
    ids: list[int] | None = None


async def _resolve_ids(ids: list[int], bucket_date: str | None) -> list[int]:
    if ids:
        return [int(i) for i in ids]
    if bucket_date:
        rows = await db.list_customers_for_bucket(bucket_date)
        return [int(r["id"]) for r in rows]
    return []


@router.post("")
async def open_keep_open(body: OpenBody) -> dict:
    resolved = await _resolve_ids(body.ids, body.bucket_date)
    if not resolved:
        raise HTTPException(400, "need ids or a bucket_date with customers")
    result = await manager.open(
        resolved, headless=body.headless, landing_url=body.landing_url)
    return result


@router.post("/close")
async def close_keep_open(body: CloseBody) -> dict:
    closed = await manager.close(body.ids)
    return {"closed": closed}


@router.get("")
async def keep_open_status() -> dict:
    return manager.status()
