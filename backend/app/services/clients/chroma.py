"""
ChromaDB-Client-Wrapper für das Homelab-Imperium.

Kapselt die offizielle ChromaDB-Python-SDK (``chromadb``) für Vektoroperationen:
Collection-Management, Embedding-Speicherung, semantische Suche und Bulk-Import.

Verwendung::

    from app.services.clients.chroma import ChromaDBClient

    client = ChromaDBClient()
    await client.ensure_collection("school_pdfs")
    results = await client.query_by_text("school_pdfs", "Was ist SQL?")
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.types import (
    Embedding,
    Embeddings,
    Metadata,
    Metadatas,
    QueryResult,
)
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import (
    ChromaError,
    InvalidDimensionException,
    NotEnoughElementsException,
)

from app.config import settings

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.clients.chroma")


# ═══════════════════════════════════════════════════════════════════════════════
# Datenklassen für Query-Ergebnisse
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ChromaQueryHit:
    """
    Ein einzelner Treffer einer semantischen Suche.
    """

    id: str
    document: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    distance: float = 0.0
    score: float = 0.0  # 1.0 = perfekte Übereinstimmung (cosine similarity)


@dataclass
class ChromaQueryResult:
    """
    Ergebnis einer semantischen Suche.
    """

    hits: list[ChromaQueryHit] = field(default_factory=list)
    query_time_ms: float = 0.0
    collection_name: str = ""
    total_in_collection: int = 0


@dataclass
class CollectionInfo:
    """Metadaten einer ChromaDB-Collection."""

    name: str
    count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# ChromaDBClient
# ═══════════════════════════════════════════════════════════════════════════════


class ChromaDBClient:
    """
    Client-Wrapper für die ChromaDB-Vektordatenbank.

    Nutzt das offizielle ``chromadb``-SDK mit ``HttpClient`` für die
    Verbindung zur Docker-basierten ChromaDB-Instanz.

    Features:
    - Collection-Lebenszyklus (create, get, delete, list)
    - Bulk-Embedding-Import mit Batches
    - Semantische Suche (via Embedding-Vektor oder Rohtext)
    - Automatische Fehlertoleranz mit Retry-Logik
    - Deutsches Logging aller Operationen
    """

    # Maximale Batch-Größe für Bulk-Inserts (verhindert OOM)
    _MAX_BATCH_SIZE: int = 500

    # Maximale Wiederholungsversuche bei transienten Fehlern
    _MAX_RETRIES: int = 3

    def __init__(self) -> None:
        """
        Initialisiert die Verbindung zur ChromaDB-Instanz.

        Liest ``chromadb_endpoint`` aus ``app.config.settings``.
        """
        self._endpoint: str = settings.chromadb_endpoint
        self._embedding_dim: int = settings.chromadb_embedding_dimension
        self._default_top_k: int = settings.chromadb_default_top_k

        # ChromaDB HttpClient (synchrone API)
        self._client: ClientAPI = chromadb.HttpClient(
            host=self._extract_host(self._endpoint),
            port=self._extract_port(self._endpoint),
            settings=ChromaSettings(
                anonymized_telemetry=False,
                allow_reset=False,  # Kein Reset im Produktivbetrieb
            ),
        )
        logger.info(
            "ChromaDB-Client initialisiert: endpoint=%s, "
            "embedding_dim=%d, default_top_k=%d",
            self._endpoint,
            self._embedding_dim,
            self._default_top_k,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Private Hilfsmethoden
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_host(endpoint: str) -> str:
        """Extrahiert den Host aus einer HTTP-URL."""
        # "http://chromadb:8000" → "chromadb"
        return endpoint.split("://")[-1].split(":")[0]

    @staticmethod
    def _extract_port(endpoint: str) -> int:
        """Extrahiert den Port aus einer HTTP-URL."""
        # "http://chromadb:8000" → 8000
        parts = endpoint.split(":")
        if len(parts) >= 3:
            return int(parts[-1].split("/")[0])
        return 8000

    async def _run_sync(self, func, *args: Any, **kwargs: Any) -> Any:
        """
        Führt eine synchrone Funktion im Thread-Pool aus.

        ChromaDB's Python-SDK ist synchron. Dieser Wrapper erlaubt
        den Aufruf aus async-Kontexten ohne Blocking des Event-Loops.
        """
        return await asyncio.to_thread(func, *args, **kwargs)

    def _retry_on_failure(self, operation_name: str) -> None:
        """Dekorator-ähnliche Logik: Loggt den Start einer Retry-Operation."""
        logger.debug("Starte ChromaDB-Operation: %s", operation_name)

    def _get_collection(self, name: str) -> Any:
        """
        Holt eine Collection oder wirft einen verständlichen Fehler.

        Args:
            name: Name der Collection.

        Returns:
            ChromaDB-Collection-Objekt.

        Raises:
            ValueError: Falls die Collection nicht existiert.
        """
        try:
            return self._client.get_collection(name=name)
        except Exception as exc:
            raise ValueError(
                f"ChromaDB-Collection {name!r} nicht gefunden. "
                f"Bitte zuerst mit ensure_collection() anlegen. "
                f"Fehler: {exc}"
            ) from exc

    # ──────────────────────────────────────────────────────────────────────
    # Health-Check
    # ──────────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """
        Prüft die Erreichbarkeit der ChromaDB-Instanz.

        Ruft ``heartbeat()`` auf und listet Collections als
        funktionalen Test.

        Returns:
            ``True`` wenn erreichbar und funktionsfähig.
        """
        try:

            def _do_ping() -> bool:
                heartbeat: int = self._client.heartbeat()
                # Zusätzlicher Funktionstest: Collections auflisten
                self._client.list_collections()
                return heartbeat > 0

            result: bool = await self._run_sync(_do_ping)
            if result:
                logger.debug("ChromaDB-Ping erfolgreich.")
            return result
        except Exception as exc:
            logger.warning("ChromaDB-Ping fehlgeschlagen: %s", exc)
            return False

    # ──────────────────────────────────────────────────────────────────────
    # Collection-Management
    # ──────────────────────────────────────────────────────────────────────

    async def ensure_collection(
        self,
        name: str,
        metadata: dict[str, Any] | None = None,
        embedding_dim: int | None = None,
        distance_metric: str = "cosine",
    ) -> str:
        """
        Stellt sicher, dass eine Collection existiert.

        Falls die Collection bereits existiert, wird sie unverändert
        zurückgegeben. Falls nicht, wird sie neu erstellt.

        Args:
            name: Eindeutiger Collection-Name.
            metadata: Beliebige Metadaten (z.B. Beschreibung, Quelle).
            embedding_dim: Dimension der Embeddings (Default aus Settings).
            distance_metric: Distanzmetrik (cosine, l2, ip).

        Returns:
            Name der Collection (Bestätigung).

        Raises:
            ChromaError: Bei Verbindungsproblemen nach allen Retries.
        """
        dim: int = embedding_dim or self._embedding_dim

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:

                def _ensure() -> str:
                    try:
                        col = self._client.get_collection(name=name)
                        logger.info(
                            "Collection %r bereits vorhanden (%d Dokumente).",
                            name,
                            col.count(),
                        )
                    except Exception:
                        col = self._client.create_collection(
                            name=name,
                            metadata=metadata or {},
                            embedding_function=None,  # Embeddings werden extern erzeugt
                        )
                        logger.info(
                            "Collection %r neu erstellt (dim=%d, "
                            "metric=%s).",
                            name,
                            dim,
                            distance_metric,
                        )
                    return col.name

                return await self._run_sync(_ensure)

            except ChromaError as exc:
                logger.error(
                    "ChromaDB-Fehler bei ensure_collection(%r), "
                    "Versuch %d/%d: %s",
                    name,
                    attempt,
                    self._MAX_RETRIES,
                    exc,
                )
                if attempt == self._MAX_RETRIES:
                    raise
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

        # Sollte nie erreicht werden (Fallback)
        raise ChromaError(
            f"Konnte Collection {name!r} nicht sicherstellen."
        )

    async def delete_collection(self, name: str) -> bool:
        """
        Löscht eine Collection und alle ihre Daten.

        Args:
            name: Name der zu löschenden Collection.

        Returns:
            ``True`` bei erfolgreicher Löschung.

        Raises:
            ValueError: Falls die Collection nicht existiert.
        """
        logger.info("Lösche ChromaDB-Collection %r.", name)

        try:

            def _delete() -> bool:
                self._client.delete_collection(name=name)
                return True

            result: bool = await self._run_sync(_delete)
            logger.info("Collection %r erfolgreich gelöscht.", name)
            return result
        except ValueError:
            raise
        except Exception as exc:
            logger.error(
                "Fehler beim Löschen der Collection %r: %s", name, exc
            )
            raise ChromaError(
                f"Löschen der Collection {name!r} fehlgeschlagen: {exc}"
            ) from exc

    async def list_collections(self) -> list[CollectionInfo]:
        """
        Listet alle existierenden Collections mit Metadaten auf.

        Returns:
            Liste von ``CollectionInfo``-Objekten.
        """
        logger.debug("Liste alle ChromaDB-Collections.")

        try:

            def _list() -> list[CollectionInfo]:
                cols = self._client.list_collections()
                return [
                    CollectionInfo(
                        name=col.name,
                        count=col.count(),
                        metadata=col.metadata or {},
                    )
                    for col in cols
                ]

            result: list[CollectionInfo] = await self._run_sync(_list)
            logger.info(
                "%d Collections gefunden: %s",
                len(result),
                [c.name for c in result],
            )
            return result
        except Exception as exc:
            logger.error("Fehler beim Auflisten der Collections: %s", exc)
            return []

    async def collection_info(self, name: str) -> CollectionInfo:
        """
        Ruft Metadaten und Dokumentanzahl einer Collection ab.

        Args:
            name: Collection-Name.

        Returns:
            ``CollectionInfo`` mit Name, Count, Metadata.
        """
        logger.debug("Rufe Collection-Info ab: %r", name)

        def _info() -> CollectionInfo:
            col = self._get_collection(name)
            return CollectionInfo(
                name=col.name,
                count=col.count(),
                metadata=col.metadata or {},
            )

        return await self._run_sync(_info)

    # ──────────────────────────────────────────────────────────────────────
    # Embedding-Operationen (Einzeln & Bulk)
    # ──────────────────────────────────────────────────────────────────────

    async def add_embeddings(
        self,
        collection_name: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str] | None = None,
        metadatas: list[dict[str, Any]] | None = None,
    ) -> int:
        """
        Fügt eine Liste von Embeddings zu einer Collection hinzu.

        Unterstützt Batches für große Datenmengen (verhindert OOM
        und Request-Timeout).

        Args:
            collection_name: Ziel-Collection.
            ids: Eindeutige IDs pro Embedding (muss gleiche Länge haben).
            embeddings: Liste von Embedding-Vektoren.
            documents: Optional: Rohtext-Dokumente (für Hybrid-Suche).
            metadatas: Optional: Metadaten pro Dokument.

        Returns:
            Anzahl der hinzugefügten Dokumente.

        Raises:
            ValueError: Bei unterschiedlichen Listenlängen.
            InvalidDimensionException: Bei falscher Embedding-Dimension.
        """
        total: int = len(ids)

        # Validierung
        if len(embeddings) != total:
            raise ValueError(
                f"embeddings-Länge ({len(embeddings)}) != ids-Länge ({total})"
            )
        if documents is not None and len(documents) != total:
            raise ValueError(
                f"documents-Länge ({len(documents)}) != ids-Länge ({total})"
            )
        if metadatas is not None and len(metadatas) != total:
            raise ValueError(
                f"metadatas-Länge ({len(metadatas)}) != ids-Länge ({total})"
            )

        logger.info(
            "Füge %d Embeddings zu Collection %r hinzu...",
            total,
            collection_name,
        )

        # Bulk-Insert in Batches
        added: int = 0
        for batch_start in range(0, total, self._MAX_BATCH_SIZE):
            batch_end: int = min(batch_start + self._MAX_BATCH_SIZE, total)
            batch_ids: list[str] = ids[batch_start:batch_end]
            batch_embeddings: list[list[float]] = embeddings[
                batch_start:batch_end
            ]
            batch_docs: list[str] | None = (
                documents[batch_start:batch_end]
                if documents
                else None
            )
            batch_meta: list[dict[str, Any]] | None = (
                metadatas[batch_start:batch_end]
                if metadatas
                else None
            )

            try:

                def _add_batch() -> None:
                    col = self._get_collection(collection_name)
                    col.add(
                        ids=batch_ids,
                        embeddings=batch_embeddings,
                        documents=batch_docs,
                        metadatas=batch_meta,
                    )

                await self._run_sync(_add_batch)
                added += len(batch_ids)
                logger.debug(
                    "Batch %d-%d/%d eingefügt (%d total).",
                    batch_start + 1,
                    batch_end,
                    total,
                    added,
                )
            except InvalidDimensionException as exc:
                logger.error(
                    "Falsche Embedding-Dimension für Collection %r: %s",
                    collection_name,
                    exc,
                )
                raise
            except Exception as exc:
                logger.error(
                    "Fehler beim Batch-Insert in Collection %r "
                    "(Batch %d-%d): %s",
                    collection_name,
                    batch_start,
                    batch_end,
                    exc,
                )
                raise ChromaError(
                    f"Batch-Insert fehlgeschlagen: {exc}"
                ) from exc

        logger.info(
            "%d/%d Embeddings erfolgreich in Collection %r eingefügt.",
            added,
            total,
            collection_name,
        )
        return added

    async def add_single_embedding(
        self,
        collection_name: str,
        doc_id: str,
        embedding: list[float],
        document: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Fügt ein einzelnes Embedding hinzu (Convenience-Wrapper).

        Args:
            collection_name: Ziel-Collection.
            doc_id: Eindeutige Dokument-ID.
            embedding: Embedding-Vektor.
            document: Optional: Rohtext.
            metadata: Optional: Metadaten.
        """
        await self.add_embeddings(
            collection_name=collection_name,
            ids=[doc_id],
            embeddings=[embedding],
            documents=[document] if document else None,
            metadatas=[metadata] if metadata else None,
        )

    async def upsert_embeddings(
        self,
        collection_name: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str] | None = None,
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Fügt Embeddings hinzu ODER aktualisiert sie, falls die ID
        bereits existiert (Upsert-Semantik).

        Args:
            collection_name: Ziel-Collection.
            ids: Eindeutige IDs.
            embeddings: Embedding-Vektoren.
            documents: Optional: Rohtexte.
            metadatas: Optional: Metadaten.
        """
        logger.info(
            "Upsert %d Embeddings in Collection %r.",
            len(ids),
            collection_name,
        )

        def _upsert() -> None:
            col = self._get_collection(collection_name)
            col.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )

        try:
            await self._run_sync(_upsert)
        except Exception as exc:
            logger.error(
                "Upsert in Collection %r fehlgeschlagen: %s",
                collection_name,
                exc,
            )
            raise ChromaError(f"Upsert fehlgeschlagen: {exc}") from exc

    # ──────────────────────────────────────────────────────────────────────
    # Semantische Suche (Query)
    # ──────────────────────────────────────────────────────────────────────

    async def query_by_embedding(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int | None = None,
        where: dict[str, Any] | None = None,
        include_documents: bool = True,
        include_metadatas: bool = True,
        include_distances: bool = True,
    ) -> ChromaQueryResult:
        """
        Führt eine semantische Suche via Embedding-Vektor durch.

        Args:
            collection_name: Zu durchsuchende Collection.
            query_embedding: Embedding-Vektor der Suchanfrage.
            top_k: Anzahl der Ergebnisse (Default aus Settings).
            where: Optionaler Metadaten-Filter.
            include_documents: Rohtexte in Ergebnis einschließen.
            include_metadatas: Metadaten in Ergebnis einschließen.
            include_distances: Distanzwerte in Ergebnis einschließen.

        Returns:
            ``ChromaQueryResult`` mit Treffern und Metriken.
        """
        k: int = top_k or self._default_top_k
        logger.debug(
            "ChromaDB-Query (Embedding): collection=%r, top_k=%d",
            collection_name,
            k,
        )
        start_time: float = time.monotonic()

        try:

            def _query() -> QueryResult:
                col = self._get_collection(collection_name)
                return col.query(
                    query_embeddings=[query_embedding],
                    n_results=k,
                    where=where,
                    include=[
                        "documents" if include_documents else "",
                        "metadatas" if include_metadatas else "",
                        "distances" if include_distances else "",
                    ],
                )

            result: QueryResult = await self._run_sync(_query)
            elapsed: float = (time.monotonic() - start_time) * 1000

            # Ergebnisse parsen
            hits: list[ChromaQueryHit] = self._parse_query_result(
                result, k
            )

            logger.info(
                "ChromaDB-Query: %d Treffer in %.1f ms "
                "(collection=%r, top_k=%d).",
                len(hits),
                elapsed,
                collection_name,
                k,
            )
            return ChromaQueryResult(
                hits=hits,
                query_time_ms=round(elapsed, 2),
                collection_name=collection_name,
                total_in_collection=(
                    await self.collection_info(collection_name)
                ).count,
            )
        except Exception as exc:
            logger.error(
                "ChromaDB-Query fehlgeschlagen (collection=%r): %s",
                collection_name,
                exc,
            )
            raise ChromaError(f"Query fehlgeschlagen: {exc}") from exc

    async def query_by_text(
        self,
        collection_name: str,
        query_text: str,
        top_k: int | None = None,
        where: dict[str, Any] | None = None,
    ) -> ChromaQueryResult:
        """
        Führt eine semantische Suche via Rohtext durch.

        ChromaDB nutzt die integrierte Embedding-Funktion der Collection
        (falls konfiguriert) oder erwartet, dass ``query_by_embedding``
        mit vorab berechnetem Embedding verwendet wird.

        Args:
            collection_name: Zu durchsuchende Collection.
            query_text: Rohtext der Suchanfrage (wird automatisch embedded).
            top_k: Anzahl der Ergebnisse.
            where: Optionaler Metadaten-Filter.

        Returns:
            ``ChromaQueryResult`` mit Treffern.
        """
        k: int = top_k or self._default_top_k
        logger.debug(
            "ChromaDB-Query (Text): collection=%r, query=%r, top_k=%d",
            collection_name,
            query_text[:100],
            k,
        )
        start_time: float = time.monotonic()

        try:

            def _query() -> QueryResult:
                col = self._get_collection(collection_name)
                return col.query(
                    query_texts=[query_text],
                    n_results=k,
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )

            result: QueryResult = await self._run_sync(_query)
            elapsed: float = (time.monotonic() - start_time) * 1000

            hits: list[ChromaQueryHit] = self._parse_query_result(
                result, k
            )

            logger.info(
                "ChromaDB-Query (Text): %d Treffer in %.1f ms "
                "(%r → collection=%r).",
                len(hits),
                elapsed,
                query_text[:50],
                collection_name,
            )
            return ChromaQueryResult(
                hits=hits,
                query_time_ms=round(elapsed, 2),
                collection_name=collection_name,
                total_in_collection=(
                    await self.collection_info(collection_name)
                ).count,
            )
        except Exception as exc:
            logger.error(
                "ChromaDB-Text-Query fehlgeschlagen (collection=%r, "
                "query=%r): %s",
                collection_name,
                query_text[:100],
                exc,
            )
            raise ChromaError(f"Text-Query fehlgeschlagen: {exc}") from exc

    def _parse_query_result(
        self,
        result: QueryResult,
        top_k: int,
    ) -> list[ChromaQueryHit]:
        """
        Parst ein rohes ChromaDB-QueryResult in ``ChromaQueryHit``-Objekte.

        Args:
            result: Rohes QueryResult von ChromaDB.
            top_k: Anzahl der angefragten Ergebnisse.

        Returns:
            Liste typisierter Treffer, absteigend nach Score sortiert.
        """
        hits: list[ChromaQueryHit] = []

        ids_all: list[list[str]] = result.get("ids", [[]])
        docs_all: list[list[str]] = result.get("documents", [[""]])
        metas_all: list[list[dict]] = result.get("metadatas", [[{}]])
        dists_all: list[list[float]] = result.get("distances", [[0.0]])

        if not ids_all or not ids_all[0]:
            return hits

        ids: list[str] = ids_all[0]
        docs: list[str] = docs_all[0] if docs_all else [""] * len(ids)
        metas: list[dict] = metas_all[0] if metas_all else [{}] * len(ids)
        dists: list[float] = dists_all[0] if dists_all else [0.0] * len(ids)

        for i in range(min(len(ids), top_k)):
            distance: float = dists[i] if i < len(dists) else 0.0
            # ChromaDB cosine distance: 0 = identisch, 2 = entgegengesetzt
            # Umrechnung in Score: 1 - distance/2  (0..1)
            score: float = max(0.0, 1.0 - (distance / 2.0))

            hits.append(
                ChromaQueryHit(
                    id=ids[i],
                    document=docs[i] if i < len(docs) else "",
                    metadata=metas[i] if i < len(metas) else {},
                    distance=round(distance, 4),
                    score=round(score, 4),
                )
            )

        return hits

    # ──────────────────────────────────────────────────────────────────────
    # Daten-Management
    # ──────────────────────────────────────────────────────────────────────

    async def delete_by_ids(
        self,
        collection_name: str,
        ids: list[str],
    ) -> int:
        """
        Löscht Dokumente anhand ihrer IDs aus einer Collection.

        Args:
            collection_name: Collection-Name.
            ids: Liste der zu löschenden IDs.

        Returns:
            Anzahl der gelöschten Dokumente.
        """
        logger.info(
            "Lösche %d Dokumente aus Collection %r.",
            len(ids),
            collection_name,
        )

        def _delete() -> None:
            col = self._get_collection(collection_name)
            col.delete(ids=ids)

        try:
            await self._run_sync(_delete)
            logger.info(
                "%d Dokumente aus Collection %r gelöscht.",
                len(ids),
                collection_name,
            )
            return len(ids)
        except Exception as exc:
            logger.error(
                "Löschen aus Collection %r fehlgeschlagen: %s",
                collection_name,
                exc,
            )
            raise ChromaError(f"Delete fehlgeschlagen: {exc}") from exc

    async def get_documents(
        self,
        collection_name: str,
        ids: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """
        Ruft Dokumente aus einer Collection ab.

        Args:
            collection_name: Collection-Name.
            ids: Optional: Nur diese IDs abrufen. None = alle.
            limit: Maximale Anzahl.
            offset: Offset für Paginierung.

        Returns:
            Dictionary mit ``ids``, ``documents``, ``metadatas``, ``embeddings``.
        """
        logger.debug(
            "Rufe Dokumente aus Collection %r ab (limit=%d, offset=%d).",
            collection_name,
            limit,
            offset,
        )

        def _get() -> dict[str, Any]:
            col = self._get_collection(collection_name)
            if ids is not None:
                return col.get(
                    ids=ids,
                    include=["documents", "metadatas", "embeddings"],
                )
            else:
                return col.get(
                    limit=limit,
                    offset=offset,
                    include=["documents", "metadatas", "embeddings"],
                )

        try:
            result: dict[str, Any] = await self._run_sync(_get)
            logger.debug(
                "%d Dokumente aus Collection %r abgerufen.",
                len(result.get("ids", [])),
                collection_name,
            )
            return result
        except Exception as exc:
            logger.error(
                "Abrufen von Dokumenten aus Collection %r fehlgeschlagen: %s",
                collection_name,
                exc,
            )
            raise ChromaError(f"Get fehlgeschlagen: {exc}") from exc

    async def count_documents(self, collection_name: str) -> int:
        """
        Zählt die Dokumente in einer Collection.

        Args:
            collection_name: Collection-Name.

        Returns:
            Anzahl der Dokumente.
        """
        info: CollectionInfo = await self.collection_info(collection_name)
        return info.count
