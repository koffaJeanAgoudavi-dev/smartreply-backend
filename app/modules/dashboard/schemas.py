"""Schémas publics du dashboard, sans identifiant ni donnée sensible."""

from datetime import date, datetime

from pydantic import BaseModel, Field


class DashboardMetrics(BaseModel):
    analyzed_emails: int = Field(ge=0)
    processed_emails: int = Field(ge=0)
    ignored_emails: int = Field(ge=0)
    pending_drafts: int = Field(ge=0)


class TimeSeriesPoint(BaseModel):
    day: date
    count: int = Field(ge=0)


class BreakdownItem(BaseModel):
    key: str
    label: str
    count: int = Field(ge=0)


class ActivityItem(BaseModel):
    event_type: str
    label: str
    occurred_at: datetime


class DashboardOverviewResponse(BaseModel):
    metrics: DashboardMetrics
    volume: list[TimeSeriesPoint]
    categories: list[BreakdownItem]
    priorities: list[BreakdownItem]
    recent_activity: list[ActivityItem]
    history_started: bool


class RecentEmailItem(BaseModel):
    sender: str = Field(max_length=255)
    subject: str = Field(max_length=500)
    received_at: datetime | None
    category: str = Field(max_length=64)
    priority: str = Field(max_length=32)
    status: str = Field(max_length=32)


class RecentEmailsResponse(BaseModel):
    emails: list[RecentEmailItem]


class DashboardEmailItem(BaseModel):
    email_id: str = Field(pattern=r"^EM-[1-9][0-9]*$")
    sender_name: str = Field(max_length=160)
    sender_email: str = Field(max_length=320)
    subject: str = Field(max_length=500)
    received_at: datetime | None
    summary: str
    category: str = Field(max_length=64)
    priority: str = Field(max_length=32)
    status: str = Field(max_length=32)


class DashboardEmailPagination(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class DashboardEmailsResponse(BaseModel):
    items: list[DashboardEmailItem]
    pagination: DashboardEmailPagination
