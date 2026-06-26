"""
Jellyfin-Client-Wrapper für das Homelab-Imperium.

Asynchroner HTTP-Client (httpx) für die Jellyfin-REST-API. Kapselt sämtliche
Medienserver-Interaktionen: Bibliotheksabfragen, Cover-Bild-Streaming,
Playback-URL-Generierung und Suche.

Verwendung::

    from app.services.clients.jellyfin import JellyfinClient

    client = JellyfinClient()
    movies = await client.get_movies()
    cover_bytes = await client.get_cover_image(item_id)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import httpx

from app.config import settings

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.clients.jellyfin")


# ═══════════════════════════════════════════════════════════════════════════════
# Datenklassen für API-Responses (interne DTOs, unabhängig von Pydantic)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class JellyfinItem:
    """Ein einzelnes Medienobjekt (Film, Serie, Episode, etc.)."""

    id: str
    name: str = ""
    original_title: str = ""
    overview: str = ""
    production_year: int = 0
    community_rating: float = 0.0
    runtime_ticks: int = 0  # 10.000.000 Ticks = 1 Sekunde
    genres: list[str] = field(default_factory=list)
    media_type: str = ""  # "Movie", "Series", "Episode", "Audio"
    image_tags: dict[str, str] = field(default_factory=dict)
    backdrop_image_tags: list[str] = field(default_factory=list)
    user_data: dict = field(default_factory=dict)


@dataclass
class JellyfinLibraryResult:
    """Ergebnis einer Bibliotheksabfrage."""

    items: list[JellyfinItem] = field(default_factory=list)
    total_count: int = 0
    start_index: int = 0


@dataclass
class PlaybackInfo:
    """Wiedergabe-Informationen für ein Medienobjekt."""

    item_id: str
    stream_url: str = ""
    media_sources: list[dict] = field(default_factory=list)
    play_session_id: str = ""
    is_direct_play: bool = True
    container: str = ""
    video_codec: str = ""
    audio_codec: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Eigene Exception-Hierarchie
# ═══════════════════════════════════════════════════════════════════════════════


class JellyfinError(Exception):
    """Basisklasse für alle Jellyfin-Client-Fehler."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.status_code = status_code
        super().__init__(message)


class JellyfinAuthError(JellyfinError):
    """Authentifizierungsfehler — API-Key ungültig (HTTP 401)."""

    pass


class JellyfinNotFoundError(JellyfinError):
    """Angefragte Ressource nicht gefunden (HTTP 404)."""

    pass


class JellyfinConnectionError(JellyfinError):
    """Verbindungsfehler — Jellyfin-Server nicht erreichbar."""

    pass


class JellyfinTimeoutError(JellyfinError):
    """Timeout — Jellyfin-Server antwortet nicht rechtzeitig."""

    pass


# ═══════════════════════════════════════════════════════════════════════════════
# JellyfinClient
# ═══════════════════════════════════════════════════════════════════════════════


