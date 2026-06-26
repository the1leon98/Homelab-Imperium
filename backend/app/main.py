"""
Haupteinstiegspunkt des Homelab-Imperium Backends.

Initialisiert die FastAPI-Anwendung, registriert alle Router,
konfiguriert CORS und richtet die Startup-/Shutdown-Lebenszyklus-
Ereignisse ein.

Starten::

    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

# ═══════════════════════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=settings.log_level_int,
    format=(
        "%(asctime)s.%(msecs)03d | %(levelname)-8s | "
        "%(name)-35s | %(message)s"
    ),
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger: logging.Logger = logging.getLogger("homelab_imperium")

# ═══════════════════════════════════════════════════════════════════════════════
# Application Lifespan (Startup & Shutdown)
# ═══════════════════════════════════════════════════════════════════════════════


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Verwaltet den Lebenszyklus der FastAPI-Anwendung.

    **Startup:**
    - Datenbank-Verbindung initialisieren
    - Datenbank-Tabellen erstellen (falls nicht existent)
    - Statische Verzeichnisse sicherstellen

    **Shutdown:**
    - Datenbank-Verbindungen schließen
    - Hintergrund-Tasks beenden
    """
    # ═══ STARTUP ═══
    logger.info(
        "╔══════════════════════════════════════════════════════╗"
    )
    logger.info(
        "║  Homelab-Imperium Backend wird gestartet...         ║"
    )
    logger.info(
        "╠══════════════════════════════════════════════════════╣"
    )
    logger.info("║  App:     %-42s ║", settings.app_name)
    logger.info("║  Umgebung: %-40s ║", settings.api_env)
    logger.info("║  Debug:    %-40s ║", str(settings.debug))
    logger.info(
        "║  DB-URL:   %-40s ║",
        settings.database_url.split("@")[-1]
        if "@" in settings.database_url
        else settings.database_url[:40],
    )
    logger.info(
        "╚══════════════════════════════════════════════════════╝"
    )

    try:
        # Datenbank initialisieren
        from app.database import init_database

        init_database()
        logger.info("✅ Datenbank initialisiert.")

        # Verzeichnisse erstellen
        created: list = settings.ensure_directories()
        if created:
            logger.info(
                "✅ %d Verzeichnisse erstellt.", len(created)
            )

    except Exception as exc:
        logger.error("❌ Startup-Fehler: %s", exc)
        # Im Entwicklungsmodus nicht abbrechen (SQLite-Fallback)
        if settings.is_production:
            raise

    yield  # ── Anwendung läuft ──

    # ═══ SHUTDOWN ═══
    logger.info("Homelab-Imperium Backend wird heruntergefahren...")

    try:
        from app.database import dispose_engines

        await dispose_engines()
        logger.info("✅ Datenbank-Verbindungen geschlossen.")
    except Exception as exc:
        logger.error("❌ Shutdown-Fehler (DB): %s", exc)

    logger.info("Homelab-Imperium Backend beendet.")


# ═══════════════════════════════════════════════════════════════════════════════
# FastAPI-Anwendung
# ═══════════════════════════════════════════════════════════════════════════════

app: FastAPI = FastAPI(
    title=settings.app_name,
    description=(
        "**Homelab-Imperium** — Das zentrale HomeOS-Backend. "
        "Verwaltet Medien, Finanzen, Gesundheit, Schule, Fahrzeuge "
        "und KI-Agenten auf einem lokalen Ubuntu-Server (HP EliteDesk). "
        "Kommuniziert ausschließlich über das verschlüsselte "
        "Tailscale-Mesh-Netzwerk."
    ),
    version="1.0.0-phase0",
    docs_url="/docs" if settings.is_development else None,
    redoc_url="/redoc" if settings.is_development else None,
    lifespan=lifespan,
)

# ═══════════════════════════════════════════════════════════════════════════════
# CORS (Cross-Origin Resource Sharing)
# ═══════════════════════════════════════════════════════════════════════════════
#
# RESTRIKTIVE KONFIGURATION:
# - Nur explizit erlaubte Tailscale-Hostnamen dürfen auf die API zugreifen
# - Keine Wildcard-Origins (``*``)
# - Im Entwicklungsmodus: zusätzlich localhost für lokales Frontend

_ALLOWED_ORIGINS: list[str] = [
    # Tailscale-MagicDNS (Produktion)
    "https://hp-server.tailscale-mesh.net",
    "https://hp-server.local",
]

# Entwicklungsmodus: localhost-Herkünfte erlauben
if settings.is_development or settings.feature_cors_dev_mode:
    _ALLOWED_ORIGINS.extend(
        [
            "http://localhost:3000",
            "http://localhost:5173",  # Vite
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Requested-With",
        "Accept",
    ],
    max_age=3600,  # Preflight-Cache: 1 Stunde
)

logger.info(
    "CORS konfiguriert: %d Origins erlaubt.", len(_ALLOWED_ORIGINS)
)
for origin in _ALLOWED_ORIGINS:
    logger.debug("  ✓ %s", origin)

# ═══════════════════════════════════════════════════════════════════════════════
# Router-Registrierung
# ═══════════════════════════════════════════════════════════════════════════════

# Health & System (Infrastruktur)
from app.routers.health import router as health_router  # noqa: E402
from app.routers.system import router as system_router  # noqa: E402

# Medien, Dateien, Finanzen (Kernmodule)
from app.routers.media import router as media_router  # noqa: E402
from app.routers.files import router as files_router  # noqa: E402
from app.routers.finance import router as finance_router  # noqa: E402

# Gesundheit & Biometrie
from app.routers.health_bio import router as health_bio_router  # noqa: E402

# Schule
from app.routers.school import router as school_router  # noqa: E402

# Automotive
from app.routers.auto import router as auto_router  # noqa: E402

# Code & Sandbox
from app.routers.code import router as code_router  # noqa: E402

# Musik
from app.routers.music import router as music_router  # noqa: E402

# Web-IDE
from app.routers.ide import router as ide_router  # noqa: E402

# KI & Agenten
from app.routers.ai import router as ai_router  # noqa: E402

# ── Alle Router mit /api-Präfix registrieren ──
app.include_router(health_router, prefix="/api")
app.include_router(system_router, prefix="/api")
app.include_router(media_router, prefix="/api")
app.include_router(files_router, prefix="/api")
app.include_router(finance_router, prefix="/api")
app.include_router(health_bio_router, prefix="/api")
app.include_router(school_router, prefix="/api")
app.include_router(auto_router, prefix="/api")
app.include_router(code_router, prefix="/api")
app.include_router(music_router, prefix="/api")
app.include_router(ide_router, prefix="/api")
app.include_router(ai_router, prefix="/api")

logger.info("✅ 12 Router registriert.")

# ═══════════════════════════════════════════════════════════════════════════════
# Wurzel-Endpunkt
# ═══════════════════════════════════════════════════════════════════════════════


@app.get(
    "/",
    summary="API-Wurzel",
    description="Begrüßungsseite mit Links zur API-Dokumentation.",
)
async def root() -> dict:
    """
    Einfacher Wurzel-Endpunkt zur API-Erkennung.
    """
    return {
        "application": settings.app_name,
        "version": "1.0.0-phase0",
        "environment": settings.api_env,
        "docs": "/docs" if settings.is_development else None,
        "health": "/api/health",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
