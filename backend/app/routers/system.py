"""
System-Metriken-Router des Homelab-Imperiums.

Stellt Echtzeit-Systemmetriken des HP-Servers bereit:
CPU, RAM, Festplatte, Uptime, Netzwerk und CPU-Temperatur.

Endpunkte:
- ``GET /api/system/metrics``      — Standard-Metriken (Dashboard)
- ``GET /api/system/metrics/full`` — Erweiterte Diagnose (Admin)
- ``GET /api/system/temperature``  — CPU-Temperatur (separat)

Verwendung::

    from app.routers.system import router
    app.include_router(router, prefix="/api")
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.schemas import SystemMetricResponse
from app.services.system import SystemService

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.routers.system")

# ═══════════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════════

router: APIRouter = APIRouter(tags=["System"])

# ═══════════════════════════════════════════════════════════════════════════════
# Dependency Injection — SystemService (Singleton-ähnlich via Modul-Level)
# ═══════════════════════════════════════════════════════════════════════════════

# Einmalige Instanz — psutil ist threadsafe und zustandslos
_system_service: SystemService = SystemService()


def get_system_service() -> SystemService:
    """
    Dependency-Injection-Factory für den SystemService.

    Gibt eine globale Instanz zurück, da ``SystemService`` zustandslos
    ist und kein Datenbank-Handle benötigt.

    Returns:
        Globale ``SystemService``-Instanz.
    """
    return _system_service


# ═══════════════════════════════════════════════════════════════════════════════
# Response-Modelle
# ═══════════════════════════════════════════════════════════════════════════════


class TemperatureResponse(BaseModel):
    """
    CPU-Temperatur-Antwort.
    """

    temperature_celsius: Optional[float] = Field(
        default=None,
        description="CPU-Temperatur in °C, oder null wenn nicht verfügbar.",
    )
    is_available: bool = Field(
        default=False,
        description="True wenn Temperatursensor verfügbar ist.",
    )
    note: str = Field(
        default="",
        description="Hinweis falls Temperatur nicht verfügbar.",
    )


class FullMetricsResponse(BaseModel):
    """
    Erweiterte Systemmetriken (Admin-Diagnose).
    """

    metrics: SystemMetricResponse = Field(
        description="Standard-Metriken."
    )
    temperature: Optional[float] = Field(
        default=None,
        description="CPU-Temperatur in °C.",
    )
    network: dict = Field(
        default_factory=dict,
        description="Netzwerk-Statistiken.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Endpunkte
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/system/metrics",
    response_model=SystemMetricResponse,
    summary="Echtzeit-Systemmetriken",
    description="Liefert CPU, RAM, Festplatte und Uptime des HP-Servers. "
    "Alle Werte haben sichere Fallbacks bei Fehlern (z.B. 0.0 bei "
    "nicht verfügbaren Sensoren).",
    responses={
        200: {"description": "Metriken erfolgreich erhoben."},
        500: {"description": "Interner Fehler bei der Metrik-Erhebung."},
    },
)
async def get_system_metrics(
    svc: SystemService = Depends(get_system_service),
) -> SystemMetricResponse:
    """
    Sammelt die wichtigsten Systemmetriken für das Dashboard.

    Ruft ``SystemService.collect_metrics()`` auf, das alle Teilmethoden
    mit Fallbacks ausführt — ein Fehler in einer Metrik (z.B. kein Zugriff
    auf /sys/class/hwmon) führt NICHT zum Komplettausfall.

    Returns:
        ``SystemMetricResponse`` mit allen aktuellen Metriken.
    """
    logger.debug("System-Metriken-Endpunkt aufgerufen.")

    try:
        metrics: SystemMetricResponse = svc.collect_metrics()
        logger.info(
            "System-Metriken geliefert: CPU=%.1f%%, RAM=%.1f%%, "
            "Disk=%.1f%%, Uptime=%s.",
            metrics.cpu_percent,
            metrics.ram_percent,
            metrics.disk_percent,
            metrics.uptime,
        )
        return metrics

    except PermissionError as exc:
        # Tritt auf, wenn psutil keine Berechtigung für bestimmte
        # Systemaufrufe hat (selten, aber möglich in restriktiven
        # Container-Umgebungen).
        logger.error("Berechtigungsfehler bei Metrik-Erhebung: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=(
                "System-Metriken konnten nicht erhoben werden: "
                f"Fehlende Berechtigung — {exc}"
            ),
        ) from exc

    except OSError as exc:
        # Tritt auf, wenn das Betriebssystem einen Systemaufruf ablehnt
        # (z.B. /proc nicht gemountet im Container).
        logger.error("OS-Fehler bei Metrik-Erhebung: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=(
                "System-Metriken konnten nicht erhoben werden: "
                f"Betriebssystem-Fehler — {exc}"
            ),
        ) from exc

    except Exception as exc:
        logger.exception("Unerwarteter Fehler bei Metrik-Erhebung.")
        raise HTTPException(
            status_code=500,
            detail=(
                "System-Metriken konnten nicht erhoben werden: "
                f"Unerwarteter Fehler — {exc}"
            ),
        ) from exc


@router.get(
    "/system/metrics/full",
    response_model=FullMetricsResponse,
    summary="Erweiterte Systemdiagnose",
    description="Liefert Standard-Metriken + CPU-Temperatur + "
    "Netzwerk-Statistiken. Nur für Admin-Dashboard.",
)
async def get_full_metrics(
    svc: SystemService = Depends(get_system_service),
) -> FullMetricsResponse:
    """
    Sammelt erweiterte Metriken inklusive Temperatur und Netzwerk.

    Returns:
        ``FullMetricsResponse`` mit allen verfügbaren Diagnosedaten.
    """
    logger.debug("Erweiterte System-Metriken aufgerufen.")

    try:
        diagnostics: dict = svc.collect_full_diagnostics()

        # CPU-Temperatur extrahieren
        temp: Optional[float] = diagnostics.get("cpu", {}).get(
            "temperature_celsius"
        )

        # Netzwerk-Statistiken
        network: dict = diagnostics.get("network", {})

        # Standard-Metriken
        metrics: SystemMetricResponse = svc.collect_metrics()

        logger.info(
            "Erweiterte Metriken geliefert (CPU-Temp: %s°C).",
            f"{temp:.1f}" if temp is not None else "?",
        )

        return FullMetricsResponse(
            metrics=metrics,
            temperature=temp,
            network=network,
        )

    except Exception as exc:
        logger.exception("Fehler bei erweiterter Diagnose.")
        raise HTTPException(
            status_code=500,
            detail=f"Erweiterte Diagnose fehlgeschlagen: {exc}",
        ) from exc


@router.get(
    "/system/temperature",
    response_model=TemperatureResponse,
    summary="CPU-Temperatur",
    description="Liest die CPU-Temperatur über Linux-hwmon-Schnittstelle. "
    "Gibt null zurück, wenn kein Sensor verfügbar ist (Container, macOS).",
)
async def get_cpu_temperature(
    svc: SystemService = Depends(get_system_service),
) -> TemperatureResponse:
    """
    Liest die CPU-Temperatur des HP-Servers aus.

    Funktioniert nur unter Linux mit geladenen hwmon-Treibern
    (``coretemp``, ``k10temp``). In Containern ohne ``/sys``-Zugriff
    oder auf macOS wird ``null`` zurückgegeben.

    Returns:
        ``TemperatureResponse`` mit Temperatur in °C oder null.
    """
    logger.debug("CPU-Temperatur-Endpunkt aufgerufen.")

    try:
        temp: Optional[float] = svc.get_cpu_temperature()

        if temp is not None:
            logger.info("CPU-Temperatur: %.1f°C.", temp)
            return TemperatureResponse(
                temperature_celsius=temp,
                is_available=True,
                note="",
            )
        else:
            logger.debug("CPU-Temperatur nicht verfügbar.")
            return TemperatureResponse(
                temperature_celsius=None,
                is_available=False,
                note=(
                    "CPU-Temperatur ist auf diesem System nicht verfügbar. "
                    "Gründe: kein Linux, kein hwmon-Zugriff (Container), "
                    "oder keine Sensor-Treiber geladen (coretemp/k10temp)."
                ),
            )

    except Exception as exc:
        logger.warning("Fehler beim CPU-Temperatur-Endpunkt: %s", exc)
        return TemperatureResponse(
            temperature_celsius=None,
            is_available=False,
            note=f"Fehler beim Auslesen: {exc}",
        )
