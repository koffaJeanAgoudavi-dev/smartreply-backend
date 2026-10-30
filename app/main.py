from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.modules.auth.router import router as auth_router
from app.modules.companies.router import router as companies_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.gmail_oauth.router import router as gmail_oauth_router
from app.modules.pipeline.router import router as pipeline_router
from app.modules.telegram.router import router as telegram_router

app = FastAPI(
    title=settings.APP_NAME,
    description="Backend API pour SmartReply Agent SaaS",
    version="1.0.0",
)

# Modules actifs
app.include_router(telegram_router)
app.include_router(pipeline_router)
app.include_router(auth_router)
app.include_router(gmail_oauth_router)
app.include_router(companies_router)
app.include_router(dashboard_router)


@app.get("/")
def read_root():
    return {
        "status": "online",
        "app": settings.APP_NAME,
        "environment": settings.ENVIRONMENT,
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}


# Dashboard Phase A : fichiers statiques légers, servis sur la même origine.
dashboard_directory = Path(__file__).resolve().parent / "dashboard"
app.mount(
    "/dashboard",
    StaticFiles(directory=dashboard_directory, html=True),
    name="dashboard",
)
