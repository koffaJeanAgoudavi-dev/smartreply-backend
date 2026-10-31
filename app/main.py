import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.docs import register_non_production_docs
from app.core.security_headers import SecurityHeadersMiddleware, apply_security_headers
from app.modules.auth.router import router as auth_router
from app.modules.companies.router import router as companies_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.gmail_oauth.router import router as gmail_oauth_router
from app.modules.pipeline.router import router as pipeline_router
from app.modules.telegram.router import router as telegram_router

logger = logging.getLogger(__name__)


def create_app(environment: str | None = None) -> FastAPI:
    """Construit l'application avec une politique sûre par défaut."""

    normalized_environment = (
        (settings.ENVIRONMENT if environment is None else environment).strip().lower()
    )
    is_production = normalized_environment == "production"
    docs_enabled = normalized_environment != "production"

    application = FastAPI(
        title=settings.APP_NAME,
        description="Backend API pour SmartReply Agent SaaS",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    application.add_middleware(
        SecurityHeadersMiddleware,
        production=is_production,
    )

    # Modules actifs
    application.include_router(telegram_router)
    application.include_router(pipeline_router)
    application.include_router(auth_router)
    application.include_router(gmail_oauth_router)
    application.include_router(companies_router)
    application.include_router(dashboard_router)

    @application.get("/")
    def read_root():
        return {"status": "online"}

    @application.get("/health")
    def health_check():
        return {"status": "ok"}

    if docs_enabled:
        register_non_production_docs(application)

    @application.get("/dashboard", include_in_schema=False)
    def dashboard_redirect() -> RedirectResponse:
        # Une cible relative conserve HTTPS même avec Uvicorn --no-proxy-headers.
        return RedirectResponse(url="/dashboard/", status_code=307)

    # Dashboard Phase A : fichiers statiques légers, servis sur la même origine.
    dashboard_directory = Path(__file__).resolve().parent / "dashboard"
    application.mount(
        "/dashboard",
        StaticFiles(directory=dashboard_directory, html=True),
        name="dashboard",
    )

    @application.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Erreur HTTP non gérée (%s)",
            type(exc).__name__,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        response = JSONResponse(
            status_code=500,
            content={"detail": "Erreur interne du serveur"},
        )
        # ServerErrorMiddleware est extérieur aux middlewares applicatifs.
        apply_security_headers(
            response.headers,
            request,
            production=is_production,
        )
        return response

    return application


app = create_app()
