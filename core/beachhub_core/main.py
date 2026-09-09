import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from beachhub_core.auth import verify_csrf
from beachhub_core.config import settings
from beachhub_core.routes import admin_auth, belegung, dashboard, kunden, stammdaten
from beachhub_core.services import storno as _storno  # noqa: F401 – verdrahtet Hooks beim Start

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

if settings.has_insecure_defaults:
    if settings.app_env == "production":
        raise RuntimeError("Start verweigert: SECRET_KEY/PIN_SCHLUESSEL sind Standardwerte.")
    logger.warning("SECRET_KEY/PIN_SCHLUESSEL sind unsichere Standardwerte – nur für Entwicklung.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


app = FastAPI(title="Beachhub Hauptsystem", docs_url=None, redoc_url=None, lifespan=lifespan)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'",
        )
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response


app.add_middleware(SecurityHeadersMiddleware)

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

csrf = [Depends(verify_csrf)]
app.include_router(admin_auth.router, prefix="/admin", dependencies=csrf)
app.include_router(dashboard.router, prefix="/admin", dependencies=csrf)
app.include_router(stammdaten.router, prefix="/admin", dependencies=csrf)
app.include_router(kunden.router, prefix="/admin", dependencies=csrf)
app.include_router(belegung.router, prefix="/admin", dependencies=csrf)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):  # type: ignore[no-untyped-def]
    if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=303)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})
