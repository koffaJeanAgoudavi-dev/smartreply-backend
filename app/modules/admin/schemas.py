"""Schémas stricts de la console admin, sans contenu ni secret externe."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ValidationStatus = Literal["pending", "validated"]
ConfigurationStatus = Literal["ready", "incomplete"]
ConnectionStatus = Literal["connected", "revoked", "missing"]
SheetStatus = Literal["configured", "missing"]
IncidentSeverity = Literal["critical", "warning"]
IncidentType = Literal[
    "gmail_revoked",
    "drive_revoked",
    "processing_failed",
    "processing_stale",
    "notification_blocked",
    "sheet_sync_pending",
]


class AdminIdentityResponse(BaseModel):
    user_id: int = Field(ge=1)
    email: str = Field(max_length=255)


class AdminOverviewMetrics(BaseModel):
    users: int = Field(ge=0)
    companies: int = Field(ge=0)
    validated_companies: int = Field(ge=0)
    pending_companies: int = Field(ge=0)
    ready_companies: int = Field(ge=0)
    incomplete_companies: int = Field(ge=0)
    gmail_issues: int = Field(ge=0)
    drive_issues: int = Field(ge=0)
    telegram_connected: int = Field(ge=0)
    processed_emails: int = Field(ge=0)
    failed_emails: int = Field(ge=0)
    pending_drafts: int = Field(ge=0)
    active_incidents: int = Field(ge=0)


class AdminOverviewResponse(BaseModel):
    metrics: AdminOverviewMetrics
    last_processing_at: datetime | None
    generated_at: datetime


class AdminCompanySummary(BaseModel):
    company_id: int = Field(ge=1)
    name: str = Field(max_length=255)
    sector: str = Field(max_length=255)
    owner_email: str = Field(max_length=255)
    created_at: datetime
    validation_status: ValidationStatus
    configuration_status: ConfigurationStatus
    gmail_status: ConnectionStatus
    drive_status: ConnectionStatus
    sheet_status: SheetStatus
    telegram_connected: bool
    pipeline_activated_at: datetime | None
    pipeline_last_processed_at: datetime | None
    processed_emails: int = Field(ge=0)
    failed_emails: int = Field(ge=0)
    pending_drafts: int = Field(ge=0)


class AdminPagination(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class AdminCompanyListResponse(BaseModel):
    items: list[AdminCompanySummary]
    pagination: AdminPagination


class AdminProfileCompleteness(BaseModel):
    configured_fields: int = Field(ge=0, le=6)
    total_fields: int = 6
    response_tone: Literal["professional", "friendly", "direct", "warm"]


class AdminCompanyDetailResponse(AdminCompanySummary):
    updated_at: datetime
    sheet_owner_mode: Literal["platform", "client"]
    gmail_connected_at: datetime | None
    gmail_updated_at: datetime | None
    drive_connected_at: datetime | None
    drive_updated_at: datetime | None
    telegram_connected_at: datetime | None
    profile: AdminProfileCompleteness


class AdminIncidentItem(BaseModel):
    incident_ref: str = Field(max_length=80)
    incident_type: IncidentType
    label: str = Field(max_length=160)
    severity: IncidentSeverity
    company_id: int = Field(ge=1)
    company_name: str = Field(max_length=255)
    code: str | None = Field(default=None, max_length=64)
    attempts: int | None = Field(default=None, ge=0, le=3)
    occurred_at: datetime


class AdminIncidentListResponse(BaseModel):
    items: list[AdminIncidentItem]
    pagination: AdminPagination
