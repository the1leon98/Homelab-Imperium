"""
Musik-Dienst des Homelab-Imperiums.

Durchsucht das Musikverzeichnis asynchron nach Audiodateien, extrahiert
ID3-Metadaten via mutagen und indiziert Tracks, Alben und Cover-Artworks
für das Frontend.

Unterstützte Formate:
- MP3 (ID3v1, ID3v2)
- FLAC (Vorbis Comments)
- WAV, OGG, M4A (eingeschränkt)

Verwendung::

    from app.services.music import MusicPlayerService
    from app.database import get_db_context

    with get_db_context() as db:
        svc = MusicPlayerService(db)
        await svc.scan_library()
        tracks = svc.get_all_tracks()
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import MusicTrack

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.services.music")


# ═══════════════════════════════════════════════════════════════════════════════
# Datenklassen
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class TrackInfo:
    """Extrahierte Metadaten eines einzelnen Audiotracks."""

    file_path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    genre: str = ""
    track_number: int = 0
    total_tracks: int = 0
    disc_number: int = 0
    year: int = 0
    duration_seconds: float = 0.0
    bitrate_kbps: int = 0
    sample_rate_hz: int = 0
    has_cover_art: bool = False
    cover_art_path: str = ""
    file_size_mb: float = 0.0
    format: str = "mp3"


@dataclass
class ScanResult:
    """Ergebnis eines Musikbibliothek-Scans."""

    total_files_scanned: int = 0
    new_tracks_added: int = 0
    tracks_updated: int = 0
    errors: int = 0
    scan_duration_seconds: float = 0.0
    errors_list: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# Unterstützte Audioformate
# ═══════════════════════════════════════════════════════════════════════════════

_SUPPORTED_EXTENSIONS: set[str] = {
    ".mp3",
    ".flac",
    ".wav",
    ".ogg",
    ".m4a",
    ".wma",
    ".aac",
    ".opus",
}


# ═══════════════════════════════════════════════════════════════════════════════
# MusicPlayerService
# ═══════════════════════════════════════════════════════════════════════════════


class MusicPlayerService:
    """
    Musikbibliothek-Verwaltung mit mutagen-basierter Metadaten-Extraktion.

    Unterstützt asynchrones Scannen des Musikverzeichnisses und
    persistente Indexierung in der ``music_tracks``-PostgreSQL-Tabelle.
    """

    def __init__(self, db: Session) -> None:
        """
        Args:
            db: SQLAlchemy-Datenbank-Session.
        """
        self.db: Session = db
        self._music_dir: Path = Path(str(settings.path_media)) / "music"
        self._cover_dir: Path = self._music_dir / ".covers"
        logger.info(
            "MusicPlayerService initialisiert: music_dir=%s", self._music_dir
        )

    # ──────────────────────────────────────────────────────────────────────
    # Bibliothek-Scan
    # ──────────────────────────────────────────────────────────────────────

    async def scan_library(
        self,
        force_reindex: bool = False,
    ) -> ScanResult:
        """
        Scannt das Musikverzeichnis rekursiv nach Audiodateien und
        indiziert alle gefundenen Tracks in der Datenbank.

        Args:
            force_reindex: ``True`` = alle Tracks neu einlesen, auch
                           wenn bereits in der DB vorhanden.

        Returns:
            ``ScanResult`` mit Statistiken.
        """
        import time

        start_time: float = time.monotonic()
        logger.info(
            "🎵 Starte Musikbibliothek-Scan: %s (force=%s)...",
            self._music_dir,
            force_reindex,
        )

        # Cover-Verzeichnis sicherstellen
        self._cover_dir.mkdir(parents=True, exist_ok=True)

        # Alle Audiodateien finden (asynchron im Thread-Pool)
        audio_files: list[Path] = await asyncio.to_thread(
            self._find_audio_files
        )

        result: ScanResult = ScanResult(
            total_files_scanned=len(audio_files)
        )
        logger.info(
            "%d Audiodateien gefunden. Extrahiere Metadaten...",
            len(audio_files),
        )

        # Bestehende Dateipfade für Deduplizierung laden
        existing_paths: set[str] = set()
        if not force_reindex:
            rows = self.db.query(MusicTrack.file_path).all()
            existing_paths = {row[0] for row in rows}

        # Jede Datei verarbeiten
        for audio_path in audio_files:
            path_str: str = str(audio_path)

            # Überspringen, wenn bereits indexiert
            if not force_reindex and path_str in existing_paths:
                continue

            try:
                info: TrackInfo = await asyncio.to_thread(
                    self.extract_metadata, path_str
                )

                if info.title:  # Nur speichern, wenn Titel extrahiert wurde
                    self._upsert_track(info, path_str in existing_paths)
                    if path_str in existing_paths:
                        result.tracks_updated += 1
                    else:
                        result.new_tracks_added += 1
            except Exception as exc:
                logger.warning(
                    "Fehler bei %s: %s", audio_path.name, exc
                )
                result.errors += 1
                result.errors_list.append(f"{audio_path.name}: {exc}")

        self.db.commit()

        result.scan_duration_seconds = round(time.monotonic() - start_time, 2)
        logger.info(
            "✅ Musik-Scan abgeschlossen: %d neu, %d aktualisiert, "
            "%d Fehler in %.1fs.",
            result.new_tracks_added,
            result.tracks_updated,
            result.errors,
            result.scan_duration_seconds,
        )
        return result

    def _find_audio_files(self) -> list[Path]:
        """
        Durchsucht das Musikverzeichnis rekursiv nach Audiodateien.

        Returns:
            Liste von ``Path``-Objekten, sortiert nach Pfad.
        """
        if not self._music_dir.exists():
            logger.warning(
                "Musikverzeichnis nicht gefunden: %s", self._music_dir
            )
            return []

        files: list[Path] = []
        for root, dirs, filenames in os.walk(self._music_dir):
            # Versteckte Verzeichnisse überspringen
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for fname in filenames:
                fpath: Path = Path(root) / fname
                if fpath.suffix.lower() in _SUPPORTED_EXTENSIONS:
                    files.append(fpath)

        files.sort()
        return files

    # ──────────────────────────────────────────────────────────────────────
    # Metadaten-Extraktion (mutagen)
    # ──────────────────────────────────────────────────────────────────────

    def extract_metadata(self, file_path: str) -> TrackInfo:
        """
        Extrahiert ID3- oder Vorbis-Metadaten aus einer Audiodatei.

        Nutzt ``mutagen`` für die Tag-Extraktion. Unterstützt:
        - MP3 (ID3v1, ID3v2)
        - FLAC (Vorbis Comments)
        - OGG, M4A (eingeschränkt)

        Args:
            file_path: Absoluter Pfad zur Audiodatei.

        Returns:
            ``TrackInfo`` mit allen extrahierten Metadaten.
        """
        try:
            import mutagen
            from mutagen.flac import FLAC
            from mutagen.id3 import ID3
            from mutagen.mp3 import MP3
        except ImportError:
            raise ImportError(
                "mutagen ist nicht installiert. "
                "Installiere mit: pip install mutagen"
            )

        fpath: Path = Path(file_path)
        info: TrackInfo = TrackInfo(
            file_path=file_path,
            title=fpath.stem,  # Fallback: Dateiname ohne Endung
            file_size_mb=round(
                fpath.stat().st_size / (1024 * 1024), 2
            ),
        )

        try:
            # Format-spezifische Extraktion
            suffix: str = fpath.suffix.lower()

            if suffix == ".mp3":
                self._extract_mp3(file_path, info)
            elif suffix == ".flac":
                self._extract_flac(file_path, info)
            else:
                # Generische Extraktion via mutagen.File()
                mf = mutagen.File(file_path)
                if mf is not None:
                    self._extract_generic(mf, info)

        except Exception as exc:
            logger.debug(
                "Metadaten-Extraktion teilweise fehlgeschlagen "
                "für %s: %s",
                fpath.name,
                exc,
            )

        # Cover-Art extrahieren (falls vorhanden)
        if info.has_cover_art:
            info.cover_art_path = self._extract_cover_art(file_path, info)

        return info

    def _extract_mp3(self, file_path: str, info: TrackInfo) -> None:
        """Extrahiert ID3-Tags aus einer MP3-Datei."""
        from mutagen.id3 import ID3
        from mutagen.mp3 import MP3

        audio: MP3 = MP3(file_path)

        # Dauer & Bitrate
        info.format = "mp3"
        info.duration_seconds = round(audio.info.length, 1)
        info.bitrate_kbps = int(audio.info.bitrate // 1000)
        info.sample_rate_hz = audio.info.sample_rate

        # ID3-Tags
        tags: ID3 | None = audio.tags
        if tags is None:
            return

        # Titel
        if "TIT2" in tags:
            info.title = str(tags["TIT2"].text[0])

        # Interpret
        if "TPE1" in tags:
            info.artist = str(tags["TPE1"].text[0])

        # Album
        if "TALB" in tags:
            info.album = str(tags["TALB"].text[0])

        # Album-Interpret
        if "TPE2" in tags:
            info.album_artist = str(tags["TPE2"].text[0])

        # Genre
        if "TCON" in tags:
            info.genre = str(tags["TCON"].text[0])

        # Track-Nummer (Format: "1/12" oder "1")
        if "TRCK" in tags:
            trck: str = str(tags["TRCK"].text[0])
            parts: list[str] = trck.split("/")
            try:
                info.track_number = int(parts[0])
            except ValueError:
                pass
            if len(parts) > 1:
                try:
                    info.total_tracks = int(parts[1])
                except ValueError:
                    pass

        # CD-Nummer
        if "TPOS" in tags:
            tpos: str = str(tags["TPOS"].text[0])
            try:
                info.disc_number = int(tpos.split("/")[0])
            except ValueError:
                pass

        # Jahr
        if "TDRC" in tags:
            info.year = self._parse_year(str(tags["TDRC"].text[0]))

        # Cover-Art vorhanden?
        info.has_cover_art = "APIC:" in tags

    def _extract_flac(self, file_path: str, info: TrackInfo) -> None:
        """Extrahiert Vorbis-Comments aus einer FLAC-Datei."""
        from mutagen.flac import FLAC

        audio: FLAC = FLAC(file_path)

        info.format = "flac"
        info.duration_seconds = round(audio.info.length, 1)
        info.bitrate_kbps = int(
            audio.info.bitrate // 1000
        ) if hasattr(audio.info, "bitrate") and audio.info.bitrate else 0
        info.sample_rate_hz = audio.info.sample_rate

        tags: dict = dict(audio.tags or {})

        info.title = self._first(tags.get("title", [info.title]))
        info.artist = self._first(tags.get("artist", [""]))
        info.album = self._first(tags.get("album", [""]))
        info.album_artist = self._first(tags.get("albumartist", [""]))
        info.genre = self._first(tags.get("genre", [""]))
        info.year = self._parse_year(self._first(tags.get("date", ["0"])))

        trck: str = self._first(tags.get("tracknumber", ["0"]))
        parts = trck.split("/")
        try:
            info.track_number = int(parts[0])
        except ValueError:
            pass
        if len(parts) > 1:
            try:
                info.total_tracks = int(parts[1])
            except ValueError:
                pass

        disc: str = self._first(tags.get("discnumber", ["0"]))
        try:
            info.disc_number = int(disc.split("/")[0])
        except ValueError:
            pass

        # FLAC hat eingebettete Bilder als metadata_block_picture
        info.has_cover_art = bool(audio.pictures)

    def _extract_generic(self, mf, info: TrackInfo) -> None:
        """Generische Extraktion für andere Formate."""
        info.duration_seconds = round(mf.info.length, 1)

        if hasattr(mf.info, "bitrate") and mf.info.bitrate:
            info.bitrate_kbps = int(mf.info.bitrate // 1000)
        if hasattr(mf.info, "sample_rate"):
            info.sample_rate_hz = mf.info.sample_rate

        tags: dict = dict(mf.tags or {}) if hasattr(mf, "tags") else {}

        info.title = self._first(tags.get("title", [info.title]))
        info.artist = self._first(tags.get("artist", [""]))
        info.album = self._first(tags.get("album", [""]))
        info.genre = self._first(tags.get("genre", [""]))

    @staticmethod
    def _first(values: list[str]) -> str:
        """Holt das erste Element einer Liste oder leeren String."""
        return str(values[0]) if values else ""

    @staticmethod
    def _parse_year(raw: str) -> int:
        """Extrahiert eine Jahreszahl aus einem String (z.B. '2024-06-15' → 2024)."""
        import re

        match = re.search(r"(\d{4})", raw)
        return int(match.group(1)) if match else 0

    # ──────────────────────────────────────────────────────────────────────
    # Cover-Art-Extraktion
    # ──────────────────────────────────────────────────────────────────────

    def _extract_cover_art(
        self,
        file_path: str,
        info: TrackInfo,
    ) -> str:
        """
        Extrahiert eingebettete Cover-Art und speichert sie als Datei.

        Args:
            file_path: Pfad zur Audiodatei.
            info: TrackInfo mit Metadaten (wird für Dateiname genutzt).

        Returns:
            Pfad zur extrahierten Cover-Art-Datei (JPEG/PNG).
        """
        # Eindeutigen Dateinamen generieren
        safe_artist: str = "".join(
            c for c in info.artist if c.isalnum() or c in " _-"
        ).strip() or "Unknown"
        safe_album: str = "".join(
            c for c in info.album if c.isalnum() or c in " _-"
        ).strip() or "Unknown"
        cover_filename: str = f"{safe_artist} - {safe_album}.jpg"
        cover_path: Path = self._cover_dir / cover_filename

        # Wenn Cover bereits existiert, nicht erneut extrahieren
        if cover_path.exists():
            return str(cover_path)

        try:
            suffix: str = Path(file_path).suffix.lower()

            if suffix == ".mp3":
                self._extract_mp3_cover(file_path, cover_path)
            elif suffix == ".flac":
                self._extract_flac_cover(file_path, cover_path)
            else:
                return ""

            if cover_path.exists():
                logger.debug(
                    "Cover-Art extrahiert: %s → %s",
                    Path(file_path).name,
                    cover_path.name,
                )
                return str(cover_path)

        except Exception as exc:
            logger.debug(
                "Cover-Art-Extraktion fehlgeschlagen für %s: %s",
                Path(file_path).name,
                exc,
            )

        return ""

    @staticmethod
    def _extract_mp3_cover(file_path: str, dest: Path) -> None:
        """Extrahiert Cover-Art aus MP3-ID3-Tag."""
        from mutagen.id3 import ID3

        tags: ID3 = ID3(file_path)
        for key in tags.keys():
            if key.startswith("APIC"):
                artwork = tags[key].data
                dest.write_bytes(artwork)
                return

    @staticmethod
    def _extract_flac_cover(file_path: str, dest: Path) -> None:
        """Extrahiert Cover-Art aus FLAC-Pictures."""
        from mutagen.flac import FLAC

        audio: FLAC = FLAC(file_path)
        if audio.pictures:
            dest.write_bytes(audio.pictures[0].data)

    # ──────────────────────────────────────────────────────────────────────
    # Datenbank-Persistenz
    # ──────────────────────────────────────────────────────────────────────

    def _upsert_track(self, info: TrackInfo, is_update: bool) -> None:
        """
        Fügt einen Track in die DB ein oder aktualisiert ihn.

        Args:
            info: Extrahierte TrackInfo.
            is_update: True = UPDATE, False = INSERT.
        """
        if is_update:
            track: MusicTrack | None = (
                self.db.query(MusicTrack)
                .filter(MusicTrack.file_path == info.file_path)
                .first()
            )
            if track:
                track.title = info.title
                track.artist = info.artist
                track.album = info.album
                track.album_artist = info.album_artist
                track.genre = info.genre
                track.track_number = info.track_number
                track.total_tracks = info.total_tracks
                track.disc_number = info.disc_number
                track.year = info.year
                track.duration_seconds = info.duration_seconds
                track.bitrate_kbps = info.bitrate_kbps
                track.sample_rate_hz = info.sample_rate_hz
                track.has_cover_art = info.has_cover_art
                track.cover_art_path = info.cover_art_path
                return

        # INSERT
        new_track: MusicTrack = MusicTrack(
            title=info.title,
            file_path=info.file_path,
            artist=info.artist or None,
            album=info.album or None,
            album_artist=info.album_artist or None,
            genre=info.genre or None,
            track_number=info.track_number or None,
            total_tracks=info.total_tracks or None,
            disc_number=info.disc_number or None,
            year=info.year or None,
            duration_seconds=info.duration_seconds or None,
            bitrate_kbps=info.bitrate_kbps or None,
            sample_rate_hz=info.sample_rate_hz or None,
            has_cover_art=info.has_cover_art,
            cover_art_path=info.cover_art_path or None,
        )
        self.db.add(new_track)

    # ──────────────────────────────────────────────────────────────────────
    # Abfrage-Methoden (für Frontend)
    # ──────────────────────────────────────────────────────────────────────

    def get_all_tracks(
        self,
        limit: int = 500,
        offset: int = 0,
    ) -> dict:
        """
        Ruft alle Tracks paginiert ab.

        Returns:
            Dict mit ``items`` (Liste), ``total``, ``limit``, ``offset``.
        """
        total: int = (
            self.db.query(func.count(MusicTrack.id)).scalar() or 0
        )
        tracks = (
            self.db.query(MusicTrack)
            .order_by(MusicTrack.artist, MusicTrack.album, MusicTrack.track_number)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return {
            "items": [self._track_to_dict(t) for t in tracks],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get_artists(self) -> list[dict]:
        """
        Ruft alle Interpreten mit Track-Anzahl ab.

        Returns:
            Liste von Dicts mit ``name`` und ``track_count``.
        """
        rows = (
            self.db.query(
                MusicTrack.artist,
                func.count(MusicTrack.id).label("count"),
            )
            .filter(MusicTrack.artist.isnot(None))
            .group_by(MusicTrack.artist)
            .order_by(MusicTrack.artist)
            .all()
        )
        return [
            {"name": row[0], "track_count": row[1]} for row in rows if row[0]
        ]

    def get_albums(self, artist: str | None = None) -> list[dict]:
        """
        Ruft alle Alben ab, optional gefiltert nach Interpret.

        Args:
            artist: Interpret-Name (None = alle).

        Returns:
            Liste von Dicts mit ``album``, ``artist``, ``track_count``,
            ``cover_art_path``, ``year``.
        """
        query = (
            self.db.query(
                MusicTrack.album,
                MusicTrack.artist,
                func.count(MusicTrack.id).label("count"),
                func.max(MusicTrack.year).label("year"),
            )
            .filter(MusicTrack.album.isnot(None))
        )

        if artist:
            query = query.filter(MusicTrack.artist == artist)

        rows = (
            query.group_by(MusicTrack.album, MusicTrack.artist)
            .order_by(MusicTrack.artist, MusicTrack.year)
            .all()
        )

        albums: list[dict] = []
        for row in rows:
            # Cover-Art-Pfad für das Album finden
            cover: MusicTrack | None = (
                self.db.query(MusicTrack)
                .filter(
                    MusicTrack.album == row[0],
                    MusicTrack.has_cover_art == True,
                )
                .first()
            )

            albums.append(
                {
                    "album": row[0],
                    "artist": row[1],
                    "track_count": row[2],
                    "year": row[3],
                    "cover_art_path": (
                        cover.cover_art_path if cover else None
                    ),
                }
            )

        return albums

    def get_genres(self) -> list[dict]:
        """
        Ruft alle Genres mit Track-Anzahl ab.

        Returns:
            Liste von Dicts mit ``name`` und ``track_count``.
        """
        rows = (
            self.db.query(
                MusicTrack.genre,
                func.count(MusicTrack.id).label("count"),
            )
            .filter(MusicTrack.genre.isnot(None))
            .group_by(MusicTrack.genre)
            .order_by(func.count(MusicTrack.id).desc())
            .all()
        )
        return [
            {"name": row[0], "track_count": row[1]} for row in rows if row[0]
        ]

    def search_tracks(self, query: str, limit: int = 50) -> list[dict]:
        """
        Durchsucht Titel, Interpret und Album.

        Args:
            query: Suchbegriff.
            limit: Maximale Treffer.

        Returns:
            Liste von Track-Dicts.
        """
        pattern: str = f"%{query}%"
        tracks = (
            self.db.query(MusicTrack)
            .filter(
                MusicTrack.title.ilike(pattern)
                | MusicTrack.artist.ilike(pattern)
                | MusicTrack.album.ilike(pattern)
                | MusicTrack.genre.ilike(pattern)
            )
            .order_by(MusicTrack.artist, MusicTrack.album)
            .limit(limit)
            .all()
        )
        return [self._track_to_dict(t) for t in tracks]

    def get_library_stats(self) -> dict:
        """Sammelt Statistiken über die Musikbibliothek."""
        total_tracks: int = (
            self.db.query(func.count(MusicTrack.id)).scalar() or 0
        )
        total_artists: int = (
            self.db.query(
                func.count(func.distinct(MusicTrack.artist))
            )
            .filter(MusicTrack.artist.isnot(None))
            .scalar()
            or 0
        )
        total_albums: int = (
            self.db.query(
                func.count(func.distinct(MusicTrack.album))
            )
            .filter(MusicTrack.album.isnot(None))
            .scalar()
            or 0
        )
        total_duration = (
            self.db.query(
                func.coalesce(func.sum(MusicTrack.duration_seconds), 0)
            ).scalar()
            or 0
        )

        return {
            "total_tracks": total_tracks,
            "total_artists": total_artists,
            "total_albums": total_albums,
            "total_duration_seconds": round(float(total_duration), 0),
            "total_duration_display": self._format_duration(
                float(total_duration)
            ),
        }

    # ──────────────────────────────────────────────────────────────────────
    # Hilfsmethoden
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _track_to_dict(track: MusicTrack) -> dict:
        """Wandelt ein MusicTrack-ORM-Objekt in ein Dict."""
        return {
            "id": track.id,
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
            "album_artist": track.album_artist,
            "genre": track.genre,
            "track_number": track.track_number,
            "total_tracks": track.total_tracks,
            "disc_number": track.disc_number,
            "year": track.year,
            "duration_seconds": (
                round(track.duration_seconds, 1)
                if track.duration_seconds
                else None
            ),
            "duration_display": (
                MusicPlayerService._format_duration(
                    track.duration_seconds or 0
                )
            ),
            "bitrate_kbps": track.bitrate_kbps,
            "sample_rate_hz": track.sample_rate_hz,
            "has_cover_art": track.has_cover_art,
            "cover_art_url": (
                f"/api/music/cover/{track.id}"
                if track.has_cover_art
                else None
            ),
            "file_path": track.file_path,
        }

    @staticmethod
    def _format_duration(total_seconds: float) -> str:
        """Formatiert Sekunden als 'Xh Ym' oder 'Ym Zs'."""
        if total_seconds <= 0:
            return "0m"
        hours: int = int(total_seconds // 3600)
        minutes: int = int((total_seconds % 3600) // 60)
        seconds: int = int(total_seconds % 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
