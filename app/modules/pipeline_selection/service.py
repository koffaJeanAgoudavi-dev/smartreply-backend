"""Sélection équitable et plan réel/simulé du pipeline multi-tenant."""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import SessionLocal
from app.modules.companies.models import Company
from app.modules.gmail_oauth.models import GmailConnection
from app.modules.telegram.models import TelegramConnection

# 0x53524D54 = "SRMT". Le verrou est global à la base PostgreSQL.
MULTI_TENANT_PIPELINE_LOCK_KEY = 0x53524D54


@dataclass(frozen=True)
class CompanyRunPlan:
    """Métadonnées internes nécessaires à un futur traitement d'entreprise."""

    company_id: int
    user_id: int
    pipeline_activated_at: datetime | None
    pipeline_last_processed_at: datetime | None
    email_limit: int


@dataclass(frozen=True)
class PipelineRunBudget:
    """Budget monotone réutilisable par la PR 2 pour arrêter un passage à temps."""

    total_seconds: int
    started_monotonic: float = field(default_factory=time.monotonic)

    def has_time_remaining(self, *, now_monotonic: float | None = None) -> bool:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        return now - self.started_monotonic < max(0, self.total_seconds)


@dataclass(frozen=True)
class SimulationResult:
    """Plan public sûr : éligibles, autorisées et plafonds, sans donnée privée."""

    status: str
    company_ids: tuple[int, ...]
    processing_company_ids: tuple[int, ...]
    eligible_company_count: int
    processing_company_count: int
    simulation_company_count: int
    max_emails_cap: int
    processing_max_emails_cap: int
    max_emails_per_company: int
    time_budget_seconds: int

    def to_public_dict(self) -> dict[str, str | int | list[int]]:
        return {
            "status": self.status,
            "company_ids": list(self.company_ids),
            "processing_company_ids": list(self.processing_company_ids),
            "eligible_company_count": self.eligible_company_count,
            "processing_company_count": self.processing_company_count,
            "simulation_company_count": self.simulation_company_count,
            "max_emails_cap": self.max_emails_cap,
            "processing_max_emails_cap": self.processing_max_emails_cap,
            "max_emails_per_company": self.max_emails_per_company,
            "time_budget_seconds": self.time_budget_seconds,
        }


@dataclass
class PreparedSimulationRun:
    """Passage préparé avant le 202, avec verrou conservé pour l'arrière-plan."""

    result: SimulationResult
    plans: tuple[CompanyRunPlan, ...]
    lock_acquired: bool
    budget: PipelineRunBudget
    db: Session | None = field(default=None, repr=False)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def received_after_activation(
    received_at: datetime,
    pipeline_activated_at: datetime | None,
) -> bool:
    """Vrai uniquement après la coupure; une entreprise non activée est refusée."""
    if pipeline_activated_at is None:
        return False
    return _as_utc(received_at) > _as_utc(pipeline_activated_at)


def _configured_limits() -> tuple[int, int]:
    return (
        max(0, settings.MAX_EMAILS_PER_COMPANY_PER_RUN),
        max(0, settings.PIPELINE_RUN_TIME_BUDGET_SECONDS),
    )


def parse_allowed_company_ids(raw_value: str | None = None) -> frozenset[int]:
    """Parse l'allowlist CSV en mode fail-closed; vide signifie aucune entreprise."""
    raw = settings.PIPELINE_ALLOWED_COMPANY_IDS if raw_value is None else raw_value
    if not raw.strip():
        return frozenset()
    company_ids: set[int] = set()
    for value in raw.split(","):
        candidate = value.strip()
        if not candidate.isdecimal() or int(candidate) <= 0:
            raise ValueError("PIPELINE_ALLOWED_COMPANY_IDS invalide")
        company_ids.add(int(candidate))
    return frozenset(company_ids)


