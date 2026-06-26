"""
Web-IDE-Router des Homelab-Imperiums.

Stellt REST-Endpunkte für die code-server-Web-IDE bereit:
Sitzungs-Token-Erzeugung, Iframe-Zugriffsautorisierung und
Container-Statusüberwachung.

Das Iframe wird NUR gerendert, wenn:
1. Der code-server-Container gesund ist (Docker-Health-Check)
2. Ein gültiges, nicht abgelaufenes Sitzungs-Token vorliegt
3. Die Sitzung als aktiv markiert ist

Endpunkte:
- ``POST /api/ide/session``      — Sitzungs-Token erzeugen
- ``GET /api/ide/authorize``     — Iframe-Zugriff prüfen (Token + Container)
- ``GET /api/ide/status``        — Container- & Sitzungs-Status
- ``DELETE /api/ide/session``    — Sitzung beenden (Logout)
- ``GET /api/ide/sessions``      — Alle aktiven Sitzungen (Admin)

Verwendung::

    from app.routers.ide import router
    app.include_router(router, prefix="/api")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.ide import WebIDEManagerService

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.routers.ide")

# ═══════════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════════

router: APIRouter = APIRouter(tags=["Web-IDE"])

# ═══════════════════════════════════════════════════════════════════════════════
# Dependency Injection
# ═══════════════════════════════════════════════════════════════════════════════

_ide_service: WebIDEManagerService = WebIDEManagerService()


def get_ide_service() -> WebIDEManagerService:
    """Factory für den WebIDEManagerService (Singleton — In-Memory-Sessions)."""
    return _ide_service


# ═══════════════════════════════════════════════════════════════════════════════
# Response-Modelle
# ═══════════════════════════════════════════════════════════════════════════════


class SessionResponse(BaseModel):
    """Antwort nach Sitzungserstellung."""

    token: str = Field(description="64-Zeichen Hex-Token (kryptografisch sicher).")
    user: str = Field(default="admin")
    expires_at: str = Field(description="ISO-8601-Zeitstempel des Ablaufs.")
    ttl_seconds: int = Field(description="Gültigkeitsdauer in Sekunden.")


class AuthorizeResponse(BaseModel):
    """Antwort der Iframe-Zugriffsprüfung."""

    authorized: bool = Field(description="True = Iframe darf gerendert werden.")
    user: str = Field(default="")
    container_healthy: bool = False
    token_valid: bool = False
    iframe_url: str = Field(default="")
    reason: str = Field(default="")


class IDEStatusResponse(BaseModel):
    """Vollständiger IDE-Status."""

    container: dict = Field(description="Docker-Container-Status.")
    sessions: dict = Field(description="Sitzungs-Statistiken.")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Endpunkte
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/ide/session",
    response_model=SessionResponse,
    summary="IDE-Sitzung erstellen",
    description="Erzeugt ein kryptografisch sicheres Sitzungs-Token "
    "für den Zugriff auf die code-server-Web-IDE. "
    "Das Token ist 8 Stunden gültig.",
    status_code=201,
)
async def create_ide_session(
    user: str = Query(default="admin", description="Benutzername."),
    svc: WebIDEManagerService = Depends(get_ide_service),
) -> SessionResponse:
    """
    Erstellt eine neue Web-IDE-Sitzung.

    Das Token muss anschließend bei jedem Iframe-Zugriff via
    ``GET /api/ide/authorize?token=...`` validiert werden.
    """
    logger.info("POST /ide/session: user=%r.", user)

    try:
        session: dict = svc.create_session(user=user)
        return SessionResponse(
            token=session["token"],
            user=session["user"],
            expires_at=session["expires_at"].isoformat(),
            ttl_seconds=session["ttl_seconds"],
        )

    except Exception as exc:
        logger.exception("Fehler bei Sitzungserstellung.")
        raise HTTPException(
            status_code=500,
            detail=f"Sitzung konnte nicht erstellt werden: {exc}",
        ) from exc


@router.get(
    "/ide/authorize",
    response_model=AuthorizeResponse,
    summary="Iframe-Zugriff autorisieren",
    description="**Kritischer Endpunkt.** Validiert das Sitzungs-Token UND "
    "prüft den Container-Health-Status. Nur wenn BEIDE Prüfungen "
    "bestanden sind, wird die Iframe-URL zurückgegeben. "
    "Andernfalls erfolgt eine detaillierte Fehlerbeschreibung.",
)
async def authorize_ide_access(
    token: str = Query(
        ...,
        min_length=64,
        max_length=64,
        description="64-Zeichen Hex-Sitzungs-Token.",
    ),
    svc: WebIDEManagerService = Depends(get_ide_service),
) -> AuthorizeResponse:
    """
    Zweistufige Iframe-Zugriffsprüfung.

    **Prüfung 1:** Container-Health (``docker inspect code_server``)
    **Prüfung 2:** Token-Validierung (existiert? aktiv? nicht abgelaufen?)

    Bei Erfolg: ``authorized=true`` + ``iframe_url``
    Bei Fehler: ``authorized=false`` + detaillierte ``reason``
    """
    logger.info("GET /ide/authorize: token=%s...", token[:12])

    try:
        result: dict = await svc.authorize_iframe_access(token=token)

        if result["authorized"]:
            logger.info(
                "IDE-Zugriff autorisiert: user=%r.",
                result["user"],
            )
        else:
            logger.warning(
                "IDE-Zugriff verweigert: %s", result["reason"]
            )

        return AuthorizeResponse(**result)

    except Exception as exc:
        logger.exception("Fehler bei Iframe-Autorisierung.")
        raise HTTPException(
            status_code=500,
            detail=f"Autorisierungsfehler: {exc}",
        ) from exc


@router.delete(
    "/ide/session",
    summary="IDE-Sitzung beenden (Logout)",
    description="Invalidiert das Sitzungs-Token. Das Iframe wird "
    "anschließend nicht mehr gerendert.",
)
async def end_ide_session(
    token: str = Query(..., min_length=64, max_length=64),
    svc: WebIDEManagerService = Depends(get_ide_service),
) -> dict:
    """
    Beendet eine Web-IDE-Sitzung (Logout).

    Das Token wird als inaktiv markiert und kann nicht
    wiederverwendet werden.
    """
    logger.info("DELETE /ide/session: token=%s...", token[:12])

    try:
        success: bool = svc.invalidate_session(token=token)

        if success:
            logger.info("IDE-Sitzung beendet.")
            return {"message": "Sitzung beendet.", "success": True}
        else:
            raise HTTPException(
                status_code=404,
                detail="Token nicht gefunden oder bereits abgelaufen.",
            )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Fehler beim Beenden der Sitzung.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.get(
    "/ide/status",
    response_model=IDEStatusResponse,
    summary="IDE-Status",
    description="Vollständiger Status: Container-Gesundheit + "
    "aktive Sitzungen.",
)
async def get_ide_status(
    svc: WebIDEManagerService = Depends(get_ide_service),
) -> IDEStatusResponse:
    """
    Kombinierter Status-Check für Monitoring.

    Returns:
        Container-Details (Running, Healthy, Uptime) und
        Sitzungs-Statistiken (Anzahl aktiv, Details).
    """
    logger.debug("GET /ide/status.")

    try:
        # Container-Status
        container_healthy: bool = await svc.is_container_healthy(
            force_check=True
        )

        # Docker-Inspect-Details
        status = svc._cached_status
        container_info: dict = {
            "is_running": status.is_running if status else False,
            "is_healthy": container_healthy,
            "container_name": status.container_name if status else "?",
            "uptime_seconds": status.uptime_seconds if status else 0,
            "error": status.error_message if status else "",
        }

        # Sitzungen
        active_sessions: list[dict] = svc.get_active_sessions()
        sessions_info: dict = {
            "active_count": len(active_sessions),
            "sessions": active_sessions,
        }

        return IDEStatusResponse(
            container=container_info,
            sessions=sessions_info,
        )

    except Exception as exc:
        logger.exception("Fehler bei IDE-Status.")
        raise HTTPException(
            status_code=500,
            detail=f"Statusabfrage fehlgeschlagen: {exc}",
        ) from exc


@router.get(
    "/ide/sessions",
    summary="Alle aktiven Sitzungen (Admin)",
    description="Listet alle aktiven IDE-Sitzungen auf. "
    "Nur für Administratoren.",
)
async def list_active_sessions(
    svc: WebIDEManagerService = Depends(get_ide_service),
) -> list[dict]:
    """
    Admin-Übersicht aller aktiven Sitzungen.

    Enthält Benutzername, Erstellungszeitpunkt und verbleibende
    Gültigkeitsdauer. Das vollständige Token wird NICHT angezeigt
    (nur die ersten 12 Zeichen als Prefix).
    """
    logger.debug("GET /ide/sessions.")

    try:
        return svc.get_active_sessions()

    except Exception as exc:
        logger.exception("Fehler beim Abrufen der Sitzungen.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler: {exc}",
        ) from exc


@router.post(
    "/ide/container/restart",
    summary="Container neustarten (Admin)",
    description="Startet den code-server-Docker-Container neu.",
)
async def restart_ide_container(
    svc: WebIDEManagerService = Depends(get_ide_service),
) -> dict:
    """
    Docker-Neustart des code-server-Containers.

    Alle bestehenden Sitzungen bleiben erhalten — die Token
    werden nicht invalidiert. Nach dem Neustart ist der Container
    in der Regel innerhalb weniger Sekunden wieder erreichbar.
    """
    logger.info("POST /ide/container/restart.")

    try:
        success: bool = await svc.restart_container()

        if success:
            logger.info("Container-Neustart erfolgreich.")
            return {"message": "Container wird neu gestartet.", "success": True}
        else:
            logger.error("Container-Neustart fehlgeschlagen.")
            raise HTTPException(
                status_code=500,
                detail="Container-Neustart fehlgeschlagen. "
                "Prüfe die Docker-Logs für Details.",
            )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Fehler beim Container-Neustart.")
        raise HTTPException(
            status_code=500,
            detail=f"Fehler beim Neustart: {exc}",
        ) from exc
