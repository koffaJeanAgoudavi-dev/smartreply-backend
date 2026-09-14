from fastapi import FastAPI
from app.core.config import settings

app = FastAPI(
    title=settings.APP_NAME,
    description="Backend API pour SmartReply Agent SaaS",
    version="1.0.0"
)

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
