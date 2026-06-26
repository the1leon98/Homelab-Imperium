"""
Datenbank-Modul des Homelab-Imperiums.

Initialisiert die SQLAlchemy-Verbindung zu PostgreSQL mit optimierten
Pool-Einstellungen für den Produktivbetrieb. Stellt sowohl synchrone als
auch asynchrone Session-Fabriken bereit und bietet FastAPI-Dependency-
Injection-Generatoren für garantierte Ressourcen-Freigabe.

Verwendung (FastAPI-Router)::

    from app.database import get_db
    from fastapi import Depends

    @router.get("/items")
    async def read_items(db: Session = Depends(get_db)):
        ...
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    sessionmaker,
)

from app.config import settings

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.database")

# ═══════════════════════════════════════════════════════════════════════════════
# Datenbank-URLs (synchron & asynchron)
# ═══════════════════════════════════════════════════════════════════════════════

# Synchrone URL für psycopg2 (WSGI, Skripte, einfache Operationen).
# Erwartet: postgresql://user:pass@host:port/db
SYNC_DATABASE_URL: str = settings.database_url

# Asynchrone URL für asyncpg (FastAPI async routes, Streaming).
# Ersetzt das Schema: postgresql:// → postgresql+asyncpg://
_ASYNC_DATABASE_URL: str = SYNC_DATABASE_URL.replace(
    "postgresql://", "postgresql+asyncpg://", 1
).replace(
    "postgres://", "postgres+asyncpg://", 1
)

# ═══════════════════════════════════════════════════════════════════════════════
# Engine-Factory — Erzeugt einen optimierten PostgreSQL-Engine
# ═══════════════════════════════════════════════════════════════════════════════


def _create_engine_kwargs() -> dict[str, Any]:
    """
    Erzeugt die gemeinsamen Engine-Argumente basierend auf den Settings.

    Returns:
        Dictionary mit pool_size, max_overflow, pool_timeout,
        pool_recycle und pool_pre_ping.
    """
    return {
        # Pool-Größe: Anzahl dauerhaft offener Verbindungen.
        # 10 ist ausreichend für ein Homelab mit geringer Parallelität.
        "pool_size": settings.db_pool_size,
        # Maximaler Überlauf: zusätzliche Verbindungen bei Spitzenlast.
        "max_overflow": settings.db_max_overflow,
        # Timeout (Sekunden), bevor ein Thread auf eine freie Verbindung
        # aus dem Pool wartet. Danach wird eine TimeoutError geworfen.
        "pool_timeout": settings.db_pool_timeout,
        # Recycling: Verbindungen, die älter als 1 Stunde sind, werden
        # geschlossen und neu aufgebaut. Verhindert "stale connection"-
        # Fehler durch PostgreSQL-Server-seitige Verbindungs-Timeouts.
        "pool_recycle": 3600,
        # Pre-Ping: Vor jeder Nutzung wird ein leichtgewichtiger Ping
        # (SELECT 1) gesendet. Erkennt getrennte Verbindungen und
        # baut sie automatisch neu auf. Kostet ~0,1 ms pro Checkout.
        "pool_pre_ping": True,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Synchroner Engine & Session-Factory
# ═══════════════════════════════════════════════════════════════════════════════

engine: Engine = create_engine(
    SYNC_DATABASE_URL,
    echo=settings.debug,  # SQL-Logging nur im Debug-Mode
    **_create_engine_kwargs(),
)
"""
Synchroner SQLAlchemy-Engine für PostgreSQL.

Nutzt psycopg2 als Treiber mit optimierten Pool-Einstellungen:
- pool_size: 10 (Standard, aus Settings)
- max_overflow: 20 (Spitzenlast-Puffer)
- pool_recycle: 3600 s (stale connection prevention)
- pool_pre_ping: True (automatische Verbindungsprüfung)
"""

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # Verhindert Lazy-Loading-Fehler nach Commit
)
"""
Synchrone Session-Fabrik.

- ``autocommit=False``: Explizites Commit erforderlich.
- ``autoflush=False``: Flush nur bei explizitem Aufruf oder vor Queries.
- ``expire_on_commit=False``: Objekte bleiben nach Commit nutzbar
  (verhindert DetachedInstanceError).
"""

# ═══════════════════════════════════════════════════════════════════════════════
# Asynchroner Engine & Session-Factory
# ═══════════════════════════════════════════════════════════════════════════════

async_engine: AsyncEngine = create_async_engine(
    _ASYNC_DATABASE_URL,
    echo=settings.debug,
    **_create_engine_kwargs(),
)
"""
Asynchroner SQLAlchemy-Engine für PostgreSQL.

