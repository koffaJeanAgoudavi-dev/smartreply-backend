"""Accorde au compte de service l'accès aux Sheets d'entreprise existants.

À lancer depuis la racine du dépôt ou dans un Render Shell :
    python scripts/share_company_sheets_with_service_account.py

Le script est idempotent : une permission writer déjà présente n'est pas recréée.
Il ne supprime jamais de Sheet et ne modifie aucune ligne PostgreSQL.
"""

import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import SessionLocal, engine  # noqa: E402
from app.modules.companies.models import Company  # noqa: E402
from app.modules.companies.service import (  # noqa: E402
    DriveConfigurationError,
    DriveOperationError,
    ensure_service_account_writer,
)


def share_existing_company_sheets() -> int:
    """Traite toutes les entreprises et retourne un code de sortie shell."""
    if engine is None:
        print("❌ DATABASE_URL non configurée.", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        companies = list(db.scalars(select(Company).order_by(Company.id)).all())

    if not companies:
        print("Aucune entreprise à rattraper.")
        return 0

    updated = 0
    already_configured = 0
    failures = 0
    for company in companies:
        try:
            changed = ensure_service_account_writer(company.sheet_id)
        except (DriveConfigurationError, DriveOperationError) as exc:
            failures += 1
            print(
                f"❌ Entreprise #{company.id} : {exc}",
                file=sys.stderr,
            )
            continue

        if changed:
            updated += 1
            print(f"✅ Entreprise #{company.id} : accès pipeline ajouté.")
        else:
            already_configured += 1
            print(f"✓ Entreprise #{company.id} : accès déjà présent.")

    print(
        "Résumé : "
        f"{updated} mise(s) à jour, "
        f"{already_configured} déjà conforme(s), "
        f"{failures} échec(s)."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(share_existing_company_sheets())
