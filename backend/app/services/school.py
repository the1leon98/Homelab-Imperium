"""
Schul-Dienst des Homelab-Imperiums.

Implementiert die Geschäftslogik für das Fachinformatiker-Schulmodul:
- Gewichtete Notendurchschnittsberechnung (GPA) pro Fach & global
- Semester-Trendanalysen
- Abgabetermin-Verfolgung & Priorisierung
- PDF-Skript-Ingestion mit RAG-Indexierung und DB-Protokollierung
- Prüfungsfach-Fokusanalyse

Verwendung::

    from app.services.school import SchoolService
    from app.database import get_db_context

    with get_db_context() as db:
        svc = SchoolService(db)
        gpa = svc.calculate_global_gpa()
        await svc.ingest_script_pdf("/path/to/skript.pdf", subject_id=1)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import and_, func, text
from sqlalchemy.orm import Session

from app.models import (
    SchoolDeadline,
    SchoolGrade,
    SchoolSubject,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger(
    "homelab_imperium.services.school"
)


# ═══════════════════════════════════════════════════════════════════════════════
# Datenklassen
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class SubjectGPA:
    """Notendurchschnitt für ein einzelnes Fach."""

    subject_id: int
    subject_name: str
    teacher: str = ""
    gpa: float = 0.0
    grade_count: int = 0
    total_weight: float = 0.0
    weighted_sum: float = 0.0
    is_exam_subject: bool = False
    grades: list[dict] = field(default_factory=list)


@dataclass
class GlobalGPA:
    """Globaler Notendurchschnitt über ALLE Fächer."""

    overall_gpa: float = 0.0
    exam_subjects_gpa: float = 0.0
    total_grades: int = 0
    total_subjects: int = 0
    subjects: list[SubjectGPA] = field(default_factory=list)
    trend_indicator: str = "stable"  # improving, declining, stable


@dataclass
class DeadlineOverview:
    """Übersicht anstehender Abgabetermine."""

    upcoming: list[dict] = field(default_factory=list)
    overdue: list[dict] = field(default_factory=list)
    completed_this_week: list[dict] = field(default_factory=list)
    total_pending: int = 0
    total_overdue: int = 0


@dataclass
class ScriptIngestionResult:
    """Ergebnis einer Skript-PDF-Ingestion."""

    file_name: str
    file_size_mb: float
    subject_name: str
    chunks_indexed: int = 0
    rag_success: bool = False
    error_message: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# SchoolService
# ═══════════════════════════════════════════════════════════════════════════════


class SchoolService:
    """
    Schul-Logik: Notenberechnung, Terminverfolgung, PDF-Indexierung.
    """

    def __init__(self, db: Session) -> None:
        """
        Args:
            db: SQLAlchemy-Datenbank-Session.
        """
        self.db: Session = db

    # ──────────────────────────────────────────────────────────────────────
    # GPA-Berechnung (Notendurchschnitt)
    # ──────────────────────────────────────────────────────────────────────

    def calculate_subject_gpa(self, subject_id: int) -> SubjectGPA:
        """
        Berechnet den gewichteten Notendurchschnitt für EIN Fach.

        Formel: ``GPA = Σ(Note × Gewichtung) / Σ(Gewichtung)``

        Args:
            subject_id: ID des Fachs.

        Returns:
            ``SubjectGPA`` mit gewichtetem Schnitt und Einzelnoten.
        """
        logger.debug("Berechne GPA für Fach-ID %d...", subject_id)

        subject: SchoolSubject | None = (
            self.db.query(SchoolSubject)
            .filter(SchoolSubject.id == subject_id)
            .first()
        )

        if not subject:
            logger.warning("Fach-ID %d nicht gefunden.", subject_id)
            return SubjectGPA(
                subject_id=subject_id, subject_name="Unbekannt"
            )

        # Alle Noten mit Gewichtung abrufen
        grades = (
            self.db.query(SchoolGrade)
            .filter(SchoolGrade.subject_id == subject_id)
            .order_by(SchoolGrade.date.desc())
            .all()
        )

        if not grades:
            logger.debug("Keine Noten für Fach %r.", subject.name)
            return SubjectGPA(
                subject_id=subject.id,
                subject_name=subject.name,
                teacher=subject.teacher or "",
                is_exam_subject=subject.is_exam_subject,
            )

        # Gewichteter Durchschnitt
        total_weight: float = sum(g.weight for g in grades)
        weighted_sum: float = sum(g.value * g.weight for g in grades)
        gpa: float = (
            round(weighted_sum / total_weight, 2)
            if total_weight > 0
            else 0.0
        )

        # Einzelnoten als Dicts
        grade_dicts: list[dict] = [
            {
                "id": g.id,
                "value": g.value,
                "weight": g.weight,
                "grade_type": g.grade_type,
                "description": g.description,
                "date": g.date.isoformat() if g.date else None,
            }
            for g in grades
        ]

        logger.info(
            "GPA %r: %.2f (%d Noten, Gewicht: %.1f).",
            subject.name,
            gpa,
            len(grades),
            total_weight,
        )

        return SubjectGPA(
            subject_id=subject.id,
            subject_name=subject.name,
            teacher=subject.teacher or "",
            gpa=gpa,
            grade_count=len(grades),
            total_weight=total_weight,
            weighted_sum=round(weighted_sum, 2),
            is_exam_subject=subject.is_exam_subject,
            grades=grade_dicts,
        )

    def calculate_global_gpa(self) -> GlobalGPA:
        """
        Berechnet den globalen Notendurchschnitt über ALLE Fächer.

        Die Gesamt-GPA wird aus den Fach-GPAs gemittelt (jedes Fach
        zählt gleich — unabhängig von der Anzahl der Noten).

        Zusätzlich wird die GPA nur für Prüfungsfächer berechnet
        (``is_exam_subject=True``).

        Returns:
            ``GlobalGPA`` mit Gesamtschnitt und Fach-Details.
        """
        logger.debug("Berechne globalen GPA...")

        subjects = (
            self.db.query(SchoolSubject)
            .order_by(SchoolSubject.name)
            .all()
        )

        if not subjects:
            return GlobalGPA()

        subject_gpas: list[SubjectGPA] = []
        exam_gpas: list[float] = []
        total_grades: int = 0

        for subject in subjects:
            sg: SubjectGPA = self.calculate_subject_gpa(subject.id)
            subject_gpas.append(sg)
            total_grades += sg.grade_count

            if sg.is_exam_subject and sg.grade_count > 0:
                exam_gpas.append(sg.gpa)

        # Nur Fächer mit Noten zählen für den Gesamtschnitt
        graded_subjects: list[SubjectGPA] = [
            s for s in subject_gpas if s.grade_count > 0
        ]

        overall: float = (
            round(
                sum(s.gpa for s in graded_subjects)
                / len(graded_subjects),
                2,
            )
            if graded_subjects
            else 0.0
        )

        exam_avg: float = (
            round(sum(exam_gpas) / len(exam_gpas), 2)
            if exam_gpas
            else 0.0
        )

        # Trend-Indikator: Verbessert oder verschlechtert?
        trend: str = self._estimate_trend(subject_gpas)

        logger.info(
            "Globaler GPA: %.2f (Prüfungsfächer: %.2f), "
            "%d Fächer, %d Noten, Trend: %s.",
            overall,
            exam_avg,
            len(graded_subjects),
            total_grades,
            trend,
        )

        return GlobalGPA(
            overall_gpa=overall,
            exam_subjects_gpa=exam_avg,
            total_grades=total_grades,
            total_subjects=len(graded_subjects),
            subjects=sorted(subject_gpas, key=lambda s: s.gpa),
            trend_indicator=trend,
        )

    def _estimate_trend(self, subjects: list[SubjectGPA]) -> str:
        """
        Schätzt den GPA-Trend basierend auf dem Verhältnis neuer zu
        alten Noten.

        Vereinfachte Heuristik:
        - Wenn die letzten 3 Noten besser als der Durchschnitt → improving
        - Wenn die letzten 3 Noten schlechter → declining
        - Sonst → stable
        """
        all_grades: list[dict] = []
        for s in subjects:
            all_grades.extend(s.grades)

        if len(all_grades) < 3:
            return "stable"

        sorted_grades: list[dict] = sorted(
            all_grades,
            key=lambda g: g["date"] or "0000",
            reverse=True,
        )

        recent: list[float] = [
            g["value"] for g in sorted_grades[:3] if g["value"] > 0
        ]
        avg_all: float = (
            sum(g["value"] for g in all_grades if g["value"] > 0)
            / max(1, sum(1 for g in all_grades if g["value"] > 0))
        )

        if not recent:
            return "stable"

        avg_recent: float = sum(recent) / len(recent)

        # Deutsche Noten: 1,0 = sehr gut, 6,0 = ungenügend
        # → niedriger = besser. Also avg_recent < avg_all bedeutet Verbesserung
        if avg_recent < avg_all - 0.2:
            return "improving"
        elif avg_recent > avg_all + 0.2:
            return "declining"
        else:
            return "stable"

    def get_subject_details(self, subject_id: int) -> dict:
        """
        Ruft die vollständigen Details eines Fachs ab (Noten + Termine).

        Args:
            subject_id: Fach-ID.

        Returns:
            Dict mit Fach-Metadaten, Noten-Liste und Termin-Liste.
        """
        subject: SchoolSubject | None = (
            self.db.query(SchoolSubject)
            .filter(SchoolSubject.id == subject_id)
            .first()
        )

        if not subject:
            raise ValueError(f"Fach-ID {subject_id} nicht gefunden.")

        gpa: SubjectGPA = self.calculate_subject_gpa(subject_id)

        # Anstehende Termine für dieses Fach
        today: date = date.today()
        deadlines = (
            self.db.query(SchoolDeadline)
            .filter(
                SchoolDeadline.subject_id == subject_id,
                SchoolDeadline.is_completed == False,
                SchoolDeadline.due_date >= today,
            )
            .order_by(SchoolDeadline.due_date.asc())
            .all()
        )

        return {
            "id": subject.id,
            "name": subject.name,
            "teacher": subject.teacher,
            "room": subject.room,
            "is_exam_subject": subject.is_exam_subject,
            "color_hex": subject.color_hex,
            "gpa": gpa.gpa,
            "grade_count": gpa.grade_count,
            "grades": gpa.grades,
            "upcoming_deadlines": [
                {
                    "id": d.id,
                    "title": d.title,
                    "due_date": d.due_date.isoformat(),
                    "deadline_type": d.deadline_type,
                    "priority": d.priority,
                    "days_remaining": (d.due_date - today).days,
                }
                for d in deadlines
            ],
        }

    # ──────────────────────────────────────────────────────────────────────
    # Abgabetermine & Deadlines
    # ──────────────────────────────────────────────────────────────────────

    def get_deadline_overview(self) -> DeadlineOverview:
        """
        Erstellt eine vollständige Übersicht aller Abgabe- und
        Prüfungstermine.

        Returns:
            ``DeadlineOverview`` mit anstehenden, überfälligen und
            kürzlich erledigten Terminen.
        """
        logger.debug("Erstelle Deadline-Übersicht...")
        today: date = date.today()
        week_from_now: date = today + timedelta(days=7)

        # Alle aktiven Deadlines mit JOIN zum Fach
        all_active = (
            self.db.query(SchoolDeadline, SchoolSubject.name)
            .join(SchoolSubject)
            .filter(SchoolDeadline.is_completed == False)
            .order_by(SchoolDeadline.due_date.asc())
            .all()
        )

        upcoming: list[dict] = []
        overdue: list[dict] = []

        for deadline, subject_name in all_active:
            entry: dict = {
                "id": deadline.id,
                "subject_id": deadline.subject_id,
                "subject_name": subject_name,
                "title": deadline.title,
                "due_date": deadline.due_date.isoformat(),
                "deadline_type": deadline.deadline_type,
                "priority": deadline.priority,
                "days_remaining": (deadline.due_date - today).days,
                "notes": deadline.notes,
            }

            if deadline.due_date < today:
                overdue.append(entry)
            else:
                upcoming.append(entry)

        # Kürzlich erledigte (diese Woche)
        week_start: date = today - timedelta(days=today.weekday())
        completed_this_week = (
            self.db.query(SchoolDeadline, SchoolSubject.name)
            .join(SchoolSubject)
            .filter(
                SchoolDeadline.is_completed == True,
                SchoolDeadline.due_date >= week_start,
            )
            .order_by(SchoolDeadline.due_date.desc())
            .all()
        )

        completed: list[dict] = [
            {
                "id": d.id,
                "subject_name": sn,
                "title": d.title,
                "due_date": d.due_date.isoformat(),
                "deadline_type": d.deadline_type,
            }
            for d, sn in completed_this_week
        ]

        logger.info(
            "Deadlines: %d anstehend, %d überfällig, %d erledigt.",
            len(upcoming),
            len(overdue),
            len(completed),
        )

        return DeadlineOverview(
            upcoming=upcoming,
            overdue=overdue,
            completed_this_week=completed,
            total_pending=len(upcoming),
            total_overdue=len(overdue),
        )

    # ──────────────────────────────────────────────────────────────────────
    # PDF-Skript-Ingestion (→ RAG-Engine)
    # ──────────────────────────────────────────────────────────────────────

    async def ingest_script_pdf(
        self,
        file_path: str,
        subject_id: int,
        force_reindex: bool = False,
    ) -> ScriptIngestionResult:
        """
        Übergibt ein Schul-Skript-PDF asynchron an die RAG-Engine zur
        semantischen Indexierung und protokolliert die Metadaten.

        Pipeline:
        1. Prüfen, ob Fach existiert
        2. Dateigröße & Name validieren
        3. RAG-Engine-Ingestion starten (async)
        4. Ergebnis in DB protokollieren

        Args:
            file_path: Absoluter Pfad zur PDF-Datei.
            subject_id: ID des zugehörigen Fachs.
            force_reindex: Neu-Indizierung erzwingen.

        Returns:
            ``ScriptIngestionResult`` mit Status und Metriken.
        """
        # Fach prüfen
        subject: SchoolSubject | None = (
            self.db.query(SchoolSubject)
            .filter(SchoolSubject.id == subject_id)
            .first()
        )

        if not subject:
            raise ValueError(f"Fach-ID {subject_id} nicht gefunden.")

        # Datei prüfen
        pdf_path: Path = Path(file_path)
        if not pdf_path.exists():
            raise FileNotFoundError(
                f"PDF nicht gefunden: {file_path}"
            )

        file_size_mb: float = pdf_path.stat().st_size / (1024 * 1024)
        logger.info(
            "Starte Skript-Ingestion: %s (%.1f MB) → Fach %r.",
            pdf_path.name,
            file_size_mb,
            subject.name,
        )

        # RAG-Engine initialisieren und Ingestion starten
        try:
            from app.services.rag_engine import RAGEngine

            rag: RAGEngine = RAGEngine()
            result: any = await rag.ingest_pdf(
                file_path=str(pdf_path),
                collection_name="school_pdfs",
                force_reindex=force_reindex,
            )

            chunks: int = (
                result.total_chunks if hasattr(result, "total_chunks") else 0
            )

            logger.info(
                "RAG-Ingestion abgeschlossen: %d Chunks für %r.",
                chunks,
                subject.name,
            )

            return ScriptIngestionResult(
                file_name=pdf_path.name,
                file_size_mb=round(file_size_mb, 2),
                subject_name=subject.name,
                chunks_indexed=chunks,
                rag_success=True,
            )

        except Exception as exc:
            logger.error(
                "RAG-Ingestion fehlgeschlagen für %r: %s",
                pdf_path.name,
                exc,
            )
            return ScriptIngestionResult(
                file_name=pdf_path.name,
                file_size_mb=round(file_size_mb, 2),
                subject_name=subject.name,
                rag_success=False,
                error_message=str(exc),
            )

    # ──────────────────────────────────────────────────────────────────────
    # Semester-Trend
    # ──────────────────────────────────────────────────────────────────────

    def get_gpa_trend(self, semesters: int = 4) -> list[dict]:
        """
        Berechnet den GPA-Trend über mehrere Semester.

        Da die DB keine expliziten Semester speichert, werden die Noten
        nach Halbjahres-Blöcken gruppiert (basierend auf ``date``-Feld).

        Args:
            semesters: Anzahl der Semester (Halbjahre).

        Returns:
            Liste von ``{"semester": "2025-H1", "gpa": 1.8, ...}``.
        """
        logger.debug("Berechne GPA-Trend (%d Semester)...", semesters)

        all_grades = (
            self.db.query(SchoolGrade)
            .order_by(SchoolGrade.date.asc())
            .all()
        )

        if not all_grades:
            return []

        # Gruppierung nach Halbjahr
        by_semester: dict[str, list[float]] = {}

        for grade in all_grades:
            if not grade.date:
                continue

            year: int = grade.date.year
            half: str = "H1" if grade.date.month <= 6 else "H2"
            sem_key: str = f"{year}-{half}"

            if sem_key not in by_semester:
                by_semester[sem_key] = []
            by_semester[sem_key].append(grade.value)

        # GPA pro Semester
        trend: list[dict] = []
        for sem_key in sorted(by_semester.keys())[-semesters:]:
            values: list[float] = by_semester[sem_key]
            gpa: float = round(sum(values) / len(values), 2)
            trend.append(
                {
                    "semester": sem_key,
                    "gpa": gpa,
                    "grade_count": len(values),
                    "best_grade": min(values),
                    "worst_grade": max(values),
                }
            )

        logger.info(
            "GPA-Trend: %d Semester analysiert.", len(trend)
        )
        return trend

    def get_all_subjects_summary(self) -> list[dict]:
        """
        Erstellt eine Kurzübersicht aller Fächer (für Sidebar/Listing).

        Returns:
            Liste von Dicts mit Fach-ID, Name, GPA und Notenanzahl.
        """
        subjects = (
            self.db.query(SchoolSubject)
            .order_by(SchoolSubject.name)
            .all()
        )

        summaries: list[dict] = []
        for subject in subjects:
            grade_count: int = (
                self.db.query(func.count(SchoolGrade.id))
                .filter(SchoolGrade.subject_id == subject.id)
                .scalar()
                or 0
            )

            gpa: float = 0.0
            if grade_count > 0:
                sg: SubjectGPA = self.calculate_subject_gpa(subject.id)
                gpa = sg.gpa

            pending: int = (
                self.db.query(func.count(SchoolDeadline.id))
                .filter(
                    SchoolDeadline.subject_id == subject.id,
                    SchoolDeadline.is_completed == False,
                    SchoolDeadline.due_date >= date.today(),
                )
                .scalar()
                or 0
            )

            summaries.append(
                {
                    "id": subject.id,
                    "name": subject.name,
                    "teacher": subject.teacher,
                    "is_exam_subject": subject.is_exam_subject,
                    "color_hex": subject.color_hex,
                    "gpa": gpa,
                    "grade_count": grade_count,
                    "pending_deadlines": pending,
                }
            )

        return summaries
