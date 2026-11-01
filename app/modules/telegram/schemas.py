"""Schémas API de liaison Telegram."""

from datetime import datetime

from pydantic import BaseModel


class TelegramLinkCodeResponse(BaseModel):
    code: str
    expires_at: datetime
    instruction: str


class TelegramStatusResponse(BaseModel):
    available: bool
    connected: bool
    connected_at: datetime | None = None
