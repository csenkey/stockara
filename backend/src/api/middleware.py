"""FastAPI middleware for request duration logging and slow query detection."""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

import structlog

logger = structlog.get_logger(__name__)

SLOW_REQUEST_THRESHOLD_SECONDS = 5.0


class RequestDurationLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that logs request duration and flags slow requests (>5s)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.perf_counter()

        response = await call_next(request)

        duration_seconds = time.perf_counter() - start_time
        duration_ms = round(duration_seconds * 1000, 2)

        log_kwargs = {
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        }

        if duration_seconds > SLOW_REQUEST_THRESHOLD_SECONDS:
            logger.warning(
                "Slow request detected",
                **log_kwargs,
                threshold_seconds=SLOW_REQUEST_THRESHOLD_SECONDS,
            )
        else:
            logger.info("Request completed", **log_kwargs)

        return response
