"""
Medien-Router des Homelab-Imperiums.

Stellt HTTP-Endpunkte für die Medienbibliothek bereit. **Sämtliche**
Anfragen laufen durch das FastAPI-Backend — das Frontend hat KEINEN
direkten Zugriff auf Jellyfin. Dieses API-Wrapper-Prinzip verhindert
die Einbettung von Drittanbieter-iFrames und zentralisiert die
Sicherheitskontrolle.

Endpunkte:
- ``GET /api/media/movies``          — Filmbibliothek
- ``GET /api/media/series``          — Serienbibliothek
- ``GET /api/media/episodes/{id}``   — Episoden einer Serie
- ``GET /api/media/continue``        — Weiterschauen
- ``GET /api/media/recent``          — Kürzlich hinzugefügt
- ``GET /api/media/search``          — Suche
- ``GET /api/media/item/{id}``       — Einzel-Item-Details
- ``GET /api/media/cover/{id}/{type}`` — Cover-Bild (Binär-Proxy)
- ``GET /api/media/stream/{id}``     — Video-Stream (Binär-Proxy)
- ``GET /api/media/genres``          — Genre-Liste
- ``GET /api/media/stats``           — Bibliotheksstatistiken

Verwendung::

    from app.routers.media import router
    app.include_router(router, prefix="/api")
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.services.media import MediaService

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.routers.media")

# ═══════════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════════

router: APIRouter = APIRouter(tags=["Medien"])

# ═══════════════════════════════════════════════════════════════════════════════
# Dependency Injection
# ═══════════════════════════════════════════════════════════════════════════════

_media_service: MediaService = MediaService()


def get_media_service() -> MediaService:
    """Factory für den MediaService (zustandslos)."""
    return _media_service


# ═══════════════════════════════════════════════════════════════════════════════
# Bibliotheks-Endpunkte
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/media/movies",
    summary="Filmbibliothek",
    description="Ruft die Filmbibliothek mit Paginierung und Sortierung ab. "
    "Alle Cover-URLs zeigen auf /api/media/cover/{id} — niemals direkt "
    "zu Jellyfin.",
)
async def get_movies(
    limit: int = Query(default=50, ge=1, le=300, description="Einträge pro Seite."),
    page: int = Query(default=1, ge=1, description="Seitennummer (1-basiert)."),
    sort_by: str = Query(
        default="SortName",
        description="Sortierfeld (SortName, DateCreated, CommunityRating, "
        "PremiereDate, Runtime).",
    ),
    sort_order: str = Query(
        default="Ascending",
        pattern=r"^(Ascending|Descending)$",
        description="Sortierreihenfolge.",
    ),
    svc: MediaService = Depends(get_media_service),
) -> dict:
    """
    Paginierte Filmbibliothek.

    Das Frontend ruft diesen Endpunkt auf und erhält eine Liste mit
    Filmen, die jeweils eine ``cover_url`` enthalten. Diese URL zeigt
    auf ``/api/media/cover/{id}``, was einen ERNEUTEN Backend-Durchlauf
    auslöst und das tatsächliche Bild von Jellyfin als Proxy ausliefert.
    """
    start_index: int = (page - 1) * limit
    logger.info(
        "GET /media/movies: limit=%d, page=%d, sort=%s %s.",
        limit,
        page,
        sort_by,
        sort_order,
    )
    return await svc.get_movies(
        limit=limit,
        start_index=start_index,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get(
    "/media/series",
    summary="Serienbibliothek",
    description="Ruft die Serienbibliothek ab.",
)
async def get_tv_shows(
    limit: int = Query(default=50, ge=1, le=300),
    page: int = Query(default=1, ge=1),
    svc: MediaService = Depends(get_media_service),
) -> dict:
    """Paginierte Serienbibliothek."""
    start_index: int = (page - 1) * limit
    logger.info("GET /media/series: limit=%d, page=%d.", limit, page)
    return await svc.get_tv_shows(limit=limit, start_index=start_index)


@router.get(
    "/media/episodes/{series_id}",
    summary="Episoden einer Serie",
    description="Ruft alle Episoden einer Serie ab, optional gefiltert nach Staffel.",
)
async def get_episodes(
    series_id: str,
    season: Optional[int] = Query(default=None, ge=1, description="Staffel-Nummer."),
    svc: MediaService = Depends(get_media_service),
) -> list[dict]:
    """Episoden-Liste mit Serien-Metadaten."""
    logger.info(
        "GET /media/episodes/%s: season=%s.", series_id, season
    )
    return await svc.get_episodes(
        series_id=series_id,
        season_number=season,
    )


@router.get(
    "/media/continue",
    summary="Weiterschauen",
    description="Angefangene, aber nicht fertig geschaute Medien mit Resume-Position.",
)
async def get_continue_watching(
    limit: int = Query(default=20, ge=1, le=50),
    svc: MediaService = Depends(get_media_service),
) -> list[dict]:
    """Medien mit Resume-Position für 'Weiterschauen'-Widget."""
    logger.info("GET /media/continue: limit=%d.", limit)
    return await svc.get_continue_watching(limit=limit)


@router.get(
    "/media/recent",
    summary="Kürzlich hinzugefügt",
    description="Die neuesten Medien in der Bibliothek.",
)
async def get_recently_added(
    limit: int = Query(default=20, ge=1, le=50),
    svc: MediaService = Depends(get_media_service),
) -> list[dict]:
    """Neueste Medien (Dashboard-Widget)."""
    logger.info("GET /media/recent: limit=%d.", limit)
    return await svc.get_recently_added(limit=limit)


@router.get(
    "/media/search",
    summary="Mediensuche",
    description="Durchsucht die gesamte Bibliothek (Filme + Serien).",
)
async def search_media(
    q: str = Query(..., min_length=2, description="Suchbegriff."),
    limit: int = Query(default=30, ge=1, le=100),
    svc: MediaService = Depends(get_media_service),
) -> dict:
    """Volltextsuche in der Medienbibliothek."""
    logger.info("GET /media/search: q=%r, limit=%d.", q, limit)
    return await svc.search(query=q, limit=limit)


@router.get(
    "/media/item/{item_id}",
    summary="Medien-Details",
    description="Detaillierte Metadaten zu einem einzelnen Medium.",
)
async def get_item_details(
    item_id: str,
    svc: MediaService = Depends(get_media_service),
) -> dict:
    """Einzel-Item mit allen verfügbaren Metadaten."""
    logger.debug("GET /media/item/%s.", item_id)
    try:
        return await svc.get_item_details(item_id)
    except Exception as exc:
        logger.error("Fehler bei Item-Details %s: %s", item_id, exc)
        raise HTTPException(
            status_code=404,
            detail=f"Medium mit ID {item_id!r} nicht gefunden.",
        ) from exc


@router.get(
    "/media/genres",
    summary="Genre-Liste",
    description="Alle verfügbaren Genres in der Bibliothek.",
)
async def get_genres(
    media_type: str = Query(default="Movie", pattern=r"^(Movie|Series|Audio)$"),
    svc: MediaService = Depends(get_media_service),
) -> list[str]:
    """Alphabetische Genre-Liste."""
    logger.debug("GET /media/genres: type=%s.", media_type)
    return await svc.get_genres(media_type=media_type)


@router.get(
    "/media/stats",
    summary="Bibliotheksstatistiken",
    description="Zähler für Filme, Serien, Episoden und Genres.",
)
async def get_library_stats(
    svc: MediaService = Depends(get_media_service),
) -> dict:
    """Bibliotheksstatistiken für das Dashboard."""
    logger.info("GET /media/stats.")
    return await svc.get_library_stats()


# ═══════════════════════════════════════════════════════════════════════════════
# Streaming-Proxy-Endpunkte (Binärdaten — direkte Jellyfin-Kapselung)
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/media/cover/{item_id}/{image_type}",
    summary="Cover-Bild (Proxy)",
    description="**Streaming-Proxy für Cover-Bilder.** Das Frontend ruft "
    "diesen Endpunkt auf. Das Backend holt das Bild von Jellyfin und "
    "leitet es als Response weiter. Die Jellyfin-URL wird NIEMALS zum "
    "Client exponiert.",
    responses={
        200: {
            "description": "Cover-Bild (JPEG/PNG).",
            "content": {"image/jpeg": {}, "image/png": {}},
        },
        404: {"description": "Kein Bild vorhanden."},
    },
)
async def get_cover_image(
    item_id: str,
    image_type: str = "Primary",
    width: int = Query(default=400, ge=100, le=1920, description="Maximale Breite in Pixel."),
    svc: MediaService = Depends(get_media_service),
) -> StreamingResponse:
    """
    Proxy-Endpunkt für Cover-Bilder.

    Das Bild wird als ``StreamingResponse`` mit dem korrekten
    ``Content-Type`` (image/jpeg oder image/png) ausgeliefert.

    **Sicherheit**: Dieser Endpunkt ist der EINZIGE Weg, wie das
    Frontend an Cover-Bilder kommt. Kein direkter Jellyfin-Zugriff.
    """
    logger.debug(
        "GET /media/cover/%s/%s (width=%d).",
        item_id,
        image_type,
        width,
    )

    try:
        image_bytes: bytes = await svc.get_cover_image(
            item_id=item_id,
            image_type=image_type,
            max_width=width,
        )

        # MIME-Type aus Magic Bytes erkennen
        media_type: str = "image/jpeg"
        if image_bytes.startswith(b"\x89PNG"):
            media_type = "image/png"
        elif image_bytes.startswith(b"GIF"):
            media_type = "image/gif"
        elif image_bytes.startswith(b"WEBP"):
            media_type = "image/webp"

        return StreamingResponse(
            iter([image_bytes]),
            media_type=media_type,
            headers={
                "Cache-Control": "public, max-age=86400",  # 24h Cache
                "Content-Length": str(len(image_bytes)),
            },
        )

    except Exception as exc:
        logger.warning(
            "Cover-Bild nicht gefunden: %s/%s — %s",
            item_id,
            image_type,
            exc,
        )
        raise HTTPException(
            status_code=404,
            detail=f"Kein {image_type}-Bild für Medium {item_id!r}.",
        ) from exc


@router.get(
    "/media/stream/{item_id}",
    summary="Video-Stream (Proxy)",
    description="**Streaming-Proxy für Video-Wiedergabe.** Das Frontend "
    "bettet die Stream-URL in ein HTML5-<video>-Tag ein. Das Backend "
    "holt den Videostream von Jellyfin und leitet ihn durch. Kein "
    "direkter Jellyfin-Zugriff vom Client.",
)
async def stream_media(
    item_id: str,
    direct: bool = Query(
        default=True,
        description="True = Direct Play, False = HLS-Transcoding.",
    ),
    max_bitrate: int = Query(
        default=40,
        ge=5,
        le=100,
        description="Maximale Bitrate in Mbps (nur bei Transcoding).",
    ),
    svc: MediaService = Depends(get_media_service),
) -> dict:
    """
    Video-Streaming-Endpunkt.

    Gibt eine ``stream_url`` zurück, die das Frontend in ein
    ``<video src="...">``-Tag einbetten kann. Die URL zeigt auf
    diesen Server (``/api/media/stream/{id}``), NICHT auf Jellyfin.

    **Streaming-Arten:**
    - ``direct=True`` → Direct Play (Originaldatei, kein Transcoding)
    - ``direct=False`` → HLS-Transcoding (adaptiv, max. Bitrate)

    Returns:
        Dict mit ``stream_url``, ``is_direct_play``, Codec-Infos.
    """
    logger.info(
        "GET /media/stream/%s: direct=%s, max_bitrate=%d Mbps.",
        item_id,
        direct,
        max_bitrate,
    )

    try:
        playback: dict = await svc.get_playback_info(
            item_id=item_id,
            prefer_direct_play=direct,
            max_bitrate_mbps=max_bitrate,
        )
        return playback

    except Exception as exc:
        logger.error(
            "Stream-Erstellung fehlgeschlagen für %s: %s", item_id, exc
        )
        raise HTTPException(
            status_code=500,
            detail=f"Stream konnte nicht erstellt werden: {exc}",
        ) from exc
