"""The ASGI application: factory, lifespan, middleware, mounts.

Everything the process needs in order to be one process. NoteVault ships as a
*single* uvicorn worker serving the REST API, both WebSockets, the background
reminder job and -- in production -- the built React bundle, because the
connection registry in ``app/ws/manager.py`` and the APScheduler instance in
``app/jobs/scheduler.py`` are both in-process state.
Two workers means two of each: half the sockets unreachable and every rental
reminder sent twice.

Three decisions worth knowing about before editing:

* **The factory exists for the tests.** ``create_app()`` builds a fresh app so
  a test can override dependencies without poisoning the module-level ``app``
  that uvicorn imports.
* **The SPA mount is conditional and silent.** ``frontend/dist`` only exists
  after ``npm run build`` has produced the Vite build. In development the UI
  is served by Vite on :5173 and proxied, so the absence of the folder is normal
  and must not warn, fail, or change any API behaviour.
* **The catch-all is last, and refuses to shadow the API.** A React Router
  deep link (``/materials/abc123``) has to return ``index.html`` on a hard
  refresh, which requires a route matching *everything*. Registered after the
  API so real routes win, and it still 404s on reserved prefixes -- otherwise
  a typo'd API path would answer 200 with a page of HTML and the frontend's
  fetch wrapper would try to parse it as JSON.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from app.api.v1 import api_router
from app.core.config import settings
from app.core.db import engine
from app.jobs.scheduler import shutdown_scheduler, start_scheduler
from app.ws.chat import router as ws_chat_router
from app.ws.notifications import router as ws_notifications_router

logger = logging.getLogger(__name__)

APP_VERSION = "1.0.0"

#: Path prefixes the SPA catch-all must never answer for. A miss under any of
#: these is a genuine 404 and has to look like one to a JSON client.
RESERVED_PREFIXES: tuple[str, ...] = (
    settings.api_prefix,
    "/ws",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)

tags_metadata: list[dict[str, str]] = [
    {
        "name": "auth",
        "description": (
            "Registration, sign-in, refresh-token rotation and logout. Outside "
            "the FR count; every other module depends on it."
        ),
    },
    {
        "name": "users",
        "description": "Own profile read/update and public profiles with their reviews.",
    },
    {
        "name": "campuses",
        "description": "The campus list behind the sign-up form and the campus filter (FR 4.3).",
    },
    {
        "name": "materials",
        "description": (
            "**Module 1 - core listing & discovery.** Upload with preview "
            "generation, duplicate detection, faceted search, detail, edit, "
            "takedown and gated file access (FR 1.1-1.5)."
        ),
    },
    {
        "name": "requests",
        "description": "**Module 2 - community.** The request board for material nobody has listed yet (FR 2.1).",
    },
    {
        "name": "wishlist",
        "description": "**Module 2 - community.** Bookmarks, and the demand signal they feed (FR 2.4).",
    },
    {
        "name": "chat",
        "description": (
            "**Module 2 - communication.** Conversation threads and paged "
            "message history; the live half is `WS /ws/chat` (FR 2.2)."
        ),
    },
    {
        "name": "notifications",
        "description": (
            "**Module 2 - communication.** The durable list behind the bell. "
            "Persisted first, pushed over `WS /ws/notifications` second "
            "(FR 2.3, FR 3.2)."
        ),
    },
    {
        "name": "reviews",
        "description": "**Module 2 - community.** Ratings gated on a completed transaction (FR 2.5).",
    },
    {
        "name": "transactions",
        "description": (
            "**Module 3 - transactions & trust.** The guarded state machine, "
            "plus the QR handoff that neither party can complete alone "
            "(FR 3.3, FR 3.4)."
        ),
    },
    {
        "name": "rentals",
        "description": "**Module 3 - transactions & trust.** Active rentals, days remaining, returns (FR 3.2).",
    },
    {
        "name": "reports",
        "description": "**Module 3 - transactions & trust.** Filing side of notice-and-takedown (FR 3.5).",
    },
    {
        "name": "admin",
        "description": (
            "**Module 4 - admin & insights.** Moderation queue, approve/reject "
            "with auto-match, report resolution, platform stats (FR 4.1)."
        ),
    },
    {
        "name": "analytics",
        "description": (
            "**Module 4 - admin & insights.** Contributor leaderboard and "
            "weighted trending courses (FR 4.2, FR 4.4)."
        ),
    },
    {
        "name": "export",
        "description": (
            "**Module 4 - admin & insights.** Your own history as CSV or PDF. "
            "The subject comes from the token, never from a parameter (FR 4.5)."
        ),
    },
]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown for the whole process.

    Storage directories are created here rather than at import time so that
    merely importing ``app.main`` (which every test does) has no side effects
    on the filesystem beyond
    what running the server would need anyway.
    """
    settings.ensure_storage_dirs()
    await start_scheduler()
    try:
        yield
    finally:
        # Ordered: stop producing work, then tear down what serves it. Disposing
        # the engine first would leave the reminder job holding a dead pool.
        await shutdown_scheduler()
        await engine.dispose()


