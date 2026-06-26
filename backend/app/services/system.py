"""
System-Diagnosedienst des Homelab-Imperiums.

Ermittelt Echtzeit-Systemmetriken des HP-Servers:
- CPU-Auslastung, Kerne, Temperatur
- RAM-Belegung (absolut + prozentual)
- Festplatten-Kapazitäten und -Belegung
- System-Uptime (menschenlesbar)
- Netzwerk-Statistiken

Nutzt ``psutil`` für plattformunabhängige Metriken und Linux-spezifische
``hwmon``-Schnittstellen für Temperaturmessung. Alle Methoden haben
sichere Fallbacks bei Fehlern (z.B. fehlende Sensor-Treiber).

Verwendung::

    from app.services.system import SystemService

    svc = SystemService()
    metrics = svc.collect_metrics()
    # → SystemMetricResponse (Pydantic-kompatibel)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psutil

from app.schemas import SystemMetricResponse

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger(
    "homelab_imperium.services.system"
)


# ═══════════════════════════════════════════════════════════════════════════════
# SystemService
# ═══════════════════════════════════════════════════════════════════════════════


class SystemService:
    """
    Zentraler Dienst für Systemmetriken und Diagnose.

    Alle Methoden sind synchron (psutil ist CPU-gebunden), können aber
    via ``asyncio.to_thread`` aus async-Kontext aufgerufen werden.
    """

    # Pfad zum Linux hwmon-Verzeichnis für CPU-Temperatur
    _HWMON_BASE: Path = Path("/sys/class/hwmon")

    # Fallback-Datenwurzel für Disk-Prüfung
    _DISK_PATH: str = "/"

    def __init__(self) -> None:
        """Initialisiert den Systemdienst."""
        logger.debug("SystemService initialisiert.")

    # ──────────────────────────────────────────────────────────────────────
    # CPU
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_cpu_percent(interval: float = 0.5) -> float:
        """
        Ermittelt die aktuelle CPU-Auslastung in Prozent (0–100).

        ``psutil.cpu_percent()`` blockiert für ``interval`` Sekunden,
        um einen validen Durchschnittswert zu messen.

        Args:
            interval: Messintervall in Sekunden (kürzer = ungenauer).

        Returns:
            CPU-Auslastung 0.0–100.0, oder 0.0 bei Fehler.
        """
        try:
            percent: float = psutil.cpu_percent(interval=interval)
            return round(percent, 1)
        except Exception as exc:
            logger.warning("Fehler bei CPU-Auslastung: %s", exc)
            return 0.0

    @staticmethod
    def get_cpu_count() -> int:
        """
        Ermittelt die Anzahl der logischen CPU-Kerne.

        Returns:
            Anzahl Kerne, oder 1 bei Fehler.
        """
        try:
            return psutil.cpu_count(logical=True) or 1
        except Exception as exc:
            logger.warning("Fehler bei CPU-Count: %s", exc)
            return 1

    @staticmethod
    def get_cpu_temperature() -> Optional[float]:
        """
        Liest die CPU-Temperatur über Linux hwmon-Schnittstelle aus.

        Durchsucht ``/sys/class/hwmon/hwmon*/temp*_input`` nach
        Temperatursensoren mit dem Label ``Package id 0``, ``Tctl``
        oder ``CPU``. Die Rohwerte sind in Milligrad Celsius.

        Returns:
            CPU-Temperatur in °C, oder ``None`` wenn nicht verfügbar.

        Hinweis:
            Funktioniert nur unter Linux mit geladenen hwmon-Treibern
            (``coretemp``, ``k10temp``, etc.). Auf anderen Plattformen
            oder in Containern ohne ``/sys``-Zugriff wird ``None``
            zurückgegeben.
        """
        try:
            if not SystemService._HWMON_BASE.exists():
                logger.debug("hwmon nicht verfügbar (kein /sys/class/hwmon).")
                return None

            # Durchsuche alle hwmon-Geräte
            for hwmon_dir in sorted(SystemService._HWMON_BASE.iterdir()):
                if not hwmon_dir.is_dir():
                    continue

                # Name des Sensors lesen
                name_file: Path = hwmon_dir / "name"
                sensor_name: str = ""
                if name_file.exists():
                    sensor_name = name_file.read_text().strip()

                # Temperatur-Eingänge durchsuchen
                for temp_file in sorted(hwmon_dir.glob("temp*_input")):
                    # Zugehöriges Label lesen
                    label_file: Path = Path(
                        str(temp_file).replace("_input", "_label")
                    )
                    label: str = ""
                    if label_file.exists():
                        label = label_file.read_text().strip()

                    # Nur relevante Sensoren auswerten
                    relevant: bool = (
                        "package" in label.lower()
                        or "tctl" in label.lower()
                        or "cpu" in label.lower()
                        or "core" in label.lower()
                    )

                    if relevant or not label:
                        try:
                            raw: int = int(temp_file.read_text().strip())
                            temp_c: float = raw / 1000.0  # Milligrad → °C
                            logger.debug(
                                "CPU-Temperatur: %.1f°C (Sensor: %s/%s, "
                                "Label: %r)",
                                temp_c,
                                sensor_name,
                                temp_file.name,
                                label,
                            )
                            return round(temp_c, 1)
                        except (ValueError, OSError):
                            continue

            logger.debug("Kein CPU-Temperatursensor gefunden.")
            return None

        except PermissionError:
            logger.debug(
                "Keine Leseberechtigung für hwmon (Container ohne "
                "privilegierte Rechte?)."
            )
            return None
        except Exception as exc:
            logger.warning("Fehler beim Lesen der CPU-Temperatur: %s", exc)
            return None

    # ──────────────────────────────────────────────────────────────────────
    # RAM
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_ram_info() -> dict:
        """
        Ermittelt die aktuelle RAM-Belegung.

        Returns:
            Dict mit ``total_gb``, ``used_gb``, ``available_gb``,
            ``percent``. Bei Fehler: Nullwerte.
        """
        try:
            mem = psutil.virtual_memory()
            return {
                "total_gb": round(mem.total / (1024**3), 2),
                "used_gb": round(mem.used / (1024**3), 2),
                "available_gb": round(mem.available / (1024**3), 2),
                "percent": round(mem.percent, 1),
            }
        except Exception as exc:
            logger.warning("Fehler bei RAM-Info: %s", exc)
            return {
                "total_gb": 0.0,
                "used_gb": 0.0,
                "available_gb": 0.0,
                "percent": 0.0,
            }

    # ──────────────────────────────────────────────────────────────────────
    # Festplatte
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_disk_info(path: str | None = None) -> dict:
        """
        Ermittelt die Festplattenbelegung für einen Pfad.

        Args:
            path: Zu prüfender Pfad (Default: ``/`` bzw. Root).

        Returns:
            Dict mit ``total_gb``, ``used_gb``, ``free_gb``, ``percent``.
        """
        target: str = path or SystemService._DISK_PATH
        try:
            usage = psutil.disk_usage(target)
            return {
                "total_gb": round(usage.total / (1024**3), 2),
                "used_gb": round(usage.used / (1024**3), 2),
                "free_gb": round(usage.free / (1024**3), 2),
                "percent": round(usage.percent, 1),
            }
        except Exception as exc:
            logger.warning(
                "Fehler bei Disk-Info für %r: %s", target, exc
            )
            return {
                "total_gb": 0.0,
                "used_gb": 0.0,
                "free_gb": 0.0,
                "percent": 0.0,
            }

    @staticmethod
    def get_data_disk_info() -> dict:
        """
        Ermittelt die Belegung der Datenpartition (``/mnt/data``).

        Fallback: Root-Partition, falls ``/mnt/data`` nicht existiert.

        Returns:
            Dict mit ``total_gb``, ``used_gb``, ``free_gb``, ``percent``.
        """
        data_path: str = "/mnt/data"
        if not Path(data_path).exists():
            logger.debug(
                "/mnt/data nicht gefunden — verwende Root-Partition."
            )
            data_path = "/"
        return SystemService.get_disk_info(data_path)

    # ──────────────────────────────────────────────────────────────────────
    # Uptime
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_uptime() -> str:
        """
        Ermittelt die Systemlaufzeit als menschenlesbaren String.

        Formatiert in Tage, Stunden und Minuten:
        ``"5d 3h 12m"``, ``"2h 45m"``, ``"gerade gestartet"``.

        Returns:
            Formatierter Uptime-String.
        """
        try:
            boot_time: float = psutil.boot_time()
            uptime_seconds: float = time.time() - boot_time

            if uptime_seconds < 60:
                return "gerade gestartet"

            days: int = int(uptime_seconds // 86400)
            hours: int = int((uptime_seconds % 86400) // 3600)
            minutes: int = int((uptime_seconds % 3600) // 60)

            parts: list[str] = []
            if days > 0:
                parts.append(f"{days}d")
            if hours > 0 or days > 0:
                parts.append(f"{hours}h")
            parts.append(f"{minutes}m")

            return " ".join(parts)
        except Exception as exc:
            logger.warning("Fehler bei Uptime: %s", exc)
            return "unbekannt"

    # ──────────────────────────────────────────────────────────────────────
    # Netzwerk (optional, für erweiterte Diagnose)
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_network_stats() -> dict:
        """
        Sammelt grundlegende Netzwerkstatistiken.

        Returns:
            Dict mit Netzwerk-Interfaces und deren Traffic-Zählern.
        """
        try:
            net_io = psutil.net_io_counters(pernic=False)
            return {
                "bytes_sent_mb": round(net_io.bytes_sent / (1024**2), 2),
                "bytes_recv_mb": round(net_io.bytes_recv / (1024**2), 2),
                "packets_sent": net_io.packets_sent,
                "packets_recv": net_io.packets_recv,
                "errin": net_io.errin,
                "errout": net_io.errout,
            }
        except Exception as exc:
            logger.warning("Fehler bei Netzwerk-Statistiken: %s", exc)
            return {}

    # ──────────────────────────────────────────────────────────────────────
    # Sammelmethode — SystemMetricResponse
    # ──────────────────────────────────────────────────────────────────────

    def collect_metrics(self) -> SystemMetricResponse:
        """
        Sammelt ALLE Systemmetriken und gibt sie als Pydantic-Modell zurück.

        Dies ist die primäre Methode für den ``/api/system/metrics``-Endpunkt.
        Alle Teilmethoden haben Fallbacks — ein Fehler in einer Metrik
        führt nicht zum Komplettausfall.

        Returns:
            ``SystemMetricResponse`` mit allen aktuellen Metriken.
        """
        # CPU
        cpu_percent: float = self.get_cpu_percent(interval=0.3)
        cpu_count: int = self.get_cpu_count()
        cpu_temp: Optional[float] = self.get_cpu_temperature()

        # RAM
        ram: dict = self.get_ram_info()

        # Disk (Root + Data)
        root_disk: dict = self.get_disk_info("/")
        data_disk: dict = self.get_data_disk_info()

        # Verwende Data-Disk für die primären Festplatten-Metriken
        disk_free_gb: float = data_disk["free_gb"]
        disk_total_gb: float = data_disk["total_gb"]
        disk_percent: float = data_disk["percent"]

        # Uptime
        uptime: str = self.get_uptime()

        # Zeitstempel
        timestamp: datetime = datetime.now(timezone.utc)

        logger.info(
            "Systemmetriken erhoben: CPU=%.1f%% (%d Kerne, %s°C), "
            "RAM=%.1f/%.1f GB (%.1f%%), Disk=%.1f/%.1f GB (%.1f%%), "
            "Uptime=%s",
            cpu_percent,
            cpu_count,
            f"{cpu_temp:.1f}" if cpu_temp is not None else "?",
            ram["used_gb"],
            ram["total_gb"],
            ram["percent"],
            disk_free_gb,
            disk_total_gb,
            disk_percent,
            uptime,
        )

        return SystemMetricResponse(
            cpu_percent=cpu_percent,
            cpu_count=cpu_count,
            ram_percent=ram["percent"],
            ram_used_gb=ram["used_gb"],
            ram_total_gb=ram["total_gb"],
            disk_free_gb=disk_free_gb,
            disk_total_gb=disk_total_gb,
            disk_percent=disk_percent,
            uptime=uptime,
            timestamp=timestamp,
        )

    def collect_full_diagnostics(self) -> dict:
        """
        Sammelt erweiterte Diagnosedaten (über SystemMetricResponse hinaus).

        Enthält zusätzlich: CPU-Temperatur, Netzwerk-Stats, Root-Disk,
        Data-Disk separat.

        Returns:
            Dictionary mit allen verfügbaren Diagnosedaten.
        """
        return {
            "cpu": {
                "percent": self.get_cpu_percent(interval=0.3),
                "count": self.get_cpu_count(),
                "temperature_celsius": self.get_cpu_temperature(),
            },
            "ram": self.get_ram_info(),
            "disks": {
                "root": self.get_disk_info("/"),
                "data": self.get_data_disk_info(),
            },
            "uptime": self.get_uptime(),
            "network": self.get_network_stats(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
