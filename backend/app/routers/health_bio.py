"""
Gesundheits- und Biometrie-Router des Homelab-Imperiums.

Stellt REST-Endpunkte für Vitaldaten, Symptome, Ernährung, Training
und das 3D-Körper-Hologramm bereit.

Endpunkte:
- ``POST /api/health/record``          — Vitalwert/Symptom erfassen
- ``GET /api/health/records``           — Einträge abrufen (gefiltert)
- ``GET /api/health/hologram``          — Aktive Symptome (3D-Hologramm)
- ``GET /api/health/hologram/{location}`` — Symptome für Körperregion
- ``GET /api/health/nutrition``         — Täglicher Ernährungsstatus
- ``GET /api/health/weight``            — Gewichts-Trend
- ``GET /api/health/exercise``          — Trainingszusammenfassung
- ``GET /api/health/exercise/history``  — Trainings-Historie
- ``GET /api/health/sleep``             — Schlafzusammenfassung
- ``GET /api/health/vitals``            — Aktuelle Vitalwerte
- ``GET /api/health/dashboard``         — Vollständiges Dashboard

Verwendung::

    from app.routers.health_bio import router
    app.include_router(router, prefix="/api")
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import HealthRecord
from app.schemas import (
    HealthRecordCreate,
    HealthRecordResponse,
    HealthRecordUpdate,
    HologramStatusResponse,
)
from app.services.health_bio import HealthBioService

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger(
    "homelab_imperium.routers.health_bio"
)

# ═══════════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════════

router: APIRouter = APIRouter(tags=["Gesundheit & Biometrie"])

# ═══════════════════════════════════════════════════════════════════════════════
# Dependency Injection
# ═══════════════════════════════════════════════════════════════════════════════


def get_health_service(
    db: Session = Depends(get_db),
) -> HealthBioService:
    """Factory für den HealthBioService mit DB-Session."""
    return HealthBioService(db)


# ═══════════════════════════════════════════════════════════════════════════════
# Health-Record CRUD
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/health/record",
    response_model=HealthRecordResponse,
    status_code=201,
    summary="Gesundheitseintrag erfassen",
    description="Erfasst einen Vitalwert, eine Mahlzeit, ein Training "
    "oder ein Symptom. Validiert den Eingabe-Payload gegen das "
    "Pydantic-Schema (inkl. Hologramm-Location-Validierung).",
)
async def create_health_record(
    record: HealthRecordCreate,
    db: Session = Depends(get_db),
) -> HealthRecordResponse:
    """
    Erstellt einen neuen Gesundheitseintrag.

    Bei ``record_type='symptom'`` sind ``symptom_location`` und
    ``intensity`` Pflichtfelder (durch Pydantic-Validator erzwungen).
    """
    logger.info(
        "POST /health/record: type=%s, location=%s.",
        record.record_type,
        record.symptom_location,
    )

    try:
        new_record: HealthRecord = HealthRecord(
            record_type=record.record_type.value,
            val1=record.val1,
            val2=record.val2,
            val3=record.val3,
            description=record.description,
            symptom_location=record.symptom_location,
            intensity=(
                record.intensity.value if record.intensity else None
            ),
            vehicle_id=record.vehicle_id,
            timestamp=record.timestamp,
        )
        db.add(new_record)
        db.commit()
        db.refresh(new_record)

        logger.info("Health-Record erstellt: id=%d.", new_record.id)
        return new_record

    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Erstellen des Health-Records.")
        raise HTTPException(
            status_code=500,
            detail=f"Eintrag konnte nicht erstellt werden: {exc}",
        ) from exc


@router.get(
    "/health/records",
    response_model=list[HealthRecordResponse],
    summary="Gesundheitseinträge abrufen",
    description="Ruft Gesundheitseinträge gefiltert nach Typ und Zeitraum ab.",
)
async def get_health_records(
    record_type: Optional[str] = Query(
        default=None,
        description="Filtern nach Typ: weight, meal, workout, symptom, "
        "vitals, sleep, water, medication.",
    ),
    days: int = Query(default=30, ge=1, le=365, description="Letzte N Tage."),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[HealthRecordResponse]:
    """
    Gefilterte Liste von Gesundheitseinträgen.
    """
    logger.info(
        "GET /health/records: type=%s, days=%d, limit=%d.",
        record_type,
        days,
        limit,
    )

    try:
        from datetime import timedelta

        query = db.query(HealthRecord)

        if record_type:
            query = query.filter(HealthRecord.record_type == record_type)

        cutoff: datetime = datetime.utcnow() - timedelta(days=days)
        query = query.filter(HealthRecord.timestamp >= cutoff)

        records = (
            query.order_by(HealthRecord.timestamp.desc())
            .limit(limit)
            .all()
        )

        logger.debug("%d Einträge gefunden.", len(records))
        return records

    except Exception as exc:
        logger.exception("Fehler beim Abrufen der Health-Records.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.delete(
    "/health/record/{record_id}",
    summary="Gesundheitseintrag löschen",
    status_code=200,
)
async def delete_health_record(
    record_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """Löscht einen Gesundheitseintrag."""
    logger.info("DELETE /health/record/%d.", record_id)

    try:
        record: HealthRecord | None = (
            db.query(HealthRecord)
            .filter(HealthRecord.id == record_id)
            .first()
        )
        if not record:
            raise HTTPException(
                status_code=404,
                detail=f"Eintrag {record_id} nicht gefunden.",
            )
        db.delete(record)
        db.commit()
        return {"message": "Eintrag gelöscht.", "id": record_id}

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Löschen.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# Hologramm — 3D-Körperdarstellung
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/health/hologram",
    response_model=HologramStatusResponse,
    summary="3D-Hologramm-Status",
    description="Aggregiert alle AKTIVEN Symptome und Verletzungen für "
    "die cyber-holografische 3D-Körperdarstellung im Frontend. "
    "Jede Anomalie enthält die exakte anatomische Location "
    "(z.B. 'shoulder_L'), Intensität (high/medium/low) und Ursache.",
)
async def get_hologram_status(
    svc: HealthBioService = Depends(get_health_service),
) -> HologramStatusResponse:
    """
    Liefert alle aktiven Symptome für das 3D-Hologramm.

    Die Farbcodierung im Frontend:
    - ``high`` → 🔴 Rot (akut, behandlungsbedürftig)
    - ``medium`` → 🟠 Orange (subakut)
    - ``low`` → 🟡 Gelb (chronisch/abklingend)

    Abgeklungene Symptome (>30 Tage, intensity=low) werden
    automatisch ausgefiltert.
    """
    logger.info("GET /health/hologram.")

    try:
        hologram = svc.get_hologram_data()
        return HologramStatusResponse(
            anomalies=[
                {
                    "location": a.location,
                    "intensity": a.intensity,
                    "cause": a.cause,
                }
                for a in hologram.anomalies
            ]
        )

    except Exception as exc:
        logger.exception("Fehler beim Hologramm-Status.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.get(
    "/health/hologram/{location}",
    summary="Symptome pro Körperregion",
    description="Ruft alle Symptome für eine bestimmte anatomische "
    "Region ab (z.B. 'knee_L', 'shoulder_R').",
)
async def get_symptoms_by_location(
    location: str,
    svc: HealthBioService = Depends(get_health_service),
) -> list[dict]:
    """
    Symptome einer bestimmten Körperregion.
    """
    logger.info("GET /health/hologram/%s.", location)

    try:
        anomalies = svc.get_symptoms_by_location(location)
        return [
            {
                "location": a.location,
                "intensity": a.intensity,
                "cause": a.cause,
                "recorded_at": a.recorded_at,
                "record_id": a.record_id,
            }
            for a in anomalies
        ]

    except Exception as exc:
        logger.exception("Fehler bei Location-Abfrage.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# Ernährung, Gewicht, Training & Schlaf
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/health/nutrition",
    summary="Täglicher Ernährungsstatus",
    description="Aggregiert Kalorien, Makronährstoffe, Wasser und "
    "glykämische Last des aktuellen Tages.",
)
async def get_nutrition_status(
    target_date: Optional[date] = Query(default=None, description="Datum (Default: heute)."),
    svc: HealthBioService = Depends(get_health_service),
) -> dict:
    """
    Vollständiger Ernährungsstatus für einen Tag.
    """
    logger.info("GET /health/nutrition: date=%s.", target_date)

    try:
        status = svc.get_daily_nutrition_status(target_date)
        return {
            "date": status.date,
            "calories_target": status.calories_target,
            "calories_consumed": status.calories_consumed,
            "calories_burned_exercise": status.calories_burned_exercise,
            "net_calories": status.net_calories,
            "is_deficit": status.is_deficit,
            "is_surplus": status.is_surplus,
            "protein_grams": status.protein_grams,
            "carbs_grams": status.carbs_grams,
            "fat_grams": status.fat_grams,
            "water_ml": status.water_ml,
            "water_target_ml": status.water_target_ml,
            "meal_count": status.meal_count,
            "glycemic_load": status.glycemic_load_total,
        }

    except Exception as exc:
        logger.exception("Fehler beim Ernährungsstatus.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.get(
    "/health/weight",
    summary="Gewichts-Trend",
    description="Aktuelles Gewicht + 7-/30-Tage-Trend + "
    "wöchentliche Änderungsrate.",
)
async def get_weight_trend(
    days: int = Query(default=30, ge=7, le=365, description="Analyse-Zeitraum in Tagen."),
    svc: HealthBioService = Depends(get_health_service),
) -> dict:
    """
    Gewichtsverlauf mit Trend-Indikatoren.
    """
    logger.info("GET /health/weight: days=%d.", days)

    try:
        trend = svc.get_weight_trend(days=days)
        return {
            "current_weight_kg": trend.current_weight_kg,
            "weight_7d_ago_kg": trend.weight_7d_ago_kg,
            "weight_30d_ago_kg": trend.weight_30d_ago_kg,
            "trend_7d_kg": trend.trend_7d_kg,
            "trend_30d_kg": trend.trend_30d_kg,
            "avg_weekly_change_kg": trend.avg_weekly_change_kg,
            "records": [
                {
                    "date": r.date,
                    "weight_kg": r.weight_kg,
                    "body_fat_pct": r.body_fat_pct,
                }
                for r in trend.records[:30]
            ],
        }

    except Exception as exc:
        logger.exception("Fehler beim Gewichts-Trend.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.get(
    "/health/exercise",
    summary="Trainingszusammenfassung (heute)",
    description="Trainingsdauer, Kalorienverbrauch und "
    "Workout-Anzahl des aktuellen Tages.",
)
async def get_exercise_summary(
    target_date: Optional[date] = Query(default=None),
    svc: HealthBioService = Depends(get_health_service),
) -> dict:
    """Trainingszusammenfassung für einen Tag."""
    logger.info("GET /health/exercise: date=%s.", target_date)

    try:
        summary = svc.get_exercise_summary(target_date)
        return {
            "date": summary.date,
            "duration_min": summary.total_duration_min,
            "calories_burned": summary.total_calories_burned,
            "workout_count": summary.workout_count,
            "avg_heart_rate": summary.avg_heart_rate,
        }

    except Exception as exc:
        logger.exception("Fehler beim Trainingsstatus.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.get(
    "/health/exercise/history",
    summary="Trainings-Historie",
    description="Trainingsdaten der letzten 30 Tage als Zeitreihe.",
)
async def get_exercise_history(
    days: int = Query(default=30, ge=1, le=90),
    svc: HealthBioService = Depends(get_health_service),
) -> list[dict]:
    """Tägliche Trainingshistorie."""
    logger.info("GET /health/exercise/history: days=%d.", days)

    try:
        history = svc.get_exercise_history(days=days)
        return [
            {
                "date": h.date,
                "duration_min": h.total_duration_min,
                "calories_burned": h.total_calories_burned,
                "workout_count": h.workout_count,
                "avg_heart_rate": h.avg_heart_rate,
            }
            for h in history
        ]

    except Exception as exc:
        logger.exception("Fehler bei Trainings-Historie.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.get(
    "/health/sleep",
    summary="Schlafzusammenfassung",
    description="Schlafdauer und -qualität des aktuellen Tages.",
)
async def get_sleep_summary(
    target_date: Optional[date] = Query(default=None),
    svc: HealthBioService = Depends(get_health_service),
) -> dict:
    """Schlafdaten für einen Tag."""
    logger.info("GET /health/sleep: date=%s.", target_date)

    try:
        summary = svc.get_sleep_summary(target_date)
        return {
            "date": summary.date,
            "duration_hours": summary.duration_hours,
            "quality": summary.quality,
            "records_count": summary.records_count,
        }

    except Exception as exc:
        logger.exception("Fehler bei Schlafdaten.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.get(
    "/health/vitals",
    summary="Aktuelle Vitalwerte",
    description="Letzte gemessene Vitalwerte: Blutdruck, Ruhepuls.",
)
async def get_latest_vitals(
    svc: HealthBioService = Depends(get_health_service),
) -> dict:
    """Aktuellste Vitalwerte."""
    logger.info("GET /health/vitals.")

    try:
        return svc.get_latest_vitals()

    except Exception as exc:
        logger.exception("Fehler bei Vitalwerten.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard — Vollständiger Gesundheitsstatus
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/health/dashboard",
    summary="Gesundheits-Dashboard",
    description="**Vollständiger Gesundheitsstatus** — aggregiert "
    "Ernährung, Gewicht, Training, Schlaf, Hologramm und Vitalwerte "
    "in einer einzigen Response für das Frontend-Dashboard.",
)
async def get_health_dashboard(
    svc: HealthBioService = Depends(get_health_service),
) -> dict:
    """
    Vollständiges Gesundheits-Dashboard.

    Kombiniert alle Teilabfragen in einer Response — spart
    Roundtrips vom Frontend.
    """
    logger.info("GET /health/dashboard.")

    try:
        return svc.generate_nutritional_status()

    except Exception as exc:
        logger.exception("Fehler beim Gesundheits-Dashboard.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc
