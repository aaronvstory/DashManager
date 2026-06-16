"""FastAPI app factory: REST routes, SSE stream, static frontend."""
from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from backend import config, db
from backend.events import bus
from backend.routes import (
    customers,
    daisy as daisy_routes,
    proxies as proxies_routes,
    reports as reports_routes,
    runs,
    settings as settings_routes,
)

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
    app.include_router(reports_routes.router, prefix="/api/reports",
                       tags=["reports"])
    app.include_router(proxies_routes.router, prefix="/api/proxies",
                       tags=["proxies"])
    app.include_router(daisy_routes.router, prefix="/api/daisy",
                       tags=["daisy"])

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

    # Daily report HTML + proof screenshots, served static. Mounted under
    # /report-files (NOT /reports — that path is the SPA's client route, and a
    # StaticFiles mount there would shadow it on direct load/refresh). The
    # report HTML links screenshots via ``../screenshots/<bucket>/file``
    # relative to /report-files/<date>.html → /screenshots/..., so the two
    # mounts line up.
    app.mount("/report-files",
              StaticFiles(directory=config.REPORTS_DIR, html=True),
              name="report-files")
    app.mount("/screenshots",
              StaticFiles(directory=config.SCREENSHOTS_DIR),
              name="screenshots")

    # Built frontend (when present) is served from the same origin, so the
    # whole app is just `python -m backend`. We serve hashed assets straight
    # off disk and fall back to index.html for everything else, so a hard load
    # or refresh on a client route (/reports, /database, …) hits the SPA shell
    # instead of 404ing. This catch-all must stay LAST — it owns every path
    # not claimed by an /api route or a static mount above.
    if config.FRONTEND_DIST.exists():
        index_html = config.FRONTEND_DIST / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            # An UNMATCHED /api/* path must 404 (JSON), NOT fall through to the
            # SPA shell — otherwise the frontend gets index.html for a missing
            # route, its response.json() chokes, and the UI shows a misleading
            # "couldn't reach <thing>" instead of a clear 404. (This is exactly
            # what a STALE backend looks like: it lacks newer /api routes, so
            # they 404 honestly instead of masquerading as the app.)
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found")
            candidate = (config.FRONTEND_DIST / full_path).resolve()
            # Serve a real built file only if it's inside the dist dir.
            if (
                full_path
                and candidate.is_file()
                and config.FRONTEND_DIST.resolve() in candidate.parents
            ):
                return FileResponse(candidate)
            return FileResponse(index_html)

    return app


app = create_app()
