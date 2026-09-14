from fastapi import APIRouter, Request, Header, HTTPException, status
from app.core.config import settings
from app.modules.telegram.service import process_telegram_update

router = APIRouter(prefix="/api/v1/telegram", tags=["Telegram"])

@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(None)
):
    # Sécurité : Vérification du secret token si configuré
    if settings.TELEGRAM_WEBHOOK_SECRET and x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized webhook call"
        )
    
    update = await request.json()
    await process_telegram_update(update)
    return {"status": "ok"}
