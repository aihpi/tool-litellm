from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.a2a_server import AgentsDispatcher, build_agent_app
from app.config import Settings, get_settings
from app.litellm_client import LiteLLMAdmin, LiteLLMGateway
from app.sources import CatalogSource, DirectorySource, KubernetesConfigMapSource
from app.sync import Reconciler, Registry, SyncReport

logger = logging.getLogger("kisz_agent_runner")


@dataclass
class SyncState:
    last_success: float | None = None
    last_attempt: float | None = None
    last_error: str | None = None
    last_report: SyncReport = field(default_factory=SyncReport)


def make_source(settings: Settings) -> CatalogSource:
    if settings.agents_source == "directory":
        if not settings.agents_dir:
            raise ValueError("AGENTS_DIR is required when AGENTS_SOURCE=directory")
        return DirectorySource(settings.agents_dir)
    return KubernetesConfigMapSource(settings.agents_namespace)


async def sync_once(source: CatalogSource, reconciler: Reconciler, state: SyncState) -> None:
    state.last_attempt = time.time()
    try:
        catalog = await source.load()
        report = await reconciler.reconcile(catalog)
    except Exception as exc:
        state.last_error = str(exc)
        logger.exception("sync failed")
        return
    state.last_report = report
    state.last_error = None
    state.last_success = time.time()
    if report.created or report.updated or report.deleted or report.invalid or report.errors:
        logger.info(
            "sync: created=%s updated=%s deleted=%s invalid=%s errors=%s",
            report.created, report.updated, report.deleted, sorted(report.invalid), sorted(report.errors),
        )


def create_app(
    settings: Settings | None = None,
    *,
    source: CatalogSource | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    run_sync_loop: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    registry = Registry()
    state = SyncState()
    http = httpx.AsyncClient(base_url=settings.normalized_litellm_base_url, transport=transport, timeout=30.0)
    admin = LiteLLMAdmin(http, settings.litellm_sync_key)
    gateway = LiteLLMGateway(http, llm_timeout=settings.llm_timeout_seconds)
    reconciler = Reconciler(admin, settings, registry)
    catalog_source = source or make_source(settings)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task = None
        if run_sync_loop:
            async def loop() -> None:
                while True:
                    await sync_once(catalog_source, reconciler, state)
                    await asyncio.sleep(settings.sync_interval_seconds)

            task = asyncio.create_task(loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await http.aclose()

    app = FastAPI(title="KISZ agent runner", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.registry = registry
    app.state.sync_state = state
    app.state.sync = lambda: sync_once(catalog_source, reconciler, state)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        ready = state.last_success is not None
        return JSONResponse({"ready": ready, "agents": sorted(registry.agents)}, status_code=200 if ready else 503)

    @app.get("/status")
    async def status() -> dict[str, object]:
        # Names, errors and timestamps only: never keys.
        return {
            "agents": sorted(registry.agents),
            "last_success": state.last_success,
            "last_attempt": state.last_attempt,
            "last_error": state.last_error,
            "last_report": asdict(state.last_report),
        }

    app.mount(
        "/agents",
        AgentsDispatcher(registry, settings, lambda runtime: build_agent_app(runtime, registry, gateway, settings)),
    )
    return app


def build() -> FastAPI:
    """Entry point for `uvicorn app.main:build --factory`, so settings are read at startup, not at import."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