def select_eligible_company_plans(
    db: Session,
    *,
    max_emails_per_company: int,
) -> tuple[CompanyRunPlan, ...]:
    """Sélectionne les entreprises 3/3, les moins récentes en premier."""
    email_limit = max(0, max_emails_per_company)
    rows = db.execute(
        select(
            Company.id.label("company_id"),
            Company.user_id.label("user_id"),
            Company.pipeline_activated_at,
            Company.pipeline_last_processed_at,
        )
        .join(
            GmailConnection,
            GmailConnection.user_id == Company.user_id,
        )
        .join(
            TelegramConnection,
            TelegramConnection.user_id == Company.user_id,
        )
        .where(
            GmailConnection.status == "connected",
            func.length(func.trim(GmailConnection.encrypted_refresh_token)) > 0,
            func.length(func.trim(Company.sheet_id)) > 0,
        )
        .order_by(
            Company.pipeline_last_processed_at.asc().nullsfirst(),
            Company.id.asc(),
        )
    ).all()
    return tuple(
        CompanyRunPlan(
            company_id=row.company_id,
            user_id=row.user_id,
            pipeline_activated_at=(
                _as_utc(row.pipeline_activated_at)
                if row.pipeline_activated_at is not None
                else None
            ),
            pipeline_last_processed_at=(
                _as_utc(row.pipeline_last_processed_at)
                if row.pipeline_last_processed_at is not None
                else None
            ),
            email_limit=email_limit,
        )
        for row in rows
    )


def build_simulation_result(
    db: Session,
    *,
    max_emails_per_company: int | None = None,
    time_budget_seconds: int | None = None,
    allowed_company_ids: frozenset[int] | None = None,
) -> tuple[SimulationResult, tuple[CompanyRunPlan, ...]]:
    """Construit sans écriture le plan réel/simulé du prochain passage."""
    configured_max, configured_budget = _configured_limits()
    email_limit = (
        configured_max
        if max_emails_per_company is None
        else max(0, max_emails_per_company)
    )
    time_budget = (
        configured_budget
        if time_budget_seconds is None
        else max(0, time_budget_seconds)
    )
    allowed = frozenset() if allowed_company_ids is None else allowed_company_ids
    plans = select_eligible_company_plans(
        db,
        max_emails_per_company=email_limit,
    )
    company_ids = tuple(plan.company_id for plan in plans)
    processing_company_ids = tuple(
        plan.company_id for plan in plans if plan.company_id in allowed
    )
    result = SimulationResult(
        status="accepted",
        company_ids=company_ids,
        processing_company_ids=processing_company_ids,
        eligible_company_count=len(plans),
        processing_company_count=len(processing_company_ids),
        simulation_company_count=len(plans) - len(processing_company_ids),
        max_emails_cap=sum(plan.email_limit for plan in plans),
        processing_max_emails_cap=len(processing_company_ids) * email_limit,
        max_emails_per_company=email_limit,
        time_budget_seconds=time_budget,
    )
    return result, plans


def try_acquire_pipeline_lock(db: Session) -> bool:
    """Prend un verrou advisory de transaction, compatible avec le pooler Neon."""
    return bool(
        db.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
            {"lock_key": MULTI_TENANT_PIPELINE_LOCK_KEY},
        ).scalar_one()
    )


def prepare_multi_tenant_simulation(
    session_factory: Callable[[], Session] = SessionLocal,
) -> PreparedSimulationRun:
    """Prend le verrou et sélectionne le plan avant le 202."""
    max_emails, time_budget = _configured_limits()
    allowed_company_ids = parse_allowed_company_ids()
    budget = PipelineRunBudget(total_seconds=time_budget)
    db = session_factory()
    try:
        db.begin()
        if not try_acquire_pipeline_lock(db):
            db.rollback()
            db.close()
            return PreparedSimulationRun(
                result=SimulationResult(
                    status="already_running",
                    company_ids=(),
                    processing_company_ids=(),
                    eligible_company_count=0,
                    processing_company_count=0,
                    simulation_company_count=0,
                    max_emails_cap=0,
                    processing_max_emails_cap=0,
                    max_emails_per_company=max_emails,
                    time_budget_seconds=time_budget,
                ),
                plans=(),
                lock_acquired=False,
                budget=budget,
            )

        result, plans = build_simulation_result(
            db,
            max_emails_per_company=max_emails,
            time_budget_seconds=time_budget,
            allowed_company_ids=allowed_company_ids,
        )
        return PreparedSimulationRun(
            result=result,
            plans=plans,
            lock_acquired=True,
            budget=budget,
            db=db,
        )
    except Exception:
        db.rollback()
        db.close()
        raise
