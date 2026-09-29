"""Schémas Pydantic du module Gmail OAuth — Étape 3 (jamais de token dans les réponses)."""
from pydantic import BaseModel


class ConnectResponse(BaseModel):
    """Réponse de GET /api/v1/gmail/connect : URL de consentement Google à ouvrir."""

    authorization_url: str


class CallbackResponse(BaseModel):
    """Réponse de GET /api/v1/gmail/callback : confirmation, sans aucun token."""

    status: str = "connected"
    message: str


class StatusResponse(BaseModel):
    """Réponse de GET /api/v1/gmail/status : connecté ou non."""

    connected: bool
