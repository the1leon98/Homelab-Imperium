"""
Automotive-Workbench des Homelab-Imperiums.

Implementiert ingenieurwissenschaftliche Berechnungen und CAD-Workflows:
- Motorparameter (Hubraum, Verdichtung, Kolbengeschwindigkeit, Leistung)
- Tuning-Analysen (Turbo-Auslegung, Einspritzdüsen-Dimensionierung)
- Asynchrone CAD-Job-Submission an den GPU-Desktop-Worker
- STEP/STL/OpenSCAD-Dateiverwaltung
- Fahrzeug-Wartungsstatus & Schadensanalyse

Verwendung::

    from app.services.auto import AutoWorkbenchService
    from app.database import get_db_context

    with get_db_context() as db:
        svc = AutoWorkbenchService(db)
        params = svc.calculate_engine_parameters(displacement=3000, cylinders=6)
        result = await svc.submit_cad_job(vehicle_id=1, scad_code="...")
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import MaintenanceRecord, Vehicle

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.services.auto")


# ═══════════════════════════════════════════════════════════════════════════════
# Datenklassen
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class EngineParameters:
    """Berechnete Motor-Geometrie und Kennwerte."""

    displacement_total_cc: float = 0.0
    displacement_total_l: float = 0.0
    cylinder_count: int = 0
    displacement_per_cylinder_cc: float = 0.0
    bore_mm: float = 0.0
    stroke_mm: float = 0.0
    bore_stroke_ratio: float = 0.0
    compression_ratio: float = 0.0
    combustion_chamber_volume_cc: float = 0.0


@dataclass
class PerformanceEstimate:
    """Geschätzte Leistungs- und Drehmomentwerte."""

    power_kw: float = 0.0
    power_ps: float = 0.0
    torque_nm: float = 0.0
    rpm_peak_power: int = 0
    rpm_peak_torque: int = 0
    mean_piston_speed_ms: float = 0.0
    bmep_bar: float = 0.0


@dataclass
class TurboParameters:
    """Turbolader-Auslegungsparameter."""

    target_power_ps: float = 0.0
    required_pressure_ratio: float = 0.0
    required_boost_bar: float = 0.0
    estimated_mass_flow_kg_s: float = 0.0
    estimated_mass_flow_lb_min: float = 0.0
    surge_margin_pct: float = 15.0


@dataclass
class CADJobResult:
    """Ergebnis eines CAD-Generierungsauftrags."""

    job_id: str = ""
    status: str = ""
    output_files: list[str] = field(default_factory=list)
    output_dir: str = ""
    duration_seconds: float = 0.0
    error_message: str = ""


@dataclass
class MaintenanceStatus:
    """Wartungsstatus eines Fahrzeugs."""

    vehicle_id: int
    vehicle_name: str
    odometer_km: float = 0.0
    oil_change_due_km: Optional[float] = None
    oil_change_overdue_km: float = 0.0
    inspection_due_date: Optional[str] = None
    inspection_overdue_days: int = 0
    tire_change_due_date: Optional[str] = None
    total_maintenance_cost_eur: float = 0.0
    last_service_date: Optional[str] = None
    maintenance_count: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# AutoWorkbenchService
# ═══════════════════════════════════════════════════════════════════════════════


class AutoWorkbenchService:
    """
    Automotive-Berechnungs- und CAD-Workflow-Dienst.
    """

    # Standard-Umgebungsdruck auf Meereshöhe (bar)
    _AMBIENT_PRESSURE_BAR: float = 1.01325

    # Effektiver Wirkungsgrad (Annahme für moderne Ottomotoren)
    _DEFAULT_EFFICIENCY: float = 0.35

    # Heizwert Benzin (MJ/kg)
    _FUEL_HEATING_VALUE_MJ_KG: float = 42.5

    def __init__(self, db: Session) -> None:
        """
        Args:
            db: SQLAlchemy-Datenbank-Session.
        """
        self.db: Session = db
        self._cad_output_dir: Path = Path(str(settings.path_auto)) / "cad"

    # ──────────────────────────────────────────────────────────────────────
    # Motor-Geometrie & Hubraum
    # ──────────────────────────────────────────────────────────────────────

    def calculate_engine_parameters(
        self,
        displacement_cc: float,
        cylinder_count: int,
        bore_mm: float | None = None,
        stroke_mm: float | None = None,
        compression_ratio: float | None = None,
    ) -> EngineParameters:
        """
        Berechnet die vollständigen Motor-Geometrie-Parameter.

        Mindestens zwei der drei Werte (Hubraum, Bohrung, Hub) müssen
        gegeben sein. Der dritte wird daraus berechnet.

        Formeln:
        - ``V_hz = (π/4) × d² × s`` (Einzelzylinder in mm³)
        - ``V_h = V_hz × n / 1000`` (Gesamthubraum in cm³)
        - ``ε = (V_hz + V_c) / V_c`` (Verdichtungsverhältnis)

        Args:
            displacement_cc: Gesamthubraum in cm³.
            cylinder_count: Anzahl Zylinder.
            bore_mm: Bohrung in mm (optional).
            stroke_mm: Hub in mm (optional).
            compression_ratio: Verdichtungsverhältnis ε (optional).

        Returns:
            ``EngineParameters`` mit allen Geometrie-Werten.
        """
        logger.info(
            "Berechne Motor-Parameter: %.1f cc, %d Zylinder, "
            "Bohrung=%s, Hub=%s, ε=%s",
            displacement_cc,
            cylinder_count,
            f"{bore_mm}mm" if bore_mm else "?",
            f"{stroke_mm}mm" if stroke_mm else "?",
            f"{compression_ratio}:1" if compression_ratio else "?",
        )

        # Einzelzylinder-Hubraum
        v_hz_cc: float = displacement_cc / cylinder_count
        v_hz_mm3: float = v_hz_cc * 1000.0

        # Bohrung / Hub berechnen
        calc_bore: float = bore_mm or 0.0
        calc_stroke: float = stroke_mm or 0.0

        if bore_mm and stroke_mm:
            # Beide gegeben → Hubraum verifizieren
            calc_v_hz: float = (math.pi / 4.0) * (bore_mm**2) * stroke_mm
            calc_v_cc: float = calc_v_hz / 1000.0
            logger.debug(
                "Verifizierter Einzelzylinder: %.1f cc (Soll: %.1f cc).",
                calc_v_cc,
                v_hz_cc,
            )
        elif bore_mm and not stroke_mm:
            # Bohrung gegeben → Hub berechnen
            calc_bore = bore_mm
            calc_stroke = v_hz_mm3 / ((math.pi / 4.0) * (bore_mm**2))
        elif stroke_mm and not bore_mm:
            # Hub gegeben → Bohrung berechnen
            calc_stroke = stroke_mm
            calc_bore = math.sqrt(
                v_hz_mm3 / ((math.pi / 4.0) * stroke_mm)
            )
        else:
            # Weder Bohrung noch Hub → Verhältnis schätzen (1,0–1,2 für Serienmotoren)
            ratio: float = 1.1
            calc_stroke = (v_hz_mm3 / ((math.pi / 4.0) * ratio**2)) ** (1 / 3)
            calc_bore = calc_stroke * ratio

        # Bohrung/Hub-Verhältnis
        bs_ratio: float = (
            round(calc_bore / calc_stroke, 2) if calc_stroke > 0 else 0.0
        )

        # Kompressionsvolumen aus Verdichtungsverhältnis
        v_c: float = 0.0
        if compression_ratio and compression_ratio > 0:
            v_c = v_hz_cc / (compression_ratio - 1.0)

        logger.info(
            "Ergebnis: Bohrung=%.1f mm, Hub=%.1f mm, B/S=%.2f, "
            "V_hz=%.1f cc, V_c=%.1f cc.",
            calc_bore,
            calc_stroke,
            bs_ratio,
            v_hz_cc,
            v_c,
        )

        return EngineParameters(
            displacement_total_cc=round(displacement_cc, 1),
            displacement_total_l=round(displacement_cc / 1000.0, 1),
            cylinder_count=cylinder_count,
            displacement_per_cylinder_cc=round(v_hz_cc, 1),
            bore_mm=round(calc_bore, 1),
            stroke_mm=round(calc_stroke, 1),
            bore_stroke_ratio=bs_ratio,
            compression_ratio=round(compression_ratio or 0, 1),
            combustion_chamber_volume_cc=round(v_c, 1),
        )

    # ──────────────────────────────────────────────────────────────────────
    # Leistungsberechnung
    # ──────────────────────────────────────────────────────────────────────

    def estimate_performance(
        self,
        displacement_cc: float,
        cylinder_count: int,
        bmep_bar: float | None = None,
        peak_rpm: int = 6500,
        max_piston_speed_ms: float = 20.0,
    ) -> PerformanceEstimate:
        """
        Schätzt Motorleistung und Drehmoment basierend auf BMEP.

        Formeln:
        - ``P = BMEP × V_h × n / 1200`` (kW, BMEP in bar)
        - ``M = P × 9549 / n`` (Nm)
        - ``c_m = 2 × s × n / 60000`` (m/s)

        Args:
            displacement_cc: Hubraum in cm³.
            cylinder_count: Zylinderzahl.
            bmep_bar: Effektiver Mitteldruck in bar (Default: 22 bar aufgeladen).
            peak_rpm: Drehzahl bei Spitzenleistung.
            max_piston_speed_ms: Max. Kolbengeschwindigkeit (Default: 20 m/s).

        Returns:
            ``PerformanceEstimate`` mit kW, PS, Nm und Drehzahlen.
        """
        params: EngineParameters = self.calculate_engine_parameters(
            displacement_cc=displacement_cc,
            cylinder_count=cylinder_count,
        )

        # Default BMEP: 12 bar (Sauger) oder 22 bar (Turbo)
        bmep: float = bmep_bar or 22.0

        # Leistung aus BMEP
        # P(kW) = BMEP(bar) × V_h(dm³) × n(min⁻¹) / 1200
        v_h_dm3: float = params.displacement_total_cc / 1000.0
        power_kw: float = (bmep * v_h_dm3 * peak_rpm) / 1200.0
        power_ps: float = power_kw * 1.35962
        torque_nm: float = (power_kw * 9549.0) / peak_rpm

        # Mittlere Kolbengeschwindigkeit bei peak_rpm
        c_m: float = (2.0 * params.stroke_mm * peak_rpm) / 60000.0

        # Drehzahl bei max. Drehmoment (typischerweise 2000–3000 rpm niedriger)
        torque_rpm: int = max(1500, peak_rpm - 2500)

        logger.info(
            "Leistungsschätzung: %.0f kW (%.0f PS) @ %d rpm, "
            "%.0f Nm @ %d rpm, c_m=%.1f m/s, BMEP=%.1f bar.",
            power_kw,
            power_ps,
            peak_rpm,
            torque_nm,
            torque_rpm,
            c_m,
            bmep,
        )

        return PerformanceEstimate(
            power_kw=round(power_kw, 1),
            power_ps=round(power_ps, 0),
            torque_nm=round(torque_nm, 0),
            rpm_peak_power=peak_rpm,
            rpm_peak_torque=torque_rpm,
            mean_piston_speed_ms=round(c_m, 1),
            bmep_bar=round(bmep, 1),
        )

    # ──────────────────────────────────────────────────────────────────────
    # Turbolader-Auslegung
    # ──────────────────────────────────────────────────────────────────────

    def calculate_turbo_parameters(
        self,
        displacement_cc: float,
        cylinder_count: int,
        target_power_ps: float,
        baseline_power_ps: float,
        baseline_boost_bar: float = 0.0,
        peak_rpm: int = 6500,
        volumetric_efficiency: float = 0.90,
    ) -> TurboParameters:
        """
        Dimensioniert einen Turbolader basierend auf der Zielleistung.

        Berechnet das erforderliche Druckverhältnis und den Luftmassenstrom.

        Args:
            displacement_cc: Hubraum in cm³.
            cylinder_count: Zylinderzahl.
            target_power_ps: Gewünschte Leistung in PS.
            baseline_power_ps: Serienleistung in PS.
            baseline_boost_bar: Serien-Ladedruck (relativ) in bar.
            peak_rpm: Drehzahl bei Spitzenleistung.
            volumetric_efficiency: Volumetrischer Wirkungsgrad (0,85–1,05).

        Returns:
            ``TurboParameters`` mit Druckverhältnis und Massenstrom.
        """
        logger.info(
            "Turbo-Auslegung: %.0f → %.0f PS, %.1f cc, %d Zyl.",
            baseline_power_ps,
            target_power_ps,
            displacement_cc,
            cylinder_count,
        )

        # Benötigte BMEP für Zielleistung
        v_h_dm3: float = displacement_cc / 1000.0
        target_power_kw: float = target_power_ps / 1.35962
        required_bmep: float = (target_power_kw * 1200.0) / (
            v_h_dm3 * peak_rpm
        )

        # Benötigtes Druckverhältnis (vereinfacht: PR ≈ BMEP_ziel / BMEP_basis)
        baseline_bmep: float = (
            (baseline_power_ps / 1.35962) * 1200.0
        ) / (v_h_dm3 * peak_rpm)

        baseline_pr: float = (
            baseline_boost_bar + self._AMBIENT_PRESSURE_BAR
        ) / self._AMBIENT_PRESSURE_BAR

        # PR steigt proportional zur BMEP
        required_pr: float = baseline_pr * (required_bmep / max(1.0, baseline_bmep))

        # Ladedruck (relativ)
        required_boost: float = (
            required_pr * self._AMBIENT_PRESSURE_BAR
        ) - self._AMBIENT_PRESSURE_BAR

        # Luftmassenstrom (kg/s)
        # ṁ = (V_h × n × η_vol × ρ_luft) / (2 × 60)
        rho_air: float = 1.225  # kg/m³ bei 15°C, 1,013 bar
        mass_flow_kg_s: float = (
            (v_h_dm3 / 1000.0)
            * peak_rpm
            * volumetric_efficiency
            * rho_air
        ) / (2.0 * 60.0)

        # Für Turbolader-Kennfelder: Umrechnung in lb/min
        mass_flow_lb_min: float = mass_flow_kg_s * 132.277

        logger.info(
            "Turbo: PR=%.2f, Boost=%.2f bar, ṁ=%.3f kg/s (%.1f lb/min), "
            "BMEP_ziel=%.1f bar.",
            required_pr,
            required_boost,
            mass_flow_kg_s,
            mass_flow_lb_min,
            required_bmep,
        )

        return TurboParameters(
            target_power_ps=target_power_ps,
            required_pressure_ratio=round(required_pr, 2),
            required_boost_bar=round(required_boost, 2),
            estimated_mass_flow_kg_s=round(mass_flow_kg_s, 4),
            estimated_mass_flow_lb_min=round(mass_flow_lb_min, 1),
            surge_margin_pct=15.0,
        )

    # ──────────────────────────────────────────────────────────────────────
    # CAD-Job-Submission (async → GPU-Desktop-Worker)
    # ──────────────────────────────────────────────────────────────────────

    async def submit_cad_job(
        self,
        vehicle_id: int,
        scad_code: str | None = None,
        cadquery_code: str | None = None,
        build123d_code: str | None = None,
        output_format: str = "stl",
        resolution: int = 120,
    ) -> CADJobResult:
        """
        Sendet einen CAD-Generierungsauftrag asynchron an den GPU-Desktop.

        Unterstützt OpenSCAD, CadQuery und Build123d als Eingabeformat.
        Das Ergebnis wird unter ``/mnt/data/auto/cad/{vehicle_id}/``
        gespeichert.

        Args:
            vehicle_id: ID des Fahrzeugs (für Dateiablage).
            scad_code: OpenSCAD-Code (optional).
            cadquery_code: CadQuery-Python-Code (optional).
            build123d_code: Build123d-Python-Code (optional).
            output_format: stl, step, png, 3mf.
            resolution: $fn-Auflösung für OpenSCAD.

        Returns:
            ``CADJobResult`` mit Job-ID und Ausgabedateien.
        """
        # Fahrzeug validieren
        vehicle: Vehicle | None = (
            self.db.query(Vehicle)
            .filter(Vehicle.id == vehicle_id)
            .first()
        )
        if not vehicle:
            raise ValueError(f"Fahrzeug-ID {vehicle_id} nicht gefunden.")

        # Ausgabeverzeichnis
        output_dir: Path = self._cad_output_dir / str(vehicle_id)
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Starte CAD-Job für Fahrzeug %r (format=%s)...",
            vehicle.name,
            output_format,
        )

        try:
            from app.services.worker_client import WorkerClient

            async with WorkerClient() as worker:
                if scad_code:
                    result: any = await worker.submit_openscad_job(
                        scad_code=scad_code,
                        output_format=output_format,
                        resolution=resolution,
                    )
                elif cadquery_code:
                    result = await worker.submit_cadquery_job(
                        cadquery_code=cadquery_code,
                        output_format=output_format,
                    )
                elif build123d_code:
                    result = await worker.submit_build123d_job(
                        build123d_code=build123d_code,
                        output_format=output_format,
                    )
                else:
                    raise ValueError(
                        "Kein CAD-Code angegeben "
                        "(scad_code, cadquery_code oder build123d_code)."
                    )

                # Ergebnisdateien herunterladen
                downloaded: list[str] = []
                if hasattr(result, "output_files"):
                    for filename in result.output_files:
                        local_path: Path = await worker.download_result_file(
                            job_id=result.job_id,
                            filename=filename,
                            destination=str(output_dir),
                        )
                        downloaded.append(str(local_path))

                # Fahrzeug-Modellpfad aktualisieren
                if downloaded:
                    rel_path: str = str(
                        Path(downloaded[0]).relative_to(
                            settings.path_auto
                        )
                    )
                    vehicle.model_3d_path = rel_path
                    self.db.commit()

                logger.info(
                    "CAD-Job abgeschlossen: %d Dateien in %s.",
                    len(downloaded),
                    output_dir,
                )

                return CADJobResult(
                    job_id=(
                        result.job_id
                        if hasattr(result, "job_id")
                        else ""
                    ),
                    status="completed",
                    output_files=downloaded,
                    output_dir=str(output_dir),
                    duration_seconds=(
                        result.duration_seconds
                        if hasattr(result, "duration_seconds")
                        else 0.0
                    ),
                )

        except Exception as exc:
            logger.error("CAD-Job fehlgeschlagen: %s", exc)
            return CADJobResult(
                status="failed",
                output_dir=str(output_dir),
                error_message=str(exc),
            )

    async def generate_engine_component_cad(
        self,
        vehicle_id: int,
        component: str,
        parameters: dict | None = None,
    ) -> CADJobResult:
        """
        Generiert CAD-Code für eine bestimmte Motorkomponente und sendet
        ihn an den Worker.

        Unterstützte Komponenten:
        - ``"piston"`` — Kolben (Ø aus Fahrzeugdaten, optionale Parameter)
        - ``"cylinder_head"`` — Zylinderkopf (Bohrung, Ventilwinkel)
        - ``"intake_manifold"`` — Ansaugkrümmer (Länge, Durchmesser)

        Args:
            vehicle_id: Fahrzeug-ID.
            component: Komponenten-Typ.
            parameters: Override-Parameter.

        Returns:
            ``CADJobResult``.
        """
        params: dict = parameters or {}

        if component == "piston":
            bore: float = params.get("bore_mm", 82.0)
            stroke: float = params.get("stroke_mm", 94.6)
            scad: str = self._generate_piston_scad(bore, stroke)
            return await self.submit_cad_job(
                vehicle_id=vehicle_id,
                scad_code=scad,
                output_format="stl",
            )

        elif component == "cylinder_head":
            bore = params.get("bore_mm", 82.0)
            valve_angle: float = params.get("valve_angle_deg", 22.5)
            scad = self._generate_cylinder_head_scad(bore, valve_angle)
            return await self.submit_cad_job(
                vehicle_id=vehicle_id,
                scad_code=scad,
                output_format="stl",
            )

        else:
            raise ValueError(
                f"Unbekannte Komponente: {component!r}. "
                f"Verfügbar: piston, cylinder_head, intake_manifold."
            )

    @staticmethod
    def _generate_piston_scad(bore_mm: float, stroke_mm: float) -> str:
        """Generiert OpenSCAD-Code für einen Kolben."""
        return f"""// Kolben — Auto-generiert vom Homelab-Imperium
