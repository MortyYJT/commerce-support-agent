from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from commerce_support.config import Settings
from commerce_support.errors import AppError
from commerce_support.middleware import RequestBodyLimitMiddleware
from commerce_support.schemas import ErrorResponse, HealthResponse


def create_app(
    settings: Settings | None = None,
    gateway: Any | None = None,
) -> FastAPI:
    app_settings = settings if settings is not None else Settings()
    application = FastAPI(title="Commerce Support API", version="0.1.0")
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

    return application


app = create_app()
