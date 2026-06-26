"""
Medien-Dienst des Homelab-Imperiums.

Transformiert rohe Jellyfin-API-Daten in einheitliche, frontend-freundliche
Datenstrukturen. Implementiert:
- Bibliotheksabfragen (Filme, Serien, zuletzt hinzugefügt, weiterschauen)
- Cover-Bild-URL-Generierung (proxied via Backend-API)
- Optimale Stream-URL-Auswahl (Direct Play vs. Transcoding)
- Metadaten-Aggregation (Codecs, Sprachen, Untertitel)

Verwendung::

    from app.services.media import MediaService

    svc = MediaService()
    movies = await svc.get_movies(limit=30)
    stream_info = await svc.get_playback_info(item_id)
"""

from __future__ import annotations

import logging
from typing import Optional

from app.services.clients.jellyfin import (
    JellyfinClient,
    JellyfinItem,
    PlaybackInfo,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.services.media")


# ═══════════════════════════════════════════════════════════════════════════════
# Medien-Dienstprogramm (statische Hilfsfunktionen)
# ═══════════════════════════════════════════════════════════════════════════════


class MediaFormatUtils:
    """Statische Hilfsfunktionen zur Medien-Metadaten-Transformation."""

    @staticmethod
    def ticks_to_seconds(ticks: int) -> int:
        """
        Wandelt Jellyfin-Ticks in Sekunden um.

        10.000.000 Ticks = 1 Sekunde (100-Nanosekunden-Intervalle).
        """
        return ticks // 10_000_000 if ticks else 0

    @staticmethod
    def seconds_to_display(seconds: int) -> str:
        """
        Wandelt Sekunden in menschenlesbare Dauer um.

        Args:
            seconds: Dauer in Sekunden.

        Returns:
            Formatierter String: ``"2h 14m"`` oder ``"45m"``.
        """
        if seconds <= 0:
            return "?"

        hours: int = seconds // 3600
        minutes: int = (seconds % 3600) // 60

        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    @staticmethod
    def rating_to_stars(rating: float) -> str:
        """
        Wandelt eine numerische Bewertung (0–10) in Sterne um.

        Jellyfin speichert Community-Rating als 0–10, das Frontend
        zeigt 0–5 Sterne an.

        Returns:
            Sterne-String wie ``"★★★★½"`` (4,2 von 5).
        """
        if rating <= 0:
            return "☆"

        stars_5: float = rating / 2.0  # 0–10 → 0–5
        full: int = int(stars_5)
        half: bool = (stars_5 - full) >= 0.3
        return "★" * full + ("½" if half else "") + "☆" * max(
            0, 5 - full - (1 if half else 0)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# MediaService
# ═══════════════════════════════════════════════════════════════════════════════


class MediaService:
    """
    Geschäftslogik für die Medienverwaltung.

    Agiert als Vermittler zwischen dem Jellyfin-Client und dem Frontend.
    Transformiert Jellyfin-spezifische Datenstrukturen in das
    einheitliche HomeOS-Medienformat.
    """

    def __init__(self) -> None:
        """Initialisiert den Jellyfin-Client."""
        self._client: JellyfinClient = JellyfinClient()
        self._utils: type[MediaFormatUtils] = MediaFormatUtils
        logger.info("MediaService initialisiert.")

    # ──────────────────────────────────────────────────────────────────────
    # Transformation: JellyfinItem → Frontend-Dict
    # ──────────────────────────────────────────────────────────────────────

    def _transform_item(self, item: JellyfinItem) -> dict:
        """
        Transformiert ein ``JellyfinItem`` in ein frontend-freundliches Dict.

        Args:
            item: Rohes Jellyfin-Item.

        Returns:
            Dictionary im HomeOS-Medienformat.
        """
        runtime_s: int = self._utils.ticks_to_seconds(item.runtime_ticks)
        has_image: bool = "Primary" in item.image_tags

        transformed: dict = {
            "id": item.id,
            "title": item.name,
            "type": item.media_type.lower(),
            "year": item.production_year or None,
            "overview": item.overview or "",
            "genres": item.genres,
            "rating": round(item.community_rating, 1),
            "rating_stars": self._utils.rating_to_stars(
                item.community_rating
            ),
            "runtime_seconds": runtime_s,
            "runtime_display": self._utils.seconds_to_display(runtime_s),
            # Cover-Bild-URLs (proxied via Backend — niemals direkt zu Jellyfin!)
            "cover_url": (
                f"/api/media/cover/{item.id}/Primary"
                if has_image
                else None
            ),
            "backdrop_url": (
                f"/api/media/cover/{item.id}/Backdrop"
                if item.backdrop_image_tags
                else None
            ),
            "logo_url": (
                f"/api/media/cover/{item.id}/Logo"
                if "Logo" in item.image_tags
                else None
            ),
            # Resume-Daten (für "Weiterschauen")
            "is_played": item.user_data.get("Played", False),
            "playback_position_ticks": item.user_data.get(
                "PlaybackPositionTicks", 0
            ),
            "playback_position_seconds": self._utils.ticks_to_seconds(
                item.user_data.get("PlaybackPositionTicks", 0)
            ),
            "play_count": item.user_data.get("PlayCount", 0),
            "is_favorite": item.user_data.get("IsFavorite", False),
        }

        return transformed

    def _transform_episode(self, item: JellyfinItem) -> dict:
        """
        Transformiert ein Episoden-Item mit zusätzlichen Serien-Metadaten.

        Args:
            item: Rohes Jellyfin-Episoden-Item.

        Returns:
            Dictionary mit Episode-spezifischen Feldern.
        """
        base: dict = self._transform_item(item)
        base["episode_title"] = item.name
        base["series_name"] = item.original_title or item.name
        return base

    # ──────────────────────────────────────────────────────────────────────
    # Bibliotheksabfragen (öffentliche API)
    # ──────────────────────────────────────────────────────────────────────

    async def get_movies(
        self,
        limit: int = 50,
        start_index: int = 0,
        sort_by: str = "SortName",
        sort_order: str = "Ascending",
    ) -> dict:
        """
        Ruft die Filmbibliothek ab — transformiert für das Frontend.

        Args:
            limit: Maximale Anzahl Filme.
            start_index: Offset für Paginierung.
            sort_by: Sortierfeld (SortName, DateCreated, CommunityRating, …).
            sort_order: Ascending / Descending.

        Returns:
            Dict mit ``items`` (Liste), ``total`` (Gesamtanzahl),
            ``start_index`` und ``limit``.
        """
        logger.info(
            "MediaService: Rufe Filme ab (limit=%d, offset=%d, "
            "sort=%s %s).",
            limit,
            start_index,
            sort_by,
            sort_order,
        )
        result = await self._client.get_movies(
            limit=limit,
            start_index=start_index,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        items: list[dict] = [
            self._transform_item(item) for item in result.items
        ]

        return {
            "items": items,
            "total": result.total_count,
            "start_index": result.start_index,
            "limit": limit,
        }

    async def get_tv_shows(
        self,
        limit: int = 50,
        start_index: int = 0,
    ) -> dict:
        """
        Ruft die Serienbibliothek ab.

        Returns:
            Dict mit Serien-Items und Paginierungs-Metadaten.
        """
        logger.info(
            "MediaService: Rufe Serien ab (limit=%d, offset=%d).",
            limit,
            start_index,
        )
        result = await self._client.get_tv_shows(
            limit=limit, start_index=start_index
        )

        items: list[dict] = [
            self._transform_item(item) for item in result.items
        ]

        return {
            "items": items,
            "total": result.total_count,
            "start_index": result.start_index,
            "limit": limit,
        }

    async def get_episodes(
        self,
        series_id: str,
        season_number: int | None = None,
    ) -> list[dict]:
        """
        Ruft alle Episoden einer Serie ab.

        Args:
            series_id: Jellyfin-Serien-ID.
            season_number: Staffel-Nummer (None = alle).

        Returns:
            Liste von Episoden-Dicts mit Serien-Metadaten.
        """
        logger.info(
            "MediaService: Rufe Episoden ab (series=%s, season=%s).",
            series_id,
            season_number,
        )
        episodes = await self._client.get_episodes(
            series_id=series_id,
            season_number=season_number,
        )
        return [self._transform_episode(ep) for ep in episodes]

    async def get_recently_added(self, limit: int = 20) -> list[dict]:
        """
        Ruft die zuletzt hinzugefügten Medien ab (Dashboard-Widget).

        Returns:
            Liste der neuesten Medien-Items (Filme + Serien gemischt).
        """
        logger.info(
            "MediaService: Rufe kürzlich hinzugefügte Medien ab "
            "(limit=%d).",
            limit,
        )
        items = await self._client.get_recently_added(limit=limit)
        return [self._transform_item(item) for item in items]

    async def get_continue_watching(self, limit: int = 20) -> list[dict]:
        """
        Ruft die "Weiterschauen"-Liste ab.

        Enthält Resume-Position für jedes angefangene Medium.

        Returns:
            Liste von Medien-Items mit ``playback_position_seconds``.
        """
        logger.info(
            "MediaService: Rufe 'Weiterschauen' ab (limit=%d).", limit
        )
        items = await self._client.get_continue_watching(limit=limit)
        return [self._transform_item(item) for item in items]

    async def search(self, query: str, limit: int = 30) -> dict:
        """
        Durchsucht die gesamte Bibliothek.

        Args:
            query: Suchbegriff.
            limit: Maximale Trefferanzahl.

        Returns:
            Dict mit ``items`` und Such-Metadaten.
        """
        logger.info("MediaService: Suche nach %r (limit=%d).", query, limit)
        items = await self._client.search(
            query=query,
            limit=limit,
        )
        return {
            "query": query,
            "items": [self._transform_item(item) for item in items],
            "total": len(items),
        }

    async def get_library_stats(self) -> dict:
        """
        Sammelt Statistiken über die gesamte Medienbibliothek.

        Returns:
            Dict mit Zählern für Movies, Series, Episodes, Genres.
        """
        logger.info("MediaService: Sammle Bibliotheksstatistiken...")
        movies = await self._client.get_movies(limit=1)
        series = await self._client.get_tv_shows(limit=1)
        genres: list[str] = await self._client.get_genres()

        return {
            "total_movies": movies.total_count,
            "total_series": series.total_count,
            "total_genres": len(genres),
            "genres": genres,
        }

    # ──────────────────────────────────────────────────────────────────────
    # Cover-Bild-Proxy
    # ──────────────────────────────────────────────────────────────────────

    async def get_cover_image(
        self,
        item_id: str,
        image_type: str = "Primary",
        max_width: int = 400,
    ) -> bytes:
        """
        Lädt ein Cover-Bild als Binärdaten (Proxy für das Frontend).

        Das Frontend ruft ``/api/media/cover/{item_id}/{type}`` auf,
        was diesen Service durchläuft, der wiederum das Bild von
        Jellyfin holt und als Response zurückschickt. So wird die
        Jellyfin-URL niemals zum Client exponiert.

        Args:
            item_id: Jellyfin-Item-ID.
            image_type: Primary, Backdrop, Logo, Thumb, Banner.
            max_width: Maximale Breite in Pixel.

        Returns:
            Binärdaten des Bildes (JPEG/PNG).
        """
        logger.debug(
            "MediaService: Lade Cover %s/%s (width=%d).",
            item_id,
            image_type,
            max_width,
        )
        return await self._client.get_cover_image(
            item_id=item_id,
            image_type=image_type,
            max_width=max_width,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Playback / Streaming
    # ──────────────────────────────────────────────────────────────────────

    async def get_playback_info(
        self,
        item_id: str,
        prefer_direct_play: bool = True,
        max_bitrate_mbps: int = 40,
    ) -> dict:
        """
        Ermittelt die optimale Wiedergabe-Strategie für ein Medium.

        Entscheidet basierend auf Codec-Kompatibilität und Bitrate, ob
        Direct Play, Direct Stream (Remux) oder volles Transcoding
        verwendet wird.

        Args:
            item_id: Jellyfin-Item-ID.
            prefer_direct_play: ``True`` = Direct Play bevorzugen.
            max_bitrate_mbps: Maximale Bitrate in Mbps.

        Returns:
            Dict mit ``stream_url``, ``is_direct_play``, Codec-Infos
            und Playback-Metadaten für das Frontend.
        """
        logger.info(
            "MediaService: Ermittle Playback-Info für item=%s "
            "(direct_play=%s, max_bitrate=%d Mbps).",
            item_id,
            prefer_direct_play,
            max_bitrate_mbps,
        )

        max_bitrate_bps: int = max_bitrate_mbps * 1_000_000

        info: PlaybackInfo = await self._client.get_playback_info(
            item_id=item_id,
            enable_direct_play=prefer_direct_play,
            enable_direct_stream=True,
            enable_transcoding=not prefer_direct_play,
            max_streaming_bitrate=max_bitrate_bps,
        )

        # Bestimme die optimale Stream-URL
        stream_url: str = self._select_optimal_stream_url(
            info, prefer_direct_play
        )

        # Baue das Frontend-freundliche Playback-Dict
        playback: dict = {
            "item_id": item_id,
            "stream_url": stream_url,
            "is_direct_play": info.is_direct_play,
            "container": info.container,
            "video_codec": info.video_codec,
            "audio_codec": info.audio_codec,
            "play_session_id": info.play_session_id,
            "media_sources_count": len(info.media_sources),
            # Zusätzliche Metadaten für die Anzeige im Player
            "playback_method": (
                "Direct Play"
                if info.is_direct_play
                else "Transcoding (HLS)"
            ),
            "quality_hint": (
                "Originalqualität"
                if info.is_direct_play
                else f"Adaptiv (max {max_bitrate_mbps} Mbps)"
            ),
        }

        logger.info(
            "Playback-Methode: %s (container=%s, video=%s, audio=%s).",
            playback["playback_method"],
            info.container,
            info.video_codec,
            info.audio_codec,
        )
        return playback

    def _select_optimal_stream_url(
        self,
        info: PlaybackInfo,
        prefer_direct: bool,
    ) -> str:
        """
        Wählt die optimale Stream-URL basierend auf den Playback-Infos.

        Strategie:
        1. Direct Play bevorzugt → ``stream_url`` aus PlaybackInfo
           (ist bereits die richtige, vom Jellyfin-Client generierte URL).
        2. Kein Direct Play → HLS-Master-M3U8.
        3. Keine Media-Sources → Fallback-URL.

        Args:
            info: PlaybackInfo vom Jellyfin-Client.
            prefer_direct: Direct-Play-Präferenz.

        Returns:
            Optimierte Stream-URL für den HTML5-Player.
        """
        # info.stream_url ist bereits korrekt (vom JellyfinClient generiert)
        if info.stream_url:
            return info.stream_url

        # Fallback: Konstruiere URL aus Media-Source
        if info.media_sources:
            source: dict = info.media_sources[0]
            source_id: str = source.get("Id", "")
            container: str = source.get("Container", "mp4")

            if prefer_direct and container in (
                "mp4",
                "mkv",
                "webm",
                "avi",
                "mov",
            ):
                # Direct Play URL
                return (
                    f"/api/media/stream/{info.item_id}"
                    f"?source_id={source_id}"
                    f"&direct=true"
                )
            else:
                # HLS Transcode URL
                return (
                    f"/api/media/stream/{info.item_id}"
                    f"?source_id={source_id}"
                    f"&direct=false"
                )

        # Letzter Fallback
        return f"/api/media/stream/{info.item_id}"

    async def get_item_details(self, item_id: str) -> dict:
        """
        Ruft detaillierte Metadaten zu einem einzelnen Medium ab.

        Args:
            item_id: Jellyfin-Item-ID.

        Returns:
            Angereichertes Dict mit allen verfügbaren Metadaten.
        """
        logger.debug("MediaService: Rufe Item-Details ab: %s", item_id)
        item: JellyfinItem = await self._client.get_item(item_id)
        return self._transform_item(item)

    async def get_genres(self, media_type: str = "Movie") -> list[str]:
        """
        Liefert alle verfügbaren Genres.

        Args:
            media_type: Movie, Series, Audio.

        Returns:
            Alphabetisch sortierte Genre-Liste.
        """
        return await self._client.get_genres(media_type=media_type)
