"""
Finanzdienst des Homelab-Imperiums.

Implementiert mathematische Aggregationen und SQLAlchemy-Abfragen für:
- Saldo-Berechnung (aktuell, monatlich, jährlich)
- Einnahmen-/Ausgaben-Analyse nach Kategorie und Zeitraum
- Budget-Vergleiche und prozentuale Verbrauchs-Metriken
- Trend-Analysen mit rollierenden Zeitfenstern
- Paginierte Transaktionshistorien mit Filterung

Verwendung::

    from app.services.finance import FinanceService
    from app.database import get_db_context

    with get_db_context() as db:
        svc = FinanceService(db)
        summary = svc.get_monthly_summary(2026, 6)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, case, extract, func, or_, text
from sqlalchemy.orm import Session

from app.models import Transaction

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger(
    "homelab_imperium.services.finance"
)


# ═══════════════════════════════════════════════════════════════════════════════
# Datenklassen für Finanz-Reports
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class BalanceSummary:
    """Zusammenfassung der aktuellen finanziellen Lage."""

    current_balance: Decimal = Decimal("0.00")
    total_income_all_time: Decimal = Decimal("0.00")
    total_expenses_all_time: Decimal = Decimal("0.00")
    transaction_count: int = 0


@dataclass
class MonthlySummary:
    """Einnahmen- und Ausgaben-Übersicht für einen Monat."""

    year: int
    month: int
    total_income: Decimal = Decimal("0.00")
    total_expenses: Decimal = Decimal("0.00")
    net_balance: Decimal = Decimal("0.00")
    transaction_count: int = 0
    top_expense_category: str = ""
    top_expense_amount: Decimal = Decimal("0.00")


@dataclass
class CategoryBreakdown:
    """Aufschlüsselung der Ausgaben pro Kategorie."""

    category: str
    total_amount: Decimal = Decimal("0.00")
    transaction_count: int = 0
    percentage: Decimal = Decimal("0.00")
    is_expense: bool = True


@dataclass
class BudgetComparison:
    """Vergleich Budget vs. Ist-Ausgaben für eine Kategorie."""

    category: str
    budget_amount: Decimal = Decimal("0.00")
    actual_amount: Decimal = Decimal("0.00")
    remaining: Decimal = Decimal("0.00")
    percentage_used: Decimal = Decimal("0.00")
    is_over_budget: bool = False


@dataclass
class TrendPoint:
    """Ein Datenpunkt in einer Zeitreihe."""

    period: str  # z.B. "2026-06", "2026-KW24"
    income: Decimal = Decimal("0.00")
    expenses: Decimal = Decimal("0.00")
    net: Decimal = Decimal("0.00")


# ═══════════════════════════════════════════════════════════════════════════════
# FinanceService
# ═══════════════════════════════════════════════════════════════════════════════


class FinanceService:
    """
    Finanzmathematischer Dienst mit SQLAlchemy-basierten Abfragen.

    Alle Methoden arbeiten mit ``Decimal`` für exakte Geldbeträge.
    Zeitangaben werden als UTC-naive ``datetime``-Objekte behandelt.
    """

    def __init__(self, db: Session) -> None:
        """
        Args:
            db: SQLAlchemy-Datenbank-Session (aus ``get_db`` oder
                ``get_db_context``).
        """
        self.db: Session = db

    # ──────────────────────────────────────────────────────────────────────
    # Saldo & Gesamtbilanz
    # ──────────────────────────────────────────────────────────────────────

    def get_balance(self) -> BalanceSummary:
        """
        Berechnet die Gesamtbilanz über ALLE Transaktionen.

        Returns:
            ``BalanceSummary`` mit aktuellem Saldo und Gesamtzählern.
        """
        logger.debug("Berechne Gesamtbilanz...")

        income_result = (
            self.db.query(
                func.coalesce(func.sum(Transaction.amount), 0)
            )
            .filter(Transaction.is_expense == False)
            .scalar()
        )

        expense_result = (
            self.db.query(
                func.coalesce(func.sum(Transaction.amount), 0)
            )
            .filter(Transaction.is_expense == True)
            .scalar()
        )

        count: int = self.db.query(func.count(Transaction.id)).scalar() or 0

        total_income: Decimal = Decimal(str(income_result or 0))
        total_expenses: Decimal = Decimal(str(expense_result or 0))
        balance: Decimal = total_income - total_expenses

        logger.info(
            "Gesamtbilanz: Einnahmen=%.2f €, Ausgaben=%.2f €, "
            "Saldo=%.2f € (%d Transaktionen).",
            total_income,
            total_expenses,
            balance,
            count,
        )

        return BalanceSummary(
            current_balance=balance,
            total_income_all_time=total_income,
            total_expenses_all_time=total_expenses,
            transaction_count=count,
        )

    def get_balance_for_period(
        self,
        start_date: date,
        end_date: date,
    ) -> BalanceSummary:
        """
        Berechnet die Bilanz für einen bestimmten Zeitraum.

        Args:
            start_date: Beginn des Zeitraums (inklusive).
            end_date: Ende des Zeitraums (exklusive).

        Returns:
            ``BalanceSummary`` für den angegebenen Zeitraum.
        """
        logger.debug(
            "Berechne Bilanz für Zeitraum: %s → %s", start_date, end_date
        )

        start_dt: datetime = datetime.combine(start_date, datetime.min.time())
        end_dt: datetime = datetime.combine(end_date, datetime.min.time())

        income_result = (
            self.db.query(
                func.coalesce(func.sum(Transaction.amount), 0)
            )
            .filter(
                Transaction.is_expense == False,
                Transaction.timestamp >= start_dt,
                Transaction.timestamp < end_dt,
            )
            .scalar()
        )

        expense_result = (
            self.db.query(
                func.coalesce(func.sum(Transaction.amount), 0)
            )
            .filter(
                Transaction.is_expense == True,
                Transaction.timestamp >= start_dt,
                Transaction.timestamp < end_dt,
            )
            .scalar()
        )

        count: int = (
            self.db.query(func.count(Transaction.id))
            .filter(
                Transaction.timestamp >= start_dt,
                Transaction.timestamp < end_dt,
            )
            .scalar()
            or 0
        )

        total_income: Decimal = Decimal(str(income_result or 0))
        total_expenses: Decimal = Decimal(str(expense_result or 0))

        return BalanceSummary(
            current_balance=total_income - total_expenses,
            total_income_all_time=total_income,
            total_expenses_all_time=total_expenses,
            transaction_count=count,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Monats-Übersicht
    # ──────────────────────────────────────────────────────────────────────

    def get_monthly_summary(
        self,
        year: int,
        month: int,
    ) -> MonthlySummary:
        """
        Erstellt eine detaillierte Monatsübersicht.

        Args:
            year: Jahr (z.B. 2026).
            month: Monat 1–12.

        Returns:
            ``MonthlySummary`` mit Einnahmen, Ausgaben, Netto und
            Top-Ausgabenkategorie.
        """
        logger.debug("Erstelle Monatsübersicht: %04d-%02d", year, month)

        # Start- und End-Zeitstempel für den Monat
        start_dt: datetime = datetime(year, month, 1, 0, 0, 0)
        if month == 12:
            end_dt = datetime(year + 1, 1, 1, 0, 0, 0)
        else:
            end_dt = datetime(year, month + 1, 1, 0, 0, 0)

        # Einnahmen
        income_result = (
            self.db.query(
                func.coalesce(func.sum(Transaction.amount), 0)
            )
            .filter(
                Transaction.is_expense == False,
                Transaction.timestamp >= start_dt,
                Transaction.timestamp < end_dt,
            )
            .scalar()
        )

        # Ausgaben
        expense_result = (
            self.db.query(
                func.coalesce(func.sum(Transaction.amount), 0)
            )
            .filter(
                Transaction.is_expense == True,
                Transaction.timestamp >= start_dt,
                Transaction.timestamp < end_dt,
            )
            .scalar()
        )

        # Transaktionsanzahl
        count: int = (
            self.db.query(func.count(Transaction.id))
            .filter(
                Transaction.timestamp >= start_dt,
                Transaction.timestamp < end_dt,
            )
            .scalar()
            or 0
        )

        # Top-Ausgabenkategorie (höchste Summe im Monat)
        top_category = (
            self.db.query(
                Transaction.category,
                func.sum(Transaction.amount).label("total"),
            )
            .filter(
                Transaction.is_expense == True,
                Transaction.timestamp >= start_dt,
                Transaction.timestamp < end_dt,
            )
            .group_by(Transaction.category)
            .order_by(text("total DESC"))
            .first()
        )

        total_income: Decimal = Decimal(str(income_result or 0))
        total_expenses: Decimal = Decimal(str(expense_result or 0))
        net: Decimal = total_income - total_expenses

        top_cat_name: str = ""
        top_cat_amount: Decimal = Decimal("0.00")
        if top_category:
            top_cat_name = top_category[0]
            top_cat_amount = Decimal(str(top_category[1]))

        logger.info(
            "Monatsübersicht %04d-%02d: +%.2f € / -%.2f € / =%.2f € "
            "(%d Transaktionen, Top-Kat: %s).",
            year,
            month,
            total_income,
            total_expenses,
            net,
            count,
            top_cat_name or "—",
        )

        return MonthlySummary(
            year=year,
            month=month,
            total_income=total_income,
            total_expenses=total_expenses,
            net_balance=net,
            transaction_count=count,
            top_expense_category=top_cat_name,
            top_expense_amount=top_cat_amount,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Kategorie-Analyse
    # ──────────────────────────────────────────────────────────────────────

    def get_category_breakdown(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        expense_only: bool = True,
    ) -> list[CategoryBreakdown]:
        """
        Analysiert die Ausgaben (oder Einnahmen) pro Kategorie.

        Args:
            start_date: Optionaler Zeitraum-Beginn.
            end_date: Optionaler Zeitraum-Ende.
            expense_only: ``True`` = nur Ausgaben, ``False`` = auch Einnahmen.

        Returns:
            Liste von ``CategoryBreakdown``, absteigend nach Betrag sortiert.
        """
        logger.debug(
            "Erstelle Kategorie-Aufschlüsselung (expense_only=%s)...",
            expense_only,
        )

        query = self.db.query(
            Transaction.category,
            Transaction.is_expense,
            func.sum(Transaction.amount).label("total"),
            func.count(Transaction.id).label("count"),
        )

        # Zeitfilter
        if start_date:
            start_dt: datetime = datetime.combine(
                start_date, datetime.min.time()
            )
            query = query.filter(Transaction.timestamp >= start_dt)
        if end_date:
            end_dt: datetime = datetime.combine(
                end_date, datetime.min.time()
            )
            query = query.filter(Transaction.timestamp < end_dt)

        # Nur Ausgaben?
        if expense_only:
            query = query.filter(Transaction.is_expense == True)

        rows = (
            query.group_by(Transaction.category, Transaction.is_expense)
            .order_by(text("total DESC"))
            .all()
        )

        # Gesamtsumme für Prozentberechnung
        total_amount: Decimal = Decimal(
            str(sum(Decimal(str(row[2])) for row in rows) or 0)
        )

        breakdown: list[CategoryBreakdown] = []
        for row in rows:
            cat_amount: Decimal = Decimal(str(row[2]))
            pct: Decimal = (
                (cat_amount / total_amount * 100)
                if total_amount > 0
                else Decimal("0.00")
            )
            breakdown.append(
                CategoryBreakdown(
                    category=row[0],
                    total_amount=cat_amount,
                    transaction_count=row[3],
                    percentage=pct.quantize(Decimal("0.01")),
                    is_expense=row[1],
                )
            )

        logger.info(
            "%d Kategorien analysiert (Gesamt: %.2f €).",
            len(breakdown),
            total_amount,
        )
        return breakdown

    def get_budget_comparison(
        self,
        budgets: dict[str, Decimal],
        year: int,
        month: int,
    ) -> list[BudgetComparison]:
        """
        Vergleicht Budget-Vorgaben mit den tatsächlichen Ausgaben.

        Args:
            budgets: Dictionary mit ``{kategorie: budget_betrag}``.
            year, month: Vergleichsmonat.

        Returns:
            Liste von ``BudgetComparison`` mit Abweichungen.
        """
        logger.debug(
            "Budget-Vergleich für %04d-%02d (%d Budgets).",
            year,
            month,
            len(budgets),
        )

        # Tatsächliche Ausgaben pro Kategorie für den Monat abrufen
        start_dt: datetime = datetime(year, month, 1, 0, 0, 0)
        if month == 12:
            end_dt = datetime(year + 1, 1, 1, 0, 0, 0)
        else:
            end_dt = datetime(year, month + 1, 1, 0, 0, 0)

        actual_rows = (
            self.db.query(
                Transaction.category,
                func.sum(Transaction.amount).label("total"),
            )
            .filter(
                Transaction.is_expense == True,
                Transaction.timestamp >= start_dt,
                Transaction.timestamp < end_dt,
                Transaction.category.in_(list(budgets.keys())),
            )
            .group_by(Transaction.category)
            .all()
        )

        # Mapping: category → actual_amount
        actual_map: dict[str, Decimal] = {
            row[0]: Decimal(str(row[1])) for row in actual_rows
        }

        comparisons: list[BudgetComparison] = []
        for category, budget_amount in budgets.items():
            actual: Decimal = actual_map.get(category, Decimal("0.00"))
            remaining: Decimal = budget_amount - actual
            pct_used: Decimal = (
                (actual / budget_amount * 100)
                if budget_amount > 0
                else Decimal("0.00")
            )

            comparisons.append(
                BudgetComparison(
                    category=category,
                    budget_amount=budget_amount,
                    actual_amount=actual,
                    remaining=remaining,
                    percentage_used=pct_used.quantize(Decimal("0.1")),
                    is_over_budget=actual > budget_amount,
                )
            )

        # Sortieren: Überschrittene Budgets zuerst
        comparisons.sort(key=lambda c: (not c.is_over_budget, -c.percentage_used))

        over_count: int = sum(1 for c in comparisons if c.is_over_budget)
        if over_count > 0:
            logger.warning(
                "%d/%d Budgets überschritten!",
                over_count,
                len(comparisons),
            )

        return comparisons

    # ──────────────────────────────────────────────────────────────────────
    # Trends & Zeitreihen
    # ──────────────────────────────────────────────────────────────────────

    def get_monthly_trends(
        self,
        months: int = 6,
    ) -> list[TrendPoint]:
        """
        Erstellt eine Zeitreihe der monatlichen Einnahmen und Ausgaben.

        Optimiert für PostgreSQL mit ``date_trunc`` und
        ``extract(year/month)``.

        Args:
            months: Anzahl der zurückliegenden Monate (Default: 6).

        Returns:
            Liste von ``TrendPoint``, chronologisch sortiert.
        """
        logger.debug("Erstelle Monats-Trends (%d Monate)...", months)

        # Berechne Start-Datum
        today: date = date.today()
        start_month: int = today.month - months + 1
        start_year: int = today.year
        while start_month <= 0:
            start_month += 12
            start_year -= 1
        start_dt: datetime = datetime(start_year, start_month, 1, 0, 0, 0)

        # Abfrage: Summe pro Monat, getrennt nach Einnahmen/Ausgaben
        rows = (
            self.db.query(
                extract("year", Transaction.timestamp).label("year"),
                extract("month", Transaction.timestamp).label("month"),
                Transaction.is_expense,
                func.sum(Transaction.amount).label("total"),
            )
            .filter(Transaction.timestamp >= start_dt)
            .group_by(
                extract("year", Transaction.timestamp),
                extract("month", Transaction.timestamp),
                Transaction.is_expense,
            )
            .order_by(
                text("year ASC"),
                text("month ASC"),
            )
            .all()
        )

        # Daten in TrendPoints aggregieren
        trend_map: dict[str, TrendPoint] = {}

        for row in rows:
            yr: int = int(row[0])
            mo: int = int(row[1])
            is_exp: bool = row[2]
            amount: Decimal = Decimal(str(row[3]))
            period: str = f"{yr}-{mo:02d}"

            if period not in trend_map:
                trend_map[period] = TrendPoint(period=period)

            if is_exp:
                trend_map[period].expenses = amount
            else:
                trend_map[period].income = amount

        # Net berechnen und sortieren
        trends: list[TrendPoint] = sorted(
            trend_map.values(), key=lambda t: t.period
        )
        for tp in trends:
            tp.net = tp.income - tp.expenses

        logger.info(
            "Monats-Trends: %d Monate analysiert.", len(trends)
        )
        return trends

    def get_spending_velocity(
        self,
        days: int = 30,
    ) -> Decimal:
        """
        Berechnet die durchschnittlichen täglichen Ausgaben.

        Nützlich für Hochrechnungen: „Bei diesem Tempo bist du am
        Monatsende bei X €."

        Args:
            days: Betrachtungszeitraum in Tagen.

        Returns:
            Durchschnittliche Tagesausgaben in €.
        """
        logger.debug("Berechne Ausgaben-Geschwindigkeit (%d Tage)...", days)

        start_dt: datetime = datetime.utcnow() - timedelta(days=days)

        result = (
            self.db.query(
                func.coalesce(func.sum(Transaction.amount), 0)
            )
            .filter(
                Transaction.is_expense == True,
                Transaction.timestamp >= start_dt,
            )
            .scalar()
        )

        total: Decimal = Decimal(str(result or 0))
        velocity: Decimal = (
            total / days if days > 0 else Decimal("0.00")
        )

        logger.info(
            "Ausgaben-Geschwindigkeit: %.2f €/Tag (letzte %d Tage).",
            velocity,
            days,
        )
        return velocity.quantize(Decimal("0.01"))

    # ──────────────────────────────────────────────────────────────────────
    # Transaktions-Historie
    # ──────────────────────────────────────────────────────────────────────

    def get_transactions(
        self,
        page: int = 1,
        page_size: int = 50,
        category: str | None = None,
        expense_only: bool | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        search: str | None = None,
        sort_desc: bool = True,
    ) -> dict:
        """
        Ruft Transaktionen paginiert und gefiltert ab.

        Alle Filter sind optional und kombinierbar.

        Args:
            page: Seitennummer (1-basiert).
            page_size: Einträge pro Seite.
            category: Nur diese Kategorie.
            expense_only: Nur Ausgaben (True), nur Einnahmen (False), alle (None).
            start_date, end_date: Zeitraum-Filter.
            search: Volltextsuche in description.
            sort_desc: ``True`` = neueste zuerst.

        Returns:
            Dict mit ``items``, ``total``, ``page``, ``page_size``, ``total_pages``.
        """
        logger.debug(
            "Rufe Transaktionen ab (page=%d, size=%d, cat=%s, "
            "exp=%s, search=%s)...",
            page,
            page_size,
            category,
            expense_only,
            search,
        )

        query = self.db.query(Transaction)

        # Filter anwenden
        if category:
            query = query.filter(Transaction.category == category)
        if expense_only is not None:
            query = query.filter(Transaction.is_expense == expense_only)
        if start_date:
            start_dt: datetime = datetime.combine(
                start_date, datetime.min.time()
            )
            query = query.filter(Transaction.timestamp >= start_dt)
        if end_date:
            end_dt: datetime = datetime.combine(
                end_date, datetime.min.time()
            )
            query = query.filter(Transaction.timestamp < end_dt)
        if search:
            query = query.filter(
                Transaction.description.ilike(f"%{search}%")
            )

        # Gesamtzahl vor Paginierung
        total: int = query.count()

        # Sortierung
        order_col = (
            Transaction.timestamp.desc()
            if sort_desc
            else Transaction.timestamp.asc()
        )
        query = query.order_by(order_col)

        # Paginierung
        offset: int = (page - 1) * page_size
        transactions = query.offset(offset).limit(page_size).all()

        # ORM → Dict (SQLAlchemy-Modell → serialisierbar)
        items: list[dict] = [
            {
                "id": t.id,
                "amount": float(t.amount),
                "category": t.category,
                "description": t.description,
                "is_expense": t.is_expense,
                "is_recurring": t.is_recurring,
                "payment_method": t.payment_method,
                "timestamp": (
                    t.timestamp.isoformat()
                    if t.timestamp
                    else None
                ),
            }
            for t in transactions
        ]

        total_pages: int = max(1, (total + page_size - 1) // page_size)

        logger.info(
            "%d Transaktionen geladen (Seite %d/%d).",
            len(items),
            page,
            total_pages,
        )
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    def get_all_categories(self) -> list[str]:
        """
        Ruft alle jemals verwendeten Kategorien ab.

        Returns:
            Alphabetisch sortierte Liste eindeutiger Kategorie-Namen.
        """
        rows = (
            self.db.query(Transaction.category)
            .distinct()
            .order_by(Transaction.category)
            .all()
        )
        categories: list[str] = [row[0] for row in rows]
        logger.debug("%d Kategorien gefunden.", len(categories))
        return categories

    # ──────────────────────────────────────────────────────────────────────
    # Jahres-Übersicht
    # ──────────────────────────────────────────────────────────────────────

    def get_yearly_summary(self, year: int) -> dict:
        """
        Erstellt eine Jahresübersicht mit Monatsvergleich.

        Args:
            year: Jahr (z.B. 2026).

        Returns:
            Dict mit Jahressummen und monatlichen Detail-Zeilen.
        """
        logger.debug("Erstelle Jahresübersicht: %d", year)

        monthly: list[MonthlySummary] = []
        yearly_income: Decimal = Decimal("0.00")
        yearly_expenses: Decimal = Decimal("0.00")
        yearly_count: int = 0

        for month in range(1, 13):
            ms: MonthlySummary = self.get_monthly_summary(year, month)
            if ms.transaction_count > 0:
                monthly.append(ms)
                yearly_income += ms.total_income
                yearly_expenses += ms.total_expenses
                yearly_count += ms.transaction_count

        # Top-Kategorie im Jahr
        start_dt: datetime = datetime(year, 1, 1, 0, 0, 0)
        end_dt: datetime = datetime(year + 1, 1, 1, 0, 0, 0)

        top_cat = (
            self.db.query(
                Transaction.category,
                func.sum(Transaction.amount).label("total"),
            )
            .filter(
                Transaction.is_expense == True,
                Transaction.timestamp >= start_dt,
                Transaction.timestamp < end_dt,
            )
            .group_by(Transaction.category)
            .order_by(text("total DESC"))
            .first()
        )

        top_cat_name: str = top_cat[0] if top_cat else ""
        top_cat_amount: Decimal = (
            Decimal(str(top_cat[1])) if top_cat else Decimal("0.00")
        )

        avg_monthly: Decimal = (
            yearly_expenses / max(1, len(monthly))
        ).quantize(Decimal("0.01"))

        logger.info(
            "Jahresübersicht %d: +%.2f € / -%.2f € / =%.2f € "
            "(%d Monate aktiv, Ø %.2f €/Monat).",
            year,
            yearly_income,
            yearly_expenses,
            yearly_income - yearly_expenses,
            len(monthly),
            avg_monthly,
        )

        return {
            "year": year,
            "total_income": float(yearly_income),
            "total_expenses": float(yearly_expenses),
            "net_balance": float(yearly_income - yearly_expenses),
            "transaction_count": yearly_count,
            "active_months": len(monthly),
            "average_monthly_expenses": float(avg_monthly),
            "top_expense_category": top_cat_name,
            "top_expense_amount": float(top_cat_amount),
            "monthly_breakdown": [
                {
                    "month": f"{m.year}-{m.month:02d}",
                    "income": float(m.total_income),
                    "expenses": float(m.total_expenses),
                    "net": float(m.net_balance),
                    "count": m.transaction_count,
                    "top_category": m.top_expense_category,
                }
                for m in monthly
            ],
        }
