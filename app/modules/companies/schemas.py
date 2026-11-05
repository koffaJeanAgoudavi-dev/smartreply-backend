"""Schémas d'entrée et de sortie de l'API Entreprises."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CompanyCreate(BaseModel):
    nom_entreprise: str = Field(
        min_length=1,
        max_length=255,
        description="Nom officiel de l'entreprise",
    )
    secteur: str = Field(
        min_length=1,
        max_length=255,
        description="Secteur d'activité de l'entreprise",
    )

    @field_validator("nom_entreprise", "secteur", mode="before")
    @classmethod
    def strip_and_require_text(cls, value: object) -> object:
        """Nettoie les espaces et refuse les valeurs visuellement vides."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Ce champ ne peut pas être vide")
        return value


ResponseTone = Literal["professional", "friendly", "direct", "warm"]

PROFILE_FIELD_MAX_LENGTHS = {
    "business_description": 1000,
    "products_services": 1500,
    "business_hours": 500,
    "response_signature": 500,
    "business_rules": 1500,
    "response_instructions": 1000,
}


class CompanySettingsUpdate(BaseModel):
    """Préférences modifiables sans exposer le nom, le Sheet ou le statut."""

    model_config = ConfigDict(extra="forbid")

    secteur: str | None = Field(default=None, min_length=1, max_length=255)
    response_tone: ResponseTone | None = None
    business_description: str | None = Field(default=None, max_length=1000)
    products_services: str | None = Field(default=None, max_length=1500)
    business_hours: str | None = Field(default=None, max_length=500)
    response_signature: str | None = Field(default=None, max_length=500)
    business_rules: str | None = Field(default=None, max_length=1500)
    response_instructions: str | None = Field(default=None, max_length=1000)

    @field_validator("secteur", mode="before")
    @classmethod
    def strip_sector(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Ce champ ne peut pas être vide")
        return value

    @field_validator(*PROFILE_FIELD_MAX_LENGTHS, mode="before")
    @classmethod
    def strip_profile_text(cls, value: object) -> object:
        """Normalise le profil ; une chaîne vide permet d'effacer un champ."""
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def require_explicit_non_null_update(self) -> "CompanySettingsUpdate":
        if not self.model_fields_set:
            raise ValueError("Au moins un paramètre doit être fourni")
        non_nullable_fields = {
            "secteur",
            "response_tone",
            *PROFILE_FIELD_MAX_LENGTHS,
        }
        if any(
            field_name in self.model_fields_set
            and getattr(self, field_name) is None
            for field_name in non_nullable_fields
        ):
            raise ValueError("Un paramètre fourni ne peut pas être nul")
        return self


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    nom_entreprise: str
    secteur: str
    response_tone: ResponseTone
    business_description: str
    products_services: str
    business_hours: str
    response_signature: str
    business_rules: str
    response_instructions: str
    sheet_id: str
    sheet_owner_mode: Literal["platform", "client"]
    created_at: datetime
    updated_at: datetime


class CompanyValidationStatusResponse(BaseModel):
    validation_status: Literal["pending", "validated"]


class PipelineAccessResponse(BaseModel):
    pipeline_access: bool
    permission_updated: bool
