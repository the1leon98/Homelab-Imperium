"""
Worker-Agent-Client für das Homelab-Imperium.

Stellt eine asynchrone WebSocket-Verbindung zum Remote-Worker-Agenten
auf dem GPU-Desktop-PC (Tailscale-Mesh) her. Ermöglicht das Senden von
rechenintensiven Aufträgen (OpenSCAD-Rendering, Blender, Code-Kompilierung),
Live-Fortschrittsüberwachung und sicheren Empfang von Ergebnisdateien.

Verwendung::

    from app.services.worker_client import WorkerClient

    async with WorkerClient() as worker:
        result = await worker.submit_openscad_job(
            scad_code="cube([10,20,30]);",
        )
        print(result.output_file_path)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import AsyncGenerator, Optional

import httpx

from app.config import settings

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger(
    "homelab_imperium.clients.worker"
)


# ═══════════════════════════════════════════════════════════════════════════════
# Enums & Datenklassen
# ═══════════════════════════════════════════════════════════════════════════════


class JobStatus(str, Enum):
    """Status eines Worker-Auftrags."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    """Unterstützte Auftragstypen."""

    OPENSCAD_RENDER = "openscad_render"
    BLENDER_RENDER = "blender_render"
    PYTHON_EXECUTE = "python_execute"
    CODE_COMPILE = "code_compile"
    CADQUERY_EXPORT = "cadquery_export"
    BUILD123D_EXPORT = "build123d_export"


@dataclass
class WorkerJob:
    """
    Ein vollständig definierter Berechnungsauftrag für den Worker-Agenten.

    Wird per WebSocket als JSON-Nachricht an den Desktop-PC gesendet.
    """

    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    job_type: JobType = JobType.OPENSCAD_RENDER
    payload: str = ""  # Der auszuführende Code / das Skript
    parameters: dict = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    timeout_seconds: int = 300
    output_format: str = "png"  # png, stl, step, obj, txt


@dataclass
class WorkerProgress:
    """Fortschrittsmeldung eines laufenden Auftrags."""

    job_id: str
    status: JobStatus = JobStatus.QUEUED
    progress_pct: float = 0.0
    message: str = ""
    stage: str = ""
    timestamp: str = ""


@dataclass
class WorkerResult:
    """Ergebnis eines abgeschlossenen Auftrags."""

    job_id: str
    status: JobStatus = JobStatus.COMPLETED
    output_file_path: str = ""
    output_files: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    error_message: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# WorkerClient
# ═══════════════════════════════════════════════════════════════════════════════


class WorkerClient:
    """
    Asynchroner Client für den Remote-Worker-Agenten.

    Kommuniziert über WebSocket (ws://) mit dem Worker-Agenten auf dem
    GPU-Desktop-PC. Alle Nachrichten sind JSON-codiert und über das
    verschlüsselte Tailscale-Mesh-Netzwerk gesichert.

    Protokoll:
        Client → Worker:  ``{"action": "submit_job", "job": {...}}``
        Worker → Client:  ``{"type": "progress", "job_id": "...", ...}``
        Worker → Client:  ``{"type": "result", "job_id": "...", ...}``
        Client → Worker:  ``{"action": "cancel_job", "job_id": "..."}``
        Client → Worker:  ``{"action": "ping"}``
        Worker → Client:  ``{"type": "pong"}``
    """

    # Maximale Wartezeit für eine Worker-Antwort (Sekunden)
    _RESPONSE_TIMEOUT: int = 10

    # Ping-Intervall (Sekunden)
    _PING_INTERVAL: int = 30

    # Maximale Reconnect-Versuche
    _MAX_RECONNECT_ATTEMPTS: int = 3

    def __init__(self) -> None:
        """
        Initialisiert den Worker-Client mit Werten aus den Settings.

        Liest ``worker_agent_endpoint``, ``worker_agent_timeout`` und
        ``worker_agent_max_polygons`` aus ``app.config.settings``.
        """
        self._ws_url: str = settings.worker_agent_endpoint.replace(
            "http://", "ws://", 1
        ).replace("https://", "wss://", 1)
        self._timeout: int = settings.worker_agent_timeout
        self._max_polygons: int = settings.worker_agent_max_polygons

        # Zustand
        self._ws: Optional[any] = None  # websockets.WebSocketClientProtocol
        self._pending_jobs: dict[str, asyncio.Future] = {}
        self._progress_callbacks: dict[
            str, list
        ] = {}  # job_id → [callbacks]

        logger.info(
            "Worker-Client initialisiert: endpoint=%s, timeout=%ds, "
            "max_polygons=%d",
            self._ws_url,
            self._timeout,
            self._max_polygons,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Context-Manager (async with)
    # ──────────────────────────────────────────────────────────────────────

    async def __aenter__(self) -> "WorkerClient":
        """Baut die WebSocket-Verbindung zum Worker-Agenten auf."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        """Schließt die WebSocket-Verbindung."""
        await self.disconnect()

    # ──────────────────────────────────────────────────────────────────────
    # Verbindungsmanagement
    # ──────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Baut die WebSocket-Verbindung zum Worker-Agenten auf.

        Startet nach erfolgreichem Verbindungsaufbau einen Hintergrund-Task
        zum Empfangen von Nachrichten und einen Ping-Task.

        Raises:
            ConnectionError: Wenn der Worker nach allen Reconnects nicht
                             erreichbar ist.
        """
        try:
            import websockets
        except ImportError:
            raise ImportError(
                "websockets ist nicht installiert. "
                "Installiere mit: pip install websockets"
            )

        last_exc: Exception | None = None

        for attempt in range(1, self._MAX_RECONNECT_ATTEMPTS + 1):
            try:
                logger.info(
                    "Verbinde mit Worker-Agent: %s (Versuch %d/%d)",
                    self._ws_url,
                    attempt,
                    self._MAX_RECONNECT_ATTEMPTS,
                )
                self._ws = await websockets.connect(
                    self._ws_url,
                    ping_interval=self._PING_INTERVAL,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=100 * 1024 * 1024,  # 100 MB max Nachrichtengröße
                )
                logger.info(
                    "WebSocket-Verbindung zu %s hergestellt.",
                    self._ws_url,
                )

                # Starte Hintergrund-Listener
                asyncio.create_task(self._listen())
                return

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Worker-Verbindungsaufbau fehlgeschlagen "
                    "(Versuch %d/%d): %s",
                    attempt,
                    self._MAX_RECONNECT_ATTEMPTS,
                    exc,
                )
                if attempt < self._MAX_RECONNECT_ATTEMPTS:
                    await asyncio.sleep(1.0 * attempt)

        raise ConnectionError(
            f"Worker-Agent unter {self._ws_url} nach "
            f"{self._MAX_RECONNECT_ATTEMPTS} Versuchen nicht erreichbar: "
            f"{last_exc}"
        )

    async def disconnect(self) -> None:
        """Schließt die WebSocket-Verbindung ordentlich."""
        if self._ws is not None:
            try:
                await self._ws.close()
                logger.info("WebSocket-Verbindung zu %s geschlossen.", self._ws_url)
            except Exception as exc:
                logger.warning(
                    "Fehler beim Schließen der WebSocket-Verbindung: %s",
                    exc,
                )
        self._ws = None

        # Alle wartenden Futures mit Fehler abbrechen
        for job_id, future in list(self._pending_jobs.items()):
            if not future.done():
                future.set_exception(
                    ConnectionError(
                        f"Verbindung zum Worker getrennt (Job {job_id})."
                    )
                )

    # ──────────────────────────────────────────────────────────────────────
    # Hintergrund-Listener
    # ──────────────────────────────────────────────────────────────────────

    async def _listen(self) -> None:
        """
        Horcht kontinuierlich auf eingehende WebSocket-Nachrichten.

        Verarbeitet Progress-Updates, Job-Ergebnisse und Pong-Antworten.
        Läuft als Hintergrund-Task bis zur Trennung.
        """
        logger.debug("Worker-Listener gestartet.")
        try:
            async for raw_message in self._ws:
                try:
                    message: dict = json.loads(raw_message)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "Ungültige Worker-Nachricht (kein JSON): %s",
                        exc,
                    )
                    continue

                msg_type: str = message.get("type", "")

                if msg_type == "progress":
                    await self._handle_progress(message)
                elif msg_type == "result":
                    await self._handle_result(message)
                elif msg_type == "pong":
                    logger.debug("Worker-Pong empfangen.")
                elif msg_type == "error":
                    await self._handle_error(message)
                else:
                    logger.debug(
                        "Unbekannter Worker-Nachrichtentyp: %r", msg_type
                    )

        except Exception as exc:
            logger.warning(
                "Worker-Listener beendet (Verbindung getrennt?): %s", exc
            )
        finally:
            logger.debug("Worker-Listener beendet.")

    async def _handle_progress(self, message: dict) -> None:
        """Verarbeitet eine Fortschrittsmeldung."""
        job_id: str = message.get("job_id", "")
        progress: WorkerProgress = WorkerProgress(
            job_id=job_id,
            status=JobStatus(message.get("status", "running")),
            progress_pct=float(message.get("progress_pct", 0.0)),
            message=message.get("message", ""),
            stage=message.get("stage", ""),
            timestamp=message.get("timestamp", ""),
        )
        logger.debug(
            "Job %s Fortschritt: %.1f%% — %s (%s)",
            job_id,
            progress.progress_pct,
            progress.message,
            progress.stage,
        )

        # Callbacks aufrufen
        for callback in self._progress_callbacks.get(job_id, []):
            try:
                await callback(progress) if asyncio.iscoroutinefunction(
                    callback
                ) else callback(progress)
            except Exception as exc:
                logger.error(
                    "Fehler in Progress-Callback für Job %s: %s",
                    job_id,
                    exc,
                )

    async def _handle_result(self, message: dict) -> None:
        """Verarbeitet das Endergebnis eines Auftrags."""
        job_id: str = message.get("job_id", "")
        result: WorkerResult = WorkerResult(
            job_id=job_id,
            status=JobStatus(message.get("status", "completed")),
            output_file_path=message.get("output_file_path", ""),
            output_files=message.get("output_files", []),
            stdout=message.get("stdout", ""),
            stderr=message.get("stderr", ""),
            duration_seconds=float(message.get("duration_seconds", 0.0)),
            error_message=message.get("error_message", ""),
        )

        status: JobStatus = result.status
        log_func = (
            logger.info
            if status == JobStatus.COMPLETED
            else logger.error
        )
        log_func(
            "Job %s abgeschlossen: status=%s, dauer=%.1fs, files=%d",
            job_id,
            status.value,
            result.duration_seconds,
            len(result.output_files),
        )

        # Zugehöriges Future auflösen
        future: asyncio.Future | None = self._pending_jobs.pop(job_id, None)
        if future and not future.done():
            if status == JobStatus.COMPLETED:
                future.set_result(result)
            else:
                future.set_exception(
                    RuntimeError(
                        f"Worker-Job {job_id} fehlgeschlagen: "
                        f"{result.error_message or 'Unbekannter Fehler'}"
                    )
                )

        # Progress-Callbacks für diesen Job löschen
        self._progress_callbacks.pop(job_id, None)

    async def _handle_error(self, message: dict) -> None:
        """Verarbeitet eine Fehlermeldung des Workers."""
        job_id: str = message.get("job_id", "")
        error_text: str = message.get("error", "Unbekannter Worker-Fehler")
        logger.error(
            "Worker-Fehler für Job %s: %s", job_id or "unbekannt", error_text
        )

        if job_id:
            future: asyncio.Future | None = self._pending_jobs.pop(
                job_id, None
            )
            if future and not future.done():
                future.set_exception(RuntimeError(error_text))

    # ──────────────────────────────────────────────────────────────────────
    # Ping / Health-Check
    # ──────────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """
        Prüft die Erreichbarkeit des Worker-Agenten.

        Sendet einen Ping über die WebSocket-Verbindung und wartet
        auf die Pong-Antwort.

        Returns:
            ``True`` wenn der Worker erreichbar ist.
        """
        try:
            await self._ws.send(json.dumps({"action": "ping"}))
            # Pong wird asynchron im Listener empfangen —
            # Für einen synchronen Ping bräuchten wir einen expliziten
            # Request-Response-Mechanismus. Vereinfacht:
            # Prüfe, ob die WebSocket-Verbindung noch offen ist.
            if self._ws and self._ws.open:
                logger.debug("Worker-Ping gesendet.")
                return True
            return False
        except Exception as exc:
            logger.warning("Worker-Ping fehlgeschlagen: %s", exc)
            return False

    # ──────────────────────────────────────────────────────────────────────
    # Job-Submission (Öffentliche API)
    # ──────────────────────────────────────────────────────────────────────

    async def _submit_job(
        self,
        job_type: JobType,
        payload: str,
        parameters: dict | None = None,
        timeout_seconds: int | None = None,
        output_format: str = "png",
        on_progress: callable | None = None,
    ) -> WorkerResult:
        """
        Sendet einen Auftrag an den Worker und wartet auf das Ergebnis.

        Args:
            job_type: Art des Auftrags (OpenSCAD, Blender, …).
            payload: Der auszuführende Code / das Skript.
            parameters: Optionale Parameter (z.B. Auflösung, Qualität).
            timeout_seconds: Timeout (Default aus Settings).
            output_format: Gewünschtes Ausgabeformat.
            on_progress: Callback für Fortschrittsmeldungen.

        Returns:
            ``WorkerResult`` mit Ausgabedateien und Metriken.

        Raises:
            ConnectionError: Wenn keine WebSocket-Verbindung besteht.
            TimeoutError: Wenn der Job das Timeout überschreitet.
            RuntimeError: Wenn der Job fehlschlägt.
        """
        if not self._ws or not self._ws.open:
            raise ConnectionError(
                "Keine WebSocket-Verbindung zum Worker-Agenten. "
                "Bitte connect() aufrufen oder async with verwenden."
            )

        job: WorkerJob = WorkerJob(
            job_type=job_type,
            payload=payload,
            parameters=parameters or {},
            timeout_seconds=timeout_seconds or self._timeout,
            output_format=output_format,
        )

        # Future für die Ergebnisrückgabe erstellen
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_jobs[job.job_id] = future

        # Progress-Callback registrieren
        if on_progress is not None:
            self._progress_callbacks.setdefault(job.job_id, []).append(
                on_progress
            )

        # Job an den Worker senden
        message: dict = {
            "action": "submit_job",
            "job": {
                "job_id": job.job_id,
                "job_type": job.job_type.value,
                "payload": job.payload,
                "parameters": job.parameters,
                "created_at": job.created_at,
                "timeout_seconds": job.timeout_seconds,
                "output_format": job.output_format,
            },
        }

        logger.info(
            "Sende Job %s (Typ: %s) an Worker-Agent...",
            job.job_id,
            job_type.value,
        )
        await self._ws.send(json.dumps(message))

        # Warten auf Ergebnis (mit Timeout)
        try:
            result: WorkerResult = await asyncio.wait_for(
                future,
                timeout=job.timeout_seconds + self._RESPONSE_TIMEOUT,
            )
            return result
        except asyncio.TimeoutError:
            self._pending_jobs.pop(job.job_id, None)
            self._progress_callbacks.pop(job.job_id, None)
            logger.error(
                "Job %s Timeout nach %.0fs.",
                job.job_id,
                job.timeout_seconds,
            )
            raise TimeoutError(
                f"Worker-Job {job.job_id} hat das Zeitlimit von "
                f"{job.timeout_seconds}s überschritten."
            )

    async def submit_openscad_job(
        self,
        scad_code: str,
        output_format: str = "stl",
        resolution: int = 120,
        on_progress: callable | None = None,
    ) -> WorkerResult:
        """
        Sendet einen OpenSCAD-Rendering-Auftrag an den Worker.

        Der GPU-Desktop rendert das OpenSCAD-Modell und liefert die
        resultierende STL-/PNG-Datei zurück.

        Args:
            scad_code: OpenSCAD-Code als String.
            output_format: Ausgabeformat (stl, png, svg, 3mf).
            resolution: $fn-Auflösung (Fragmente pro Kreis).
            on_progress: Optionaler Fortschritts-Callback.

        Returns:
            ``WorkerResult`` mit Pfad zur Ausgabedatei.
        """
        logger.info(
            "Sende OpenSCAD-Job: %d Zeichen Code, format=%s, $fn=%d",
            len(scad_code),
            output_format,
            resolution,
        )
        return await self._submit_job(
            job_type=JobType.OPENSCAD_RENDER,
            payload=scad_code,
            parameters={
                "resolution": resolution,
                "max_polygons": self._max_polygons,
            },
            output_format=output_format,
            on_progress=on_progress,
        )

    async def submit_blender_job(
        self,
        bpy_script: str,
        output_format: str = "png",
        resolution_x: int = 1920,
        resolution_y: int = 1080,
        samples: int = 128,
        on_progress: callable | None = None,
    ) -> WorkerResult:
        """
        Sendet einen Blender-Rendering-Auftrag an den Worker.

        Args:
            bpy_script: Blender-Python-Skript als String.
            output_format: png, jpg, exr, avi.
            resolution_x, resolution_y: Bildauflösung.
            samples: Cycles-Render-Samples.
            on_progress: Optionaler Fortschritts-Callback.

        Returns:
            ``WorkerResult`` mit Pfad zum gerenderten Bild/Video.
        """
        logger.info(
            "Sende Blender-Job: %d Zeichen Skript, "
            "%dx%d, %d samples",
            len(bpy_script),
            resolution_x,
            resolution_y,
            samples,
        )
        return await self._submit_job(
            job_type=JobType.BLENDER_RENDER,
            payload=bpy_script,
            parameters={
                "resolution_x": resolution_x,
                "resolution_y": resolution_y,
                "samples": samples,
            },
            output_format=output_format,
            on_progress=on_progress,
        )

    async def submit_python_job(
        self,
        code: str,
        timeout_seconds: int = 30,
        on_progress: callable | None = None,
    ) -> WorkerResult:
        """
        Sendet einen Python-Code-Ausführungsauftrag an den Worker.

        Nützlich für rechenintensive Berechnungen, die die GPU oder
        mehr CPU-Kerne benötigen (z.B. NumPy, SciPy, CadQuery).

        Args:
            code: Python-Code als String.
            timeout_seconds: Ausführungs-Timeout.
            on_progress: Optionaler Fortschritts-Callback.

        Returns:
            ``WorkerResult`` mit stdout, stderr und Ausgabedateien.
        """
        logger.info(
            "Sende Python-Job: %d Zeichen Code, timeout=%ds",
            len(code),
            timeout_seconds,
        )
        return await self._submit_job(
            job_type=JobType.PYTHON_EXECUTE,
            payload=code,
            parameters={},
            timeout_seconds=timeout_seconds,
            output_format="txt",
            on_progress=on_progress,
        )

    async def submit_cadquery_job(
        self,
        cadquery_code: str,
        output_format: str = "step",
        on_progress: callable | None = None,
    ) -> WorkerResult:
        """
        Sendet einen CadQuery-Export-Auftrag an den Worker.

        Args:
            cadquery_code: CadQuery-Python-Code.
            output_format: step, stl, svg, dxf.
            on_progress: Optionaler Fortschritts-Callback.

        Returns:
            ``WorkerResult`` mit Pfad zur exportierten CAD-Datei.
        """
        logger.info(
            "Sende CadQuery-Job: %d Zeichen, format=%s",
            len(cadquery_code),
            output_format,
        )
        return await self._submit_job(
            job_type=JobType.CADQUERY_EXPORT,
            payload=cadquery_code,
            parameters={},
            output_format=output_format,
            on_progress=on_progress,
        )

    async def submit_build123d_job(
        self,
        build123d_code: str,
        output_format: str = "step",
        on_progress: callable | None = None,
    ) -> WorkerResult:
        """
        Sendet einen Build123d-Export-Auftrag an den Worker.

        Args:
            build123d_code: Build123d-Python-Code.
            output_format: step, stl, glb, 3mf.
            on_progress: Optionaler Fortschritts-Callback.

        Returns:
            ``WorkerResult`` mit Pfad zur exportierten CAD-Datei.
        """
        logger.info(
            "Sende Build123d-Job: %d Zeichen, format=%s",
            len(build123d_code),
            output_format,
        )
        return await self._submit_job(
            job_type=JobType.BUILD123D_EXPORT,
            payload=build123d_code,
            parameters={},
            output_format=output_format,
            on_progress=on_progress,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Job-Verwaltung
    # ──────────────────────────────────────────────────────────────────────

    async def cancel_job(self, job_id: str) -> bool:
        """
        Bricht einen laufenden Auftrag auf dem Worker ab.

        Args:
            job_id: ID des abzubrechenden Auftrags.

        Returns:
            ``True``, wenn die Abbruch-Nachricht gesendet wurde.
        """
        if not self._ws or not self._ws.open:
            logger.warning(
                "Kann Job %s nicht abbrechen: Keine Verbindung.", job_id
            )
            return False

        logger.info("Breche Job %s auf Worker ab.", job_id)
        await self._ws.send(
            json.dumps({"action": "cancel_job", "job_id": job_id})
        )

        # Lokales Future auch abbrechen
        future: asyncio.Future | None = self._pending_jobs.pop(job_id, None)
        if future and not future.done():
            future.set_exception(
                RuntimeError(f"Job {job_id} wurde abgebrochen.")
            )

        self._progress_callbacks.pop(job_id, None)
        return True

    async def download_result_file(
        self,
        job_id: str,
        filename: str,
        destination: str,
    ) -> Path:
        """
        Lädt eine Ergebnisdatei vom Worker über HTTP herunter.

        Der Worker stellt Ergebnisse unter ``/results/{job_id}/{filename}``
        bereit. Diese Methode lädt die Datei per HTTP-GET herunter und
        speichert sie unter ``destination``.

        Args:
            job_id: Job-ID.
            filename: Dateiname (z.B. ``"output.stl"``).
            destination: Zielverzeichnis auf dem Server.

        Returns:
            ``Path`` zur heruntergeladenen Datei.
        """
        http_base: str = settings.worker_agent_endpoint
        url: str = f"{http_base}/results/{job_id}/{filename}"
        dest_path: Path = Path(destination) / filename
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Lade Ergebnisdatei herunter: %s → %s", url, dest_path
        )

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120.0),
            ) as client:
                response: httpx.Response = await client.get(url)
                response.raise_for_status()
                dest_path.write_bytes(response.content)

            file_size_kb: float = dest_path.stat().st_size / 1024
            logger.info(
                "Ergebnisdatei gespeichert: %s (%.1f KB)",
                dest_path,
                file_size_kb,
            )
            return dest_path
        except Exception as exc:
            logger.error(
                "Download fehlgeschlagen: %s → %s: %s",
                url,
                dest_path,
                exc,
            )
            raise