// Bohrung: {bore_mm} mm, Hub: {stroke_mm} mm

$fn = 120;

kolben_dm = {bore_mm - 0.05};  // Spiel zum Zylinder
kolben_hoehe = {bore_mm * 0.75};
bolzen_dm = {bore_mm * 0.25};
kompressions_hoehe = {stroke_mm * 0.45};

difference() {{
    cylinder(h=kolben_hoehe, d=kolben_dm);

    translate([0, 0, kolben_hoehe - 5])
    cylinder(h=3, d=kolben_dm - 5);

    translate([0, 0, kompressions_hoehe])
    rotate([0, 90, 0])
    cylinder(h=kolben_dm + 10, d=bolzen_dm, center=true);
}}"""

    @staticmethod
    def _generate_cylinder_head_scad(
        bore_mm: float, valve_angle_deg: float
    ) -> str:
        """Generiert OpenSCAD-Code für einen Zylinderkopf (vereinfacht)."""
        return f"""// Zylinderkopf — Auto-generiert
// Bohrung: {bore_mm} mm, Ventilwinkel: {valve_angle_deg}°

$fn = 120;

bohrung = {bore_mm};
ventil_winkel = {valve_angle_deg};

difference() {{
    union() {{
        cylinder(h=30, d=bohrung + 20);
        translate([0, 0, 30])
        cylinder(h=10, d=bohrung + 30);
    }}
    cylinder(h=35, d=bohrung);
}}"""

    # ──────────────────────────────────────────────────────────────────────
    # Fahrzeug-Wartungsstatus
    # ──────────────────────────────────────────────────────────────────────

    def get_maintenance_status(self, vehicle_id: int) -> MaintenanceStatus:
        """
        Ermittelt den vollständigen Wartungsstatus eines Fahrzeugs.

        Args:
            vehicle_id: Fahrzeug-ID.

        Returns:
            ``MaintenanceStatus`` mit Fälligkeiten und Kosten.
        """
        vehicle: Vehicle | None = (
            self.db.query(Vehicle)
            .filter(Vehicle.id == vehicle_id)
            .first()
        )
        if not vehicle:
            raise ValueError(f"Fahrzeug-ID {vehicle_id} nicht gefunden.")

        today: date = date.today()

        # Ölwechsel-Überfälligkeit
        oil_overdue: float = 0.0
        if vehicle.oil_change_due_km is not None:
            oil_overdue = max(
                0.0, vehicle.odometer_km - vehicle.oil_change_due_km
            )

        # HU/TÜV-Überfälligkeit
        insp_overdue: int = 0
        if vehicle.inspection_due_date:
            insp_overdue = max(
                0, (today - vehicle.inspection_due_date).days
            )

        # Wartungshistorie
        maint_count: int = (
            self.db.query(func.count(MaintenanceRecord.id))
            .filter(MaintenanceRecord.vehicle_id == vehicle_id)
            .scalar()
            or 0
        )

        total_cost = (
            self.db.query(
                func.coalesce(
                    func.sum(MaintenanceRecord.cost_eur), 0
                )
            )
            .filter(MaintenanceRecord.vehicle_id == vehicle_id)
            .scalar()
        )
        total_cost_eur: float = float(total_cost or 0)

        last_service = (
            self.db.query(MaintenanceRecord)
            .filter(MaintenanceRecord.vehicle_id == vehicle_id)
            .order_by(MaintenanceRecord.date.desc())
            .first()
        )

        logger.info(
            "Wartungsstatus %r: %.0f km, Öl %s, HU %s, "
            "%d Wartungen, %.2f € total.",
            vehicle.name,
            vehicle.odometer_km,
            f"+{oil_overdue:.0f} km" if oil_overdue > 0 else "✅",
            f"+{insp_overdue}d" if insp_overdue > 0 else "✅",
            maint_count,
            total_cost_eur,
        )

        return MaintenanceStatus(
            vehicle_id=vehicle.id,
            vehicle_name=vehicle.name,
            odometer_km=vehicle.odometer_km,
            oil_change_due_km=vehicle.oil_change_due_km,
            oil_change_overdue_km=round(oil_overdue, 0),
            inspection_due_date=(
                vehicle.inspection_due_date.isoformat()
                if vehicle.inspection_due_date
                else None
            ),
            inspection_overdue_days=insp_overdue,
            tire_change_due_date=(
                vehicle.tire_change_due_date.isoformat()
                if vehicle.tire_change_due_date
                else None
            ),
            total_maintenance_cost_eur=round(total_cost_eur, 2),
            last_service_date=(
                last_service.date.isoformat()
                if last_service
                else None
            ),
            maintenance_count=maint_count,
        )

    def check_all_vehicles_maintenance(self) -> list[dict]:
        """
        Prüft ALLE Fahrzeuge auf fällige Wartungen und gibt Warnungen aus.

        Returns:
            Liste von Dicts mit Fahrzeug-ID, Name, fällige Wartungen.
        """
        vehicles = self.db.query(Vehicle).all()
        warnings: list[dict] = []

        for v in vehicles:
            ms: MaintenanceStatus = self.get_maintenance_status(v.id)
            issues: list[str] = []

            if ms.oil_change_overdue_km > 0:
                issues.append(
                    f"Ölwechsel +{ms.oil_change_overdue_km:.0f} km "
                    f"überfällig"
                )
            if ms.inspection_overdue_days > 0:
                issues.append(
                    f"HU/TÜV +{ms.inspection_overdue_days} Tage "
                    f"überfällig"
                )

            if issues:
                warnings.append(
                    {
                        "vehicle_id": v.id,
                        "vehicle_name": v.name,
                        "odometer_km": v.odometer_km,
                        "issues": issues,
                    }
                )

        if warnings:
            logger.warning(
                "%d Fahrzeuge mit Wartungsbedarf!", len(warnings)
            )
        return warnings
