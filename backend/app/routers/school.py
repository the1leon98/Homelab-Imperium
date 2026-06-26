"""
Schul-Router des Homelab-Imperiums.

Stellt REST-Endpunkte für das Fachinformatiker-Schulmodul bereit:
Fächer, Noten, Abgabetermine, GPA-Berechnungen und PDF-Skript-Upload
mit automatischer RAG-Indexierung.

Endpunkte:
- ``GET /api/school/dashboard``          — Dashboard-Übersicht
- ``GET /api/school/subjects``            — Alle Fächer
- ``POST /api/school/subjects``           — Fach erstellen
- ``GET /api/school/subjects/{id}``       — Fach-Details
- ``POST /api/school/grades``             — Note erfassen
- ``PUT /api/school/grades/{id}``         — Note aktualisieren
- ``GET /api/school/gpa``                 — Globaler GPA
- ``GET /api/school/gpa/{subject_id}``    — Fach-GPA
- ``GET /api/school/gpa/trend``           — Semester-Trend
- ``POST /api/school/deadlines``          — Termin erstellen
- ``GET /api/school/deadlines``           — Termin-Übersicht
- ``POST /api/school/documents/ingest``   — PDF hochladen → RAG

Verwendung::

    from app.routers.school import router
    app.include_router(router, prefix="/api")
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SchoolDeadline, SchoolGrade, SchoolSubject
from app.schemas import (
    SchoolDeadlineCreate,
    SchoolGradeCreate,
    SchoolGradeUpdate,
    SchoolSubjectCreate,
    SchoolSubjectUpdate,
)
from app.services.school import SchoolService

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.routers.school")

# ═══════════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════════

router: APIRouter = APIRouter(tags=["Schule"])

# ═══════════════════════════════════════════════════════════════════════════════
# Dependency Injection
# ═══════════════════════════════════════════════════════════════════════════════


def get_school_service(
    db: Session = Depends(get_db),
) -> SchoolService:
    """Factory für den SchoolService mit DB-Session."""
    return SchoolService(db)


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/school/dashboard",
    summary="Schul-Dashboard",
    description="Kombinierte Übersicht: GPA, anstehende Deadlines, "
    "Fächer-Zusammenfassung.",
)
async def get_school_dashboard(
    svc: SchoolService = Depends(get_school_service),
) -> dict:
    """
    Dashboard mit GPA, Deadlines und Fächer-Übersicht in einer Response.
    """
    logger.info("GET /school/dashboard.")

    try:
        gpa = svc.calculate_global_gpa()
        deadlines = svc.get_deadline_overview()
        subjects = svc.get_all_subjects_summary()

        return {
            "gpa": {
                "overall": gpa.overall_gpa,
                "exam_subjects": gpa.exam_subjects_gpa,
                "trend": gpa.trend_indicator,
                "total_grades": gpa.total_grades,
            },
            "deadlines": {
                "upcoming": deadlines.upcoming[:5],
                "overdue_count": deadlines.total_overdue,
                "pending_count": deadlines.total_pending,
            },
            "subjects": subjects,
        }

    except Exception as exc:
        logger.exception("Fehler beim Schul-Dashboard.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler beim Dashboard: {exc}",
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# Fächer-CRUD
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/school/subjects",
    summary="Alle Fächer",
    description="Liste aller Unterrichtsfächer mit GPA und Notenanzahl.",
)
async def get_subjects(
    svc: SchoolService = Depends(get_school_service),
) -> list[dict]:
    """Kurzübersicht aller Fächer."""
    logger.info("GET /school/subjects.")
    return svc.get_all_subjects_summary()


@router.post(
    "/school/subjects",
    status_code=201,
    summary="Fach erstellen",
    description="Erstellt ein neues Unterrichtsfach.",
)
async def create_subject(
    subject: SchoolSubjectCreate,
    db: Session = Depends(get_db),
) -> dict:
    """
    Neues Fach anlegen (z.B. 'IT-Systeme', 'Wirtschaftskunde').
    """
    logger.info("POST /school/subjects: name=%r.", subject.name)

    try:
        new_subject: SchoolSubject = SchoolSubject(
            name=subject.name,
            teacher=subject.teacher,
            room=subject.room,
            is_exam_subject=subject.is_exam_subject,
            color_hex=subject.color_hex,
        )
        db.add(new_subject)
        db.commit()
        db.refresh(new_subject)

        logger.info("Fach erstellt: id=%d, name=%r.", new_subject.id, new_subject.name)
        return {
            "id": new_subject.id,
            "name": new_subject.name,
            "teacher": new_subject.teacher,
            "is_exam_subject": new_subject.is_exam_subject,
        }

    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Erstellen des Fachs.")
        raise HTTPException(
            status_code=500,
            detail=f"Fach konnte nicht erstellt werden: {exc}",
        ) from exc


@router.get(
    "/school/subjects/{subject_id}",
    summary="Fach-Details",
    description="Vollständige Fach-Details: Metadaten, Noten, Deadlines.",
)
async def get_subject_details(
    subject_id: int,
    svc: SchoolService = Depends(get_school_service),
) -> dict:
    """Einzelnes Fach mit GPA, Noten und Terminen."""
    logger.info("GET /school/subjects/%d.", subject_id)

    try:
        return svc.get_subject_details(subject_id)

    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Fehler bei Fach-Details.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# Noten-CRUD
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/school/grades",
    status_code=201,
    summary="Note erfassen",
    description="Erfasst eine neue Note für ein Fach. "
    "Validiert den Wert gegen das GermanGrade-Schema (1,0–6,0).",
)
async def create_grade(
    grade: SchoolGradeCreate,
    db: Session = Depends(get_db),
) -> dict:
    """
    Neue Note eintragen.

    Die Gewichtung (``weight``) bestimmt, wie stark die Note in den
    GPA einfließt: 1,0 = einfach, 2,0 = doppelt (Klassenarbeit).
    """
    logger.info(
        "POST /school/grades: subject=%d, value=%.1f, weight=%.1f.",
        grade.subject_id,
        grade.value,
        grade.weight,
    )

    # Prüfen, ob das Fach existiert
    subject: SchoolSubject | None = (
        db.query(SchoolSubject)
        .filter(SchoolSubject.id == grade.subject_id)
        .first()
    )
    if not subject:
        raise HTTPException(
            status_code=404,
            detail=f"Fach-ID {grade.subject_id} nicht gefunden.",
        )

    try:
        new_grade: SchoolGrade = SchoolGrade(
            subject_id=grade.subject_id,
            value=grade.value,
            weight=grade.weight,
            grade_type=grade.grade_type.value if grade.grade_type else None,
            description=grade.description,
            date=grade.date,
        )
        db.add(new_grade)
        db.commit()
        db.refresh(new_grade)

        logger.info(
            "Note erstellt: id=%d, subject=%r, value=%.1f.",
            new_grade.id,
            subject.name,
            new_grade.value,
        )
        return {
            "id": new_grade.id,
            "subject_id": new_grade.subject_id,
            "subject_name": subject.name,
            "value": new_grade.value,
            "weight": new_grade.weight,
            "grade_type": new_grade.grade_type,
            "date": new_grade.date.isoformat() if new_grade.date else None,
        }

    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Erstellen der Note.")
        raise HTTPException(
            status_code=500,
            detail=f"Note konnte nicht erstellt werden: {exc}",
        ) from exc


@router.put(
    "/school/grades/{grade_id}",
    summary="Note aktualisieren",
)
async def update_grade(
    grade_id: int,
    update: SchoolGradeUpdate,
    db: Session = Depends(get_db),
) -> dict:
    """Partielles Update einer Note."""
    logger.info("PUT /school/grades/%d.", grade_id)

    try:
        grade: SchoolGrade | None = (
            db.query(SchoolGrade)
            .filter(SchoolGrade.id == grade_id)
            .first()
        )
        if not grade:
            raise HTTPException(
                status_code=404,
                detail=f"Note {grade_id} nicht gefunden.",
            )

        update_data: dict = update.model_dump(exclude_unset=True)
        if "grade_type" in update_data and update_data["grade_type"]:
            update_data["grade_type"] = update_data["grade_type"].value

        for field, value in update_data.items():
            setattr(grade, field, value)

        db.commit()
        db.refresh(grade)
        logger.info("Note %d aktualisiert.", grade_id)

        return {
            "id": grade.id,
            "value": grade.value,
            "weight": grade.weight,
            "grade_type": grade.grade_type,
        }

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Aktualisieren der Note.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


@router.delete(
    "/school/grades/{grade_id}",
    summary="Note löschen",
    status_code=200,
)
async def delete_grade(
    grade_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """Löscht eine Note."""
    logger.info("DELETE /school/grades/%d.", grade_id)

    try:
        grade: SchoolGrade | None = (
            db.query(SchoolGrade)
            .filter(SchoolGrade.id == grade_id)
            .first()
        )
        if not grade:
            raise HTTPException(
                status_code=404,
                detail=f"Note {grade_id} nicht gefunden.",
            )
        db.delete(grade)
        db.commit()
        return {"message": "Note gelöscht.", "id": grade_id}

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Löschen.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# GPA
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/school/gpa",
    summary="Globaler GPA",
    description="Gewichteter Notendurchschnitt über ALLE Fächer + "
    "separater Prüfungsfächer-GPA.",
)
async def get_global_gpa(
    svc: SchoolService = Depends(get_school_service),
) -> dict:
    """Globaler GPA mit Trend-Indikator."""
    logger.info("GET /school/gpa.")

    try:
        gpa = svc.calculate_global_gpa()
        return {
            "overall_gpa": gpa.overall_gpa,
            "exam_subjects_gpa": gpa.exam_subjects_gpa,
            "total_grades": gpa.total_grades,
            "total_subjects": gpa.total_subjects,
            "trend": gpa.trend_indicator,
            "subjects": [
                {
                    "id": s.subject_id,
                    "name": s.subject_name,
                    "gpa": s.gpa,
                    "grade_count": s.grade_count,
                    "is_exam_subject": s.is_exam_subject,
                }
                for s in gpa.subjects
            ],
        }

    except Exception as exc:
        logger.exception("Fehler bei GPA-Berechnung.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


@router.get(
    "/school/gpa/{subject_id}",
    summary="Fach-GPA",
    description="Gewichteter Notendurchschnitt für EIN Fach.",
)
async def get_subject_gpa(
    subject_id: int,
    svc: SchoolService = Depends(get_school_service),
) -> dict:
    """Einzelfach-GPA mit allen Noten."""
    logger.info("GET /school/gpa/%d.", subject_id)

    try:
        sgpa = svc.calculate_subject_gpa(subject_id)
        return {
            "subject_id": sgpa.subject_id,
            "subject_name": sgpa.subject_name,
            "teacher": sgpa.teacher,
            "gpa": sgpa.gpa,
            "grade_count": sgpa.grade_count,
            "total_weight": sgpa.total_weight,
            "is_exam_subject": sgpa.is_exam_subject,
            "grades": sgpa.grades,
        }

    except Exception as exc:
        logger.exception("Fehler bei Fach-GPA.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


@router.get(
    "/school/gpa/trend",
    summary="GPA-Trend",
    description="GPA-Entwicklung über Semester (Halbjahre).",
)
async def get_gpa_trend(
    semesters: int = Query(default=4, ge=1, le=8),
    svc: SchoolService = Depends(get_school_service),
) -> list[dict]:
    """Semester-Trend als Zeitreihe."""
    logger.info("GET /school/gpa/trend: semesters=%d.", semesters)
    return svc.get_gpa_trend(semesters=semesters)


# ═══════════════════════════════════════════════════════════════════════════════
# Deadlines
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/school/deadlines",
    status_code=201,
    summary="Abgabetermin erstellen",
)
async def create_deadline(
    deadline: SchoolDeadlineCreate,
    db: Session = Depends(get_db),
) -> dict:
    """Neuen Abgabe- oder Prüfungstermin anlegen."""
    logger.info(
        "POST /school/deadlines: subject=%d, title=%r, due=%s.",
        deadline.subject_id,
        deadline.title,
        deadline.due_date,
    )

    subject: SchoolSubject | None = (
        db.query(SchoolSubject)
        .filter(SchoolSubject.id == deadline.subject_id)
        .first()
    )
    if not subject:
        raise HTTPException(
            status_code=404,
            detail=f"Fach-ID {deadline.subject_id} nicht gefunden.",
        )

    try:
        new_dl: SchoolDeadline = SchoolDeadline(
            subject_id=deadline.subject_id,
            title=deadline.title,
            due_date=deadline.due_date,
            deadline_type=(
                deadline.deadline_type.value
                if deadline.deadline_type
                else None
            ),
            is_completed=deadline.is_completed,
            priority=(
                deadline.priority.value if deadline.priority else None
            ),
            notes=deadline.notes,
        )
        db.add(new_dl)
        db.commit()
        db.refresh(new_dl)

        logger.info("Deadline erstellt: id=%d.", new_dl.id)
        return {
            "id": new_dl.id,
            "subject_id": new_dl.subject_id,
            "title": new_dl.title,
            "due_date": new_dl.due_date.isoformat(),
            "priority": new_dl.priority,
        }

    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Erstellen der Deadline.")
        raise HTTPException(
            status_code=500,
            detail=f"Termin konnte nicht erstellt werden: {exc}",
        ) from exc


@router.get(
    "/school/deadlines",
    summary="Termin-Übersicht",
    description="Anstehende, überfällige und kürzlich erledigte Termine.",
)
async def get_deadlines(
    svc: SchoolService = Depends(get_school_service),
) -> dict:
    """Vollständige Deadline-Übersicht."""
    logger.info("GET /school/deadlines.")

    try:
        overview = svc.get_deadline_overview()
        return {
            "upcoming": overview.upcoming,
            "overdue": overview.overdue,
            "completed_this_week": overview.completed_this_week,
            "total_pending": overview.total_pending,
            "total_overdue": overview.total_overdue,
        }

    except Exception as exc:
        logger.exception("Fehler bei Deadline-Übersicht.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


@router.put(
    "/school/deadlines/{deadline_id}/complete",
    summary="Termin als erledigt markieren",
)
async def complete_deadline(
    deadline_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """Markiert einen Termin als erledigt."""
    logger.info("PUT /school/deadlines/%d/complete.", deadline_id)

    try:
        dl: SchoolDeadline | None = (
            db.query(SchoolDeadline)
            .filter(SchoolDeadline.id == deadline_id)
            .first()
        )
        if not dl:
            raise HTTPException(
                status_code=404,
                detail=f"Termin {deadline_id} nicht gefunden.",
            )
        dl.is_completed = True
        db.commit()
        return {"message": "Termin als erledigt markiert.", "id": deadline_id}

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# PDF-Upload → RAG-Ingestion
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/school/documents/ingest",
    summary="PDF-Skript hochladen & indizieren",
    description="**Kritischer Endpunkt:** Lädt eine PDF-Datei hoch, "
    "speichert sie temporär, übergibt sie an die RAG-Engine zur "
    "semantischen Indexierung (Chunking → Embedding → ChromaDB) und "
    "protokolliert die Metadaten. "
    "Maximale Dateigröße: 50 MB.",
)
async def ingest_document(
    file: UploadFile = File(
        ...,
        description="PDF-Datei (max. 50 MB).",
    ),
    subject_id: int = Query(
        ...,
        ge=1,
        description="ID des zugehörigen Fachs.",
    ),
    force_reindex: bool = Query(
        default=False,
        description="Neu-Indizierung erzwingen (auch wenn bereits indexiert).",
    ),
    svc: SchoolService = Depends(get_school_service),
) -> dict:
    """
    Lädt ein Schul-Skript-PDF hoch und übergibt es an die RAG-Engine.

    **Pipeline:**
    1. Datei validieren (Größe, Typ)
    2. Temporär speichern
    3. Async an ``SchoolService.ingest_script_pdf()`` übergeben
    4. PDF → Chunks → Embeddings → ChromaDB
    5. Temporäre Datei löschen
    6. Ergebnis zurückgeben

    **Sicherheit:** Die Datei wird NUR temporär gespeichert und nach
    der Verarbeitung sofort gelöscht.
    """
    logger.info(
        "POST /school/documents/ingest: file=%r, subject=%d, "
        "reindex=%s.",
        file.filename,
        subject_id,
        force_reindex,
    )

    # ── Validierung: Dateityp ──
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Nur PDF-Dateien (.pdf) werden akzeptiert.",
        )

    # ── Validierung: Dateigröße (max 50 MB) ──
    if file.size and file.size > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"Datei zu groß ({file.size / 1024 / 1024:.1f} MB). "
            f"Maximum: 50 MB.",
        )

    tmp_path: str = ""

    try:
        # ── Temporär speichern ──
        suffix: str = Path(file.filename).suffix
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, prefix="school_upload_"
        ) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name

        logger.debug(
            "PDF temporär gespeichert: %s (%.1f KB).",
            tmp_path,
            Path(tmp_path).stat().st_size / 1024,
        )

        # ── RAG-Ingestion starten ──
        result = await svc.ingest_script_pdf(
            file_path=tmp_path,
            subject_id=subject_id,
            force_reindex=force_reindex,
        )

        if result.rag_success:
            logger.info(
                "PDF-Ingestion erfolgreich: %s → %d Chunks in "
                "Collection 'school_pdfs'.",
                result.file_name,
                result.chunks_indexed,
            )
            return {
                "message": "PDF erfolgreich indiziert.",
                "file_name": result.file_name,
                "file_size_mb": result.file_size_mb,
                "subject_name": result.subject_name,
                "chunks_indexed": result.chunks_indexed,
                "rag_success": True,
            }
        else:
            logger.error(
                "PDF-Ingestion fehlgeschlagen: %s — %s",
                result.file_name,
                result.error_message,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    f"RAG-Indexierung fehlgeschlagen: "
                    f"{result.error_message}"
                ),
            )

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Fehler bei PDF-Ingestion.")
        raise HTTPException(
            status_code=500,
            detail=f"PDF-Verarbeitung fehlgeschlagen: {exc}",
        ) from exc

    finally:
        # ── Temporäre Datei LÖSCHEN ──
        if tmp_path:
            try:
                Path(tmp_path).unlink()
                logger.debug("Temporäre Datei gelöscht: %s.", tmp_path)
            except OSError:
                logger.warning(
                    "Konnte temporäre Datei nicht löschen: %s.", tmp_path
                )