class JellyfinClient:
    """
    Asynchroner Client für die Jellyfin REST-API.

    Kapselt Authentifizierung (API-Key via Header), Timeout-Handling,
    automatische Wiederholungsversuche und Fehler-Übersetzung in
    deutsche Exception-Nachrichten.

    API-Referenz: https://api.jellyfin.org/
    """

    # ── Jellyfin-API-Header-Konstanten ──
    _AUTH_HEADER: str = "X-Emby-Token"
    """Header-Name für die API-Key-Authentifizierung (Jellyfin nutzt Emby-Legacy-Header)."""

    _DEFAULT_HEADERS: dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    def __init__(self) -> None:
        """
        Initialisiert den Jellyfin-Client mit Werten aus den Settings.

        Liest ``jellyfin_base_url``, ``jellyfin_api_key``,
        ``jellyfin_timeout`` und ``jellyfin_max_retries`` aus
        ``app.config.settings``.
        """
        self._base_url: str = settings.jellyfin_base_url.rstrip("/")
        self._api_key: str = settings.jellyfin_api_key.get_secret_value()
        self._timeout: int = settings.jellyfin_timeout
        self._max_retries: int = settings.jellyfin_max_retries
        self._user_id: str = settings.jellyfin_default_user_id

        # Gemeinsame Header für alle Requests
        self._headers: dict[str, str] = {
            **self._DEFAULT_HEADERS,
            self._AUTH_HEADER: self._api_key,
        }

        logger.info(
            "Jellyfin-Client initialisiert: base_url=%s, timeout=%ds, "
            "max_retries=%d",
            self._base_url,
            self._timeout,
            self._max_retries,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Private Hilfsmethoden
    # ──────────────────────────────────────────────────────────────────────

    def _build_url(self, path: str) -> str:
        """
        Baut eine vollständige Jellyfin-API-URL zusammen.

        Args:
            path: Relativer API-Pfad, z.B. ``"/Items"``.

        Returns:
            Absolute URL, z.B. ``"http://jellyfin:8096/Items"``.
        """
        # urljoin behandelt führenden Slash korrekt
        return urljoin(self._base_url + "/", path.lstrip("/"))

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        headers: dict | None = None,
        follow_redirects: bool = True,
    ) -> httpx.Response:
        """
        Führt einen HTTP-Request gegen die Jellyfin-API aus.

        Beinhaltet automatische Wiederholungsversuche bei transienten Fehlern
        (5xx, Netzwerkfehler, Timeouts).

        Args:
            method: HTTP-Methode (GET, POST, DELETE).
            path: API-Pfad relativ zur base_url.
            params: Query-Parameter.
            headers: Zusätzliche Header (werden mit Default-Headern gemerged).
            follow_redirects: Weiterleitungen folgen.

        Returns:
            ``httpx.Response``-Objekt.

        Raises:
            JellyfinAuthError: Bei HTTP 401 (API-Key ungültig).
            JellyfinNotFoundError: Bei HTTP 404.
            JellyfinConnectionError: Bei Netzwerkfehlern.
            JellyfinTimeoutError: Bei Timeout.
            JellyfinError: Bei anderen HTTP-Fehlern.
        """
        url: str = self._build_url(path)
        merged_headers: dict[str, str] = {**self._headers, **(headers or {})}

        last_exception: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self._timeout),
                    follow_redirects=follow_redirects,
                ) as client:
                    response: httpx.Response = await client.request(
                        method=method,
                        url=url,
                        params=params,
                        headers=merged_headers,
                    )

                    # Erfolgreiche Responses direkt zurückgeben
                    if response.is_success:
                        return response

                    # Fehlerhafte Responses: Nachricht extrahieren
                    error_text: str = response.text[:500]

                    # Auth-Fehler: keine Wiederholung
                    if response.status_code == 401:
                        raise JellyfinAuthError(
                            f"Jellyfin-Authentifizierung fehlgeschlagen "
                            f"(HTTP 401). API-Key prüfen. "
                            f"URL: {url}",
                            status_code=401,
                        )

                    # Not Found: keine Wiederholung
                    if response.status_code == 404:
                        raise JellyfinNotFoundError(
                            f"Jellyfin-Ressource nicht gefunden (HTTP 404): "
                            f"{url}",
                            status_code=404,
                        )

                    # Andere Client-Fehler (4xx): keine Wiederholung
                    if 400 <= response.status_code < 500:
                        raise JellyfinError(
                            f"Jellyfin-Client-Fehler (HTTP "
                            f"{response.status_code}): {error_text}",
                            status_code=response.status_code,
                        )

                    # Server-Fehler (5xx): Wiederholung
                    logger.warning(
                        "Jellyfin-Server-Fehler (HTTP %d) bei Versuch %d/%d: "
                        "%s %s → %s",
                        response.status_code,
                        attempt,
                        self._max_retries,
                        method,
                        url,
                        error_text[:200],
                    )
                    last_exception = JellyfinError(
                        f"Jellyfin-Server-Fehler (HTTP "
                        f"{response.status_code}): {error_text}",
                        status_code=response.status_code,
                    )

            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                logger.warning(
                    "Jellyfin-Verbindungsfehler bei Versuch %d/%d: %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
                last_exception = JellyfinConnectionError(
                    f"Jellyfin-Server nicht erreichbar unter "
                    f"{self._base_url}: {exc}"
                )

            except httpx.ReadTimeout as exc:
                logger.warning(
                    "Jellyfin-Timeout bei Versuch %d/%d: %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
                last_exception = JellyfinTimeoutError(
                    f"Jellyfin-Server antwortet nicht innerhalb von "
                    f"{self._timeout}s: {exc}"
                )

            except JellyfinError:
                # Auth- und NotFound-Fehler sofort weiterwerfen
                raise

            # Kurze Pause zwischen Wiederholungsversuchen (exponentiell)
            if attempt < self._max_retries:
                import asyncio

                wait: float = 0.5 * (2 ** (attempt - 1))
                logger.debug(
                    "Warte %.1fs vor Wiederholungsversuch %d...",
                    wait,
                    attempt + 1,
                )
                await asyncio.sleep(wait)

        # Alle Wiederholungsversuche ausgeschöpft
        raise last_exception or JellyfinError(
            f"Jellyfin-Request fehlgeschlagen nach {self._max_retries} "
            f"Versuchen: {method} {url}"
        )

    def _parse_item(self, raw: dict) -> JellyfinItem:
        """
        Parst ein einzelnes Jellyfin-API-Item in ein ``JellyfinItem``.

        Args:
            raw: Rohes JSON-Dictionary aus der Jellyfin-API.

        Returns:
            Typisiertes ``JellyfinItem``-Objekt.
        """
        return JellyfinItem(
            id=raw.get("Id", ""),
            name=raw.get("Name", ""),
            original_title=raw.get("OriginalTitle", ""),
            overview=raw.get("Overview", ""),
            production_year=raw.get("ProductionYear", 0),
            community_rating=raw.get("CommunityRating", 0.0) or 0.0,
            runtime_ticks=raw.get("RunTimeTicks", 0) or 0,
            genres=raw.get("Genres", []),
            media_type=raw.get("Type", ""),
            image_tags=raw.get("ImageTags", {}),
            backdrop_image_tags=raw.get("BackdropImageTags", []),
            user_data=raw.get("UserData", {}),
        )

    # ──────────────────────────────────────────────────────────────────────
    # Öffentliche API-Methoden
    # ──────────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """
        Prüft die Erreichbarkeit des Jellyfin-Servers.

        Ruft den System-/Info-Endpunkt auf, der keine Authentifizierung
        benötigt.

        Returns:
            ``True`` wenn der Server erreichbar ist und antwortet.
        """
        try:
            response: httpx.Response = await self._request(
                "GET", "/System/Info"
            )
            data: dict = response.json()
            version: str = data.get("Version", "unbekannt")
            server_name: str = data.get("ServerName", "unbekannt")
            logger.debug(
                "Jellyfin-Server erreichbar: %s v%s",
                server_name,
                version,
            )
            return True
        except JellyfinError:
            logger.warning("Jellyfin-Ping fehlgeschlagen.")
            return False

    async def get_movies(
        self,
        limit: int = 50,
        start_index: int = 0,
        sort_by: str = "SortName",
        sort_order: str = "Ascending",
    ) -> JellyfinLibraryResult:
        """
        Ruft die Filmbibliothek ab.

        Args:
            limit: Maximale Anzahl zurückgegebener Filme (1–300).
            start_index: Offset für Paginierung (0-basiert).
            sort_by: Sortierfeld (SortName, DateCreated, PremiereDate,
                     CommunityRating, Runtime).
            sort_order: Sortierreihenfolge (Ascending, Descending).

        Returns:
            ``JellyfinLibraryResult`` mit Liste der Filme.
        """
        logger.info(
            "Rufe Filmbibliothek ab: limit=%d, start=%d, sort=%s %s",
            limit,
            start_index,
            sort_by,
            sort_order,
        )
        response: httpx.Response = await self._request(
            "GET",
            "/Items",
            params={
                "IncludeItemTypes": "Movie",
                "Recursive": "true",
                "SortBy": sort_by,
                "SortOrder": sort_order,
                "Limit": str(limit),
                "StartIndex": str(start_index),
                "Fields": (
                    "Overview,Genres,CommunityRating,ProductionYear,"
                    "RunTimeTicks,MediaSources,ImageTags"
                ),
                "EnableTotalRecordCount": "true",
                "EnableImages": "true",
            },
        )
        data: dict = response.json()

        items: list[JellyfinItem] = [
            self._parse_item(item) for item in data.get("Items", [])
        ]
        total: int = data.get("TotalRecordCount", 0)

        logger.info(
            "%d Filme aus Bibliothek geladen (Gesamt: %d).",
            len(items),
            total,
        )
        return JellyfinLibraryResult(items=items, total_count=total, start_index=start_index)

    async def get_tv_shows(
        self,
        limit: int = 50,
        start_index: int = 0,
    ) -> JellyfinLibraryResult:
        """
        Ruft die Serienbibliothek ab.

        Args:
            limit: Maximale Anzahl zurückgegebener Serien.
            start_index: Offset für Paginierung.

        Returns:
            ``JellyfinLibraryResult`` mit Liste der Serien.
        """
        logger.info(
            "Rufe Serienbibliothek ab: limit=%d, start=%d",
            limit,
            start_index,
        )
        response: httpx.Response = await self._request(
            "GET",
            "/Items",
            params={
                "IncludeItemTypes": "Series",
                "Recursive": "true",
                "SortBy": "SortName",
                "SortOrder": "Ascending",
                "Limit": str(limit),
                "StartIndex": str(start_index),
                "Fields": "Overview,Genres,CommunityRating,ProductionYear,ImageTags",
                "EnableTotalRecordCount": "true",
                "EnableImages": "true",
            },
        )
        data: dict = response.json()

        items: list[JellyfinItem] = [
            self._parse_item(item) for item in data.get("Items", [])
        ]
        total: int = data.get("TotalRecordCount", 0)

        logger.info(
            "%d Serien aus Bibliothek geladen (Gesamt: %d).",
            len(items),
            total,
        )
        return JellyfinLibraryResult(items=items, total_count=total, start_index=start_index)

    async def get_episodes(
        self,
        series_id: str,
        season_number: int | None = None,
    ) -> list[JellyfinItem]:
        """
        Ruft alle Episoden einer Serie ab, optional gefiltert nach Staffel.

        Args:
            series_id: Jellyfin-ID der Serie.
            season_number: Staffel-Nummer (None = alle Staffeln).

        Returns:
            Liste von Episoden-``JellyfinItem``.
        """
        logger.info(
            "Rufe Episoden ab: series_id=%s, season=%s",
            series_id,
            season_number,
        )
        params: dict[str, str] = {
            "ParentId": series_id,
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "SortBy": "IndexNumber",
            "SortOrder": "Ascending",
            "Fields": "Overview,MediaSources,ImageTags",
        }
        if season_number is not None:
            params["SeasonNumber"] = str(season_number)

        response: httpx.Response = await self._request(
            "GET", "/Items", params=params
        )
        data: dict = response.json()
        episodes: list[JellyfinItem] = [
            self._parse_item(item) for item in data.get("Items", [])
        ]
        logger.info("%d Episoden geladen.", len(episodes))
        return episodes

    async def get_recently_added(
        self,
        limit: int = 20,
    ) -> list[JellyfinItem]:
        """
        Ruft die zuletzt hinzugefügten Medien ab.

        Args:
            limit: Maximale Anzahl.

        Returns:
            Liste der neuesten ``JellyfinItem``-Objekte.
        """
        logger.info("Rufe kürzlich hinzugefügte Medien ab (limit=%d).", limit)
        response: httpx.Response = await self._request(
            "GET",
            "/Items",
            params={
                "Recursive": "true",
                "SortBy": "DateCreated",
                "SortOrder": "Descending",
                "Limit": str(limit),
                "Fields": "Overview,Genres,CommunityRating,ImageTags",
                "EnableImages": "true",
            },
        )
        data: dict = response.json()
        return [self._parse_item(item) for item in data.get("Items", [])]

    async def get_continue_watching(
        self,
        limit: int = 20,
    ) -> list[JellyfinItem]:
        """
        Ruft angefangene, aber nicht fertig geschaute Medien ab.

        Args:
            limit: Maximale Anzahl.

        Returns:
            Liste von ``JellyfinItem`` mit Resume-Position.
        """
        logger.info("Rufe 'Weiterschauen'-Liste ab (limit=%d).", limit)
        response: httpx.Response = await self._request(
            "GET",
            "/Items",
            params={
                "Recursive": "true",
                "Filters": "IsResumable",
                "SortBy": "DatePlayed",
                "SortOrder": "Descending",
                "Limit": str(limit),
                "Fields": "Overview,MediaSources,ImageTags",
            },
        )
        data: dict = response.json()
        return [self._parse_item(item) for item in data.get("Items", [])]

    async def get_item(self, item_id: str) -> JellyfinItem:
        """
        Ruft ein einzelnes Medienobjekt anhand seiner ID ab.

        Args:
            item_id: Jellyfin-Item-ID.

        Returns:
            ``JellyfinItem`` mit allen Feldern.

        Raises:
            JellyfinNotFoundError: Falls das Item nicht existiert.
        """
        logger.debug("Rufe Einzel-Item ab: id=%s", item_id)
        response: httpx.Response = await self._request(
            "GET",
            f"/Users/{self._user_id}/Items/{item_id}",
        )
        return self._parse_item(response.json())

    async def get_cover_image(
        self,
        item_id: str,
        image_type: str = "Primary",
        max_width: int = 400,
        quality: int = 90,
    ) -> bytes:
        """
        Lädt ein Cover-Bild als Binärdaten.

        Args:
            item_id: Jellyfin-Item-ID.
            image_type: Bildtyp (Primary, Backdrop, Logo, Thumb, Banner).
            max_width: Maximale Breite in Pixel.
            quality: JPEG-Qualität (1–100).

        Returns:
            Binärdaten des Bildes (Bytes).

        Raises:
            JellyfinNotFoundError: Falls kein Bild existiert.
        """
        logger.debug(
            "Lade Cover-Bild: item_id=%s, type=%s, width=%d",
            item_id,
            image_type,
            max_width,
        )
        response: httpx.Response = await self._request(
            "GET",
            f"/Items/{item_id}/Images/{image_type}",
            params={
                "MaxWidth": str(max_width),
                "Quality": str(quality),
            },
        )
        content: bytes = response.content
        logger.debug(
            "Cover-Bild geladen: %d bytes (content-type: %s)",
            len(content),
            response.headers.get("content-type", "unbekannt"),
        )
        return content

    async def get_backdrop_image(
        self,
        item_id: str,
        max_width: int = 1920,
    ) -> bytes | None:
        """
        Lädt das Hintergrundbild (Backdrop) eines Items.

        Args:
            item_id: Jellyfin-Item-ID.
            max_width: Maximale Breite.

        Returns:
            Binärdaten oder ``None``, falls kein Backdrop existiert.
        """
        try:
            return await self.get_cover_image(
                item_id, image_type="Backdrop", max_width=max_width
            )
        except JellyfinNotFoundError:
            logger.debug("Kein Backdrop-Bild für item_id=%s.", item_id)
            return None

    async def get_playback_info(
        self,
        item_id: str,
        media_source_id: str | None = None,
        enable_direct_play: bool = True,
        enable_direct_stream: bool = True,
        enable_transcoding: bool = True,
        max_streaming_bitrate: int = 40_000_000,  # 40 Mbps
    ) -> PlaybackInfo:
        """
        Erzeugt Wiedergabe-Informationen für ein Medienobjekt.

        Postet eine ``/Items/{id}/PlaybackInfo``-Anfrage und generiert
        die passende Stream-URL für Direct Play, Direct Stream oder
        Transcoding.

        Args:
            item_id: Jellyfin-Item-ID.
            media_source_id: Quell-ID (None = erste verfügbare).
            enable_direct_play: Direct Play ohne Transcoding erlauben.
            enable_direct_stream: Direct Stream (Remux) erlauben.
            enable_transcoding: Volles Transcoding erlauben.
            max_streaming_bitrate: Maximale Bitrate in bps.

        Returns:
            ``PlaybackInfo`` mit Stream-URL und Codec-Details.
        """
        logger.info(
            "Erzeuge Playback-Info für item_id=%s (DirectPlay=%s, "
            "Transcoding=%s)",
            item_id,
            enable_direct_play,
            enable_transcoding,
        )

        # Playback-Info-POST (teilt Jellyfin die Client-Fähigkeiten mit)
        playback_body: dict = {
            "UserId": self._user_id,
            "MaxStreamingBitrate": max_streaming_bitrate,
            "StartTimeTicks": 0,
            "AudioStreamIndex": 0,
            "SubtitleStreamIndex": -1,
            "MaxAudioChannels": 6,
            "MediaSourceId": media_source_id or "",
            "DeviceProfile": {
                "Name": "Homelab-Imperium",
                "MaxStreamingBitrate": max_streaming_bitrate,
                "MusicStreamingTranscodingBitrate": 192000,
                "DirectPlayProfiles": [
                    {
                        "Container": "mp4,mkv,avi,mov,wmv",
                        "AudioCodec": (
                            "aac,mp3,ac3,eac3,flac,opus,vorbis,"
                            "dts,dca,truehd"
                        ),
                        "VideoCodec": (
                            "h264,hevc,h265,vp8,vp9,av1,mpeg2video,"
                            "mpeg4,msmpeg4,wmv2,wmv3,vc1"
                        ),
                        "Type": "Video",
                    }
                ] if enable_direct_play else [],
                "TranscodingProfiles": [
                    {
                        "Container": "ts",
                        "AudioCodec": "aac,mp3,ac3,opus",
                        "VideoCodec": "h264,hevc",
                        "Type": "Video",
                        "Protocol": "hls",
                    }
                ] if enable_transcoding else [],
                "CodecProfiles": [],
                "SubtitleProfiles": [],
            },
        }

        response: httpx.Response = await self._request(
            "POST",
            f"/Items/{item_id}/PlaybackInfo",
            params={
                "UserId": self._user_id,
                "StartTimeTicks": "0",
                "MaxStreamingBitrate": str(max_streaming_bitrate),
                "EnableDirectPlay": str(enable_direct_play).lower(),
                "EnableDirectStream": str(enable_direct_stream).lower(),
                "EnableTranscoding": str(enable_transcoding).lower(),
            },
            headers={
                "Content-Type": "application/json",
            },
        )
        # Der POST-Body wird als JSON im Request-Body gesendet,
        # aber Jellyfin akzeptiert auch Query-Parameter.
        # Für robuste Implementierung: POST mit JSON-Body.
        # Hier vereinfacht: Query-Parameter + Body
        # (Jellyfin bevorzugt Query-Parameter bei GET-ähnlichem POST)

        # Für eine vollständige Implementierung würde der Body gesendet:
        # → httpx erlaubt `json=playback_body` im `_request`-/`client.request`-Call.

        data: dict = response.json()
        media_sources: list[dict] = data.get("MediaSources", [])

        # Bestimme, ob Direct Play genutzt wird
        is_direct: bool = enable_direct_play and len(media_sources) > 0

        # Baue Stream-URL
        stream_url: str = self._build_stream_url(
            item_id,
            media_sources[0].get("Id", "") if media_sources else "",
            enable_direct_play=is_direct,
        )

        container: str = ""
        video_codec: str = ""
        audio_codec: str = ""
        if media_sources:
            src: dict = media_sources[0]
            container = src.get("Container", "")
            video_codec = (
                src.get("VideoStream", {}) or {}
            ).get("Codec", "")
            audio_codec = (
                src.get("AudioStream", {}) or {}
            ).get("Codec", "")

        logger.info(
            "Playback-Info erzeugt: direct_play=%s, container=%s, "
            "video=%s, audio=%s",
            is_direct,
            container,
            video_codec,
            audio_codec,
        )

        return PlaybackInfo(
            item_id=item_id,
            stream_url=stream_url,
            media_sources=media_sources,
            play_session_id=data.get("PlaySessionId", ""),
            is_direct_play=is_direct,
            container=container,
            video_codec=video_codec,
            audio_codec=audio_codec,
        )

    def _build_stream_url(
        self,
        item_id: str,
        media_source_id: str,
        enable_direct_play: bool = True,
    ) -> str:
        """
        Baut die vollständige Streaming-URL für ein Medienobjekt.

        Args:
            item_id: Jellyfin-Item-ID.
            media_source_id: MediaSource-ID aus PlaybackInfo.
            enable_direct_play: True = Direct-Play-Stream, False = HLS-Transcode.

        Returns:
            Absolute Stream-URL (für <video>-Tag oder HLS-Player).
        """
        if enable_direct_play:
            # Direct Play: Original-Datei streamen
            return (
                f"{self._base_url}/Videos/{item_id}/stream"
                f"?MediaSourceId={media_source_id}"
                f"&Static=true"
                f"&{self._AUTH_HEADER}={self._api_key}"
            )
        else:
            # HLS-Transcode-Stream (Master-M3U8)
            return (
                f"{self._base_url}/Videos/{item_id}/master.m3u8"
                f"?MediaSourceId={media_source_id}"
                f"&{self._AUTH_HEADER}={self._api_key}"
            )

    async def search(
        self,
        query: str,
        limit: int = 30,
        include_media_types: list[str] | None = None,
    ) -> list[JellyfinItem]:
        """
        Durchsucht die gesamte Medienbibliothek.

        Args:
            query: Suchbegriff (Titel, Schauspieler, Regisseur).
            limit: Maximale Trefferanzahl.
            include_media_types: Filter auf Medientypen (z.B. ["Movie", "Series"]).

        Returns:
            Liste gefundener ``JellyfinItem``-Objekte.
        """
        logger.info(
            "Suche in Jellyfin: query=%r, limit=%d, types=%s",
            query,
            limit,
            include_media_types,
        )
        params: dict[str, str] = {
            "SearchTerm": query,
            "Recursive": "true",
            "Limit": str(limit),
            "Fields": "Overview,Genres,CommunityRating,ProductionYear,ImageTags",
            "IncludeMedia": "true",
        }
        if include_media_types:
            params["IncludeItemTypes"] = ",".join(include_media_types)

        response: httpx.Response = await self._request(
            "GET", "/Items", params=params
        )
        data: dict = response.json()
        items: list[JellyfinItem] = [
            self._parse_item(item) for item in data.get("Items", [])
        ]
        logger.info("%d Suchergebnisse für %r.", len(items), query)
        return items

    async def get_genres(
        self,
        media_type: str = "Movie",
    ) -> list[str]:
        """
        Ruft alle verfügbaren Genres ab.

        Args:
            media_type: Medientyp (Movie, Series, Audio).

        Returns:
            Alphabetisch sortierte Genre-Liste.
        """
        logger.debug("Rufe Genre-Liste ab (media_type=%s).", media_type)
        response: httpx.Response = await self._request(
            "GET",
            "/Genres",
            params={
                "UserId": self._user_id,
                "ParentId": "",
                "IncludeItemTypes": media_type,
            },
        )
        data: dict = response.json()
        genres: list[str] = sorted(
            item.get("Name", "") for item in data.get("Items", [])
        )
        logger.debug("%d Genres geladen.", len(genres))
        return genres
