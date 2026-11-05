"""Schémas stricts de la console admin, sans contenu ni secret externe."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator

ValidationStatus = Literal["pending", "validated"]
ConfigurationStatus = Literal["ready", "incomplete"]
ConnectionStatus = Literal["connected", "revoked", "missing"]
SheetStatus = Literal["configured", "missing"]
IncidentSeverity = Literal["critical", "warning"]
OperationalStatus = Literal["active", "suspended"]
AdminAuditEventType = Literal[
    "message_sent",
    "company_suspended",
    "company_resumed",
]
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


class AdminAccessStatusResponse(AdminIdentityResponse):
    unlocked: bool
    expires_at: datetime | None


class AdminUnlockRequest(BaseModel):
    code: SecretStr = Field(min_length=1, max_length=256)


class AdminUnlockResponse(BaseModel):
    unlocked: bool = True
    expires_at: datetime


class AdminMessageCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("title", "body", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Ce champ ne peut pas être vide")
        return value


class AdminMessageResponse(BaseModel):
    message_ref: str = Field(pattern=r"^INFO-[1-9][0-9]*$")
    company_id: int = Field(ge=1)
    title: str = Field(max_length=120)
    created_at: datetime


class AdminSuspensionCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    suspended_until: datetime | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Le motif ne peut pas être vide")
        return value


class AdminSuspensionResponse(BaseModel):
    company_id: int = Field(ge=1)
    operational_status: OperationalStatus
    suspended_at: datetime | None
    suspended_until: datetime | None
    suspension_reason: str | None = Field(default=None, max_length=500)


class AdminAuditItem(BaseModel):
    event_type: AdminAuditEventType
    label: str = Field(max_length=120)
    actor_email: str = Field(max_length=255)
    occurred_at: datetime


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
    operational_status: OperationalStatus
    suspended_until: datetime | None
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
    suspended_at: datetime | None
    suspension_reason: str | None = Field(default=None, max_length=500)
    gmail_connected_at: datetime | None
    gmail_updated_at: datetime | None
    drive_connected_at: datetime | None
    drive_updated_at: datetime | None
    telegram_connected_at: datetime | None
    profile: AdminProfileCompleteness
    audit_events: list[AdminAuditItem]


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
