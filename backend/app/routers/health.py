"""
Health-Check-Router des Homelab-Imperiums.

Stellt standardkonforme Endpunkte für Kubernetes/Docker-Health-Checks,
Monitoring und Fehlerdiagnose bereit.

Endpunkte:
- ``GET /api/health``          — Einfacher Liveness-Check (immer 200)
- ``GET /api/health/ready``    — Readiness-Check (200 nur wenn alle Dienste bereit)
- ``GET /api/health/live``     — Liveness-Check (identisch zu /health)
- ``GET /api/health/detailed`` — Detaillierte Systemdiagnose (DB, ChromaDB, Version)

Verwendung::

    from app.routers.health import router
    app.include_router(router, prefix="/api")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.database import check_async_database_health, check_database_health

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.routers.health")

# ═══════════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════════

router: APIRouter = APIRouter(tags=["Health"])

# ═══════════════════════════════════════════════════════════════════════════════
# Response-Modelle
# ═══════════════════════════════════════════════════════════════════════════════


class HealthResponse(BaseModel):
    """
    Einfache Health-Check-Antwort (Liveness).
    """

    status: str = Field(
        default="ok",
        description="'ok' wenn der Server läuft.",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Aktueller UTC-Zeitstempel.",
    )
    version: str = Field(
        default="1.0.0",
        description="API-Version.",
    )
    uptime_seconds: Optional[float] = Field(
        default=None,
        description="Server-Uptime in Sekunden.",
    )


class ReadinessResponse(BaseModel):
    """
    Readiness-Check-Antwort — prüft, ob der Server Requests annehmen kann.
    """

    status: str = Field(description="'ready' oder 'not_ready'.")
    checks: dict = Field(
        default_factory=dict,
        description="Detailergebnisse der einzelnen Prüfungen.",
    )


class DetailedHealthResponse(BaseModel):
    """
    Detaillierte Systemdiagnose.
    """

    status: str = Field(description="'healthy', 'degraded' oder 'unhealthy'.")
    server: dict = Field(description="Server-Metadaten.")
    database: dict = Field(description="PostgreSQL-Status.")
    chromadb: Optional[dict] = Field(
        default=None, description="ChromaDB-Status."
    )
    dependencies: dict = Field(
        default_factory=dict,
        description="Status aller externen Abhängigkeiten.",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Endpunkte
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Einfacher Liveness-Check",
    description="Gibt 200 OK zurück, solange der FastAPI-Server läuft. "
    "Geeignet für Docker-HEALTHCHECK und Kubernetes livenessProbe.",
    status_code=200,
)
async def health_check() -> HealthResponse:
    """
    Liveness-Check — immer 200, solange der Prozess läuft.

    Returns:
        ``HealthResponse`` mit Status "ok".
    """
    logger.debug("Health-Check aufgerufen.")
    return HealthResponse(status="ok")


@router.get(
    "/health/live",
    response_model=HealthResponse,
    summary="Liveness-Check (Alias)",
    description="Alias für /health. Immer 200 OK.",
    status_code=200,
)
async def liveness_check() -> HealthResponse:
    """
    Liveness-Check — identisch zu /health.
    """
    return await health_check()


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness-Check",
    description="Prüft, ob alle Abhängigkeiten bereit sind. "
    "Gibt 200 nur wenn DB + optionale Dienste erreichbar sind. "
    "Geeignet für Kubernetes readinessProbe.",
    responses={
        200: {"description": "Alle Dienste bereit."},
        503: {"description": "Mindestens ein Dienst nicht bereit."},
    },
)
async def readiness_check() -> ReadinessResponse:
    """
    Readiness-Check — prüft kritische Abhängigkeiten.

    Returns 503 Service Unavailable, wenn die Datenbank nicht
    erreichbar ist.
    """
    checks: dict = {}
    all_ready: bool = True

    # ── Datenbank-Prüfung ──
    try:
        db_health: dict = check_database_health()
        checks["database"] = db_health
        if db_health.get("status") != "healthy":
            all_ready = False
    except Exception as exc:
        checks["database"] = {"status": "unhealthy", "error": str(exc)}
        all_ready = False

    status: str = "ready" if all_ready else "not_ready"
    logger.info(
        "Readiness-Check: %s (checks=%s).",
        status,
        {k: v.get("status", "?") for k, v in checks.items()},
    )

    if not all_ready:
        raise HTTPException(
            status_code=503,
            detail={
                "status": status,
                "checks": checks,
            },
        )

    return ReadinessResponse(status=status, checks=checks)


@router.get(
    "/health/detailed",
    response_model=DetailedHealthResponse,
    summary="Detaillierte Systemdiagnose",
    description="Umfassender Health-Check mit DB, ggf. ChromaDB und "
    "weiteren Abhängigkeiten. Nur für Admin/Monitoring.",
)
async def detailed_health() -> DetailedHealthResponse:
    """
    Detaillierte Systemdiagnose — sammelt Status aller Komponenten.

    Returns:
        ``DetailedHealthResponse`` mit Server-, DB- und Abhängigkeits-Status.
    """
    import time

    start_time: float = time.monotonic()

    # ── Server ──
    server_info: dict = {
        "app_name": settings.app_name,
        "version": "1.0.0",
        "api_env": settings.api_env,
        "debug": settings.debug,
        "python_version": __import__("sys").version,
    }

    # ── Datenbank ──
    db_status: dict = {}
    try:
        db_status = check_database_health()
    except Exception as exc:
        db_status = {"status": "unhealthy", "error": str(exc)}

    # ── ChromaDB (optional) ──
    chroma_status: Optional[dict] = None
    try:
        from app.services.clients.chroma import ChromaDBClient

        chroma_client: ChromaDBClient = ChromaDBClient()
        chroma_ok: bool = await _async_ping_fallback(chroma_client)
        chroma_status = {
            "endpoint": settings.chromadb_endpoint,
            "status": "healthy" if chroma_ok else "unhealthy",
        }
    except Exception as exc:
        chroma_status = {
            "endpoint": settings.chromadb_endpoint,
            "status": "unhealthy",
            "error": str(exc),
        }

    # ── Gesamtstatus bestimmen ──
    overall: str = "healthy"
    if db_status.get("status") != "healthy":
        overall = "unhealthy"
    elif chroma_status and chroma_status.get("status") != "healthy":
        overall = "degraded"

    elapsed_ms: float = (time.monotonic() - start_time) * 1000
    logger.info(
        "Detaillierte Diagnose: %s (%.1f ms).", overall, elapsed_ms
    )

    return DetailedHealthResponse(
        status=overall,
        server=server_info,
        database=db_status,
        chromadb=chroma_status,
        dependencies={
            "database": db_status.get("status", "unknown"),
            "chromadb": (
                chroma_status.get("status", "unknown")
                if chroma_status
                else "not_configured"
            ),
        },
    )


async def _async_ping_fallback(client) -> bool:
    """
    Hilfsfunktion: Versucht einen async-Ping, fällt auf sync zurück.

    Args:
        client: ChromaDB-Client (hat ``ping()`` als async-Methode).

    Returns:
        ``True`` wenn Ping erfolgreich.
    """
    try:
        if hasattr(client, "ping"):
            result = client.ping()
            if hasattr(result, "__await__"):
                return await result
            return bool(result)
    except Exception:
        pass
    return False
