"""
Gesundheits- und Biometrie-Dienst des Homelab-Imperiums.

Verarbeitet Vitaldaten, berechnet Ernährungsmetriken und aggregiert
Symptomdaten für das 3D-Körper-Hologramm. Greift auf die
``health_records``-PostgreSQL-Tabelle zu.

Funktionen:
- Kalorienbilanz & Makronährstoff-Verteilung
- Glykämische Last von Mahlzeiten
- Gewichts-Tracking & Trendanalyse
- Symptom-Aggregation → 3D-Hologramm-Koordinaten
- Trainingsvolumen & Schlafqualität
- Wasserbilanz & Mikronährstoff-Tracking

Verwendung::

    from app.services.health_bio import HealthBioService
    from app.database import get_db_context

    with get_db_context() as db:
        svc = HealthBioService(db)
        status = svc.get_daily_nutrition_status()
        hologram = svc.get_hologram_data()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, text
from sqlalchemy.orm import Session

from app.models import HealthRecord

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger(
    "homelab_imperium.services.health_bio"
)


# ═══════════════════════════════════════════════════════════════════════════════
# Datenklassen
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class NutritionStatus:
    """Täglicher Ernährungsstatus."""

    date: str = ""
    calories_target: float = 2300.0
    calories_consumed: float = 0.0
    calories_burned_exercise: float = 0.0
    net_calories: float = 0.0  # Konsumiert − (Grundumsatz-Anteil + Training)
    protein_grams: float = 0.0
    carbs_grams: float = 0.0
    fat_grams: float = 0.0
    fiber_grams: float = 0.0
    water_ml: float = 0.0
    water_target_ml: float = 2500.0
    meal_count: int = 0
    glycemic_load_total: float = 0.0
    is_deficit: bool = False
    is_surplus: bool = False


@dataclass
class WeightRecord:
    """Ein einzelner Gewichtseintrag."""

    date: str
    weight_kg: float
    body_fat_pct: Optional[float] = None


@dataclass
class WeightTrend:
    """Gewichts-Trend über einen Zeitraum."""

    current_weight_kg: float = 0.0
    weight_7d_ago_kg: Optional[float] = None
    weight_30d_ago_kg: Optional[float] = None
    trend_7d_kg: float = 0.0
    trend_30d_kg: float = 0.0
    avg_weekly_change_kg: float = 0.0
    records: list[WeightRecord] = field(default_factory=list)


@dataclass
class HologramAnomaly:
    """
    Eine aktive Anomalie für das 3D-Körper-Hologramm.

    Die ``location`` MUSS einem der 51 gültigen Hologramm-Strings
    entsprechen (siehe ``config/agents/medical_health.yaml``).
    """

    location: str
    intensity: str  # high, medium, low
    cause: str
    recorded_at: str = ""
    record_id: int = 0


@dataclass
class HologramStatus:
    """Gesamtstatus des 3D-Körper-Hologramms."""

    anomalies: list[HologramAnomaly] = field(default_factory=list)
    total_active: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0


@dataclass
class ExerciseSummary:
    """Trainingszusammenfassung."""

    date: str = ""
    total_duration_min: float = 0.0
    total_calories_burned: float = 0.0
    workout_count: int = 0
    avg_heart_rate: Optional[float] = None


@dataclass
class SleepSummary:
    """Schlafzusammenfassung."""

    date: str = ""
    duration_hours: float = 0.0
    quality: Optional[float] = None  # 1–5
    records_count: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# HealthBioService
# ═══════════════════════════════════════════════════════════════════════════════


class HealthBioService:
    """
    Biometrie- und Gesundheitsdienst.

    Führt SQLAlchemy-Abfragen auf ``health_records`` durch und
    berechnet Ernährung, Gewichtstrends und Hologramm-Daten.
    """

    # Glykämischer Index (GI)-Referenztabelle (vereinfacht)
    # glykämische_last = (GI × Kohlenhydrate_g) / 100
    _GI_TABLE: dict[str, float] = {
        "weißbrot": 75,
        "vollkornbrot": 50,
        "reis_weiss": 73,
        "reis_vollkorn": 55,
        "nudeln": 50,
        "kartoffeln": 78,
        "süßkartoffeln": 50,
        "banane": 55,
        "apfel": 38,
        "orange": 42,
        "trauben": 55,
        "schokolade": 40,
        "zucker": 65,
        "haferflocken": 55,
        "quinoa": 53,
        "linsen": 28,
        "kichererbsen": 33,
        "joghurt": 35,
        "milch": 30,
    }

    def __init__(self, db: Session) -> None:
        """
        Args:
            db: SQLAlchemy-Datenbank-Session.
        """
        self.db: Session = db

    # ──────────────────────────────────────────────────────────────────────
    # Ernährungsstatus
    # ──────────────────────────────────────────────────────────────────────

    def get_daily_nutrition_status(
        self,
        target_date: date | None = None,
    ) -> NutritionStatus:
        """
        Berechnet den vollständigen Ernährungsstatus für einen Tag.

        Aggregiert alle Mahlzeiten-Einträge (``record_type='meal'``)
        des Tages und berechnet Kalorien, Makronährstoffe, Wasser und
        glykämische Last.

        Args:
            target_date: Datum (Default: heute UTC).

        Returns:
            ``NutritionStatus`` mit allen aggregierten Werten.
        """
        dt: date = target_date or date.today()
        logger.debug("Berechne Ernährungsstatus für %s...", dt)

        start_dt: datetime = datetime.combine(dt, datetime.min.time())
        end_dt: datetime = start_dt + timedelta(days=1)

        # Alle Mahlzeiten des Tages
        meals = (
            self.db.query(HealthRecord)
            .filter(
                HealthRecord.record_type == "meal",
                HealthRecord.timestamp >= start_dt,
                HealthRecord.timestamp < end_dt,
            )
            .all()
        )

        # Aggregation
        total_kcal: float = sum(m.val1 or 0 for m in meals)
        total_protein: float = sum(m.val2 or 0 for m in meals)
        total_carbs: float = sum(m.val3 or 0 for m in meals)
        # Fett indirekt: Restkalorien / 9
        kcal_from_pc: float = (total_protein * 4) + (total_carbs * 4)
        total_fat: float = (
            max(0, total_kcal - kcal_from_pc) / 9 if total_kcal > 0 else 0
        )

        # Wasseraufnahme (record_type='water')
        water_result = (
            self.db.query(
                func.coalesce(func.sum(HealthRecord.val1), 0)
            )
            .filter(
                HealthRecord.record_type == "water",
                HealthRecord.timestamp >= start_dt,
                HealthRecord.timestamp < end_dt,
            )
            .scalar()
        )
        total_water: float = float(water_result or 0)

        # Trainingskalorien (record_type='workout')
        workout_result = (
            self.db.query(
                func.coalesce(func.sum(HealthRecord.val2), 0)
            )
            .filter(
                HealthRecord.record_type == "workout",
                HealthRecord.timestamp >= start_dt,
                HealthRecord.timestamp < end_dt,
            )
            .scalar()
        )
        workout_kcal: float = float(workout_result or 0)

        # Glykämische Last abschätzen
        gl_total: float = self._estimate_glycemic_load(
            [m.description or "" for m in meals], total_carbs
        )

        # Netto-Kalorien (vereinfacht: Kalorien − Trainingskalorien)
        net_kcal: float = total_kcal - workout_kcal

        status: NutritionStatus = NutritionStatus(
            date=dt.isoformat(),
            calories_target=2300.0,
            calories_consumed=round(total_kcal, 1),
            calories_burned_exercise=round(workout_kcal, 1),
            net_calories=round(net_kcal, 1),
            protein_grams=round(total_protein, 1),
            carbs_grams=round(total_carbs, 1),
            fat_grams=round(total_fat, 1),
            fiber_grams=0.0,  # Nicht direkt trackbar ohne detaillierte DB
            water_ml=round(total_water, 0),
            water_target_ml=2500.0,
            meal_count=len(meals),
            glycemic_load_total=round(gl_total, 1),
            is_deficit=net_kcal < 0,
            is_surplus=net_kcal > 0,
        )

        logger.info(
            "Ernährungsstatus %s: %.0f kcal (Ziel: %.0f), "
            "P: %.0fg, C: %.0fg, F: %.0fg, Wasser: %.0f ml, "
            "%d Mahlzeiten.",
            dt,
            total_kcal,
            status.calories_target,
            total_protein,
            total_carbs,
            total_fat,
            total_water,
            len(meals),
        )
        return status

    def _estimate_glycemic_load(
        self,
        descriptions: list[str],
        total_carbs: float,
    ) -> float:
        """
        Schätzt die glykämische Last basierend auf Lebensmittel-Beschreibungen.

        Vereinfachte Berechnung:
        ``GL = (geschätzter_GI × Kohlenhydrate_g) / 100``

        Args:
            descriptions: Mahlzeit-Beschreibungen aus der DB.
            total_carbs: Gesamt-Kohlenhydrate in Gramm.

        Returns:
            Geschätzte glykämische Last.
        """
        if total_carbs <= 0:
            return 0.0

        # Durchschnittlichen GI aus Beschreibungen schätzen
        matched_gis: list[float] = []
        for desc in descriptions:
            desc_lower: str = desc.lower()
            for food, gi in self._GI_TABLE.items():
                if food in desc_lower:
                    matched_gis.append(gi)
                    break

        avg_gi: float = (
            sum(matched_gis) / len(matched_gis)
            if matched_gis
            else 60.0  # Default: mittlerer GI
        )

        return (avg_gi * total_carbs) / 100.0

    # ──────────────────────────────────────────────────────────────────────
    # Gewichts-Tracking
    # ──────────────────────────────────────────────────────────────────────

    def get_weight_trend(self, days: int = 30) -> WeightTrend:
        """
        Ermittelt den Gewichtsverlauf über einen Zeitraum.

        Args:
            days: Anzahl der zurückliegenden Tage.

        Returns:
            ``WeightTrend`` mit aktuellen Werten und Wochentrend.
        """
        logger.debug("Ermittle Gewichts-Trend (%d Tage)...", days)

        start_dt: datetime = datetime.utcnow() - timedelta(days=days)

        records = (
            self.db.query(HealthRecord)
            .filter(
                HealthRecord.record_type == "weight",
                HealthRecord.timestamp >= start_dt,
            )
            .order_by(HealthRecord.timestamp.desc())
            .all()
        )

        weight_records: list[WeightRecord] = []
        for r in records:
            weight_records.append(
                WeightRecord(
                    date=r.timestamp.strftime("%Y-%m-%d"),
                    weight_kg=r.val1 or 0.0,
                    body_fat_pct=r.val2,
                )
            )

        current: float = weight_records[0].weight_kg if weight_records else 0.0

        # Gewicht vor ~7 Tagen und ~30 Tagen
        w_7d: Optional[float] = None
        w_30d: Optional[float] = None
        cutoff_7d: datetime = datetime.utcnow() - timedelta(days=7)
        cutoff_30d: datetime = datetime.utcnow() - timedelta(days=30)

        for wr in weight_records:
            record_date: datetime = (
                datetime.strptime(wr.date, "%Y-%m-%d")
                if wr.date
                else datetime.min
            )
            if w_7d is None and record_date <= cutoff_7d:
                w_7d = wr.weight_kg
            if w_30d is None and record_date <= cutoff_30d:
                w_30d = wr.weight_kg

        trend_7d: float = (
            round(current - w_7d, 1) if w_7d is not None else 0.0
        )
        trend_30d: float = (
            round(current - w_30d, 1) if w_30d is not None else 0.0
        )

        # Durchschnittliche wöchentliche Änderung
        weeks: float = days / 7.0
        avg_weekly: float = round(trend_30d / weeks, 2) if weeks > 0 else 0.0

        logger.info(
            "Gewicht: aktuell=%.1f kg, Δ7d=%.1f kg, Δ30d=%.1f kg, "
            "Ø/Woche=%.2f kg.",
            current,
            trend_7d,
            trend_30d,
            avg_weekly,
        )

        return WeightTrend(
            current_weight_kg=current,
            weight_7d_ago_kg=w_7d,
            weight_30d_ago_kg=w_30d,
            trend_7d_kg=trend_7d,
            trend_30d_kg=trend_30d,
            avg_weekly_change_kg=avg_weekly,
            records=weight_records,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Hologramm-Daten — Symptom → 3D-Koordinate
    # ──────────────────────────────────────────────────────────────────────

    def get_hologram_data(self) -> HologramStatus:
        """
        Aggregiert alle aktiven Symptome für das 3D-Körper-Hologramm.

        Sucht in ``health_records`` nach Einträgen mit
        ``record_type='symptom'``, die nicht als abgeklungen markiert
        sind (``intensity != 'low'`` ODER Alter < 30 Tage für 'low').

        Returns:
            ``HologramStatus`` mit Liste aller Anomalien.
        """
        logger.debug("Sammle Hologramm-Daten...")

        # Alle Symptom-Einträge
        symptoms = (
            self.db.query(HealthRecord)
            .filter(
                HealthRecord.record_type == "symptom",
                HealthRecord.symptom_location.isnot(None),
            )
            .order_by(HealthRecord.timestamp.desc())
            .all()
        )

        anomalies: list[HologramAnomaly] = []
        high_count: int = 0
        medium_count: int = 0
        low_count: int = 0

        for s in symptoms:
            intensity: str = s.intensity or "medium"

            # Als "aktiv" zählen:
            # - high/medium: immer
            # - low: nur wenn jünger als 30 Tage
            is_active: bool = True
            if intensity == "low" and s.timestamp:
                age_days: float = (
                    datetime.utcnow() - s.timestamp
                ).total_seconds() / 86400
                if age_days > 30:
                    is_active = False

            if is_active and s.symptom_location:
                anomalies.append(
                    HologramAnomaly(
                        location=s.symptom_location,
                        intensity=intensity,
                        cause=s.description or "Unbekannte Ursache",
                        recorded_at=(
                            s.timestamp.isoformat()
                            if s.timestamp
                            else ""
                        ),
                        record_id=s.id,
                    )
                )

                if intensity == "high":
                    high_count += 1
                elif intensity == "medium":
                    medium_count += 1
                elif intensity == "low":
                    low_count += 1

        # Sortieren: Schwerste zuerst, dann chronologisch neueste
        intensity_order: dict[str, int] = {
            "high": 0, "medium": 1, "low": 2
        }
        anomalies.sort(
            key=lambda a: (
                intensity_order.get(a.intensity, 9),
                a.recorded_at,
            ),
        )

        logger.info(
            "Hologramm: %d aktive Anomalien (🔴%d 🟠%d 🟡%d).",
            len(anomalies),
            high_count,
            medium_count,
            low_count,
        )

        return HologramStatus(
            anomalies=anomalies,
            total_active=len(anomalies),
            high_count=high_count,
            medium_count=medium_count,
            low_count=low_count,
        )

    def get_symptoms_by_location(
        self,
        location: str,
    ) -> list[HologramAnomaly]:
        """
        Ruft alle Symptome für eine bestimmte Körperregion ab.

        Args:
            location: Gültiger Hologramm-String (z.B. ``"knee_L"``).

        Returns:
            Liste von ``HologramAnomaly`` für diese Region.
        """
        logger.debug("Rufe Symptome für Location %r ab.", location)

        records = (
            self.db.query(HealthRecord)
            .filter(
                HealthRecord.record_type == "symptom",
                HealthRecord.symptom_location == location,
            )
            .order_by(HealthRecord.timestamp.desc())
            .all()
        )

        return [
            HologramAnomaly(
                location=r.symptom_location or location,
                intensity=r.intensity or "medium",
                cause=r.description or "Unbekannt",
                recorded_at=(
                    r.timestamp.isoformat() if r.timestamp else ""
                ),
                record_id=r.id,
            )
            for r in records
        ]

    # ──────────────────────────────────────────────────────────────────────
    # Trainings-Analyse
    # ──────────────────────────────────────────────────────────────────────

    def get_exercise_summary(
        self,
        target_date: date | None = None,
    ) -> ExerciseSummary:
        """
        Fasst die Trainingsdaten eines Tages zusammen.

        Args:
            target_date: Datum (Default: heute).

        Returns:
            ``ExerciseSummary`` mit Dauer, Kalorien und Workout-Zählern.
        """
        dt: date = target_date or date.today()
        start_dt: datetime = datetime.combine(dt, datetime.min.time())
        end_dt: datetime = start_dt + timedelta(days=1)

        workouts = (
            self.db.query(HealthRecord)
            .filter(
                HealthRecord.record_type == "workout",
                HealthRecord.timestamp >= start_dt,
                HealthRecord.timestamp < end_dt,
            )
            .all()
        )

        total_min: float = sum(w.val1 or 0 for w in workouts)
        total_kcal: float = sum(w.val2 or 0 for w in workouts)
        heart_rates: list[float] = [
            w.val3 for w in workouts if w.val3 is not None
        ]
        avg_hr: Optional[float] = (
            sum(heart_rates) / len(heart_rates) if heart_rates else None
        )

        logger.debug(
            "Training %s: %.0f min, %.0f kcal, %d Workouts.",
            dt,
            total_min,
            total_kcal,
            len(workouts),
        )

        return ExerciseSummary(
            date=dt.isoformat(),
            total_duration_min=round(total_min, 0),
            total_calories_burned=round(total_kcal, 0),
            workout_count=len(workouts),
            avg_heart_rate=(
                round(avg_hr, 0) if avg_hr is not None else None
            ),
        )

    def get_exercise_history(
        self,
        days: int = 30,
    ) -> list[ExerciseSummary]:
        """
        Erstellt eine Trainings-Historie über mehrere Tage.

        Args:
            days: Anzahl der zurückliegenden Tage.

        Returns:
            Liste von ``ExerciseSummary`` pro Tag.
        """
        logger.debug("Erstelle Trainings-Historie (%d Tage)...", days)

        start_dt: datetime = datetime.utcnow() - timedelta(days=days)

        rows = (
            self.db.query(
                func.date(HealthRecord.timestamp).label("day"),
                func.sum(HealthRecord.val1).label("total_min"),
                func.sum(HealthRecord.val2).label("total_kcal"),
                func.count(HealthRecord.id).label("count"),
                func.avg(HealthRecord.val3).label("avg_hr"),
            )
            .filter(
                HealthRecord.record_type == "workout",
                HealthRecord.timestamp >= start_dt,
            )
            .group_by(func.date(HealthRecord.timestamp))
            .order_by(text("day ASC"))
            .all()
        )

        history: list[ExerciseSummary] = []
        for row in rows:
            history.append(
                ExerciseSummary(
                    date=str(row[0]),
                    total_duration_min=round(float(row[1] or 0), 0),
                    total_calories_burned=round(float(row[2] or 0), 0),
                    workout_count=int(row[3] or 0),
                    avg_heart_rate=(
                        round(float(row[4]), 0)
                        if row[4] is not None
                        else None
                    ),
                )
            )

        logger.info(
            "%d Trainingstage in %d Tagen analysiert.",
            len(history),
            days,
        )
        return history

    # ──────────────────────────────────────────────────────────────────────
    # Schlaf-Analyse
    # ──────────────────────────────────────────────────────────────────────

    def get_sleep_summary(
        self,
        target_date: date | None = None,
    ) -> SleepSummary:
        """
        Fasst die Schlafdaten eines Tages zusammen.

        Args:
            target_date: Datum (Default: heute).

        Returns:
            ``SleepSummary`` mit Dauer und Qualität.
        """
        dt: date = target_date or date.today()
        start_dt: datetime = datetime.combine(dt, datetime.min.time())
        end_dt: datetime = start_dt + timedelta(days=1)

        records = (
            self.db.query(HealthRecord)
            .filter(
                HealthRecord.record_type == "sleep",
                HealthRecord.timestamp >= start_dt,
                HealthRecord.timestamp < end_dt,
            )
            .all()
        )

        total_hours: float = sum(r.val1 or 0 for r in records)
        qualities: list[float] = [
            r.val2 for r in records if r.val2 is not None
        ]
        avg_quality: Optional[float] = (
            sum(qualities) / len(qualities) if qualities else None
        )

        logger.debug(
            "Schlaf %s: %.1f h, Qualität=%s, %d Einträge.",
            dt,
            total_hours,
            f"{avg_quality:.1f}/5" if avg_quality else "?",
            len(records),
        )

        return SleepSummary(
            date=dt.isoformat(),
            duration_hours=round(total_hours, 1),
            quality=(
                round(avg_quality, 1) if avg_quality is not None else None
            ),
            records_count=len(records),
        )

    # ──────────────────────────────────────────────────────────────────────
    # Vitalwerte
    # ──────────────────────────────────────────────────────────────────────

    def get_latest_vitals(self) -> dict:
        """
        Ruft die aktuellsten Vitalwerte ab.

        Returns:
            Dict mit Blutdruck, Herzfrequenz und Messzeitpunkt.
        """
        latest = (
            self.db.query(HealthRecord)
            .filter(HealthRecord.record_type == "vitals")
            .order_by(HealthRecord.timestamp.desc())
            .first()
        )

        if not latest:
            return {
                "blood_pressure_systolic": None,
                "blood_pressure_diastolic": None,
                "resting_heart_rate": None,
                "measured_at": None,
            }

        return {
            "blood_pressure_systolic": (
                round(latest.val1, 0) if latest.val1 else None
            ),
            "blood_pressure_diastolic": (
                round(latest.val2, 0) if latest.val2 else None
            ),
            "resting_heart_rate": (
                round(latest.val3, 0) if latest.val3 else None
            ),
            "measured_at": (
                latest.timestamp.isoformat() if latest.timestamp else None
            ),
        }

    # ──────────────────────────────────────────────────────────────────────
    # Gesamt-Status (Dashboard-Aggregat)
    # ──────────────────────────────────────────────────────────────────────

    def generate_nutritional_status(self) -> dict:
        """
        Erzeugt den vollständigen Gesundheits-Dashboard-Status.

        Aggregiert Ernährung, Gewicht, Training, Schlaf und
        Hologramm-Daten in einem Dict für das Frontend.

        Returns:
            Dict mit allen Dashboard-Widget-Daten.
        """
        today: date = date.today()
        nutrition: NutritionStatus = self.get_daily_nutrition_status(today)
        weight: WeightTrend = self.get_weight_trend(days=30)
        exercise: ExerciseSummary = self.get_exercise_summary(today)
        sleep: SleepSummary = self.get_sleep_summary(today)
        hologram: HologramStatus = self.get_hologram_data()
        vitals: dict = self.get_latest_vitals()

        # Makro-Prozent berechnen
        total_macro_kcal: float = (
            nutrition.protein_grams * 4
            + nutrition.carbs_grams * 4
            + nutrition.fat_grams * 9
        )
        protein_pct: float = (
            (nutrition.protein_grams * 4 / total_macro_kcal * 100)
            if total_macro_kcal > 0
            else 0
        )
        carbs_pct: float = (
            (nutrition.carbs_grams * 4 / total_macro_kcal * 100)
            if total_macro_kcal > 0
            else 0
        )
        fat_pct: float = (
            (nutrition.fat_grams * 9 / total_macro_kcal * 100)
            if total_macro_kcal > 0
            else 0
        )

        result: dict = {
            "date": today.isoformat(),
            "nutrition": {
                "calories_target": nutrition.calories_target,
                "calories_consumed": nutrition.calories_consumed,
                "calories_burned_exercise": nutrition.calories_burned_exercise,
                "net_calories": nutrition.net_calories,
                "is_deficit": nutrition.is_deficit,
                "is_surplus": nutrition.is_surplus,
                "macros": {
                    "protein_grams": nutrition.protein_grams,
                    "carbs_grams": nutrition.carbs_grams,
                    "fat_grams": round(nutrition.fat_grams, 1),
                    "protein_pct": round(protein_pct, 1),
                    "carbs_pct": round(carbs_pct, 1),
                    "fat_pct": round(fat_pct, 1),
                },
                "glycemic_load": nutrition.glycemic_load_total,
                "water_ml": nutrition.water_ml,
                "water_target_ml": nutrition.water_target_ml,
                "meal_count": nutrition.meal_count,
            },
            "weight": {
                "current_kg": weight.current_weight_kg,
                "trend_7d_kg": weight.trend_7d_kg,
                "trend_30d_kg": weight.trend_30d_kg,
                "avg_weekly_change_kg": weight.avg_weekly_change_kg,
            },
            "exercise": {
                "duration_min": exercise.total_duration_min,
                "calories_burned": exercise.total_calories_burned,
                "workout_count": exercise.workout_count,
                "avg_heart_rate": exercise.avg_heart_rate,
            },
            "sleep": {
                "duration_hours": sleep.duration_hours,
                "quality": sleep.quality,
            },
            "hologram": {
                "total_anomalies": hologram.total_active,
                "high_count": hologram.high_count,
                "medium_count": hologram.medium_count,
                "low_count": hologram.low_count,
                "anomalies": [
                    {
                        "location": a.location,
                        "intensity": a.intensity,
                        "cause": a.cause,
                    }
                    for a in hologram.anomalies
                ],
            },
            "vitals": vitals,
        }

        logger.info(
            "Gesundheits-Dashboard generiert: %.0f kcal, %.1f kg, "
            "%d Workouts, %.1f h Schlaf, %d Anomalien.",
            nutrition.calories_consumed,
            weight.current_weight_kg,
            exercise.workout_count,
            sleep.duration_hours,
            hologram.total_active,
        )
        return result
