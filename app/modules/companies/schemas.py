"""Schémas d'entrée et de sortie de l'API Entreprises."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    nom_entreprise: str
    secteur: str
    sheet_id: str
    created_at: datetime
    updated_at: datetime
