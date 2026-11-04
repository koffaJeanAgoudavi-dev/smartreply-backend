"""Réponses publiques de l'intégration Google Drive, sans aucun token."""

from typing import Literal

from pydantic import BaseModel


class DriveConnectResponse(BaseModel):
    authorization_url: str


class DriveStatusResponse(BaseModel):
    connected: bool
    ownership: Literal["client", "platform", "none"]
    sheet_state: Literal[
        "not_created",
        "ready",
        "disconnected",
        "oauth_revoked",
        "missing",
        "trashed",
        "ownership_changed",
        "access_revoked",
        "structure_invalid",
        "unavailable",
    ]
    pipeline_access: bool


class DriveDisconnectResponse(BaseModel):
    disconnected: bool
    pipeline_access_removed: bool
    manual_action_required: bool
