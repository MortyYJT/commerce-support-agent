import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from commerce_support.async_cleanup import run_shielded_cleanup
from commerce_support.config import Settings
from commerce_support.database import Database
from commerce_support.database.repository import ChatRepository, FAQRepository, TicketRepository
from commerce_support.errors import AppError
from commerce_support.middleware import RequestBodyLimitMiddleware
from commerce_support.model import ChatOpenAIModelGateway
from commerce_support.routes import register_chat_routes
from commerce_support.schemas import ErrorResponse, HealthResponse
from commerce_support.tools.executor import ToolExecutor
from commerce_support.tools.registry import build_registry

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    settings: Settings | None = None,
    gateway: Any | None = None,
    *,
    repository: Any | None = None,
    faq_repository: Any | None = None,
    ticket_repository: Any | None = None,
    tool_executor: Any | None = None,
    registry_factory: Any | None = None,
) -> FastAPI:
    app_settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            if application.state.gateway is None and app_settings.llm_api_key is not None:
                application.state.gateway = ChatOpenAIModelGateway(app_settings)
            if application.state.database is None and app_settings.database_url is not None:
                database = Database(app_settings)
                application.state.database = database
                application.state.database_owned = True
                if application.state.repository is None:
                    application.state.repository = ChatRepository(database)
                if application.state.faq_repository is None:
                    application.state.faq_repository = FAQRepository(database)
                if application.state.ticket_repository is None:
                    application.state.ticket_repository = TicketRepository(database)
                if application.state.registry_factory is None:
                    application.state.registry_factory = _registry_factory(
                        application.state.repository,
                        application.state.faq_repository,
                        application.state.ticket_repository,
                    )
            yield
        finally:
            try:
                current_database = application.state.database
                if application.state.database_owned and current_database is not None:
                    try:
                        await run_shielded_cleanup(current_database.aclose())
                    finally:
                        application.state.database = None
                        application.state.database_owned = False
            finally:
                current_gateway = application.state.gateway
                close = getattr(current_gateway, "aclose", None)
                if close is not None:
                    await run_shielded_cleanup(close())

    application = FastAPI(
        title="Commerce Support API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.gateway = gateway
    application.state.database = None
    application.state.database_owned = False
    application.state.repository = repository
    application.state.faq_repository = faq_repository
    application.state.ticket_repository = ticket_repository
    application.state.tool_executor = (
        tool_executor if tool_executor is not None else ToolExecutor(app_settings)
    )
    application.state.registry_factory = registry_factory
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=app_settings.max_request_body_bytes,
    )

    @application.exception_handler(AppError)
    async def handle_app_error(_request: Request, error: AppError) -> JSONResponse:
        response = ErrorResponse(code=error.code, message=error.public_message)
        return JSONResponse(
            status_code=error.status_code,
            content=response.model_dump(),
        )

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get("/ready")
    async def ready() -> dict[str, str]:
        database = application.state.database
        if database is None:
            raise AppError(
                "database_not_ready",
                "聊天数据库尚未就绪。",
                status_code=503,
            )
        try:
            is_ready = await database.check_ready()
        except Exception:  # noqa: BLE001 - expose only safe readiness state.
            is_ready = False
        if not is_ready:
            raise AppError(
                "database_not_ready",
                "聊天数据库尚未就绪。",
                status_code=503,
            )
        return {"status": "ready"}

    @application.get("/", include_in_schema=False)
    async def chat_page() -> Response:
        index_page = STATIC_DIR / "index.html"
        if not index_page.is_file():
            return HTMLResponse(
                "聊天页面资源尚未安装。",
                status_code=503,
            )
        return FileResponse(index_page, media_type="text/html")

    application.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR, check_dir=False),
        name="static",
    )
    register_chat_routes(application)

    return application


def _registry_factory(
    repository: Any,
    faq_repository: Any,
    ticket_repository: Any,
):
    def build(ctx):
        return build_registry(
            repository=repository,
            faq=faq_repository,
            tickets=ticket_repository,
            ctx=ctx,
            rng=random.Random(),
        )

    return build


app = create_app()
