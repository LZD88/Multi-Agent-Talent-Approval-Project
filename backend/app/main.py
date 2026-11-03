import logging
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

from app.api import router
from app.evaluation_api import router as evaluation_router
from app.config import get_settings
from app.database import SessionLocal
from app.logging_config import configure_logging
from app.talent_run_api import conversation_router, router as talent_run_router
from app.talent_run_history import SqlTalentRunHistoryStore
from app.talent_run_stream import TalentRunManager


settings = get_settings()
configure_logging(settings.log_dir, settings.service_name)
logger = logging.getLogger(__name__)


def create_app(*, talent_run_manager: TalentRunManager | None = None) -> FastAPI:
    manager = talent_run_manager or TalentRunManager(
        record_store=SqlTalentRunHistoryStore(SessionLocal)
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await manager.startup()
        try:
            yield
        finally:
            await manager.shutdown()

    app = FastAPI(title="人才文档解析 API", version="0.1.0", lifespan=lifespan)
    app.state.talent_run_manager = manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins.split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (perf_counter() - started_at) * 1000
            logger.exception(
                "http_request_failed method=%s path=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                duration_ms,
            )
            raise
        duration_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "http_request method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    @app.get("/api/health")
    def health() -> dict[str, str]:
        logger.info("health_check status=ok")
        return {"service": "talent-document-api", "status": "ok"}

    app.include_router(router)
    app.include_router(evaluation_router)
    app.include_router(talent_run_router)
    app.include_router(conversation_router)
    return app


app = create_app()
