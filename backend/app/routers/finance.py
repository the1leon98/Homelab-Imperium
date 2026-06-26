"""
Finanz-Router des Homelab-Imperiums.

Stellt REST-Endpunkte für die persönliche Finanzverwaltung bereit:
Transaktions-CRUD, Saldo-Berechnung, monatliche/jährliche Berichte,
Kategorie-Analysen und Budget-Vergleiche.

Alle Beträge werden als ``Decimal`` mit 2 Nachkommastellen verarbeitet
und als ``float`` im JSON serialisiert.

Verwendung::

    from app.routers.finance import router
    app.include_router(router, prefix="/api")
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Transaction
from app.schemas import (
    TransactionCreate,
    TransactionResponse,
    TransactionSummary,
    TransactionUpdate,
)
from app.services.finance import FinanceService

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.routers.finance")

# ═══════════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════════

router: APIRouter = APIRouter(tags=["Finanzen"])

# ═══════════════════════════════════════════════════════════════════════════════
# Dependency Injection — FinanceService (benötigt DB-Session pro Request)
# ═══════════════════════════════════════════════════════════════════════════════


def get_finance_service(
    db: Session = Depends(get_db),
) -> FinanceService:
    """
    Factory für den FinanceService — erhält pro Request eine
    frische Datenbank-Session.
    """
    return FinanceService(db)


# ═══════════════════════════════════════════════════════════════════════════════
# Transaktions-CRUD
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/transactions",
    response_model=TransactionResponse,
    status_code=201,
    summary="Transaktion erstellen",
    description="Erstellt eine neue Finanztransaktion (Einnahme oder Ausgabe). "
    "Validiert den Eingabe-Payload gegen das Pydantic-Schema.",
)
async def create_transaction(
    transaction: TransactionCreate,
    db: Session = Depends(get_db),
) -> TransactionResponse:
    """
    Erstellt eine neue Transaktion in der Datenbank.

    Der ``amount``-Wert wird als ``Decimal`` mit 2 Nachkommastellen
    gespeichert (kein Float-Genauigkeitsverlust).
    """
    logger.info(
        "POST /transactions: amount=%.2f, category=%r, expense=%s.",
        transaction.amount,
        transaction.category,
        transaction.is_expense,
    )

    try:
        new_tx: Transaction = Transaction(
            amount=Decimal(str(transaction.amount)),
            category=transaction.category,
            is_expense=transaction.is_expense,
            timestamp=transaction.timestamp,
            description=transaction.description,
            payment_method=transaction.payment_method,
            is_recurring=transaction.is_recurring,
        )
        db.add(new_tx)
        db.commit()
        db.refresh(new_tx)

        logger.info(
            "Transaktion erstellt: id=%d, amount=%.2f.",
            new_tx.id,
            new_tx.amount,
        )
        return new_tx

    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Erstellen der Transaktion.")
        raise HTTPException(
            status_code=500,
            detail=f"Transaktion konnte nicht erstellt werden: {exc}",
        ) from exc


@router.get(
    "/transactions",
    response_model=dict,
    summary="Transaktionen abrufen",
    description="Paginiert und gefiltert Transaktionen abrufen. "
    "Unterstützt Filter nach Kategorie, Typ, Datum und Volltextsuche.",
)
async def get_transactions(
    page: int = Query(default=1, ge=1, description="Seitennummer."),
    page_size: int = Query(default=50, ge=1, le=500, description="Einträge pro Seite."),
    category: Optional[str] = Query(default=None, description="Nur diese Kategorie."),
    expense_only: Optional[bool] = Query(
        default=None,
        description="True = nur Ausgaben, False = nur Einnahmen, None = alle.",
    ),
    start_date: Optional[date] = Query(default=None, description="Filter: ab Datum (inklusive)."),
    end_date: Optional[date] = Query(default=None, description="Filter: bis Datum (exklusive)."),
    search: Optional[str] = Query(default=None, description="Volltextsuche in Beschreibung."),
    db: Session = Depends(get_db),
) -> dict:
    """
    Paginierte Transaktionsliste mit 5 optionalen Filtern.
    """
    logger.info(
        "GET /transactions: page=%d, size=%d, cat=%s, exp=%s, "
        "start=%s, end=%s, search=%s.",
        page,
        page_size,
        category,
        expense_only,
        start_date,
        end_date,
        search,
    )

    try:
        svc: FinanceService = FinanceService(db)
        return svc.get_transactions(
            page=page,
            page_size=page_size,
            category=category,
            expense_only=expense_only,
            start_date=start_date,
            end_date=end_date,
            search=search,
        )

    except Exception as exc:
        logger.exception("Fehler beim Abrufen der Transaktionen.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler beim Abrufen: {exc}",
        ) from exc


@router.put(
    "/transactions/{transaction_id}",
    response_model=TransactionResponse,
    summary="Transaktion aktualisieren",
    description="Aktualisiert eine bestehende Transaktion (partielles Update).",
)
async def update_transaction(
    transaction_id: int,
    update: TransactionUpdate,
    db: Session = Depends(get_db),
) -> TransactionResponse:
    """
    Partielles Update einer Transaktion (PATCH-artig via PUT).

    Nur die im Body gesendeten Felder werden aktualisiert.
    """
    logger.info("PUT /transactions/%d.", transaction_id)

    try:
        tx: Transaction | None = (
            db.query(Transaction)
            .filter(Transaction.id == transaction_id)
            .first()
        )
        if not tx:
            raise HTTPException(
                status_code=404,
                detail=f"Transaktion {transaction_id} nicht gefunden.",
            )

        # Nur gesetzte Felder aktualisieren
        update_data: dict = update.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if field == "amount" and value is not None:
                value = Decimal(str(value))
            setattr(tx, field, value)

        db.commit()
        db.refresh(tx)
        logger.info("Transaktion %d aktualisiert.", transaction_id)
        return tx

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Aktualisieren.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler beim Aktualisieren: {exc}",
        ) from exc


@router.delete(
    "/transactions/{transaction_id}",
    summary="Transaktion löschen",
    description="Löscht eine Transaktion dauerhaft.",
    status_code=200,
)
async def delete_transaction(
    transaction_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """Löscht eine Transaktion anhand ihrer ID."""
    logger.info("DELETE /transactions/%d.", transaction_id)

    try:
        tx: Transaction | None = (
            db.query(Transaction)
            .filter(Transaction.id == transaction_id)
            .first()
        )
        if not tx:
            raise HTTPException(
                status_code=404,
                detail=f"Transaktion {transaction_id} nicht gefunden.",
            )

        db.delete(tx)
        db.commit()
        logger.info("Transaktion %d gelöscht.", transaction_id)
        return {
            "message": "Transaktion gelöscht.",
            "id": transaction_id,
        }

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Löschen.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler beim Löschen: {exc}",
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# Finanzberichte & Analysen
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/finance/balance",
    response_model=TransactionSummary,
    summary="Aktueller Saldo",
    description="Gesamtbilanz über ALLE Transaktionen: "
    "Einnahmen, Ausgaben und Netto-Saldo.",
)
async def get_balance(
    svc: FinanceService = Depends(get_finance_service),
) -> TransactionSummary:
    """
    Gesamtbilanz über alle Zeiten.

    Returns:
        ``TransactionSummary`` mit total_income, total_expenses,
        net_balance und transaction_count.
    """
    logger.info("GET /finance/balance.")

    try:
        balance = svc.get_balance()
        return TransactionSummary(
            total_income=balance.total_income_all_time,
            total_expenses=balance.total_expenses_all_time,
            net_balance=balance.current_balance,
            transaction_count=balance.transaction_count,
        )

    except Exception as exc:
        logger.exception("Fehler bei Saldo-Berechnung.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler bei Saldo-Berechnung: {exc}",
        ) from exc


@router.get(
    "/finance/summary/{year}/{month}",
    summary="Monatsübersicht",
    description="Einnahmen, Ausgaben und Netto für einen bestimmten Monat "
    "inklusive Top-Ausgabenkategorie.",
)
async def get_monthly_summary(
    year: int,
    month: int,
    svc: FinanceService = Depends(get_finance_service),
) -> dict:
    """
    Monatsübersicht mit Top-Kategorie.

    Args:
        year: Jahr (z.B. 2026).
        month: Monat 1–12.
    """
    logger.info("GET /finance/summary/%d/%d.", year, month)

    if not (1 <= month <= 12):
        raise HTTPException(
            status_code=422,
            detail="Monat muss zwischen 1 und 12 liegen.",
        )

    try:
        summary = svc.get_monthly_summary(year=year, month=month)
        return {
            "year": summary.year,
            "month": summary.month,
            "total_income": float(summary.total_income),
            "total_expenses": float(summary.total_expenses),
            "net_balance": float(summary.net_balance),
            "transaction_count": summary.transaction_count,
            "top_expense_category": summary.top_expense_category,
            "top_expense_amount": float(summary.top_expense_amount),
        }

    except Exception as exc:
        logger.exception("Fehler bei Monatsübersicht.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.get(
    "/finance/categories",
    summary="Kategorie-Analyse",
    description="Ausgaben aufgeschlüsselt nach Kategorie mit Prozent-Anteil.",
)
async def get_category_breakdown(
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    svc: FinanceService = Depends(get_finance_service),
) -> list[dict]:
    """
    Kategorie-Auswertung für einen optionalen Zeitraum.

    Returns:
        Liste mit category, total_amount, percentage, transaction_count.
    """
    logger.info(
        "GET /finance/categories: start=%s, end=%s.", start_date, end_date
    )

    try:
        breakdown = svc.get_category_breakdown(
            start_date=start_date,
            end_date=end_date,
        )
        return [
            {
                "category": b.category,
                "total_amount": float(b.total_amount),
                "transaction_count": b.transaction_count,
                "percentage": float(b.percentage),
            }
            for b in breakdown
        ]

    except Exception as exc:
        logger.exception("Fehler bei Kategorie-Analyse.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.get(
    "/finance/trends",
    summary="Monatliche Trends",
    description="Zeitreihe der monatlichen Einnahmen und Ausgaben "
    "(letzte 6 Monate).",
)
async def get_monthly_trends(
    months: int = Query(default=6, ge=1, le=24, description="Anzahl Monate."),
    svc: FinanceService = Depends(get_finance_service),
) -> list[dict]:
    """
    Monatliche Einnahmen-/Ausgaben-Trends als Zeitreihe.

    Returns:
        Liste mit period, income, expenses, net.
    """
    logger.info("GET /finance/trends: months=%d.", months)

    try:
        trends = svc.get_monthly_trends(months=months)
        return [
            {
                "period": t.period,
                "income": float(t.income),
                "expenses": float(t.expenses),
                "net": float(t.net),
            }
            for t in trends
        ]

    except Exception as exc:
        logger.exception("Fehler bei Trend-Analyse.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.get(
    "/finance/budget",
    summary="Budget-Vergleich",
    description="Vergleicht Budget-Vorgaben mit tatsächlichen Ausgaben "
    "für einen Monat. Zeigt Überschreitungen an.",
)
async def get_budget_comparison(
    year: int = Query(..., description="Jahr."),
    month: int = Query(..., ge=1, le=12, description="Monat."),
    svc: FinanceService = Depends(get_finance_service),
) -> list[dict]:
    """
    Budget vs. Ist-Vergleich.

    Die Budget-Vorgaben werden als Query-Parameter übergeben
    (z.B. ``?miete=800&lebensmittel=400&mobilitaet=200``).
    In einer erweiterten Version könnten diese aus einer
    separaten Budget-Tabelle kommen.
    """
    logger.info("GET /finance/budget: %d-%02d.", year, month)

    try:
        # TODO: Budgets aus separater DB-Tabelle oder Config lesen
        # Für jetzt: Beispiel-Budgets
        budgets: dict[str, Decimal] = {
            "Miete": Decimal("800.00"),
            "Lebensmittel": Decimal("400.00"),
            "Mobilität": Decimal("200.00"),
            "Internet/Telefon": Decimal("60.00"),
            "Versicherung": Decimal("150.00"),
            "Freizeit": Decimal("250.00"),
        }

        comparisons = svc.get_budget_comparison(budgets, year, month)
        return [
            {
                "category": c.category,
                "budget_amount": float(c.budget_amount),
                "actual_amount": float(c.actual_amount),
                "remaining": float(c.remaining),
                "percentage_used": float(c.percentage_used),
                "is_over_budget": c.is_over_budget,
            }
            for c in comparisons
        ]

    except Exception as exc:
        logger.exception("Fehler bei Budget-Vergleich.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.get(
    "/finance/yearly/{year}",
    summary="Jahresübersicht",
    description="Vollständige Jahresübersicht mit Monatsvergleich, "
    "Top-Kategorie und Durchschnittswerten.",
)
async def get_yearly_summary(
    year: int,
    svc: FinanceService = Depends(get_finance_service),
) -> dict:
    """
    Jahresübersicht mit allen Monats-Details.

    Returns:
        Dict mit Jahressummen und monatlichem Breakdown.
    """
    logger.info("GET /finance/yearly/%d.", year)

    try:
        return svc.get_yearly_summary(year=year)

    except Exception as exc:
        logger.exception("Fehler bei Jahresübersicht.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.get(
    "/finance/categories/list",
    summary="Alle Kategorien",
    description="Liste aller jemals verwendeten Kategorien.",
)
async def get_all_categories(
    db: Session = Depends(get_db),
) -> list[str]:
    """Eindeutige Kategorie-Liste (alphabetisch)."""
    logger.debug("GET /finance/categories/list.")

    try:
        svc: FinanceService = FinanceService(db)
        return svc.get_all_categories()

    except Exception as exc:
        logger.exception("Fehler beim Abrufen der Kategorien.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc

async def get_transactions(limit: int = 100, db: Session = Depends(get_db)):
    """
    Liefert die Transaktionshistorie für Visualisierungen.
    """
    raise NotImplementedError("Wird in Phase 4 implementiert.")