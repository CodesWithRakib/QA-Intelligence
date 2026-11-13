"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.ai.service import ensure_defaults
from app.api import admin, findings, projects, qa
from app.api.deps import require_token
from app.config import get_settings
from app.db import SessionLocal
from app.ingestion.service import IngestionError
from app.logging_setup import configure_logging
from app.repair.service import RepairRejected
from app.runs.service import RunRejected

log = logging.getLogger("app.api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings().log_level)
    with SessionLocal() as db:
        ensure_defaults(db)
    log.info("api ready", extra={"version": __version__})
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="QA Intelligence API", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)  # fmt: skip
    # The API is local-only. Rejecting unknown Host headers defends against DNS rebinding.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.get("/health", include_in_schema=False)
    def liveness() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    secured = [Depends(require_token)]
    for module in (projects, qa, findings, admin):
        app.include_router(module.router, prefix="/api", dependencies=secured)

    @app.exception_handler(IngestionError)
    @app.exception_handler(RunRejected)
    async def unprocessable(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status.HTTP_422_UNPROCESSABLE_CONTENT)

    @app.exception_handler(RepairRejected)
    async def conflict(_: Request, exc: RepairRejected) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status.HTTP_409_CONFLICT)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Internal details go to the log only, never to the client.
        log.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse({"detail": "internal server error"}, status.HTTP_500_INTERNAL_SERVER_ERROR)

    return app


app = create_app()
