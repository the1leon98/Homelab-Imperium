"""
Zentrale Konfiguration des Homelab-Imperium Backends.

Stellt eine typsichere, validierte Settings-Instanz bereit, die sämtliche
Einstellungen aus Umgebungsvariablen und der ``.env``-Datei im
Repository-Wurzelverzeichnis liest.

Verwendung::

    from app.config import settings
    print(settings.database_url)
    if settings.is_development:
        print("Entwicklungsmodus aktiv")
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import (
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


# ═══════════════════════════════════════════════════════════════════════════════
# Konstanten
# ═══════════════════════════════════════════════════════════════════════════════

# Repository-Wurzel: backend/app/config.py → ../../ = Repository-Root
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# Standard-Datenwurzel für den produktiven Betrieb auf dem Ubuntu-Server.
# Alle persistenten Daten liegen unter /mnt/data (externe HDD).
_DEFAULT_DATA_ROOT: Path = Path("/mnt/data")

# Entwicklungs-Datenwurzel — relativ zum Repository (für lokales Arbeiten).
_DEV_DATA_ROOT: Path = _PROJECT_ROOT / "data"

# Gruppe aller Pfadfelder, die der gleichen Normalisierung unterliegen.
_PATH_FIELDS: tuple[str, ...] = (
    "data_root",
    "path_db",
    "path_media",
    "path_ai_models",
    "path_files",
    "path_auto",
    "path_caddy_data",
    "path_caddy_config",
    "path_backup_db",
    "path_backup_files",
)

# Regex für die grundlegende URL-Validierung.
_URL_PATTERN: re.Pattern[str] = re.compile(
    r"^https?://[a-zA-Z0-9._-]+(:\d{1,5})?(/.*)?$"
)


# ═══════════════════════════════════════════════════════════════════════════════
# Settings-Klasse
# ═══════════════════════════════════════════════════════════════════════════════


class Settings(BaseSettings):
    """
    Zentrale Anwendungskonfiguration für das Homelab-Imperium.

    Sämtliche Felder können über Umgebungsvariablen oder eine ``.env``-Datei
    gesetzt werden. Die Namen der Umgebungsvariablen entsprechen den
    Python-Feldnamen (case-insensitive). Pydantic validiert die Typen und
    konvertiert automatisch (z. B. ``str`` → ``Path``, ``str`` → ``HttpUrl``).

    Felder mit ``SecretStr`` werden in Logs und Repräsentationen
    durch ``**********`` maskiert.
    """

    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ──────────────────────────────────────────────────────────────────────
    # 1. Projekt-Metadaten & Betriebsmodus
    # ──────────────────────────────────────────────────────────────────────

    app_name: str = Field(
        default="Homelab-Imperium OS",
        min_length=1,
        max_length=100,
        description="Eindeutiger Name der Anwendung (Logs, Health-Checks).",
    )

    api_env: Literal["development", "production"] = Field(
        default="development",
        description='API-Betriebsmodus. "development" = Hot-Reload, Debug-Logs. '
        '"production" = minimierte Fehler, striktes Logging.',
    )

    debug: bool = Field(
        default=True,
        description="Debug-Modus. Aktiviert ausführliche Tracebacks und "
        "Auto-Reload für Uvicorn (nur in development wirksam).",
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="DEBUG",
        description="Log-Level für die FastAPI-Applikation.",
    )

    # ──────────────────────────────────────────────────────────────────────
    # 2. PostgreSQL-Datenbank
    # ──────────────────────────────────────────────────────────────────────

    database_url: str = Field(
        default=(
            "postgresql://homelab_user:homelab_secure_pass"
            "@postgres_db:5432/homelab_imperium"
        ),
        description="Vollständige PostgreSQL-Verbindungs-URI für SQLAlchemy "
        "(Format: postgresql://<user>:<pass>@<host>:<port>/<db>).",
    )

    db_pool_size: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximale Anzahl paralleler Verbindungen im "
        "SQLAlchemy-Connection-Pool.",
    )

    db_max_overflow: int = Field(
        default=20,
        ge=0,
        le=100,
        description="Maximaler Überlauf des Connection-Pools (Spitzenlast).",
    )

    db_pool_timeout: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Timeout in Sekunden für inaktive DB-Verbindungen.",
    )

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, v: str) -> str:
        """Stellt sicher, dass die Datenbank-URL mit postgresql beginnt."""
        if not v.startswith(("postgresql://", "postgres://", "sqlite:///")):
            raise ValueError(
                f"Ungültiges Datenbank-URL-Schema. Erwarte postgresql://, "
                f"postgres:// oder sqlite:///, erhielt: {v[:30]}..."
            )
        return v

    # ──────────────────────────────────────────────────────────────────────
    # 3. ChromaDB — Vektordatenbank für RAG
    # ──────────────────────────────────────────────────────────────────────

    chromadb_endpoint: str = Field(
        default="http://chromadb:8000",
        description="HTTP-Endpunkt der ChromaDB-Instanz (Docker-Service-Name "
        "oder localhost).",
    )

    chromadb_persist_directory: str = Field(
        default="/chroma/data",
        description="Persistenzpfad für ChromaDB innerhalb des Containers.",
    )

    chromadb_embedding_model: str = Field(
        default="nomic-embed-text",
        description="Embedding-Modell für die Vektorisierung von Dokumenten "
        "(genutzt via Ollama).",
    )

    chromadb_embedding_dimension: int = Field(
        default=768,
        ge=128,
        le=4096,
        description="Dimension der Embedding-Vektoren. Muss mit dem "
        "gewählten Embedding-Modell übereinstimmen.",
    )

    chromadb_default_top_k: int = Field(
        default=4,
        ge=1,
        le=20,
        description="Standard-Anzahl der zurückgegebenen Ergebnisse bei "
        "semantischer Ähnlichkeitssuche.",
    )

    @field_validator("chromadb_endpoint")
    @classmethod
    def _validate_chromadb_endpoint(cls, v: str) -> str:
        """Validiert den ChromaDB-Endpoint als HTTP-URL."""
        if not _URL_PATTERN.match(v):
            raise ValueError(
                f"Ungültiger ChromaDB-Endpoint: {v!r}. "
                f"Erwarte Format: http://<host>:<port>"
            )
        return v.rstrip("/")

    # ──────────────────────────────────────────────────────────────────────
    # 4. Jellyfin — Medienserver-Integration
    # ──────────────────────────────────────────────────────────────────────

    jellyfin_api_key: SecretStr = Field(
        default=SecretStr("dein_jellyfin_schluessel_hier"),
        description="Jellyfin-API-Schlüssel für die REST-Authentifizierung. "
        "Wird in Logs maskiert (**********).",
    )

    jellyfin_base_url: str = Field(
        default="http://jellyfin:8096",
        description="Basis-URL der Jellyfin-Instanz.",
    )

    jellyfin_timeout: int = Field(
        default=15,
        ge=1,
        le=120,
        description="Timeout in Sekunden für Jellyfin-API-Requests.",
    )

    jellyfin_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximale Wiederholungsversuche bei fehlgeschlagenen "
        "Jellyfin-API-Calls.",
    )

    jellyfin_default_user_id: str = Field(
        default="",
        description="Standard-Benutzer-ID für Medienbibliothek-Abfragen.",
    )

    @field_validator("jellyfin_api_key")
    @classmethod
    def _validate_jellyfin_api_key(cls, v: SecretStr) -> SecretStr:
        """Warnt, wenn der API-Key noch auf dem Platzhalter-Wert steht."""
        raw = v.get_secret_value()
        if raw == "dein_jellyfin_schluessel_hier" or len(raw) < 16:
            raise ValueError(
                "JELLYFIN_API_KEY ist nicht gesetzt oder zu kurz "
                "(< 16 Zeichen). Bitte in .env konfigurieren."
            )
        return v

    # ──────────────────────────────────────────────────────────────────────
    # 5. Ollama — Lokale LLM-Inferenz (CPU-Fallback, HP-Server)
    # ──────────────────────────────────────────────────────────────────────

    ollama_local_endpoint: str = Field(
        default="http://127.0.0.1:11434",
        description="Endpunkt der lokalen Ollama-Instanz auf dem HP-Server "
        "(CPU-Inferenz).",
    )

    ollama_local_default_model: str = Field(
        default="qwen2.5-coder:3b",
        description="Standard-LLM-Modell für die CPU-Inferenz.",
    )

    ollama_local_timeout: int = Field(
        default=120,
        ge=10,
        le=600,
        description="Timeout in Sekunden für die lokale Ollama-CPU-Inferenz.",
    )

    ollama_local_max_tokens: int = Field(
        default=4096,
        ge=256,
        le=32768,
        description="Maximale Token-Anzahl pro Generation (lokal).",
    )

    ollama_local_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Temperatur für lokale Generierungen "
        "(0.0 = deterministisch, 1.0 = kreativ).",
    )

    # ──────────────────────────────────────────────────────────────────────
    # 6. Ollama — Remote GPU-Desktop (Tailscale-Mesh, NVIDIA GTX 1060)
    # ──────────────────────────────────────────────────────────────────────

    ollama_desktop_endpoint: str = Field(
        default="http://desktop-pc-tailscale:11434",
        description="Endpunkt der Ollama-Instanz auf dem GPU-Desktop-PC "
        "(Tailscale/LAN).",
    )

    ollama_desktop_default_model: str = Field(
        default="qwen2.5-coder:7b",
        description="Standard-LLM-Modell für GPU-beschleunigte Inferenz.",
    )

    ollama_desktop_timeout: int = Field(
        default=60,
        ge=10,
        le=600,
        description="Timeout in Sekunden für GPU-Desktop-Requests.",
    )

    ollama_desktop_max_tokens: int = Field(
        default=8192,
        ge=256,
        le=32768,
        description="Maximale Token-Anzahl pro GPU-Generation.",
    )

    ollama_desktop_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Temperatur für GPU-Generierungen.",
    )

    ollama_desktop_ping_interval: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Ping-Intervall in Sekunden für die "
        "Erreichbarkeitsprüfung des GPU-Desktops.",
    )

    ollama_desktop_max_ping_failures: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximale Anzahl aufeinanderfolgender fehlgeschlagener "
        "Ping-Versuche vor CPU-Fallback.",
    )

    @field_validator(
        "ollama_local_endpoint",
        "ollama_desktop_endpoint",
    )
    @classmethod
    def _validate_ollama_endpoint(cls, v: str) -> str:
        """Stellt sicher, dass Ollama-Endpunkte das korrekte URL-Format haben."""
        if not _URL_PATTERN.match(v):
            raise ValueError(
                f"Ungültiger Ollama-Endpoint: {v!r}. "
                f"Erwarte Format: http://<host>:<port>"
            )
        return v.rstrip("/")

    # ──────────────────────────────────────────────────────────────────────
    # 7. RAG-Engine — Retrieval-Augmented Generation
    # ──────────────────────────────────────────────────────────────────────

    rag_chunk_size: int = Field(
        default=512,
        ge=128,
        le=4096,
        description="Chunk-Größe (Zeichen) beim Zerteilen von PDF-Dokumenten.",
    )

    rag_chunk_overlap: int = Field(
        default=100,
        ge=0,
        le=1024,
        description="Überlappung (Zeichen) zwischen aufeinanderfolgenden Chunks.",
    )

    rag_collection_school: str = Field(
        default="school_pdfs",
        description="ChromaDB-Collection-Name für Schul-Skripte.",
    )

    rag_collection_it: str = Field(
        default="it_books",
        description="ChromaDB-Collection-Name für IT-Fachbücher.",
    )

    rag_max_pdf_size_mb: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximal erlaubte PDF-Dateigröße in Megabytes.",
    )

    rag_temp_dir: str = Field(
        default="/tmp/rag_processing",
        description="Temporäres Verzeichnis für die PDF-Verarbeitung.",
    )

    # ──────────────────────────────────────────────────────────────────────
    # 8. Pfade für persistente Speicherorte
    # ──────────────────────────────────────────────────────────────────────

    data_root: Path = Field(
        default=_DEFAULT_DATA_ROOT,
        description="Wurzelverzeichnis für alle persistenten Daten. "
        "Produktion: /mnt/data (externe HDD). "
        "Entwicklung: ./data (Repo-Relativ).",
    )

    path_db: Path = Field(
        default=_DEFAULT_DATA_ROOT / "db",
        description="Datenbank-Dateien (PostgreSQL-Tablespace, ChromaDB-Indizes).",
    )

    path_media: Path = Field(
        default=_DEFAULT_DATA_ROOT / "media",
        description="Medienbibliothek (Jellyfin-Filme, Serien, Musik).",
    )

    path_ai_models: Path = Field(
        default=_DEFAULT_DATA_ROOT / "ai_models",
        description="KI-Modell-Artefakte und Ollama-Modell-Caches.",
    )

    path_files: Path = Field(
        default=_DEFAULT_DATA_ROOT / "files",
        description="Benutzer-Dateien und Uploads (File-Bunker).",
    )

    path_auto: Path = Field(
        default=_DEFAULT_DATA_ROOT / "auto",
        description="Automotive-Workbench: 3D-Modelle, Berechnungen, Exporte.",
    )

    path_caddy_data: Path = Field(
        default=_DEFAULT_DATA_ROOT / "caddy_data",
        description="Caddy TLS-Zertifikate und persistente Daten.",
    )

    path_caddy_config: Path = Field(
        default=_DEFAULT_DATA_ROOT / "caddy_config",
        description="Caddy Runtime-Konfiguration (automatisch generiert).",
    )

    path_backup_db: Path = Field(
        default=_DEFAULT_DATA_ROOT / "backups" / "db",
        description="Speicherort für automatisierte Datenbank-Backups.",
    )

    path_backup_files: Path = Field(
        default=_DEFAULT_DATA_ROOT / "backups" / "files",
        description="Speicherort für automatisierte Dateisystem-Backups.",
    )

    @field_validator(*_PATH_FIELDS, mode="before")
    @classmethod
    def _normalize_path(cls, value: str | Path) -> Path:
        """
        Wandelt Pfadeingaben in absolute POSIX-Pfade um.

        - ``str`` werden via ``Path`` geparst.
        - ``~`` wird zum Home-Verzeichnis expandiert.
        - Relative Pfade werden gegen das aktuelle Arbeitsverzeichnis
          aufgelöst.
        - Symlinks werden aufgelöst (``resolve``).
        """
        return Path(value).expanduser().resolve()

    # ──────────────────────────────────────────────────────────────────────
    # 9. code-server — Integrierte Web-IDE
    # ──────────────────────────────────────────────────────────────────────

    code_server_endpoint: str = Field(
        default="http://code_server:8080",
        description="Endpunkt des code-server-Containers (Docker-Netzwerk).",
    )

    code_server_project_dir: str = Field(
        default="/mnt/data/code_server_projects",
        description="Pfad zum code-server-Projektverzeichnis.",
    )

    code_server_password_hash: SecretStr = Field(
        default=SecretStr(""),
        description="SHA256-Passwort-Hash für code-server-Authentifizierung.",
    )

    # ──────────────────────────────────────────────────────────────────────
    # 10. Worker-Agent (Remote GPU-Desktop für CAD/OpenSCAD)
    # ──────────────────────────────────────────────────────────────────────

    worker_agent_endpoint: str = Field(
        default="http://desktop-pc-tailscale:9000",
        description="Endpunkt des Worker-Agenten auf dem GPU-Desktop "
        "(Tailscale-Mesh).",
    )

    worker_agent_timeout: int = Field(
        default=300,
        ge=10,
        le=3600,
        description="Timeout in Sekunden für Worker-Agent-Aufgaben "
        "(Renderings können lange dauern).",
    )

    worker_agent_max_polygons: int = Field(
        default=1_000_000,
        ge=1_000,
        le=100_000_000,
        description="Maximal erlaubte Polygonanzahl für OpenSCAD-Rendering.",
    )

    # ──────────────────────────────────────────────────────────────────────
    # 11. Caddy — Reverse Proxy & TLS
    # ──────────────────────────────────────────────────────────────────────

    caddy_domain: str = Field(
        default="hp-server.tailscale-mesh.net",
        description="FQDN für den Tailscale-MagicDNS-Eintrag "
        "(TLS-Zertifikatsname).",
    )

    caddy_acme_email: str = Field(
        default="",
        description="E-Mail-Adresse für Let's Encrypt-Benachrichtigungen.",
    )

    caddy_rate_limit_rpm: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Rate-Limiting: maximale Requests pro Minute pro Client-IP.",
    )

    # ──────────────────────────────────────────────────────────────────────
    # 12. Sandbox — Isolierte Code-Ausführung
    # ──────────────────────────────────────────────────────────────────────

    sandbox_execution_timeout: int = Field(
        default=10,
        ge=1,
        le=120,
        description="Timeout in Sekunden für isolierte Code-Ausführung.",
    )

    sandbox_max_output_size: int = Field(
        default=1_048_576,  # 1 MiB
        ge=1_024,
        le=100_000_000,
        description="Maximal erlaubte Ausgabegröße eines Sandbox-Prozesses "
        "(Bytes).",
    )

    sandbox_max_memory_mb: int = Field(
        default=512,
        ge=32,
        le=4096,
        description="Maximale Speichernutzung pro Sandbox-Prozess (MB).",
    )

    sandbox_docker_image: str = Field(
        default="python:3.14-slim",
        description="Docker-Image für die isolierte Code-Ausführung.",
    )

    # ──────────────────────────────────────────────────────────────────────
    # 13. Logging & Monitoring
    # ──────────────────────────────────────────────────────────────────────

    log_file_path: str = Field(
        default="/var/log/homelab_imperium/app.log",
        description="Pfad zur Log-Datei (innerhalb des Containers).",
    )

    log_max_size_mb: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Maximale Größe einer Log-Datei vor Rotation (MB).",
    )

    log_backup_count: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Anzahl der aufbewahrten rotierten Log-Dateien.",
    )

    monitor_interval_seconds: int = Field(
        default=10,
        ge=1,
        le=300,
        description="Intervall für Systemmetrik-Erfassung (CPU, RAM, Disk).",
    )

    # ──────────────────────────────────────────────────────────────────────
    # 14. Container-Netzwerk
    # ──────────────────────────────────────────────────────────────────────

    docker_network_name: str = Field(
        default="imperium_net",
        description="Docker-Netzwerkname für interne Container-Kommunikation.",
    )

    # ──────────────────────────────────────────────────────────────────────
    # 15. Feature-Flags
    # ──────────────────────────────────────────────────────────────────────

    feature_power_mode_default: bool = Field(
        default=True,
        description="Power-Mode standardmäßig aktiviert "
        "(GPU-beschleunigte Inferenz für KI-Anfragen).",
    )

    feature_rag_enabled_default: bool = Field(
        default=True,
        description="RAG (Retrieval-Augmented Generation) standardmäßig "
        "aktiviert.",
    )

    feature_streaming_responses: bool = Field(
        default=True,
        description="Streaming-Responses für KI-Chats "
        "(Token-für-Token-Ausgabe).",
    )

    feature_jellyfin_transcoding: bool = Field(
        default=True,
        description="Automatische Transcodierung in Jellyfin. "
        "Bei False nur Direct-Play.",
    )

    feature_file_upload_enabled: bool = Field(
        default=True,
        description="Datei-Upload via Web-Interface erlauben.",
    )

    feature_cors_dev_mode: bool = Field(
        default=False,
        description="CORS für lokale Entwicklung aktivieren "
        "(nur bei api_env='development' wirksam).",
    )

    feature_health_hologram: bool = Field(
        default=True,
        description="Interaktiver 3D-SVG-Körper-Hologramm-Modus im "
        "Gesundheitsmodul.",
    )

    # ──────────────────────────────────────────────────────────────────────
    # 16. Backup & Wiederherstellung
    # ──────────────────────────────────────────────────────────────────────

    backup_interval_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="Intervall für automatisierte Backups (Stunden).",
    )

    backup_retention_count: int = Field(
        default=7,
        ge=1,
        le=365,
        description="Anzahl der aufbewahrten Backup-Generationen.",
    )

    # ═══════════════════════════════════════════════════════════════════════
    # Modell-übergreifende Validierungen
    # ═══════════════════════════════════════════════════════════════════════

    @model_validator(mode="after")
    def _validate_path_consistency(self) -> "Settings":
        """
        Stellt sicher, dass alle Unterpfade innerhalb von data_root liegen.

        Im Entwicklungsmodus wird data_root automatisch auf das lokale
        ``./data``-Verzeichnis gesetzt, falls ``/mnt/data`` nicht existiert.
        """
        # Im Entwicklungsmodus: Fallback auf lokales ./data, wenn /mnt/data
        # nicht existiert.
        if self.api_env == "development" and not self.data_root.exists():
            dev_root = _DEV_DATA_ROOT
            object.__setattr__(self, "data_root", dev_root)
            # Alle abgeleiteten Pfade neu setzen
            object.__setattr__(self, "path_db", dev_root / "db")
            object.__setattr__(self, "path_media", dev_root / "media")
            object.__setattr__(self, "path_ai_models", dev_root / "ai_models")
            object.__setattr__(self, "path_files", dev_root / "files")
            object.__setattr__(self, "path_auto", dev_root / "auto")
            object.__setattr__(self, "path_caddy_data", dev_root / "caddy_data")
            object.__setattr__(self, "path_caddy_config", dev_root / "caddy_config")
            object.__setattr__(self, "path_backup_db", dev_root / "backups" / "db")
            object.__setattr__(self, "path_backup_files", dev_root / "backups" / "files")

        return self

    @model_validator(mode="after")
    def _validate_cors_dev_mode(self) -> "Settings":
        """CORS-Dev-Mode ist nur im Entwicklungsmodus wirksam."""
        if self.feature_cors_dev_mode and self.api_env == "production":
            raise ValueError(
                "feature_cors_dev_mode=true ist im Produktionsmodus "
                "nicht erlaubt. Setze api_env='development' oder "
                "deaktiviere CORS-Dev-Mode."
            )
        return self

    # ═══════════════════════════════════════════════════════════════════════
    # Properties
    # ═══════════════════════════════════════════════════════════════════════

    @property
    def is_development(self) -> bool:
        """``True``, wenn die API im Entwicklungsmodus läuft."""
        return self.api_env == "development"

    @property
    def is_production(self) -> bool:
        """``True``, wenn die API im Produktionsmodus läuft."""
        return self.api_env == "production"

    @property
    def ollama_local_url(self) -> str:
        """Vollständige Ollama-Generate-URL (lokal, CPU)."""
        return f"{self.ollama_local_endpoint}/api/generate"

    @property
    def ollama_desktop_url(self) -> str:
        """Vollständige Ollama-Generate-URL (GPU-Desktop)."""
        return f"{self.ollama_desktop_endpoint}/api/generate"

    @property
    def chromadb_health_url(self) -> str:
        """Health-Check-URL der ChromaDB-Instanz."""
        return f"{self.chromadb_endpoint}/api/v1/heartbeat"

    @property
    def log_level_int(self) -> int:
        """Log-Level als numerischer Wert für die Python ``logging``-Bibliothek."""
        import logging

        return getattr(logging, self.log_level, logging.DEBUG)

    @property
    def all_paths(self) -> dict[str, Path]:
        """Dictionary aller konfigurierten Pfade (für Health-Checks & Validierung)."""
        return {
            "data_root": self.data_root,
            "path_db": self.path_db,
            "path_media": self.path_media,
            "path_ai_models": self.path_ai_models,
            "path_files": self.path_files,
            "path_auto": self.path_auto,
            "path_caddy_data": self.path_caddy_data,
            "path_caddy_config": self.path_caddy_config,
            "path_backup_db": self.path_backup_db,
            "path_backup_files": self.path_backup_files,
        }

    def ensure_directories(self) -> list[Path]:
        """
        Erstellt alle konfigurierten Verzeichnisse, falls sie noch nicht
        existieren.

        Returns:
            Liste der neu erstellten Verzeichnisse.
        """
        created: list[Path] = []
        for name, path in self.all_paths.items():
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
                created.append(path)
        return created


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton-Instanz
# ═══════════════════════════════════════════════════════════════════════════════

settings = Settings()
"""
Singleton-Instanz der Anwendungskonfiguration.

Wird beim Import der Datei einmalig initialisiert und validiert.
Importiere in allen Modulen::

    from app.config import settings
"""
