"""Schémas publics du formulaire de contact."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class ContactReason(str, Enum):
    PRODUCT_INFORMATION = "Information sur le produit"
    USAGE_SUPPORT = "Utilisation et assistance"
    OFFER_BILLING = "Offre et facturation"
    PARTNERSHIP = "Partenariat"
    OTHER = "Autre demande"


class ContactRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    subject: str = Field(min_length=2, max_length=160)
    reason: ContactReason
    message: str = Field(min_length=10, max_length=5000)

    @field_validator("full_name", "subject", "message", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("full_name", "subject")
    @classmethod
    def reject_header_line_breaks(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Les retours à la ligne ne sont pas autorisés")
        return value


class ContactResponse(BaseModel):
    status: Literal["sent"] = "sent"
