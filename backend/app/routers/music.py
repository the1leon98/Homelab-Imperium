"""
Musik-Router des Homelab-Imperiums.

Stellt REST-Endpunkte für die lokale MP3-/FLAC-Musiksammlung bereit:
Bibliothek-Scan, Track-/Artist-/Album-Abfragen und Audio-Streaming
via HTML5-<audio>-kompatiblem Bitstrom.

Endpunkte:
- ``POST /api/music/scan``          — Bibliothek-Scan starten
- ``GET /api/music/tracks``          — Alle Tracks (paginiert)
- ``GET /api/music/artists``         — Interpreten-Liste
- ``GET /api/music/albums``          — Alben-Liste
- ``GET /api/music/genres``          — Genre-Liste
- ``GET /api/music/search``          — Suche
- ``GET /api/music/stats``           — Bibliotheksstatistiken
- ``GET /api/music/stream/{id}``     — Audio-Stream (Bytes)
- ``GET /api/music/cover/{id}``      — Cover-Bild (Bytes)

Verwendung::

    from app.routers.music import router
    app.include_router(router, prefix="/api")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import MusicTrack
from app.services.music import MusicPlayerService

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.routers.music")

# ═══════════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════════

router: APIRouter = APIRouter(tags=["Musik"])

# ═══════════════════════════════════════════════════════════════════════════════
# Dependency Injection
# ═══════════════════════════════════════════════════════════════════════════════


def get_music_service(
    db: Session = Depends(get_db),
) -> MusicPlayerService:
    """Factory für den MusicPlayerService mit DB-Session."""
    return MusicPlayerService(db)


# ═══════════════════════════════════════════════════════════════════════════════
# Bibliothek-Scan
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/music/scan",
    summary="Musikbibliothek scannen",
    description="Durchsucht das Musikverzeichnis rekursiv nach "
    "Audiodateien und indiziert ID3-Metadaten in der Datenbank. "
    "Wird als Background-Task gestartet und gibt sofort 202 zurück.",
    status_code=202,
)
async def scan_music_library(
    background_tasks: BackgroundTasks,
    force: bool = Query(default=False, description="Alle Tracks neu indizieren."),
    svc: MusicPlayerService = Depends(get_music_service),
) -> dict:
    """
    Startet einen asynchronen Bibliothek-Scan im Hintergrund.

    Der Scan durchsucht das Musikverzeichnis rekursiv nach
    .mp3, .flac, .ogg, .wav, .m4a-Dateien und extrahiert
    ID3/Vorbis-Metadaten via mutagen.
    """
    logger.info("POST /music/scan: force=%s.", force)

    async def _run_scan() -> None:
        """Hintergrund-Scan mit eigener DB-Session."""
        from app.database import SessionLocal

        db: Session = SessionLocal()
        try:
            scan_svc: MusicPlayerService = MusicPlayerService(db)
            result = await scan_svc.scan_library(force_reindex=force)
            logger.info(
                "Scan abgeschlossen: %d neu, %d aktualisiert, "
                "%d Fehler in %.1fs.",
                result.new_tracks_added,
                result.tracks_updated,
                result.errors,
                result.scan_duration_seconds,
            )
        except Exception as exc:
            logger.error("Scan-Fehler: %s", exc)
        finally:
            db.close()

    background_tasks.add_task(_run_scan)

    return {
        "message": "Bibliothek-Scan im Hintergrund gestartet.",
        "force": force,
        "status_endpoint": "/api/music/stats",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Tracks, Artists, Albums, Genres
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/music/tracks",
    summary="Alle Tracks",
    description="Paginiert Liste aller indizierten Musiktitel.",
)
async def get_tracks(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    svc: MusicPlayerService = Depends(get_music_service),
) -> dict:
    """
    Paginierte Track-Liste — sortiert nach Interpret → Album → Track-Nr.
    """
    logger.debug("GET /music/tracks: limit=%d, offset=%d.", limit, offset)
    return svc.get_all_tracks(limit=limit, offset=offset)


@router.get(
    "/music/artists",
    summary="Alle Interpreten",
    description="Liste aller Interpreten mit Track-Anzahl.",
)
async def get_artists(
    svc: MusicPlayerService = Depends(get_music_service),
) -> list[dict]:
    """Interpret-Liste mit Track-Count."""
    logger.debug("GET /music/artists.")
    return svc.get_artists()


@router.get(
    "/music/albums",
    summary="Alle Alben",
    description="Liste aller Alben mit Cover-Art-Pfad und Track-Anzahl. "
    "Optional nach Interpret filterbar.",
)
async def get_albums(
    artist: Optional[str] = Query(default=None, description="Nach Interpret filtern."),
    svc: MusicPlayerService = Depends(get_music_service),
) -> list[dict]:
    """Alben-Liste, optional gefiltert."""
    logger.debug("GET /music/albums: artist=%s.", artist)
    return svc.get_albums(artist=artist)


@router.get(
    "/music/genres",
    summary="Alle Genres",
    description="Liste aller Genres mit Track-Anzahl, absteigend sortiert.",
)
async def get_genres(
    svc: MusicPlayerService = Depends(get_music_service),
) -> list[dict]:
    """Genre-Liste mit Track-Count."""
    logger.debug("GET /music/genres.")
    return svc.get_genres()


@router.get(
    "/music/search",
    summary="Musiksuche",
    description="Durchsucht Titel, Interpret, Album und Genre.",
)
async def search_music(
    q: str = Query(..., min_length=2, description="Suchbegriff."),
    limit: int = Query(default=50, ge=1, le=200),
    svc: MusicPlayerService = Depends(get_music_service),
) -> list[dict]:
    """
    Volltextsuche in der Musikbibliothek.
    """
    logger.info("GET /music/search: q=%r, limit=%d.", q, limit)
    return svc.search_tracks(query=q, limit=limit)


@router.get(
    "/music/stats",
    summary="Bibliotheksstatistiken",
    description="Gesamtzahlen: Tracks, Interpreten, Alben, Gesamtdauer.",
)
async def get_library_stats(
    svc: MusicPlayerService = Depends(get_music_service),
) -> dict:
    """Statistiken der Musikbibliothek."""
    logger.debug("GET /music/stats.")
    return svc.get_library_stats()


# ═══════════════════════════════════════════════════════════════════════════════
# Audio-Streaming & Cover-Art
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/music/stream/{track_id}",
    summary="Audio-Stream",
    description="**Streaming-Endpunkt für HTML5-<audio>-Tag.** "
    "Liefert die Audiodatei als Binärstream mit korrektem Content-Type "
    "(audio/mpeg für MP3, audio/flac für FLAC). "
    "Unterstützt HTTP-Range-Requests für seeking.",
    responses={
        200: {
            "description": "Audio-Bitstrom.",
            "content": {
                "audio/mpeg": {},
                "audio/flac": {},
                "audio/ogg": {},
            },
        },
    },
)
async def stream_audio(
    track_id: int,
    db: Session = Depends(get_db),
) -> FileResponse:
    """
    Streamt eine Audiodatei direkt an den Browser.

    Nutzt ``FileResponse`` mit ``accept_ranges`` für HTTP-Range-Requests,
    sodass der HTML5-<audio>-Player vor- und zurückspulen kann.
    """
    logger.info("GET /music/stream/%d.", track_id)

    track: MusicTrack | None = (
        db.query(MusicTrack).filter(MusicTrack.id == track_id).first()
    )
    if not track:
        raise HTTPException(
            status_code=404,
            detail=f"Track {track_id} nicht gefunden.",
        )

    file_path: str = track.file_path
    if not file_path or not os.path.isfile(file_path):
        raise HTTPException(
            status_code=404,
            detail="Audiodatei auf dem Server nicht gefunden.",
        )

    # MIME-Type aus Dateiendung ableiten
    suffix: str = Path(file_path).suffix.lower()
    media_type_map: dict[str, str] = {
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".opus": "audio/opus",
    }
    media_type: str = media_type_map.get(suffix, "audio/mpeg")

    file_size: int = os.path.getsize(file_path)
    logger.debug(
        "Streame %s (%s, %s).",
        track.title,
        media_type,
        MusicPlayerService._format_duration(
            track.duration_seconds or 0
        ),
    )

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=f"{track.artist} - {track.title}{suffix}",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Cache-Control": "public, max-age=86400",
        },
    )


@router.get(
    "/music/cover/{track_id}",
    summary="Cover-Bild",
    description="Liefert das eingebettete Cover-Art eines Tracks "
    "als Bild (JPEG/PNG).",
    responses={
        200: {
            "description": "Cover-Bild.",
            "content": {"image/jpeg": {}, "image/png": {}},
        },
    },
)
async def get_cover_art(
    track_id: int,
    db: Session = Depends(get_db),
) -> FileResponse:
    """
    Cover-Art eines Tracks als Bild liefern.

    Wenn der Track kein Cover hat (``has_cover_art=False``), wird
    ein Fallback-Image zurückgegeben.
    """
    logger.debug("GET /music/cover/%d.", track_id)

    track: MusicTrack | None = (
        db.query(MusicTrack).filter(MusicTrack.id == track_id).first()
    )
    if not track:
        raise HTTPException(
            status_code=404,
            detail=f"Track {track_id} nicht gefunden.",
        )

    # Cover-Art-Pfad aus der DB
    cover_path: str = track.cover_art_path or ""

    if cover_path and os.path.isfile(cover_path):
        logger.debug("Cover geliefert: %s.", cover_path)
        return FileResponse(
            path=cover_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    # Kein Cover → Fallback
    logger.debug("Kein Cover für Track %d.", track_id)

    # Versuche, ein generisches Album-Cover aus dem Cover-Verzeichnis zu finden
    music_dir: Path = Path(str(settings.path_media)) / "music" / ".covers"
    if track.album:
        for ext in (".jpg", ".png"):
            for fname in music_dir.glob(f"*{track.album}*{ext}"):
                return FileResponse(
                    path=str(fname),
                    media_type="image/jpeg" if ext == ".jpg" else "image/png",
                    headers={"Cache-Control": "public, max-age=86400"},
                )

    raise HTTPException(
        status_code=404,
        detail="Kein Cover-Art für diesen Track verfügbar.",
    )
