"""
Web-IDE-Dienst des Homelab-Imperiums.

Verwaltet Benutzer-Sitzungen für die integrierte code-server-Instanz:
- Container-Health-Checks (Docker-Statusüberwachung)
- Sitzungs-Token-Generierung und -Validierung
- Authentifizierte Iframe-Zugriffskontrolle
- Automatische Sitzungsbereinigung (Timeout)

Das Iframe im Frontend wird NUR gerendert, wenn:
1. Der code-server-Container läuft (Docker-Health-Check)
2. Ein gültiges Sitzungs-Token vorliegt (noch nicht abgelaufen)
3. Der Benutzer authentifiziert ist

Verwendung::

    from app.services.ide import WebIDEManagerService

    svc = WebIDEManagerService()
    if await svc.is_container_healthy():
        session = svc.create_session(user="admin")
        token = session["token"]
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import settings

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.services.ide")


# ═══════════════════════════════════════════════════════════════════════════════
# Datenklassen
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class IDESession:
    """Eine aktive Web-IDE-Sitzung."""

    token: str
    user: str
    created_at: float  # Unix-Timestamp
    expires_at: float
    is_active: bool = True
    last_activity: float = field(default_factory=time.time)


@dataclass
class ContainerStatus:
    """Status des code-server-Docker-Containers."""

    is_running: bool = False
    is_healthy: bool = False
    container_id: str = ""
    container_name: str = ""
    image: str = ""
    uptime_seconds: int = 0
    error_message: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# WebIDEManagerService
# ═══════════════════════════════════════════════════════════════════════════════


class WebIDEManagerService:
    """
    Verwaltung der code-server-Web-IDE.

    - Überwacht den Docker-Container-Status
    - Generiert und validiert Sitzungs-Token
    - Stellt sicher, dass das Iframe nur für autorisierte Nutzer
      gerendert wird
    """

    # Sitzungs-Timeout: 8 Stunden
    _SESSION_TIMEOUT_SECONDS: int = 28800

    # Container-Name (aus docker-compose.yml oder Settings)
    _CONTAINER_NAME: str = "code_server"

    # Health-Check-Interval (Sekunden)
    _HEALTH_CHECK_INTERVAL: int = 60

    def __init__(self) -> None:
        """
        Initialisiert den IDE-Manager mit einem In-Memory-Sitzungsspeicher.
        """
        # In-Memory-Session-Store: token → IDESession
        self._sessions: dict[str, IDESession] = {}

        # Container-Status-Cache
        self._cached_status: Optional[ContainerStatus] = None
        self._last_health_check: float = 0.0

        # Docker-Verfügbarkeit
        self._docker_available: bool | None = None

        # Bereinigung starten (Hintergrund-Task)
        self._cleanup_task: asyncio.Task | None = None

        logger.info(
            "WebIDEManagerService initialisiert: container=%s, "
            "session_timeout=%ds.",
            self._CONTAINER_NAME,
            self._SESSION_TIMEOUT_SECONDS,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Docker-Container-Health
    # ──────────────────────────────────────────────────────────────────────

    async def is_container_healthy(
        self,
        force_check: bool = False,
    ) -> bool:
        """
        Prüft, ob der code-server-Container läuft und gesund ist.

        Nutzt ``docker inspect`` für den Health-Status. Das Ergebnis
        wird zwischengespeichert (TTL: ``_HEALTH_CHECK_INTERVAL``).

        Args:
            force_check: ``True`` = Cache ignorieren, sofort prüfen.

        Returns:
            ``True`` wenn Container läuft UND gesund ist.
        """
        now: float = time.time()

        # Cache noch gültig?
        if (
            not force_check
            and self._cached_status is not None
            and (now - self._last_health_check) < self._HEALTH_CHECK_INTERVAL
        ):
            return self._cached_status.is_healthy

        status: ContainerStatus = await asyncio.to_thread(
            self._check_container_status
        )
        self._cached_status = status
        self._last_health_check = now

        if status.is_healthy:
            logger.debug(
                "code-server-Container ✅ gesund (Uptime: %ds).",
                status.uptime_seconds,
            )
        else:
            logger.warning(
                "code-server-Container ❌ nicht verfügbar: %s",
                status.error_message or "Unbekannter Grund",
            )

        return status.is_healthy

    def _check_container_status(self) -> ContainerStatus:
        """
        Führt ``docker inspect`` für den code-server-Container aus.

        Returns:
            ``ContainerStatus`` mit Laufzeit- und Health-Details.
        """
        # Prüfen, ob Docker verfügbar ist
        if not self._is_docker_available():
            return ContainerStatus(
                error_message="Docker ist nicht verfügbar."
            )

        try:
            result: subprocess.CompletedProcess = subprocess.run(
                [
                    "docker",
                    "inspect",
                    self._CONTAINER_NAME,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return ContainerStatus(
                    error_message=(
                        f"Container '{self._CONTAINER_NAME}' nicht "
                        f"gefunden oder nicht gestartet."
                    ),
                )

            import json

            data: list[dict] = json.loads(result.stdout)
            if not data:
                return ContainerStatus(
                    error_message=f"Keine Docker-Inspect-Daten für "
                    f"'{self._CONTAINER_NAME}'."
                )

            container: dict = data[0]
            state: dict = container.get("State", {})
            is_running: bool = state.get("Running", False)

            if not is_running:
                return ContainerStatus(
                    container_id=container.get("Id", "")[:12],
                    container_name=self._CONTAINER_NAME,
                    error_message="Container ist gestoppt.",
                )

            # Health-Status (aus Docker-Healthcheck)
            health: dict = state.get("Health", {})
            health_status: str = health.get("Status", "unknown")
            is_healthy: bool = health_status == "healthy"

            # Uptime berechnen
            started_at: str = state.get("StartedAt", "")
            uptime: int = 0
            if started_at:
                try:
                    # Docker-Zeitstempel parsen
                    from datetime import datetime as dt

                    start_dt: dt = dt.fromisoformat(
                        started_at.replace("Z", "+00:00")
                    )
                    uptime = int(
                        (
                            dt.now(timezone.utc) - start_dt
                        ).total_seconds()
                    )
                except Exception:
                    pass

            return ContainerStatus(
                is_running=True,
                is_healthy=is_healthy,
                container_id=container.get("Id", "")[:12],
                container_name=self._CONTAINER_NAME,
                image=container.get("Config", {}).get(
                    "Image", "unbekannt"
                ),
                uptime_seconds=uptime,
                error_message=(
                    ""
                    if is_healthy
                    else f"Health-Status: {health_status}"
                ),
            )

        except subprocess.TimeoutExpired:
            return ContainerStatus(
                error_message="Docker-Inspect-Timeout (Container hängt?)."
            )
        except json.JSONDecodeError as exc:
            return ContainerStatus(
                error_message=f"Docker-Inspect-JSON-Fehler: {exc}"
            )
        except Exception as exc:
            logger.error("Docker-Inspect-Fehler: %s", exc)
            return ContainerStatus(error_message=str(exc))

    def _is_docker_available(self) -> bool:
        """Prüft, ob Docker auf dem Host installiert und erreichbar ist."""
        if self._docker_available is not None:
            return self._docker_available

        try:
            result: subprocess.CompletedProcess = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            available: bool = result.returncode == 0
            self._docker_available = available
            if available:
                logger.debug(
                    "Docker verfügbar: %s", result.stdout.strip()
                )
            else:
                logger.warning("Docker nicht verfügbar.")
            return available
        except Exception:
            self._docker_available = False
            return False

    async def restart_container(self) -> bool:
        """
        Startet den code-server-Container neu.

        Returns:
            ``True`` bei erfolgreichem Neustart.
        """
        logger.info("Starte code-server-Container neu...")
        try:
            result: subprocess.CompletedProcess = await asyncio.to_thread(
                lambda: subprocess.run(
                    ["docker", "restart", self._CONTAINER_NAME],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            )
            if result.returncode == 0:
                # Cache leeren
                self._cached_status = None
                logger.info("Container-Neustart erfolgreich.")
                return True
            else:
                logger.error(
                    "Container-Neustart fehlgeschlagen: %s",
                    result.stderr.strip(),
                )
                return False
        except Exception as exc:
            logger.error("Container-Neustart-Fehler: %s", exc)
            return False

    # ──────────────────────────────────────────────────────────────────────
    # Sitzungs-Management
    # ──────────────────────────────────────────────────────────────────────

    def create_session(self, user: str = "admin") -> dict:
        """
        Erstellt eine neue Web-IDE-Sitzung mit einem kryptografisch
        sicheren Token.

        Args:
            user: Benutzername (Default: "admin").

        Returns:
            Dict mit ``token``, ``user``, ``expires_at`` (ISO), ``ttl_seconds``.
        """
        # Token generieren: 32 Bytes Zufall → 64 Hex-Zeichen
        token: str = secrets.token_hex(32)

        now: float = time.time()
        session: IDESession = IDESession(
            token=token,
            user=user,
            created_at=now,
            expires_at=now + self._SESSION_TIMEOUT_SECONDS,
        )

        self._sessions[token] = session

        # Bereinigung starten (falls noch nicht)
        self._ensure_cleanup_running()

        logger.info(
            "IDE-Sitzung erstellt: user=%r, token=%s..., "
            "gültig bis %s.",
            user,
            token[:12],
            datetime.fromtimestamp(
                session.expires_at, tz=timezone.utc
            ).isoformat(),
        )

        return {
            "token": token,
            "user": user,
            "expires_at": datetime.fromtimestamp(
                session.expires_at, tz=timezone.utc
            ).isoformat(),
            "ttl_seconds": self._SESSION_TIMEOUT_SECONDS,
        }

    def validate_token(self, token: str) -> dict | None:
        """
        Validiert ein Sitzungs-Token für den Iframe-Zugriff.

        Prüft:
        1. Token existiert im Session-Store
        2. Token ist noch nicht abgelaufen
        3. Sitzung ist als aktiv markiert

        Args:
            token: Sitzungs-Token (Hex-String, 64 Zeichen).

        Returns:
            Dict mit ``user`` und ``expires_at`` wenn gültig,
            ``None`` wenn ungültig oder abgelaufen.
        """
        if not token:
            logger.debug("Token-Validierung: Leeres Token.")
            return None

        session: IDESession | None = self._sessions.get(token)

        if session is None:
            logger.debug("Token-Validierung: Token nicht gefunden.")
            return None

        if not session.is_active:
            logger.debug(
                "Token-Validierung: Sitzung deaktiviert "
                "(user=%r).",
                session.user,
            )
            return None

        now: float = time.time()
        if now > session.expires_at:
            logger.debug(
                "Token-Validierung: Sitzung abgelaufen "
                "(user=%r, abgelaufen vor %.0fs).",
                session.user,
                now - session.expires_at,
            )
            session.is_active = False
            return None

        # Aktivitäts-Zeitstempel aktualisieren
        session.last_activity = now

        logger.debug(
            "Token-Validierung: ✅ gültig (user=%r, "
            "verbleibend: %.0fs).",
            session.user,
            session.expires_at - now,
        )
        return {
            "user": session.user,
            "expires_at": datetime.fromtimestamp(
                session.expires_at, tz=timezone.utc
            ).isoformat(),
        }

    def invalidate_session(self, token: str) -> bool:
        """
        Invalidiert eine Sitzung (Logout).

        Args:
            token: Sitzungs-Token.

        Returns:
            ``True`` wenn die Sitzung gefunden und invalidiert wurde.
        """
        session: IDESession | None = self._sessions.get(token)
        if session:
            session.is_active = False
            logger.info(
                "IDE-Sitzung invalidiert: user=%r.", session.user
            )
            return True
        return False

    def invalidate_all_sessions(self) -> int:
        """
        Invalidiert ALLE aktiven Sitzungen (Admin-Notfall-Funktion).

        Returns:
            Anzahl der invalidierten Sitzungen.
        """
        count: int = 0
        for session in self._sessions.values():
            if session.is_active:
                session.is_active = False
                count += 1
        logger.warning(
            "%d IDE-Sitzungen zwangsweise invalidiert.", count
        )
        return count

    def get_active_sessions(self) -> list[dict]:
        """
        Listet alle aktiven Sitzungen auf (für Admin-Übersicht).

        Returns:
            Liste von Dicts mit ``user``, ``created_at``, ``expires_at``.
        """
        now: float = time.time()
        active: list[dict] = []

        for token, session in self._sessions.items():
            if session.is_active and now <= session.expires_at:
                active.append(
                    {
                        "token_prefix": token[:12] + "...",
                        "user": session.user,
                        "created_at": datetime.fromtimestamp(
                            session.created_at, tz=timezone.utc
                        ).isoformat(),
                        "expires_at": datetime.fromtimestamp(
                            session.expires_at, tz=timezone.utc
                        ).isoformat(),
                        "remaining_seconds": round(
                            session.expires_at - now
                        ),
                    }
                )

        return active

    # ──────────────────────────────────────────────────────────────────────
    # Iframe-Zugriffsprüfung (kombiniert Container + Token)
    # ──────────────────────────────────────────────────────────────────────

    async def authorize_iframe_access(
        self,
        token: str,
    ) -> dict:
        """
        Vollständige Zugriffsprüfung für das IDE-Iframe.

        Kombiniert Container-Health-Check und Token-Validierung.
        Nur wenn BEIDE Prüfungen bestanden sind, wird das Iframe
        gerendert.

        Args:
            token: Sitzungs-Token.

        Returns:
            Dict mit:
            - ``authorized``: ``True`` wenn Iframe gerendert werden darf
            - ``user``: Benutzername
            - ``container_healthy``: Container-Status
            - ``token_valid``: Token-Status
            - ``iframe_url``: URL für das Iframe (nur bei authorized=True)
            - ``reason``: Grund bei Ablehnung
        """
        # Prüfung 1: Container-Health
        container_ok: bool = await self.is_container_healthy()

        if not container_ok:
            return {
                "authorized": False,
                "user": "",
                "container_healthy": False,
                "token_valid": False,
                "iframe_url": "",
                "reason": (
                    "Der code-server-Container ist derzeit nicht "
                    "verfügbar. Bitte versuche es in einigen Minuten "
                    "erneut."
                ),
            }

        # Prüfung 2: Token-Validierung
        token_info: dict | None = self.validate_token(token)

        if token_info is None:
            return {
                "authorized": False,
                "user": "",
                "container_healthy": True,
                "token_valid": False,
                "iframe_url": "",
                "reason": (
                    "Ungültiges oder abgelaufenes Sitzungs-Token. "
                    "Bitte melde dich erneut an."
                ),
            }

        # Beide Prüfungen bestanden → Iframe-URL generieren
        iframe_url: str = self._build_iframe_url(token)

        logger.info(
            "IDE-Iframe-Zugriff autorisiert: user=%r.",
            token_info["user"],
        )

        return {
            "authorized": True,
            "user": token_info["user"],
            "container_healthy": True,
            "token_valid": True,
            "iframe_url": iframe_url,
            "reason": "",
        }

    def _build_iframe_url(self, token: str) -> str:
        """
        Baut die Iframe-URL für den code-server zusammen.

        Die URL zeigt auf den Caddy-Proxy-Pfad ``/ide/`` mit dem
        Token als Query-Parameter (optional, je nach code-server-Konfig).

        Returns:
            Relative URL, z.B. ``"/ide/?token=abc123"``.
        """
        return f"/ide/?token={token}"

    # ──────────────────────────────────────────────────────────────────────
    # Sitzungsbereinigung
    # ──────────────────────────────────────────────────────────────────────

    def _cleanup_expired_sessions(self) -> int:
        """
        Entfernt alle abgelaufenen Sitzungen aus dem Store.

        Returns:
            Anzahl der bereinigten Sitzungen.
        """
        now: float = time.time()
        expired_tokens: list[str] = []

        for token, session in self._sessions.items():
            if now > session.expires_at:
                expired_tokens.append(token)

        for token in expired_tokens:
            del self._sessions[token]

        if expired_tokens:
            logger.info(
                "%d abgelaufene IDE-Sitzungen bereinigt.",
                len(expired_tokens),
            )
        return len(expired_tokens)

    def _ensure_cleanup_running(self) -> None:
        """
        Startet den periodischen Bereinigungs-Task, falls nicht bereits
        aktiv.
        """
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(
                self._periodic_cleanup()
            )
            logger.debug("Sitzungsbereinigung gestartet.")

    async def _periodic_cleanup(self) -> None:
        """
        Führt alle 10 Minuten eine Bereinigung abgelaufener Sitzungen durch.
        """
        while True:
            await asyncio.sleep(600)  # 10 Minuten
            self._cleanup_expired_sessions()
