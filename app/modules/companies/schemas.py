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


class CompanySettingsUpdate(BaseModel):
    """Préférences modifiables sans exposer le nom, le Sheet ou le statut."""

    model_config = ConfigDict(extra="forbid")

    secteur: str | None = Field(default=None, min_length=1, max_length=255)
    response_tone: ResponseTone | None = None

    @field_validator("secteur", mode="before")
    @classmethod
    def strip_sector(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Ce champ ne peut pas être vide")
        return value

    @model_validator(mode="after")
    def require_explicit_non_null_update(self) -> "CompanySettingsUpdate":
        if not self.model_fields_set:
            raise ValueError("Au moins un paramètre doit être fourni")
        if "secteur" in self.model_fields_set and self.secteur is None:
            raise ValueError("Le secteur ne peut pas être nul")
        if "response_tone" in self.model_fields_set and self.response_tone is None:
            raise ValueError("Le ton ne peut pas être nul")
        return self


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    nom_entreprise: str
    secteur: str
    response_tone: ResponseTone
    sheet_id: str
    sheet_owner_mode: Literal["platform", "client"]
    created_at: datetime
    updated_at: datetime


class CompanyValidationStatusResponse(BaseModel):
    validation_status: Literal["pending", "validated"]


class PipelineAccessResponse(BaseModel):
    pipeline_access: bool
    permission_updated: bool