Nutzt asyncpg als Treiber — empfohlen für FastAPI async routes.
Gleiche Pool-Einstellungen wie der synchrone Engine.
"""

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=AsyncSession,
)
"""
Asynchrone Session-Fabrik.

Erzeugt ``AsyncSession``-Instanzen für die Verwendung in
``async def``-Endpunkten. Alle Operationen müssen mit ``await``
ausgeführt werden.
"""

# ═══════════════════════════════════════════════════════════════════════════════
# SQLAlchemy ORM Basis-Klasse
# ═══════════════════════════════════════════════════════════════════════════════


class Base(DeclarativeBase):
    """
    Gemeinsame Basisklasse für alle ORM-Modelle des Homelab-Imperiums.

    Verwendung::

        from app.database import Base
        from sqlalchemy import Column, Integer, String

        class MeinModell(Base):
            __tablename__ = "meine_tabelle"
            id = Column(Integer, primary_key=True)
    """

    pass


# ═══════════════════════════════════════════════════════════════════════════════
# FastAPI Dependency-Injection-Generatoren
# ═══════════════════════════════════════════════════════════════════════════════


def get_db() -> Generator[Session, None, None]:
    """
    Synchrone FastAPI-Dependency für Datenbank-Sessions.

    Garantiert, dass die Session nach Abschluss des Requests geschlossen
    wird — auch bei Exceptions. Rollback erfolgt automatisch bei nicht
    committeden Transaktionen.

    Verwendung::

        from fastapi import Depends
        from app.database import get_db

        @router.get("/items")
        def read_items(db: Session = Depends(get_db)):
            return db.query(Item).all()

    Yields:
        Eine SQLAlchemy ``Session``, gebunden an den synchronen Engine.
    """
    db: Session = SessionLocal()
    try:
        yield db
    except Exception:
        # Bei Fehlern: Rollback, dann Exception weiterwerfen.
        db.rollback()
        logger.exception("Datenbank-Exception — Rollback durchgeführt.")
        raise
    finally:
        db.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Asynchrone FastAPI-Dependency für Datenbank-Sessions.

    Garantiert, dass die AsyncSession nach Abschluss des Requests
    geschlossen wird. Für ``async def``-Endpunkte.

    Verwendung::

        from fastapi import Depends
        from sqlalchemy.ext.asyncio import AsyncSession
        from app.database import get_async_db
        from sqlalchemy import select

        @router.get("/items")
        async def read_items(db: AsyncSession = Depends(get_async_db)):
            result = await db.execute(select(Item))
            return result.scalars().all()

    Yields:
        Eine SQLAlchemy ``AsyncSession``, gebunden an den asynchronen Engine.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            logger.exception("Asynchrone DB-Exception — Rollback durchgeführt.")
            raise


# ═══════════════════════════════════════════════════════════════════════════════
# Context-Manager für synchrone Skripte (außerhalb von FastAPI)
# ═══════════════════════════════════════════════════════════════════════════════


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """
    Synchroner Context-Manager für Datenbank-Sessions.

    Nützlich für Skripte, Cron-Jobs oder Tests, die außerhalb des
    FastAPI-Request-Lebenszyklus laufen.

    Verwendung::

        from app.database import get_db_context

        with get_db_context() as db:
            items = db.query(Item).all()

    Yields:
        Eine SQLAlchemy ``Session`` mit automatischem Rollback bei Fehlern.
    """
    db: Session = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Datenbank-Exception im Context-Manager — Rollback.")
        raise
    finally:
        db.close()


@asynccontextmanager
async def get_async_db_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Asynchroner Context-Manager für Datenbank-Sessions.

    Nützlich für asynchrone Skripte oder Tests.

    Verwendung::

        from app.database import get_async_db_context
        from sqlalchemy import select

        async with get_async_db_context() as db:
            result = await db.execute(select(Item))
            items = result.scalars().all()

    Yields:
        Eine ``AsyncSession`` mit automatischem Commit/Rollback.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "Asynchrone DB-Exception im Context-Manager — Rollback."
            )
            raise


# ═══════════════════════════════════════════════════════════════════════════════
# Engine-Event-Listener
# ═══════════════════════════════════════════════════════════════════════════════


@event.listens_for(engine, "connect")
def _on_connect(dbapi_connection: Any, connection_record: Any) -> None:
    """
    Wird bei jeder neuen Verbindung zum PostgreSQL-Server ausgelöst.

    Setzt sessionspezifische PostgreSQL-Parameter:
    - ``application_name``: Erscheint in ``pg_stat_activity`` — nützlich
      für Debugging und Monitoring.
    - ``statement_timeout``: Begrenzt die maximale Ausführungszeit einer
      Query auf 30 Sekunden. Verhindert hängende Queries.
    - ``idle_in_transaction_session_timeout``: Beendet Sessions, die
      länger als 5 Minuten in einer offenen Transaktion idle sind.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(
            "SET application_name = %s",
            (settings.app_name,),
        )
        cursor.execute("SET statement_timeout = '30s'")
        cursor.execute(
            "SET idle_in_transaction_session_timeout = '300s'"
        )
    except Exception:
        logger.warning(
            "Konnte PostgreSQL-Session-Parameter nicht setzen "
            "(möglicherweise SQLite im Entwicklungsmodus)."
        )
    finally:
        cursor.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Health-Check
