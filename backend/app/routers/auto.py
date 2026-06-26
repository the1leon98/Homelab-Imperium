"""
Automotive-Router des Homelab-Imperiums.

Stellt REST-Endpunkte für Fahrzeugverwaltung, Wartungsstatus,
Motorberechnungen und CAD-Generierung bereit. Rechenintensive
CAD-Jobs werden als asynchrone Background-Tasks gestartet.

Endpunkte:
- ``GET /api/auto/vehicles``            — Alle Fahrzeuge
- ``POST /api/auto/vehicles``           — Fahrzeug erstellen
- ``GET /api/auto/vehicles/{id}``       — Fahrzeug-Details
- ``GET /api/auto/vehicles/{id}/maintenance`` — Wartungsstatus
- ``POST /api/auto/maintenance``        — Wartungseintrag erstellen
- ``GET /api/auto/calculate/engine``    — Motorparameter berechnen
- ``GET /api/auto/calculate/performance`` — Leistung schätzen
- ``GET /api/auto/calculate/turbo``     — Turbo-Auslegung
- ``POST /api/auto/cad/generate``       — CAD-Job starten (Background Task)
- ``GET /api/auto/cad/status/{job_id}`` — CAD-Job-Status abfragen

Verwendung::

    from app.routers.auto import router
    app.include_router(router, prefix="/api")
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import MaintenanceRecord, Vehicle
from app.schemas import (
    MaintenanceRecordCreate,
    VehicleCreate,
    VehicleResponse,
    VehicleUpdate,
)
from app.services.auto import AutoWorkbenchService

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.routers.auto")

# ═══════════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════════

router: APIRouter = APIRouter(tags=["Automotive"])

# ═══════════════════════════════════════════════════════════════════════════════
# Dependency Injection
# ═══════════════════════════════════════════════════════════════════════════════


def get_auto_service(
    db: Session = Depends(get_db),
) -> AutoWorkbenchService:
    """Factory für AutoWorkbenchService mit DB-Session."""
    return AutoWorkbenchService(db)


# ═══════════════════════════════════════════════════════════════════════════════
# In-Memory-Job-Tracker (für Background-Tasks)
# ═══════════════════════════════════════════════════════════════════════════════

_cad_jobs: dict[str, dict] = {}
"""Speichert Status aller laufenden CAD-Jobs: {job_id: {status, result, error}}."""


# ═══════════════════════════════════════════════════════════════════════════════
# Fahrzeug-CRUD
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/auto/vehicles",
    response_model=list[VehicleResponse],
    summary="Alle Fahrzeuge",
    description="Liste aller Fahrzeuge mit Stammdaten, Kilometerstand "
    "und Schadensstatus.",
)
async def get_all_vehicles(
    db: Session = Depends(get_db),
) -> list[VehicleResponse]:
    """
    Fahrzeugliste — sortiert nach Name.
    """
    logger.info("GET /auto/vehicles.")

    vehicles = (
        db.query(Vehicle)
        .order_by(Vehicle.name)
        .all()
    )
    return vehicles


@router.post(
    "/auto/vehicles",
    response_model=VehicleResponse,
    status_code=201,
    summary="Fahrzeug erstellen",
)
async def create_vehicle(
    vehicle: VehicleCreate,
    db: Session = Depends(get_db),
) -> VehicleResponse:
    """
    Neues Fahrzeug in der Datenbank anlegen.
    """
    logger.info("POST /auto/vehicles: name=%r, type=%s.", vehicle.name, vehicle.vehicle_type)

    try:
        new_vehicle: Vehicle = Vehicle(
            name=vehicle.name,
            vehicle_type=vehicle.vehicle_type.value,
            odometer_km=vehicle.odometer_km,
            year_of_manufacture=vehicle.year_of_manufacture,
            license_plate=vehicle.license_plate,
            vin=vehicle.vin,
            oil_change_due_km=vehicle.oil_change_due_km,
            oil_change_due_date=vehicle.oil_change_due_date,
            tire_change_due_date=vehicle.tire_change_due_date,
            inspection_due_date=vehicle.inspection_due_date,
            is_damaged=vehicle.is_damaged,
            damaged_parts_json=vehicle.damaged_parts_json,
            model_3d_path=vehicle.model_3d_path,
            image_path=vehicle.image_path,
            notes=vehicle.notes,
        )
        db.add(new_vehicle)
        db.commit()
        db.refresh(new_vehicle)

        logger.info("Fahrzeug erstellt: id=%d.", new_vehicle.id)
        return new_vehicle

    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Erstellen des Fahrzeugs.")
        raise HTTPException(
            status_code=500,
            detail=f"Fahrzeug konnte nicht erstellt werden: {exc}",
        ) from exc


@router.get(
    "/auto/vehicles/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Fahrzeug-Details",
)
async def get_vehicle(
    vehicle_id: int,
    db: Session = Depends(get_db),
) -> VehicleResponse:
    """
    Einzelfahrzeug mit allen Metadaten und Wartungshistorie.
    """
    logger.info("GET /auto/vehicles/%d.", vehicle_id)

    vehicle: Vehicle | None = (
        db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    )
    if not vehicle:
        raise HTTPException(
            status_code=404,
            detail=f"Fahrzeug {vehicle_id} nicht gefunden.",
        )
    return vehicle


@router.put(
    "/auto/vehicles/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Fahrzeug aktualisieren",
)
async def update_vehicle(
    vehicle_id: int,
    update: VehicleUpdate,
    db: Session = Depends(get_db),
) -> VehicleResponse:
    """Partielles Update eines Fahrzeugs."""
    logger.info("PUT /auto/vehicles/%d.", vehicle_id)

    try:
        vehicle: Vehicle | None = (
            db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
        )
        if not vehicle:
            raise HTTPException(
                status_code=404,
                detail=f"Fahrzeug {vehicle_id} nicht gefunden.",
            )

        update_data: dict = update.model_dump(exclude_unset=True)
        if "vehicle_type" in update_data and update_data["vehicle_type"]:
            update_data["vehicle_type"] = update_data["vehicle_type"].value

        for field, value in update_data.items():
            setattr(vehicle, field, value)

        db.commit()
        db.refresh(vehicle)
        logger.info("Fahrzeug %d aktualisiert.", vehicle_id)
        return vehicle

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Aktualisieren.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# Wartungsstatus
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/auto/vehicles/{vehicle_id}/maintenance",
    summary="Wartungsstatus",
    description="Detaillierter Wartungsstatus: Ölwechsel, HU/TÜV, "
    "Reifen, Kostenübersicht und nächste Fälligkeiten.",
)
async def get_maintenance_status(
    vehicle_id: int,
    svc: AutoWorkbenchService = Depends(get_auto_service),
) -> dict:
    """
    Wartungsstatus eines Fahrzeugs mit Fälligkeitswarnungen.
    """
    logger.info("GET /auto/vehicles/%d/maintenance.", vehicle_id)

    try:
        status = svc.get_maintenance_status(vehicle_id)
        return {
            "vehicle_id": status.vehicle_id,
            "vehicle_name": status.vehicle_name,
            "odometer_km": status.odometer_km,
            "oil_change_due_km": status.oil_change_due_km,
            "oil_change_overdue_km": status.oil_change_overdue_km,
            "inspection_due_date": status.inspection_due_date,
            "inspection_overdue_days": status.inspection_overdue_days,
            "tire_change_due_date": status.tire_change_due_date,
            "total_maintenance_cost_eur": status.total_maintenance_cost_eur,
            "last_service_date": status.last_service_date,
            "maintenance_count": status.maintenance_count,
        }

    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Fehler beim Wartungsstatus.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


@router.get(
    "/auto/maintenance/check-all",
    summary="Alle Fahrzeuge — Wartungscheck",
    description="Prüft ALLE Fahrzeuge auf fällige Wartungen.",
)
async def check_all_maintenance(
    svc: AutoWorkbenchService = Depends(get_auto_service),
) -> list[dict]:
    """Liste aller Fahrzeuge mit Wartungsbedarf."""
    logger.info("GET /auto/maintenance/check-all.")
    return svc.check_all_vehicles_maintenance()


@router.post(
    "/auto/maintenance",
    status_code=201,
    summary="Wartungseintrag erstellen",
)
async def create_maintenance_record(
    record: MaintenanceRecordCreate,
    db: Session = Depends(get_db),
) -> dict:
    """
    Neuen Wartungseintrag für ein Fahrzeug anlegen.
    """
    logger.info(
        "POST /auto/maintenance: vehicle=%d, type=%s, date=%s.",
        record.vehicle_id,
        record.maintenance_type,
        record.date,
    )

    vehicle: Vehicle | None = (
        db.query(Vehicle).filter(Vehicle.id == record.vehicle_id).first()
    )
    if not vehicle:
        raise HTTPException(
            status_code=404,
            detail=f"Fahrzeug {record.vehicle_id} nicht gefunden.",
        )

    try:
        new_maint: MaintenanceRecord = MaintenanceRecord(
            vehicle_id=record.vehicle_id,
            maintenance_type=record.maintenance_type.value,
            description=record.description,
            date=record.date,
            odometer_at_service_km=record.odometer_at_service_km,
            cost_eur=(
                Decimal(str(record.cost_eur))
                if record.cost_eur
                else None
            ),
            workshop=record.workshop,
            next_due_km=record.next_due_km,
            next_due_date=record.next_due_date,
            invoice_path=record.invoice_path,
            notes=record.notes,
        )
        db.add(new_maint)

        # Fahrzeug-Kilometerstand aktualisieren
        if record.odometer_at_service_km:
            vehicle.odometer_km = record.odometer_at_service_km

        # Nächste Fälligkeit aktualisieren
        if record.next_due_km:
            vehicle.oil_change_due_km = record.next_due_km
        if record.next_due_date:
            vehicle.oil_change_due_date = record.next_due_date

        db.commit()
        db.refresh(new_maint)

        logger.info("Wartungseintrag erstellt: id=%d.", new_maint.id)
        return {
            "id": new_maint.id,
            "vehicle_id": new_maint.vehicle_id,
            "maintenance_type": new_maint.maintenance_type,
            "date": new_maint.date.isoformat(),
            "cost_eur": float(new_maint.cost_eur) if new_maint.cost_eur else None,
        }

    except Exception as exc:
        db.rollback()
        logger.exception("Fehler beim Erstellen des Wartungseintrags.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# Motorberechnungen
# ═══════════════════════════════════════════════════════════════════════════════


class EngineCalcRequest(BaseModel):
    """Query-Parameter für Motorberechnung."""

    displacement_cc: float = Field(..., gt=0, description="Hubraum in cm³.")
    cylinder_count: int = Field(..., ge=1, le=16, description="Zylinderanzahl.")
    bore_mm: Optional[float] = Field(default=None, gt=0, description="Bohrung in mm.")
    stroke_mm: Optional[float] = Field(default=None, gt=0, description="Hub in mm.")
    compression_ratio: Optional[float] = Field(default=None, gt=0, description="Verdichtungsverhältnis ε.")


@router.get(
    "/auto/calculate/engine",
    summary="Motorparameter berechnen",
    description="Berechnet Hubraum, Bohrung, Hub und Verdichtungsverhältnis "
    "basierend auf gegebenen Eingangswerten.",
)
async def calculate_engine(
    displacement_cc: float = Query(..., gt=0, description="Hubraum in cm³."),
    cylinder_count: int = Query(..., ge=1, le=16, description="Zylinderanzahl."),
    bore_mm: Optional[float] = Query(default=None, gt=0),
    stroke_mm: Optional[float] = Query(default=None, gt=0),
    compression_ratio: Optional[float] = Query(default=None, gt=0),
    svc: AutoWorkbenchService = Depends(get_auto_service),
) -> dict:
    """
    Motor-Geometrieberechnung.

    Formeln: V_hz = (π/4) × d² × s, ε = V_hz/V_c + 1
    """
    logger.info(
        "GET /auto/calculate/engine: %.1f cc, %d Zyl.",
        displacement_cc,
        cylinder_count,
    )

    try:
        params = svc.calculate_engine_parameters(
            displacement_cc=displacement_cc,
            cylinder_count=cylinder_count,
            bore_mm=bore_mm,
            stroke_mm=stroke_mm,
            compression_ratio=compression_ratio,
        )
        return {
            "displacement_total_cc": params.displacement_total_cc,
            "displacement_total_l": params.displacement_total_l,
            "cylinder_count": params.cylinder_count,
            "displacement_per_cylinder_cc": params.displacement_per_cylinder_cc,
            "bore_mm": params.bore_mm,
            "stroke_mm": params.stroke_mm,
            "bore_stroke_ratio": params.bore_stroke_ratio,
            "compression_ratio": params.compression_ratio,
            "combustion_chamber_volume_cc": params.combustion_chamber_volume_cc,
        }

    except Exception as exc:
        logger.exception("Fehler bei Motorberechnung.")
        raise HTTPException(
            status_code=500, detail=f"Berechnungsfehler: {exc}"
        ) from exc


@router.get(
    "/auto/calculate/performance",
    summary="Leistung schätzen",
    description="Schätzt kW, PS und Nm aus Hubraum und BMEP.",
)
async def estimate_performance(
    displacement_cc: float = Query(..., gt=0),
    cylinder_count: int = Query(..., ge=1, le=16),
    bmep_bar: Optional[float] = Query(default=None, gt=0, description="Effektiver Mitteldruck (Default: 22 bar Turbo)."),
    peak_rpm: int = Query(default=6500, ge=1000, le=12000),
    svc: AutoWorkbenchService = Depends(get_auto_service),
) -> dict:
    """Leistungsschätzung aus BMEP."""
    logger.info("GET /auto/calculate/performance: %.1f cc.", displacement_cc)

    try:
        perf = svc.estimate_performance(
            displacement_cc=displacement_cc,
            cylinder_count=cylinder_count,
            bmep_bar=bmep_bar,
            peak_rpm=peak_rpm,
        )
        return {
            "power_kw": perf.power_kw,
            "power_ps": perf.power_ps,
            "torque_nm": perf.torque_nm,
            "rpm_peak_power": perf.rpm_peak_power,
            "rpm_peak_torque": perf.rpm_peak_torque,
            "mean_piston_speed_ms": perf.mean_piston_speed_ms,
            "bmep_bar": perf.bmep_bar,
        }

    except Exception as exc:
        logger.exception("Fehler bei Leistungsschätzung.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


@router.get(
    "/auto/calculate/turbo",
    summary="Turbolader auslegen",
    description="Berechnet Druckverhältnis und Luftmassenstrom "
    "für eine Zielleistung.",
)
async def calculate_turbo(
    displacement_cc: float = Query(..., gt=0),
    cylinder_count: int = Query(..., ge=1, le=16),
    target_power_ps: float = Query(..., gt=0, description="Zielleistung in PS."),
    baseline_power_ps: float = Query(..., gt=0, description="Serienleistung in PS."),
    baseline_boost_bar: float = Query(default=0.0, description="Serien-Ladedruck (relativ)."),
    svc: AutoWorkbenchService = Depends(get_auto_service),
) -> dict:
    """
    Turbo-Dimensionierung für Tuning-Projekte.

    Berechnet benötigtes Druckverhältnis, Ladedruck und Luftmassenstrom
    (in kg/s und lb/min für Turbolader-Kennfelder).
    """
    logger.info(
        "GET /auto/calculate/turbo: %.0f → %.0f PS.",
        baseline_power_ps,
        target_power_ps,
    )

    try:
        turbo = svc.calculate_turbo_parameters(
            displacement_cc=displacement_cc,
            cylinder_count=cylinder_count,
            target_power_ps=target_power_ps,
            baseline_power_ps=baseline_power_ps,
            baseline_boost_bar=baseline_boost_bar,
        )
        return {
            "target_power_ps": turbo.target_power_ps,
            "required_pressure_ratio": turbo.required_pressure_ratio,
            "required_boost_bar": turbo.required_boost_bar,
            "mass_flow_kg_s": turbo.estimated_mass_flow_kg_s,
            "mass_flow_lb_min": turbo.estimated_mass_flow_lb_min,
            "surge_margin_pct": turbo.surge_margin_pct,
        }

    except Exception as exc:
        logger.exception("Fehler bei Turbo-Berechnung.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# CAD-Generierung — Background Tasks
# ═══════════════════════════════════════════════════════════════════════════════


class CADGenerateRequest(BaseModel):
    """Request-Body für CAD-Generierung."""

    vehicle_id: int = Field(..., ge=1, description="Ziel-Fahrzeug-ID.")
    component: str = Field(
        default="piston",
        pattern=r"^(piston|cylinder_head|intake_manifold)$",
        description="Komponententyp: piston, cylinder_head, intake_manifold.",
    )
    parameters: Optional[str] = Field(
        default=None,
        description="JSON-String mit Override-Parametern (z.B. '{\"bore_mm\": 84.0}').",
    )
    output_format: str = Field(
        default="stl",
        pattern=r"^(stl|step|png|3mf)$",
        description="Ausgabeformat.",
    )


async def _run_cad_job(
    job_id: str,
    vehicle_id: int,
    component: str,
    parameters: dict,
    output_format: str,
) -> None:
    """
    Hintergrundjob: CAD-Generierung via Worker-Client ausführen.

    Wird von FastAPI als BackgroundTask gestartet. Der Status wird in
    ``_cad_jobs`` gespeichert und kann via ``GET /cad/status/{job_id}``
    abgefragt werden.
    """
    _cad_jobs[job_id] = {"status": "running", "result": None, "error": None}
    logger.info("CAD-Background-Job %s gestartet.", job_id)

    try:
        from app.database import SessionLocal

        db: Session = SessionLocal()
        try:
            svc: AutoWorkbenchService = AutoWorkbenchService(db)
            result = await svc.generate_engine_component_cad(
                vehicle_id=vehicle_id,
                component=component,
                parameters=parameters,
            )
            _cad_jobs[job_id] = {
                "status": "completed" if result.status == "completed" else "failed",
                "result": {
                    "output_files": result.output_files,
                    "output_dir": result.output_dir,
                    "duration_seconds": result.duration_seconds,
                },
                "error": result.error_message if result.status == "failed" else None,
            }
        finally:
            db.close()

    except Exception as exc:
        logger.error("CAD-Job %s fehlgeschlagen: %s", job_id, exc)
        _cad_jobs[job_id] = {"status": "failed", "result": None, "error": str(exc)}


@router.post(
    "/auto/cad/generate",
    summary="CAD-Komponente generieren (Background Task)",
    description="Startet die Generierung einer CAD-Komponente "
    "(Kolben, Zylinderkopf, Ansaugkrümmer) als asynchronen "
    "Background-Task. Der Job sendet OpenSCAD-Code an den "
    "GPU-Desktop-Worker und speichert das Ergebnis. "
    "Statusabfrage via ``GET /auto/cad/status/{job_id}``.",
    status_code=202,
)
async def generate_cad_component(
    request: CADGenerateRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Startet einen CAD-Generierungs-Job im Hintergrund.

    Gibt sofort 202 Accepted + job_id zurück. Der Job läuft
    asynchron weiter. Ergebnisabfrage via Status-Endpunkt.

    **Rechenintensive Pipeline:**
    1. OpenSCAD-Code generieren (lokal)
    2. An GPU-Desktop-Worker via WebSocket senden
    3. Worker rendert STL/STEP
    4. Ergebnisdateien herunterladen
    5. In /mnt/data/auto/cad/{vehicle_id}/ speichern
    """
    import uuid

    job_id: str = uuid.uuid4().hex[:12]

    # Parameter parsen
    params: dict = {}
    if request.parameters:
        try:
            params = json.loads(request.parameters)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400,
                detail="parameters muss ein gültiger JSON-String sein.",
            )

    logger.info(
        "POST /auto/cad/generate: job=%s, vehicle=%d, component=%s, "
        "format=%s.",
        job_id,
        request.vehicle_id,
        request.component,
        request.output_format,
    )

    # Initialer Status
    _cad_jobs[job_id] = {"status": "queued", "result": None, "error": None}

    # Background-Task starten
    background_tasks.add_task(
        _run_cad_job,
        job_id=job_id,
        vehicle_id=request.vehicle_id,
        component=request.component,
        parameters=params,
        output_format=request.output_format,
    )

    return {
        "message": "CAD-Job gestartet.",
        "job_id": job_id,
        "status_endpoint": f"/api/auto/cad/status/{job_id}",
    }


@router.get(
    "/auto/cad/status/{job_id}",
    summary="CAD-Job-Status",
    description="Fragt den Status eines laufenden CAD-Jobs ab.",
)
async def get_cad_job_status(job_id: str) -> dict:
    """
    Status eines CAD-Background-Jobs.

    Returns:
        ``{status: "queued"|"running"|"completed"|"failed", result, error}``
    """
    logger.debug("GET /auto/cad/status/%s.", job_id)

    job: dict | None = _cad_jobs.get(job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail=f"Job {job_id!r} nicht gefunden.",
        )

    return {
        "job_id": job_id,
        "status": job["status"],
        "result": job["result"],
        "error": job["error"],
    }
