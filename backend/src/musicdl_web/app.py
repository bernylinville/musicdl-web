"""FastAPI application factory and lazily initialized production application."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from musicdl_web.api import BatchSubmitter, create_api_router, create_platform_router
from musicdl_web.api.platform_router import (
    PlatformSessionView,
    PublicSessionState,
    QrChallengeView,
    QrObservationView,
    QualitySnapshotView,
    SearchGroupView,
    SearchResponseView,
)
from musicdl_web.artwork import Artwork
from musicdl_web.db import Repository
from musicdl_web.models import Source
from musicdl_web.queue import QueueService


def create_app(
    repository: Repository | None = None,
    queue: QueueService | None = None,
    submitter: BatchSubmitter | None = None,
    platform_router: APIRouter | None = None,
    *,
    production: bool = False,
) -> FastAPI:
    application = FastAPI(
        title="musicdl-web",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_production_lifespan if production else None,
    )
    if repository is not None and queue is not None and submitter is not None:
        application.include_router(create_api_router(repository, queue, submitter))
    if production:
        if platform_router is not None:
            application.include_router(platform_router)
        bootstrap = _bootstrap_router()
        before = set(id(route) for route in application.router.routes)
        application.include_router(bootstrap)
        application.state.bootstrap_routes = tuple(
            route for route in application.router.routes if id(route) not in before
        )
    else:
        application.include_router(platform_router or _unavailable_platform_router())

    frontend = _frontend_root()
    assets = frontend / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="assets")

    @application.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "healthy"}

    @application.get("/", response_class=HTMLResponse, response_model=None)
    def workbench() -> Response:
        index = frontend / "index.html"
        if index.is_file():
            return FileResponse(index, media_type="text/html")
        return HTMLResponse(_frontend_missing_page(), status_code=503)

    return application


def _unavailable_platform_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def unavailable() -> None:
        raise HTTPException(status_code=503, detail="platform capability is not configured")

    router.add_api_route("/sessions", unavailable, methods=["GET"])
    router.add_api_route("/search", unavailable, methods=["GET"])
    router.add_api_route(
        "/tracks/{source}/{track_id}/qualities", unavailable, methods=["GET"]
    )
    router.add_api_route("/sessions/{source}/qr", unavailable, methods=["POST"])
    router.add_api_route(
        "/sessions/{source}/qr/{challenge_id}", unavailable, methods=["GET", "DELETE"]
    )
    router.add_api_route("/sessions/{source}/import", unavailable, methods=["POST"])
    router.add_api_route("/sessions/{source}", unavailable, methods=["DELETE"])

    @router.get("/tracks/{source}/{track_id}/preview", status_code=204)
    def preview_unavailable() -> Response:
        return Response(status_code=204)

    return router


@asynccontextmanager
async def _production_lifespan(application: FastAPI) -> AsyncIterator[None]:
    # Key and writable-volume access intentionally happen at startup, not import time. This
    # keeps tooling/imports usable while making a misconfigured production container fail closed.
    from musicdl_web.runtime import build_application_runtime

    runtime = build_application_runtime()
    bootstrap = set(id(route) for route in application.state.bootstrap_routes)
    application.router.routes[:] = [
        route for route in application.router.routes if id(route) not in bootstrap
    ]
    application.include_router(
        create_api_router(runtime.repository, runtime.queue, runtime.platform)
    )
    application.include_router(create_platform_router(runtime.platform))
    application.state.runtime = runtime
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


def _frontend_root() -> Path:
    configured = os.environ.get("MUSICDL_WEB_FRONTEND_DIR")
    if configured:
        return Path(configured)
    container = Path("/app/frontend")
    if container.is_dir():
        return container
    return Path(__file__).resolve().parents[3] / "frontend" / "dist"


class _BootstrapPlatformService:
    """Import-safe API shape; production startup atomically replaces these routes."""

    def session_statuses(self) -> tuple[PlatformSessionView, ...]:
        return tuple(
            PlatformSessionView(
                source=source,
                state=PublicSessionState.ANONYMOUS,
                displayName=None,
                qrEnabled=False,
                checkedAt=None,
                message="运行时尚未启动",
            )
            for source in Source
        )

    def import_session(self, source: Source, value: str) -> PlatformSessionView:
        del source, value
        raise ValueError("runtime is not initialized")

    def clear_session(self, source: Source) -> None:
        del source

    def start_qr(self, source: Source) -> QrChallengeView:
        del source
        raise ValueError("runtime is not initialized")

    def poll_qr(self, source: Source, challenge_id: str) -> QrObservationView:
        del source, challenge_id
        raise LookupError("runtime is not initialized")

    def cancel_qr(self, source: Source, challenge_id: str) -> None:
        del source, challenge_id
        raise LookupError("runtime is not initialized")

    def search(
        self, query: str, source: Source | None, page: int
    ) -> SearchResponseView:
        sources = tuple(Source) if source is None else (source,)
        return SearchResponseView(
            query=query,
            groups=tuple(
                SearchGroupView(
                    source=current,
                    tracks=(),
                    page=page,
                    hasMore=False,
                    status="unavailable",
                    message="运行时尚未启动",
                )
                for current in sources
            ),
        )

    def quality_snapshot(
        self, source: Source, track_id: str
    ) -> QualitySnapshotView:
        del source, track_id
        raise PermissionError("runtime is not initialized")

    def artwork(self, source: Source, track_id: str) -> Artwork:
        del source, track_id
        raise LookupError("runtime is not initialized")


def _bootstrap_router() -> APIRouter:
    router = create_platform_router(_BootstrapPlatformService())

    @router.get("/tasks")
    def empty_tasks() -> list[dict[str, object]]:
        return []

    def runtime_unavailable() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "运行时尚未启动", "code": "runtime_unavailable"},
        )

    router.add_api_route("/batches", runtime_unavailable, methods=["POST"])
    router.add_api_route(
        "/tasks/{task_id}/cancel", runtime_unavailable, methods=["POST"]
    )
    router.add_api_route(
        "/tasks/{task_id}/retry", runtime_unavailable, methods=["POST"]
    )

    @router.delete("/history", status_code=204)
    def empty_history() -> Response:
        return Response(status_code=204)

    return router


def _frontend_missing_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>musicdl-web</title>
</head>
<body>
  <main><h1>musicdl-web</h1><p>前端构建产物不可用。</p></main>
</body>
</html>
"""


app = create_app(production=True)
