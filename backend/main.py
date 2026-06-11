"""FastAPI app factory: REST routes, SSE stream, static frontend."""
from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from backend import config, db
from backend.events import bus
from backend.routes import customers, runs, settings as settings_routes

HEARTBEAT_S = 15


def create_app() -> FastAPI:
    db.init_db()
    app = FastAPI(title="DashManager")

    app.add_middleware(
        CORSMiddleware,
        # Vite dev server origins only; in "prod" the same origin serves all.
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(customers.router, prefix="/api/customers",
                       tags=["customers"])
    app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
    app.include_router(settings_routes.router, prefix="/api/settings",
                       tags=["settings"])

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/events")
    async def events(request: Request) -> EventSourceResponse:
        last_id_raw = request.headers.get("Last-Event-ID")

        async def gen():
            q = bus.subscribe()
            try:
                if last_id_raw and last_id_raw.isdigit():
                    for ev in bus.replay_after(int(last_id_raw)):
                        yield {"id": str(ev["id"]), "event": ev["type"],
                               "data": json.dumps(ev)}
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(),
                                                    timeout=HEARTBEAT_S)
                    except asyncio.TimeoutError:
                        yield {"event": "heartbeat", "data": "{}"}
                        continue
                    yield {"id": str(ev["id"]), "event": ev["type"],
                           "data": json.dumps(ev)}
            finally:
                bus.unsubscribe(q)

        return EventSourceResponse(gen())

    # Built frontend (when present) is served from the same origin, so the
    # whole app is just `python -m backend`.
    if config.FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=config.FRONTEND_DIST, html=True),
                  name="frontend")

    return app


app = create_app()
