"""
Dateisystemdienst des Homelab-Imperiums („FileBunker").

Stellt gesicherte Dateisystemoperationen bereit:
- Verzeichnisauflistung mit Metadaten
- Ordner-Erstellung
- Streaming-Upload und -Download großer Dateien
- Sicheres Löschen mit optionaler Überschreibung
- Verschieben/Umbenennen

**SICHERHEITSKRITISCH:** Alle Operationen validieren Zielpfade mehrstufig
gegen das konfigurierte Basisverzeichnis (``/mnt/data/files``). Directory-
Traversal-Angriffe (``../``, Symlinks, Null-Bytes) werden auf mehreren
Ebenen abgefangen.

Verwendung::

    from app.services.files import FileBunkerService

    svc = FileBunkerService()
    contents = await svc.list_directory("projekte/")
    await svc.upload_file("projekte/readme.txt", file_content)
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Optional

import aiofiles
import aiofiles.os as aio_os

from app.config import settings

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.services.files")


# ═══════════════════════════════════════════════════════════════════════════════
# Dateisystemdienst (FileBunker)
# ═══════════════════════════════════════════════════════════════════════════════


class FileBunkerService:
    """
    Gesicherter Dateisystemdienst für das Homelab-Imperium.

    Alle Operationen sind auf das ``base_dir`` beschränkt. Jeder Pfad
    durchläuft ``secure_path()``, das Directory-Traversal, Symlink-
    Manipulation und Null-Byte-Injection abwehrt.
    """

    # Chunk-Größe für Streaming (1 MiB)
    _CHUNK_SIZE: int = 1_048_576

    # Maximale Dateigröße für Uploads (500 MiB)
    _MAX_UPLOAD_SIZE: int = 524_288_000

    # Maximale Pfadlänge (verhindert Buffer-Overflow-Angriffe)
    _MAX_PATH_LENGTH: int = 4096

    def __init__(
        self,
        base_directory: str | None = None,
    ) -> None:
        """
        Initialisiert den Dateidienst mit einem gesicherten Basisverzeichnis.

        Args:
            base_directory: Absoluter Pfad zum Basisverzeichnis.
                            Default aus ``settings.path_files``.
        """
        raw_base: str = (
            base_directory or str(settings.path_files)
        )
        # Schritt 1: Absoluten Pfad auflösen
        # Schritt 2: Symlinks auflösen (realpath)
        # Schritt 3: Nochmal normalisieren
        self.base_dir: str = os.path.realpath(
            os.path.abspath(os.path.normpath(raw_base))
        )

        # Stelle sicher, dass das Basisverzeichnis existiert
        os.makedirs(self.base_dir, exist_ok=True)

        logger.info(
            "FileBunkerService initialisiert: base_dir=%s", self.base_dir
        )

    # ──────────────────────────────────────────────────────────────────────
    # Pfadsicherheit — MEHRSTUFIGE TRAVERSAL-ABWEHR
    # ──────────────────────────────────────────────────────────────────────

    def secure_path(self, relative_path: str) -> str:
        """
        Validiert einen relativen Pfad gegen das Basisverzeichnis.

        **Mehrstufiger Schutz gegen Directory-Traversal:**

        1. **Längenprüfung** — Pfad darf ``_MAX_PATH_LENGTH`` (4096) nicht
           überschreiten (Buffer-Overflow-Abwehr).
        2. **Null-Byte-Erkennung** — ``\\x00`` im Pfad wird abgelehnt
           (Poison-Null-Byte-Angriff).
        3. **os.path.normpath** — Eliminiert redundante Separatoren und
           ``..``-Segmente auf String-Ebene.
        4. **os.path.abspath** — Macht den Pfad absolut.
        5. **os.path.realpath** — Löst ALLE Symlinks auf.
        6. **Präfix-Prüfung** — Das Ergebnis MUSS mit ``base_dir`` beginnen.

        Args:
            relative_path: Relativer Pfad innerhalb des Basisverzeichnisses.

        Returns:
            Absoluter, validierter Pfad.

        Raises:
            PermissionError: Bei Traversal-Versuch oder ungültigem Pfad.
            ValueError: Bei Null-Byte im Pfad.
        """
        # Schritt 0: Typ-Prüfung
        if not isinstance(relative_path, str):
            raise PermissionError(
                f"Ungültiger Pfad-Typ: {type(relative_path).__name__}. "
                f"Erwarte str."
            )

        # Schritt 1: Längenprüfung
        if len(relative_path) > self._MAX_PATH_LENGTH:
            logger.warning(
                "BLOCKIERT: Pfad überschreitet Maximallänge "
                "(%d > %d): %r...",
                len(relative_path),
                self._MAX_PATH_LENGTH,
                relative_path[:100],
            )
            raise PermissionError(
                f"Pfad zu lang ({len(relative_path)} > "
                f"{self._MAX_PATH_LENGTH} Zeichen)."
            )

        # Schritt 2: Null-Byte-Erkennung
        if "\x00" in relative_path:
            logger.warning(
                "BLOCKIERT: Null-Byte im Pfad erkannt: %r",
                relative_path,
            )
            raise ValueError(
                "Ungültiges Zeichen (Null-Byte) im Pfad."
            )

        # Schritt 3+4: Normalisieren + absolut machen
        # normpath entfernt "../"-Segmente und doppelte Separatoren
        normalized: str = os.path.normpath(relative_path)

        # Verhindere Pfade, die nach normpath mit ".." beginnen
        # (normpath macht aus "../../etc" → "../../etc", wir müssen
        #  es vor join abfangen)
        if normalized.startswith(".."):
            logger.warning(
                "BLOCKIERT: Pfad beginnt mit '..' nach Normalisierung: "
                "%r → %r",
                relative_path,
                normalized,
            )
            raise PermissionError(
                "Unzulässige Pfadmanipulation (Traversal) detektiert."
            )

        target: str = os.path.abspath(
            os.path.join(self.base_dir, normalized)
        )

        # Schritt 5: Symlinks auflösen
        try:
            target = os.path.realpath(target)
        except OSError as exc:
            logger.error(
                "Fehler bei realpath-Auflösung: %s → %s", target, exc
            )
            raise PermissionError(
                f"Pfad kann nicht aufgelöst werden: {exc}"
            ) from exc

        # Schritt 6: Präfix-Prüfung
        if not target.startswith(self.base_dir + os.sep) and target != self.base_dir:
            logger.warning(
                "BLOCKIERT: Traversal-Versuch!\n"
                "  Eingabe:    %r\n"
                "  Normalisiert: %r\n"
                "  Absolut:    %r\n"
                "  Real:       %r\n"
                "  Basis:      %r",
                relative_path,
                normalized,
                os.path.abspath(os.path.join(self.base_dir, normalized)),
                target,
                self.base_dir,
            )
            raise PermissionError(
                "Unzulässige Pfadmanipulation (Directory Traversal) "
                "detektiert und blockiert."
            )

        return target

    def secure_path_exists(self, relative_path: str) -> bool:
        """
        Wie ``secure_path()``, gibt aber ``False`` zurück, wenn der
        Pfad nicht existiert (statt einen Fehler zu werfen).

        Args:
            relative_path: Relativer Pfad.

        Returns:
            ``True`` wenn der Pfad existiert und sicher ist.
        """
        try:
            safe: str = self.secure_path(relative_path)
            return os.path.exists(safe)
        except PermissionError:
            return False

    # ──────────────────────────────────────────────────────────────────────
    # Verzeichnis-Operationen
    # ──────────────────────────────────────────────────────────────────────

    async def list_directory(
        self,
        relative_path: str = "",
        include_hidden: bool = False,
    ) -> list[dict]:
        """
        Listet den Inhalt eines Verzeichnisses mit Metadaten auf.

        Args:
            relative_path: Relativer Pfad (leer = Basisverzeichnis).
            include_hidden: ``True`` = versteckte Dateien anzeigen.

        Returns:
            Liste von Dicts mit ``name``, ``type``, ``size_bytes``,
            ``modified``, ``permissions``, ``is_symlink``.
        """
        safe_dir: str = self.secure_path(relative_path or ".")

        if not os.path.isdir(safe_dir):
            raise FileNotFoundError(
                f"Verzeichnis nicht gefunden: {relative_path!r}"
            )

        entries: list[dict] = []

        # os.scandir ist schneller als os.listdir (gibt DirEntry-Objekte)
        try:
            with os.scandir(safe_dir) as scanner:
                for entry in scanner:
                    name: str = entry.name

                    # Versteckte Dateien überspringen
                    if not include_hidden and name.startswith("."):
                        continue

                    try:
                        stat_info: os.stat_result = entry.stat()
                    except OSError:
                        stat_info = None

                    entry_type: str = (
                        "directory"
                        if entry.is_dir(follow_symlinks=False)
                        else "file"
                    )
                    if entry.is_symlink():
                        entry_type = "symlink"

                    size: int = (
                        stat_info.st_size
                        if stat_info and entry_type == "file"
                        else 0
                    )

                    modified: str = (
                        datetime.fromtimestamp(
                            stat_info.st_mtime, tz=timezone.utc
                        ).isoformat()
                        if stat_info
                        else ""
                    )

                    # Berechtigungen als rwx-String
                    perms: str = ""
                    if stat_info:
                        mode: int = stat_info.st_mode
                        perms = (
                            ("r" if mode & stat.S_IRUSR else "-")
                            + ("w" if mode & stat.S_IWUSR else "-")
                            + ("x" if mode & stat.S_IXUSR else "-")
                        )

                    entries.append(
                        {
                            "name": name,
                            "type": entry_type,
                            "size_bytes": size,
                            "size_display": self._format_size(size),
                            "modified": modified,
                            "permissions": perms,
                            "is_symlink": entry.is_symlink(),
                        }
                    )
        except PermissionError as exc:
            logger.error(
                "Keine Leseberechtigung für %r: %s", safe_dir, exc
            )
            raise PermissionError(
                f"Keine Leseberechtigung für Verzeichnis: {relative_path!r}"
            ) from exc

        # Sortierung: Ordner zuerst, dann alphabetisch
        entries.sort(key=lambda e: (e["type"] != "directory", e["name"].lower()))

        logger.debug(
            "%d Einträge in %r gelistet.", len(entries), relative_path
        )
        return entries

    async def create_directory(
        self,
        relative_path: str,
        exist_ok: bool = True,
    ) -> str:
        """
        Erstellt ein neues Verzeichnis (rekursiv).

        Args:
            relative_path: Relativer Pfad des neuen Verzeichnisses.
            exist_ok: ``True`` = kein Fehler, wenn bereits vorhanden.

        Returns:
            Absoluter Pfad des erstellten Verzeichnisses.

        Raises:
            FileExistsError: Wenn exist_ok=False und Verzeichnis existiert.
        """
        safe_path: str = self.secure_path(relative_path)

        if os.path.exists(safe_path):
            if not exist_ok:
                raise FileExistsError(
                    f"Verzeichnis existiert bereits: {relative_path!r}"
                )
            logger.debug("Verzeichnis bereits vorhanden: %s", safe_path)
            return safe_path

        os.makedirs(safe_path, exist_ok=exist_ok)
        logger.info("Verzeichnis erstellt: %s", safe_path)
        return safe_path

    async def delete_directory(
        self,
        relative_path: str,
        recursive: bool = False,
    ) -> bool:
        """
        Löscht ein Verzeichnis.

        Args:
            relative_path: Relativer Pfad.
            recursive: ``True`` = rekursiv löschen (wie rm -rf).

        Returns:
            ``True`` bei erfolgreicher Löschung.

        Raises:
            OSError: Wenn nicht leer und recursive=False.
        """
        safe_path: str = self.secure_path(relative_path)

        if not os.path.isdir(safe_path):
            raise FileNotFoundError(
                f"Verzeichnis nicht gefunden: {relative_path!r}"
            )

        # Basisverzeichnis selbst darf NIE gelöscht werden
        if safe_path == self.base_dir:
            raise PermissionError(
                "Das Basisverzeichnis selbst darf nicht gelöscht werden."
            )

        if recursive:
            shutil.rmtree(safe_path)
            logger.info(
                "Verzeichnis rekursiv gelöscht: %s", safe_path
            )
        else:
            os.rmdir(safe_path)  # Nur leere Verzeichnisse
            logger.info("Verzeichnis gelöscht: %s", safe_path)

        return True

    # ──────────────────────────────────────────────────────────────────────
    # Datei-Operationen
    # ──────────────────────────────────────────────────────────────────────

    async def get_file_info(self, relative_path: str) -> dict:
        """
        Ruft Metadaten einer einzelnen Datei ab.

        Args:
            relative_path: Relativer Pfad zur Datei.

        Returns:
            Dict mit ``name``, ``size_bytes``, ``size_display``,
            ``modified``, ``mime_type``, ``extension``, ``is_file``.
        """
        safe_path: str = self.secure_path(relative_path)

        if not os.path.isfile(safe_path):
            raise FileNotFoundError(
                f"Datei nicht gefunden: {relative_path!r}"
            )

        stat_info: os.stat_result = os.stat(safe_path)

        # MIME-Type anhand der Dateiendung erraten
        mime_type: str
        _mime, _ = mimetypes.guess_type(safe_path)
        mime_type = _mime or "application/octet-stream"

        return {
            "name": os.path.basename(safe_path),
            "path": relative_path,
            "size_bytes": stat_info.st_size,
            "size_display": self._format_size(stat_info.st_size),
            "modified": datetime.fromtimestamp(
                stat_info.st_mtime, tz=timezone.utc
            ).isoformat(),
            "created": datetime.fromtimestamp(
                stat_info.st_ctime, tz=timezone.utc
            ).isoformat(),
            "mime_type": mime_type,
            "extension": os.path.splitext(safe_path)[1].lower(),
            "is_file": True,
        }

    async def upload_file(
        self,
        relative_path: str,
        content: bytes,
        overwrite: bool = True,
    ) -> dict:
        """
        Schreibt eine Datei (aus Bytes) auf die Festplatte.

        Args:
            relative_path: Zielpfad relativ zum Basisverzeichnis.
            content: Dateiinhalt als Bytes.
            overwrite: ``True`` = bestehende Datei überschreiben.

        Returns:
            Dict mit Datei-Metadaten.

        Raises:
            FileExistsError: Wenn overwrite=False und Datei existiert.
            ValueError: Wenn die Datei zu groß ist.
        """
        if len(content) > self._MAX_UPLOAD_SIZE:
            raise ValueError(
                f"Datei zu groß: {self._format_size(len(content))} "
                f"(max: {self._format_size(self._MAX_UPLOAD_SIZE)})."
            )

        safe_path: str = self.secure_path(relative_path)

        # Überprüfe, ob Ziel bereits existiert
        if os.path.exists(safe_path) and not overwrite:
            raise FileExistsError(
                f"Datei existiert bereits: {relative_path!r}"
            )

        # Stelle sicher, dass das Zielverzeichnis existiert
        target_dir: str = os.path.dirname(safe_path)
        os.makedirs(target_dir, exist_ok=True)

        # Asynchron schreiben via aiofiles
        async with aiofiles.open(safe_path, "wb") as f:
            await f.write(content)

        file_size_kb: float = len(content) / 1024
        logger.info(
            "Datei gespeichert: %s (%.1f KB)", safe_path, file_size_kb
        )

        return await self.get_file_info(relative_path)

    async def upload_file_stream(
        self,
        relative_path: str,
        reader: AsyncGenerator[bytes, None],
        overwrite: bool = True,
    ) -> dict:
        """
        Streamt eine große Datei in Chunks auf die Festplatte.

        Ideal für Datei-Uploads via HTTP Multipart, bei denen der
        gesamte Inhalt nicht auf einmal im RAM gehalten werden soll.

        Args:
            relative_path: Zielpfad.
            reader: AsyncGenerator, der Chunks (bytes) liefert.
            overwrite: Bestehende Datei überschreiben.

        Returns:
            Dict mit Datei-Metadaten.
        """
        safe_path: str = self.secure_path(relative_path)

        if os.path.exists(safe_path) and not overwrite:
            raise FileExistsError(
                f"Datei existiert bereits: {relative_path!r}"
            )

        target_dir: str = os.path.dirname(safe_path)
        os.makedirs(target_dir, exist_ok=True)

        total_bytes: int = 0

        async with aiofiles.open(safe_path, "wb") as f:
            async for chunk in reader:
                chunk_size: int = len(chunk)
                total_bytes += chunk_size

                if total_bytes > self._MAX_UPLOAD_SIZE:
                    # Bereits geschriebene Daten löschen
                    await f.close()
                    os.unlink(safe_path)
                    raise ValueError(
                        f"Upload-Größe überschreitet Limit von "
                        f"{self._format_size(self._MAX_UPLOAD_SIZE)}."
                    )

                await f.write(chunk)

        logger.info(
            "Datei gestreamt: %s (%s).",
            safe_path,
            self._format_size(total_bytes),
        )
        return await self.get_file_info(relative_path)

    async def download_file(
        self,
        relative_path: str,
    ) -> bytes:
        """
        Liest eine Datei vollständig in den RAM.

        Für große Dateien (> 100 MB) sollte ``download_file_stream``
        verwendet werden, um RAM-Überlast zu vermeiden.

        Args:
            relative_path: Relativer Pfad.

        Returns:
            Dateiinhalt als Bytes.
        """
        safe_path: str = self.secure_path(relative_path)

        if not os.path.isfile(safe_path):
            raise FileNotFoundError(
                f"Datei nicht gefunden: {relative_path!r}"
            )

        async with aiofiles.open(safe_path, "rb") as f:
            return await f.read()

    async def download_file_stream(
        self,
        relative_path: str,
        chunk_size: int | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Streamt eine Datei in Chunks aus (für Downloads großer Dateien).

        Args:
            relative_path: Relativer Pfad.
            chunk_size: Chunk-Größe in Bytes (Default: 1 MiB).

        Yields:
            Datei-Chunks als Bytes.
        """
        safe_path: str = self.secure_path(relative_path)
        cs: int = chunk_size or self._CHUNK_SIZE

        if not os.path.isfile(safe_path):
            raise FileNotFoundError(
                f"Datei nicht gefunden: {relative_path!r}"
            )

        file_size: int = os.path.getsize(safe_path)
        sent: int = 0

        async with aiofiles.open(safe_path, "rb") as f:
            while True:
                chunk: bytes = await f.read(cs)
                if not chunk:
                    break
                sent += len(chunk)
                yield chunk

        logger.debug(
            "Datei-Stream abgeschlossen: %s (%s gesendet).",
            safe_path,
            self._format_size(sent),
        )

    async def move_file(
        self,
        source_path: str,
        dest_path: str,
        overwrite: bool = False,
    ) -> dict:
        """
        Verschiebt oder benennt eine Datei um.

        Args:
            source_path: Quellpfad (relativ).
            dest_path: Zielpfad (relativ).
            overwrite: ``True`` = Zieldatei überschreiben.

        Returns:
            Metadaten der verschobenen Datei.
        """
        safe_src: str = self.secure_path(source_path)
        safe_dst: str = self.secure_path(dest_path)

        if not os.path.exists(safe_src):
            raise FileNotFoundError(
                f"Quelldatei nicht gefunden: {source_path!r}"
            )

        if os.path.exists(safe_dst) and not overwrite:
            raise FileExistsError(
                f"Zieldatei existiert bereits: {dest_path!r}"
            )

        # Stelle sicher, dass das Zielverzeichnis existiert
        dst_dir: str = os.path.dirname(safe_dst)
        os.makedirs(dst_dir, exist_ok=True)

        shutil.move(safe_src, safe_dst)
        logger.info(
            "Datei verschoben: %s → %s", safe_src, safe_dst
        )
        return await self.get_file_info(dest_path)

    async def delete_file(
        self,
        relative_path: str,
        secure: bool = False,
    ) -> bool:
        """
        Löscht eine Datei.

        Args:
            relative_path: Relativer Pfad.
            secure: ``True`` = sicheres Löschen (mit Nullen überschreiben
                    vor dem Löschen). Verhindert forensische Wiederherstellung.

        Returns:
            ``True`` bei erfolgreicher Löschung.
        """
        safe_path: str = self.secure_path(relative_path)

        if not os.path.isfile(safe_path):
            raise FileNotFoundError(
                f"Datei nicht gefunden: {relative_path!r}"
            )

        if secure:
            # Sicheres Löschen: Überschreibe mit Nullen vor dem Unlink
            file_size: int = os.path.getsize(safe_path)
            try:
                with open(safe_path, "r+b") as f:
                    # Einmal mit Nullen, einmal mit Einsen, einmal zufällig
                    for pattern in (b"\x00", b"\xFF", os.urandom(file_size)):
                        f.seek(0)
                        f.write(
                            pattern * (file_size // len(pattern) + 1)
                        )
                        f.flush()
                        os.fsync(f.fileno())
                logger.info(
                    "Datei sicher gelöscht: %s (%s überschrieben).",
                    safe_path,
                    self._format_size(file_size),
                )
            except Exception as exc:
                logger.warning(
                    "Sicheres Überschreiben teilweise fehlgeschlagen "
                    "(%s): %s — führe normales Löschen durch.",
                    safe_path,
                    exc,
                )

        os.unlink(safe_path)
        return True

    async def copy_file(
        self,
        source_path: str,
        dest_path: str,
        overwrite: bool = False,
    ) -> dict:
        """
        Kopiert eine Datei.

        Args:
            source_path: Quellpfad (relativ).
            dest_path: Zielpfad (relativ).
            overwrite: Zieldatei überschreiben.

        Returns:
            Metadaten der kopierten Datei.
        """
        safe_src: str = self.secure_path(source_path)
        safe_dst: str = self.secure_path(dest_path)

        if not os.path.isfile(safe_src):
            raise FileNotFoundError(
                f"Quelldatei nicht gefunden: {source_path!r}"
            )

        if os.path.exists(safe_dst) and not overwrite:
            raise FileExistsError(
                f"Zieldatei existiert bereits: {dest_path!r}"
            )

        dst_dir: str = os.path.dirname(safe_dst)
        os.makedirs(dst_dir, exist_ok=True)

        shutil.copy2(safe_src, safe_dst)  # copy2 erhält Metadaten
        logger.info("Datei kopiert: %s → %s", safe_src, safe_dst)
        return await self.get_file_info(dest_path)

    # ──────────────────────────────────────────────────────────────────────
    # Speicher-Info
    # ──────────────────────────────────────────────────────────────────────

    async def get_storage_info(self) -> dict:
        """
        Ermittelt den Speicherplatz-Status des Basisverzeichnisses.

        Returns:
            Dict mit ``total_gb``, ``used_gb``, ``free_gb``, ``percent``,
            ``base_dir``.
        """
        try:
            usage = shutil.disk_usage(self.base_dir)
            return {
                "base_dir": self.base_dir,
                "total_gb": round(usage.total / (1024**3), 2),
                "used_gb": round(usage.used / (1024**3), 2),
                "free_gb": round(usage.free / (1024**3), 2),
                "percent": round(
                    (usage.used / usage.total) * 100, 1
                ),
            }
        except Exception as exc:
            logger.warning("Fehler bei Speicher-Info: %s", exc)
            return {
                "base_dir": self.base_dir,
                "total_gb": 0.0,
                "used_gb": 0.0,
                "free_gb": 0.0,
                "percent": 0.0,
            }

    # ──────────────────────────────────────────────────────────────────────
    # Hilfsmethoden
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """
        Formatiert eine Byte-Angabe menschenlesbar.

        Args:
            size_bytes: Größe in Bytes.

        Returns:
            Formatierter String: ``"1.5 GB"``, ``"256 MB"``, ``"42 KB"``.
        """
        if size_bytes >= 1024**3:
            return f"{size_bytes / (1024**3):.1f} GB"
        elif size_bytes >= 1024**2:
            return f"{size_bytes / (1024**2):.1f} MB"
        elif size_bytes >= 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes} B"
