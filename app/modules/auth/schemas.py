"""Schémas Pydantic (corps de requête / réponse) du module auth — Étape 2."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    """Corps de POST /api/v1/auth/register."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    """Corps de POST /api/v1/auth/login."""

    email: EmailStr
    password: str


class UserOut(BaseModel):
    """Utilisateur exposé à l'API (jamais le mot de passe haché)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    created_at: datetime


class Token(BaseModel):
    """Réponse de POST /api/v1/auth/login."""

    access_token: str
    token_type: str = "bearer"


class RegisterResponse(BaseModel):
    """Réponse de POST /api/v1/auth/register (201)."""

    user: UserOut
    access_token: str
    token_type: str = "bearer"
