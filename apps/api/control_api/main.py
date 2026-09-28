from __future__ import annotations

import logging
import re
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from control_api.api.v1.routes import router
from control_api.config import get_settings
from control_api.errors import ApiError
from control_api.services.projects import RepositoryInspector, UnverifiedInspector
from control_api.services.setup import LsRemoteProbe, RemoteProbe

log = logging.getLogger("control_api")
_CORRELATION = re.compile(r"^[A-Za-z0-9\-]{8,64}$")


def _error(request: Request, status: int, code: str, message: str, *, retryable: bool = False,
           field_errors: dict | None = None, details: dict | None = None) -> JSONResponse:
    body = {"code": code, "message": message, "retryable": retryable,
            "correlation_id": getattr(request.state, "correlation_id", None),
            "field_errors": field_errors or {}}
    if details:
        body["details"] = details
    return JSONResponse(body, status_code=status)


def create_app(inspector: RepositoryInspector | None = None, remote_probe: RemoteProbe | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AI Software Team Control Center API", version="0.1.0")
    app.state.repository_inspector = inspector or UnverifiedInspector()
    app.state.remote_probe = remote_probe or LsRemoteProbe(settings.github_token)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT"],
                       allow_headers=["authorization", "content-type", "idempotency-key",
                                      "x-correlation-id"])

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        incoming = request.headers.get("x-correlation-id", "")
        request.state.correlation_id = incoming if _CORRELATION.match(incoming) else uuid.uuid4().hex
        response = await call_next(request)
        response.headers["x-correlation-id"] = request.state.correlation_id
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return _error(request, exc.status_code, exc.code, exc.message, retryable=exc.retryable,
                      field_errors=exc.field_errors, details=exc.details)

    @app.exception_handler(RequestValidationError)
    async def malformed(request: Request, exc: RequestValidationError):
        fields = {".".join(str(p) for p in e["loc"]): e["msg"] for e in exc.errors()}
        return _error(request, 400, "malformed_request", "request failed validation",
                      field_errors=fields)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"correlation_id": request.state.correlation_id})
        return _error(request, 500, "internal_error", "unexpected error; quote the correlation id")

    @app.get("/v1/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app


app = create_app()
