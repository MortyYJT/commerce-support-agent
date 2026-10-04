from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from commerce_support.config import Settings
from commerce_support.errors import AppError
from commerce_support.middleware import RequestBodyLimitMiddleware
from commerce_support.model import ChatOpenAIModelGateway
from commerce_support.routes import register_chat_routes
from commerce_support.schemas import ErrorResponse, HealthResponse

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    settings: Settings | None = None,
    gateway: Any | None = None,
) -> FastAPI:
    app_settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if application.state.gateway is None and app_settings.llm_api_key is not None:
            application.state.gateway = ChatOpenAIModelGateway(app_settings)
        try:
            yield
        finally:
            current_gateway = application.state.gateway
            close = getattr(current_gateway, "aclose", None)
            if close is not None:
                await close()

    application = FastAPI(
        title="Commerce Support API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.gateway = gateway
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


app = create_app()