# ═══════════════════════════════════════════════════════════════════════════════


def check_database_health() -> dict[str, Any]:
    """
    Führt einen Health-Check gegen die Datenbank durch.

    Führt ``SELECT 1`` aus und sammelt Pool-Statistiken.

    Returns:
        Dictionary mit Status-Indikatoren:
        - ``status``: ``"healthy"`` oder ``"unhealthy"``
        - ``response_time_ms``: Antwortzeit der Query
        - ``pool_size``, ``checked_out``, ``overflow``: Pool-Metriken
    """
    import time

    start: float = time.monotonic()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        elapsed: float = (time.monotonic() - start) * 1000
        pool = engine.pool
        return {
            "status": "healthy",
            "response_time_ms": round(elapsed, 2),
            "pool_size": pool.size(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        }
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        logger.error("Datenbank-Health-Check fehlgeschlagen: %s", e)
        return {
            "status": "unhealthy",
            "error": str(e),
            "response_time_ms": round(elapsed, 2),
        }


async def check_async_database_health() -> dict[str, Any]:
    """
    Führt einen asynchronen Health-Check gegen die Datenbank durch.

    Returns:
        Dictionary mit Status-Indikatoren (siehe ``check_database_health``).
    """
    import time

    start: float = time.monotonic()
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        elapsed: float = (time.monotonic() - start) * 1000
        pool = async_engine.pool
        return {
            "status": "healthy",
            "response_time_ms": round(elapsed, 2),
            "pool_size": pool.size(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        }
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        logger.error("Async-DB-Health-Check fehlgeschlagen: %s", e)
        return {
            "status": "unhealthy",
            "error": str(e),
            "response_time_ms": round(elapsed, 2),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Datenbank-Initialisierung (Schema-Erstellung)
# ═══════════════════════════════════════════════════════════════════════════════


def init_database() -> None:
    """
    Erstellt alle ORM-Tabellen, die noch nicht existieren.

    Wird beim Anwendungsstart aufgerufen. Im Entwicklungsmodus werden
    fehlende Tabellen automatisch erstellt. Im Produktionsmodus sollte
    stattdessen Alembic für Migrationen verwendet werden.

    Achtung:
        Diese Funktion führt KEIN ``DROP TABLE`` aus. Bestehende Tabellen
        bleiben unangetastet. Für Migrationen siehe Alembic-Konfiguration.
    """
    # Import aller Modelle, damit sie bei Base registriert werden
    import app.models  # noqa: F401 — Import für Registrierung

    logger.info(
        "Erstelle Datenbank-Tabellen (falls nicht existent) "
        "in Umgebung: %s",
        settings.api_env,
    )
    Base.metadata.create_all(bind=engine)
    logger.info("Datenbank-Initialisierung abgeschlossen.")


async def init_async_database() -> None:
    """Asynchrone Variante der Datenbank-Initialisierung."""
    import app.models  # noqa: F401

    logger.info(
        "Erstelle Datenbank-Tabellen (async) in Umgebung: %s",
        settings.api_env,
    )
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Async-Datenbank-Initialisierung abgeschlossen.")


# ═══════════════════════════════════════════════════════════════════════════════
# Graceful Shutdown
# ═══════════════════════════════════════════════════════════════════════════════


async def dispose_engines() -> None:
    """
    Schließt alle Verbindungen und gibt Pool-Ressourcen frei.

    Sollte beim Herunterfahren der Anwendung aufgerufen werden
    (FastAPI lifespan event: ``shutdown``).
    """
    logger.info("Schließe Datenbank-Verbindungen...")

    # Synchroner Engine
    engine.dispose()

    # Asynchroner Engine
    await async_engine.dispose()

    logger.info("Alle Datenbank-Pool-Ressourcen freigegeben.")
