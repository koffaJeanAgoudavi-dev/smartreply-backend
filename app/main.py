from fastapi import FastAPI
from app.core.config import settings
from app.modules.telegram.router import router as telegram_router
from app.modules.ai.router import router as ai_router
from app.modules.gmail.router import router as gmail_router
from app.modules.filtering.router import router as filtering_router
from app.modules.sheets.router import router as sheets_router
from app.modules.drafts.router import router as drafts_router
from app.modules.notifications.router import router as notifications_router

app = FastAPI(
    title=settings.APP_NAME,
    description="Backend API pour SmartReply Agent SaaS",
    version="1.0.0"
)

# Modules actifs
app.include_router(telegram_router)
app.include_router(ai_router)
app.include_router(gmail_router)
app.include_router(filtering_router)
app.include_router(sheets_router)
app.include_router(drafts_router)
app.include_router(notifications_router)


@app.get("/")
def read_root():
    return {
        "status": "online",
        "app": settings.APP_NAME,
        "environment": settings.ENVIRONMENT
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}