def _integrity_detail(exc: IntegrityError) -> str:
    """Turn a SQLite constraint message into something a user can act on.

    The database is the last line of defence behind checks the routers already
    make (one open report per listing, one review per transaction, ...), so
    reaching here usually means two requests raced. That is a conflict, not a
    server fault, and the caller deserves better than a stack trace.
    """
    raw = str(getattr(exc, "orig", exc))

    if "UNIQUE constraint failed" in raw:
        _, _, columns = raw.partition("UNIQUE constraint failed:")
        target = columns.strip().rstrip(".") or "that value"
        return f"That already exists and cannot be duplicated ({target})."
    if "FOREIGN KEY constraint failed" in raw:
        return "That refers to a record which does not exist (or no longer does)."
    if "NOT NULL constraint failed" in raw:
        _, _, columns = raw.partition("NOT NULL constraint failed:")
        target = columns.strip().rstrip(".") or "a required field"
        return f"A required value is missing ({target})."
    if "CHECK constraint failed" in raw:
        return "One of the values sent is not allowed for that field."
    return "That request conflicts with data already stored."


def _mount_spa(app: FastAPI) -> None:
    """Serve the built React bundle from the same origin as the API.

    Same origin means the browser needs no CORS preflight and the WebSockets
    can reuse the page's host, which is why production is one process rather
    than an nginx sitting in front of two.

    Does nothing at all when ``frontend/dist`` is absent -- that is the normal
    state in ``--dev`` mode, not an error.
    """
    dist: Path = settings.frontend_dist
    index = dist / "index.html"
    if not index.is_file():
        return

    assets = dist / "assets"
    if assets.is_dir():
        # Vite fingerprints everything under /assets, so it is safe to let the
        # browser cache aggressively; StaticFiles' ETag/Last-Modified handling
        # is enough for the rest.
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def spa(spa_path: str) -> FileResponse:
        """Any unmatched GET: a real file from dist/, else ``index.html``."""
        path = "/" + spa_path.lstrip("/")
        if any(path == p or path.startswith(f"{p}/") for p in RESERVED_PREFIXES):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No such endpoint: {path}",
            )

        # Root-level bundle files (favicon, robots.txt, manifest) are not under
        # /assets and would otherwise be answered with the HTML shell. resolve()
        # + the containment check is what stops ``/../../notevault.db``.
        candidate = (dist / spa_path).resolve()
        if spa_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)

        # index.html must never be cached: its <script src> points at a
        # fingerprinted bundle that changes on every deploy.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    logger.info("serving the built frontend from %s", dist)


def create_app() -> FastAPI:
    """Build a fully wired application.

    A factory rather than a module-level assembly so tests can construct an
    isolated instance (with their own dependency overrides and their own
    lifespan) without mutating the one uvicorn serves.
    """
    app = FastAPI(
        title=settings.app_name,
        version=APP_VERSION,
        summary="Campus peer-to-peer study-material exchange.",
        description=(
            "Four SRS modules behind one API: listing & discovery, community & "
            "communication, transactions & trust, and admin & insights. "
            "Authenticate with `POST /api/v1/auth/login` and send the access "
            "token as `Authorization: Bearer <token>`."
        ),
        openapi_tags=tags_metadata,
        lifespan=lifespan,
    )

    # allow_credentials with an explicit origin list, never "*": the two are
    # mutually exclusive per the CORS spec, and browsers silently drop the
    # response rather than telling you why.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(_request: Request, exc: IntegrityError) -> JSONResponse:
        """A raced constraint is a 409, not a 500."""
        logger.warning("integrity error surfaced to the client: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": _integrity_detail(exc)},
        )

    @app.get("/health", tags=["health"], summary="Liveness probe")
    async def health() -> dict[str, Any]:
        """Deliberately touches nothing. It answers "is the process up?" only;
        a check that queried the database would fail the probe during a WAL
        checkpoint and get the container restarted for no reason."""
        return {"status": "ok", "app": settings.app_name, "version": APP_VERSION}

    app.include_router(api_router, prefix=settings.api_prefix)

    # The socket routers already carry their full ``/ws/...`` paths, and the
    # frontend's dev proxy is configured for that prefix, so they mount at the
    # root rather than under the versioned API.
    app.include_router(ws_chat_router)
    app.include_router(ws_notifications_router)

    # Last, always: it matches every remaining path by design.
    _mount_spa(app)

    return app


app = create_app()

__all__ = ["APP_VERSION", "app", "create_app", "lifespan", "tags_metadata"]
