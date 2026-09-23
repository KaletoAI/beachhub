import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from beachhub_portal.config import INSECURE_SECRET, pruefe_produktionsstart, settings
from beachhub_portal.templating import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

pruefe_produktionsstart(settings)
if settings.secret_key == INSECURE_SECRET:
    logger.warning("PORTAL_SECRET_KEY ist der Standardwert – nur für Entwicklung.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


app = FastAPI(
    title="Beachhub Portal", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)


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
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.exception_handler(StarletteHTTPException)
async def http_fehler(request: Request, exc: StarletteHTTPException) -> Response:
    if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=303)
    if request.url.path.startswith("/core/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    # Eigene Texte (etwa „Link abgelaufen“) zeigen; Starlettes englische Standardtexte nicht.
    standard = exc.detail in (None, "", "Not Found", "Method Not Allowed")
    text = "Seite nicht gefunden." if standard else str(exc.detail)
    return render(request, "fehler.html", status_code=exc.status_code, text=text)
