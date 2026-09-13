import logging
import time
import traceback

from fastapi import Request, status
from fastapi.responses import JSONResponse
from google.api_core.exceptions import ResourceExhausted

logger = logging.getLogger("api")


def setup_exception_handlers(app):

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )

    @app.exception_handler(FileNotFoundError)
    async def file_not_found_handler(request: Request, exc: FileNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": f"Data or model not ready: {str(exc)}. Run ingestion and training first."
            },
        )

    @app.exception_handler(ResourceExhausted)
    async def rate_limit_handler(request: Request, exc: ResourceExhausted):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": "Google API rate limit exceeded. Please try again later."
            },
            headers={"Retry-After": "60"},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.error(f"Unhandled exception: {str(exc)}\n{traceback.format_exc()}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal server error occurred."},
        )


async def logging_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start_time) * 1000

    logger.info(
        f"Method: {request.method} | Path: {request.url.path} | "
        f"Status: {response.status_code} | Duration: {duration_ms:.2f}ms"
    )
    return response
