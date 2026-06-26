"""
RAG-Engine (Retrieval-Augmented Generation) des Homelab-Imperiums.

Implementiert die vollständige RAG-Pipeline:
1. **PDF-Extraktion** — Text aus PDF-Dateien parsen (pypdf).
2. **Semantisches Chunking** — Text in überlappende Abschnitte zerlegen.
3. **Embedding-Generierung** — Vektorisierung via Ollama (nomic-embed-text).
4. **ChromaDB-Indexierung** — Embeddings in Collections speichern.
5. **Kontext-Retrieval** — Relevante Passagen abfragen und als formatierte
   System-Prompt-Injektion zurückgeben.

Verwendung::

    from app.services.rag_engine import RAGEngine

    engine = RAGEngine()
    await engine.ingest_pdf("/path/to/skript.pdf", "school_pdfs")
    context = await engine.retrieve_formatted_context(
        query="Was ist eine Hashmap?",
        collection_name="school_pdfs",
    )
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.config import settings
from app.services.clients.chroma import ChromaDBClient, ChromaQueryHit
from app.services.clients.ollama import OllamaClient

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.services.rag")


# ═══════════════════════════════════════════════════════════════════════════════
# Datenklassen
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class TextChunk:
    """Ein einzelner Text-Abschnitt (Chunk) nach dem Zerlegen."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    text: str = ""
    chunk_index: int = 0
    source_file: str = ""
    page_number: int = 0
    char_start: int = 0
    char_end: int = 0


@dataclass
class IngestionResult:
    """Ergebnis einer PDF-Ingestion."""

    file_path: str
    collection_name: str
    total_chunks: int = 0
    total_chars: int = 0
    pages_processed: int = 0
    embedding_success: int = 0
    embedding_failed: int = 0
    duration_seconds: float = 0.0
    file_hash: str = ""


@dataclass
class RetrievalResult:
    """Ergebnis einer Kontext-Retrieval-Anfrage."""

    query: str
    hits: list[ChromaQueryHit] = field(default_factory=list)
    formatted_context: str = ""
    query_time_ms: float = 0.0
    total_in_collection: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# RAGEngine
# ═══════════════════════════════════════════════════════════════════════════════


class RAGEngine:
    """
    RAG-Pipeline: PDF → Chunks → Embeddings → ChromaDB → Retrieval.

    Orchestriert den gesamten Retrieval-Augmented-Generation-Workflow
    mit Ollama als Embedding-Provider und ChromaDB als Vektordatenbank.
    """

    def __init__(self) -> None:
        """
        Initialisiert die RAG-Engine mit Clients und Konfiguration.

        Liest Chunk-Size, Overlap, Embedding-Modell und Temp-Dir aus
        ``app.config.settings``.
        """
        # ── Clients ──
        self._chroma: ChromaDBClient = ChromaDBClient()
        self._ollama: OllamaClient = OllamaClient(
            host=settings.ollama_local_endpoint,
        )

        # ── Konfiguration ──
        self._chunk_size: int = settings.rag_chunk_size
        self._chunk_overlap: int = settings.rag_chunk_overlap
        self._embedding_model: str = settings.chromadb_embedding_model
        self._embedding_dim: int = settings.chromadb_embedding_dimension
        self._default_top_k: int = settings.chromadb_default_top_k
        self._max_pdf_size_mb: int = settings.rag_max_pdf_size_mb
        self._temp_dir: Path = Path(settings.rag_temp_dir)

        # Cache: Datei-Hash → bereits indiziert?
        self._indexed_files: dict[str, str] = {}  # hash → collection

        logger.info(
            "RAG-Engine initialisiert: chunk_size=%d, overlap=%d, "
            "embedding_model=%s, embedding_dim=%d, top_k=%d",
            self._chunk_size,
            self._chunk_overlap,
            self._embedding_model,
            self._embedding_dim,
            self._default_top_k,
        )

    # ──────────────────────────────────────────────────────────────────────
    # PDF-Text-Extraktion
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def extract_text_from_pdf(file_path: str) -> list[dict]:
        """
        Extrahiert Rohtext aus einer PDF-Datei — seitenweise.

        Nutzt ``pypdf`` für das Parsen. Jede Seite wird einzeln
        extrahiert, um Seitenzahlen für Chunks zu erhalten.

        Args:
            file_path: Absoluter Pfad zur PDF-Datei.

        Returns:
            Liste von Dicts: ``[{"page": 1, "text": "...", "chars": 1234}, ...]``

        Raises:
            ImportError: Falls pypdf nicht installiert ist.
            FileNotFoundError: Falls die PDF nicht existiert.
            ValueError: Falls die Datei zu groß ist.
        """
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ImportError(
                "pypdf ist nicht installiert. "
                "Installiere mit: pip install pypdf"
            )

        pdf_path: Path = Path(file_path)
        if not pdf_path.exists():
            raise FileNotFoundError(
                f"PDF-Datei nicht gefunden: {file_path}"
            )

        file_size_mb: float = pdf_path.stat().st_size / (1024 * 1024)
        max_size: int = settings.rag_max_pdf_size_mb
        if file_size_mb > max_size:
            raise ValueError(
                f"PDF zu groß: {file_size_mb:.1f} MB (Limit: {max_size} MB)."
            )

        logger.info(
            "Extrahiere Text aus PDF: %s (%.1f MB)...",
            pdf_path.name,
            file_size_mb,
        )

        pages: list[dict] = []
        reader: PdfReader = PdfReader(str(pdf_path))

        for i, page in enumerate(reader.pages, start=1):
            text: str = page.extract_text() or ""
            if text.strip():
                pages.append(
                    {
                        "page": i,
                        "text": text,
                        "chars": len(text),
                    }
                )

        total_chars: int = sum(p["chars"] for p in pages)
        logger.info(
            "PDF-Extraktion: %d Seiten, %d Zeichen insgesamt.",
            len(pages),
            total_chars,
        )
        return pages

    # ──────────────────────────────────────────────────────────────────────
    # Semantisches Chunking
    # ──────────────────────────────────────────────────────────────────────

    def chunk_text(
        self,
        pages: list[dict],
        source_file: str,
    ) -> list[TextChunk]:
        """
        Zerlegt extrahierten PDF-Text in überlappende Chunks.

        Der Overlap zwischen aufeinanderfolgenden Chunks stellt sicher,
        dass Sätze an den Chunk-Grenzen nicht abgeschnitten werden.

        Args:
            pages: Liste von Dicts aus ``extract_text_from_pdf``.
            source_file: Name der Quelldatei (für Metadaten).

        Returns:
            Liste von ``TextChunk``-Objekten.
        """
        chunks: list[TextChunk] = []
        chunk_index: int = 0

        for page_data in pages:
            text: str = page_data["text"]
            page_num: int = page_data["page"]

            # Zerlege den Seitentext in Chunks mit Overlap
            start: int = 0
            while start < len(text):
                end: int = min(start + self._chunk_size, len(text))
                chunk_text: str = text[start:end]

                # Vermeide Chunks, die nur aus Whitespace bestehen
                if chunk_text.strip():
                    chunks.append(
                        TextChunk(
                            id=f"{source_file}_p{page_num}_c{chunk_index}",
                            text=chunk_text.strip(),
                            chunk_index=chunk_index,
                            source_file=source_file,
                            page_number=page_num,
                            char_start=start,
                            char_end=end,
                        )
                    )
                    chunk_index += 1

                # Nächster Chunk-Start: mit Overlap
                step: int = self._chunk_size - self._chunk_overlap
                start += max(step, 1)  # Mindestens 1 Zeichen vorrücken

        logger.info(
            "Chunking: %d Chunks aus %d Seiten (size=%d, overlap=%d).",
            len(chunks),
            len(pages),
            self._chunk_size,
            self._chunk_overlap,
        )
        return chunks

    # ──────────────────────────────────────────────────────────────────────
    # Embedding-Generierung via Ollama
    # ──────────────────────────────────────────────────────────────────────

    async def _embed_texts(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """
        Generiert Embedding-Vektoren für eine Liste von Texten.

        Nutzt Ollamas ``/api/embeddings``-Endpunkt mit dem konfigurierten
        Embedding-Modell (Default: ``nomic-embed-text``).

        Args:
            texts: Liste von Text-Strings.

        Returns:
            Liste von Embedding-Vektoren (``list[list[float]]``).
        """
        import httpx

        embeddings: list[list[float]] = []
        url: str = f"{settings.ollama_local_endpoint}/api/embeddings"

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
        ) as client:
            for i, text in enumerate(texts):
                try:
                    response: httpx.Response = await client.post(
                        url,
                        json={
                            "model": self._embedding_model,
                            "prompt": text,
                        },
                    )
                    response.raise_for_status()
                    data: dict = response.json()
                    embedding: list[float] = data.get("embedding", [])

                    if len(embedding) != self._embedding_dim:
                        logger.warning(
                            "Embedding-Dimension %d != erwartet %d "
                            "für Text %d.",
                            len(embedding),
                            self._embedding_dim,
                            i,
                        )

                    embeddings.append(embedding)

                except Exception as exc:
                    logger.error(
                        "Embedding-Generierung fehlgeschlagen für "
                        "Text %d/%d: %s",
                        i + 1,
                        len(texts),
                        exc,
                    )
                    # Leeren Vektor als Platzhalter
                    embeddings.append([0.0] * self._embedding_dim)

        logger.debug(
            "%d Embeddings generiert (%d fehlgeschlagen).",
            len(embeddings),
            sum(1 for e in embeddings if all(v == 0.0 for v in e)),
        )
        return embeddings

    # ──────────────────────────────────────────────────────────────────────
    # PDF-Ingestion (öffentliche API)
    # ──────────────────────────────────────────────────────────────────────

    def _compute_file_hash(self, file_path: str) -> str:
        """Berechnet den SHA256-Hash einer Datei."""
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                sha.update(block)
        return sha.hexdigest()

    async def ingest_pdf(
        self,
        file_path: str,
        collection_name: str,
        force_reindex: bool = False,
    ) -> IngestionResult:
        """
        Führt die vollständige PDF-Ingestion-Pipeline durch:
        Extraktion → Chunking → Embedding → ChromaDB-Indexierung.

        Args:
            file_path: Pfad zur PDF-Datei.
            collection_name: Ziel-ChromaDB-Collection (z.B. ``"school_pdfs"``).
            force_reindex: ``True`` = Neu-Indizierung auch wenn bereits
                           indexiert.

        Returns:
            ``IngestionResult`` mit Statistiken.

        Raises:
            FileNotFoundError, ValueError: Siehe ``extract_text_from_pdf``.
        """
        import time

        start_time: float = time.monotonic()
        pdf_name: str = Path(file_path).name

        logger.info(
            "🚀 Starte PDF-Ingestion: %s → Collection %r",
            pdf_name,
            collection_name,
        )

        # ── Schritt 0: Duplikat-Prüfung via Hash ──
        file_hash: str = self._compute_file_hash(file_path)
        if (
            not force_reindex
            and file_hash in self._indexed_files
            and self._indexed_files[file_hash] == collection_name
        ):
            logger.info(
                "PDF %s bereits in Collection %r indiziert (Hash: %s...) "
                "→ überspringe.",
                pdf_name,
                collection_name,
                file_hash[:12],
            )
            return IngestionResult(
                file_path=file_path,
                collection_name=collection_name,
                total_chunks=0,
                file_hash=file_hash,
                duration_seconds=0.0,
            )

        # ── Schritt 1: Collection sicherstellen ──
        await self._chroma.ensure_collection(
            name=collection_name,
            metadata={
                "description": f"RAG-Collection: {collection_name}",
                "embedding_model": self._embedding_model,
                "chunk_size": str(self._chunk_size),
            },
        )

        # ── Schritt 2: PDF-Text extrahieren ──
        pages: list[dict] = self.extract_text_from_pdf(file_path)
        total_chars: int = sum(p["chars"] for p in pages)

        # ── Schritt 3: Chunking ──
        chunks: list[TextChunk] = self.chunk_text(
            pages=pages,
            source_file=pdf_name,
        )

        if not chunks:
            logger.warning(
                "Keine Chunks aus PDF %s extrahiert.", pdf_name
            )
            return IngestionResult(
                file_path=file_path,
                collection_name=collection_name,
                total_chunks=0,
                total_chars=total_chars,
                pages_processed=len(pages),
                file_hash=file_hash,
                duration_seconds=time.monotonic() - start_time,
            )

        # ── Schritt 4: Embeddings generieren ──
        logger.info(
            "Generiere %d Embeddings via %s...",
            len(chunks),
            self._embedding_model,
        )
        chunk_texts: list[str] = [c.text for c in chunks]
        embeddings: list[list[float]] = await self._embed_texts(chunk_texts)

        # Erfolgreiche vs. fehlgeschlagene zählen
        valid_indices: list[int] = [
            i
            for i, emb in enumerate(embeddings)
            if not all(v == 0.0 for v in emb)
        ]

        # ── Schritt 5: In ChromaDB indexieren ──
        if valid_indices:
            valid_ids: list[str] = [chunks[i].id for i in valid_indices]
            valid_embeddings: list[list[float]] = [
                embeddings[i] for i in valid_indices
            ]
            valid_docs: list[str] = [chunks[i].text for i in valid_indices]
            valid_metas: list[dict] = [
                {
                    "source_file": chunks[i].source_file,
                    "page_number": chunks[i].page_number,
                    "chunk_index": chunks[i].chunk_index,
                    "char_start": chunks[i].char_start,
                    "char_end": chunks[i].char_end,
                    "file_hash": file_hash,
                }
                for i in valid_indices
            ]

            added: int = await self._chroma.add_embeddings(
                collection_name=collection_name,
                ids=valid_ids,
                embeddings=valid_embeddings,
                documents=valid_docs,
                metadatas=valid_metas,
            )

            # ── Schritt 6: Hash cachen ──
            self._indexed_files[file_hash] = collection_name

            elapsed: float = time.monotonic() - start_time
            logger.info(
                "✅ PDF-Ingestion abgeschlossen: %d/%d Chunks indiziert "
                "in %.1fs (%s → %r).",
                added,
                len(chunks),
                elapsed,
                pdf_name,
                collection_name,
            )

            return IngestionResult(
                file_path=file_path,
                collection_name=collection_name,
                total_chunks=len(chunks),
                total_chars=total_chars,
                pages_processed=len(pages),
                embedding_success=len(valid_indices),
                embedding_failed=len(chunks) - len(valid_indices),
                duration_seconds=round(elapsed, 2),
                file_hash=file_hash,
            )
        else:
            elapsed = time.monotonic() - start_time
            logger.error(
                "❌ Alle Embeddings fehlgeschlagen für %s.", pdf_name
            )
            return IngestionResult(
                file_path=file_path,
                collection_name=collection_name,
                total_chunks=len(chunks),
                total_chars=total_chars,
                pages_processed=len(pages),
                embedding_success=0,
                embedding_failed=len(chunks),
                duration_seconds=round(elapsed, 2),
                file_hash=file_hash,
            )

    async def ingest_text(
        self,
        text: str,
        collection_name: str,
        source_name: str = "manuell",
    ) -> int:
        """
        Indiziert einen einzelnen Rohtext (kein PDF) in eine Collection.

        Nützlich für Code-Snippets, Markdown-Dokumente oder direkte
        Texteingaben.

        Args:
            text: Der zu indizierende Text.
            collection_name: Ziel-Collection.
            source_name: Quellbezeichnung für Metadaten.

        Returns:
            Anzahl der erstellten Chunks.
        """
        logger.info(
            "Indiziere Rohtext: %d Zeichen → Collection %r.",
            len(text),
            collection_name,
        )

        # Sicherstellen, dass Collection existiert
        await self._chroma.ensure_collection(collection_name)

        # Chunking
        pages: list[dict] = [{"page": 1, "text": text, "chars": len(text)}]
        chunks: list[TextChunk] = self.chunk_text(
            pages=pages,
            source_file=source_name,
        )

        if not chunks:
            return 0

        # Embeddings
        chunk_texts: list[str] = [c.text for c in chunks]
        embeddings: list[list[float]] = await self._embed_texts(chunk_texts)

        # Indexieren
        valid_indices: list[int] = [
            i
            for i, emb in enumerate(embeddings)
            if not all(v == 0.0 for v in emb)
        ]
        if valid_indices:
            await self._chroma.add_embeddings(
                collection_name=collection_name,
                ids=[chunks[i].id for i in valid_indices],
                embeddings=[embeddings[i] for i in valid_indices],
                documents=[chunks[i].text for i in valid_indices],
                metadatas=[
                    {
                        "source_file": source_name,
                        "chunk_index": chunks[i].chunk_index,
                    }
                    for i in valid_indices
                ],
            )

        logger.info(
            "%d/%d Chunks indiziert (Rohtext).",
            len(valid_indices),
            len(chunks),
        )
        return len(valid_indices)

    # ──────────────────────────────────────────────────────────────────────
    # Kontext-Retrieval (öffentliche API)
    # ──────────────────────────────────────────────────────────────────────

    async def retrieve_context(
        self,
        query: str,
        collection_name: str,
        top_k: int | None = None,
    ) -> RetrievalResult:
        """
        Ermittelt die relevantesten Kontextpassagen zu einer Anfrage.

        Führt eine semantische Suche in der angegebenen Collection durch
        und gibt die gefundenen Passagen als ``RetrievalResult`` zurück.

        Args:
            query: Die Suchanfrage (Nutzerfrage).
            collection_name: Zu durchsuchende Collection.
            top_k: Anzahl der Ergebnisse (Default aus Settings).

        Returns:
            ``RetrievalResult`` mit Treffern und formatiertem Kontext.
        """
        k: int = top_k or self._default_top_k
        logger.debug(
            "RAG-Retrieval: query=%r, collection=%r, top_k=%d",
            query[:100],
            collection_name,
            k,
        )

        # Text-Query via ChromaDB (nutzt interne Embedding-Funktion)
        # Da wir keine Embedding-Funktion bei Collection-Erstellung setzen,
        # müssen wir das Embedding selbst generieren und via
        # query_by_embedding suchen.
        query_embeddings: list[list[float]] = await self._embed_texts([query])

        if not query_embeddings or all(
            v == 0.0 for v in query_embeddings[0]
        ):
            logger.error("Konnte kein Embedding für Query generieren.")
            return RetrievalResult(query=query)

        from app.services.clients.chroma import ChromaQueryResult

        result: ChromaQueryResult = await self._chroma.query_by_embedding(
            collection_name=collection_name,
            query_embedding=query_embeddings[0],
            top_k=k,
        )

        return RetrievalResult(
            query=query,
            hits=result.hits,
            formatted_context="",
            query_time_ms=result.query_time_ms,
            total_in_collection=result.total_in_collection,
        )

    async def retrieve_formatted_context(
        self,
        query: str,
        collection_name: str,
        top_k: int | None = None,
        max_context_chars: int = 4096,
    ) -> str:
        """
        Ermittelt Kontextpassagen und formatiert sie als System-Prompt-Injektion.

        Das Ergebnis kann direkt in den System-Prompt eines LLMs eingefügt
        werden::

            system_prompt = f"{base_prompt}\\n\\n{context}"

        Args:
            query: Die Suchanfrage.
            collection_name: Zu durchsuchende Collection.
            top_k: Anzahl der Ergebnisse.
            max_context_chars: Maximale Länge des formatierten Kontexts.

        Returns:
            Formatierter Kontext-String (Markdown) zur Prompt-Injektion.
        """
        retrieval: RetrievalResult = await self.retrieve_context(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
        )

        if not retrieval.hits:
            logger.debug(
                "Keine relevanten Kontextpassagen für Query=%r gefunden.",
                query[:100],
            )
            return ""

        # Kontext formatieren
        context_parts: list[str] = []
        total_chars: int = 0

        context_parts.append(
            "## 📚 Relevante Kontextinformationen\n"
        )

        for i, hit in enumerate(retrieval.hits, start=1):
            source: str = hit.metadata.get("source_file", "Unbekannt")
            page: str = str(hit.metadata.get("page_number", "?"))
            score_pct: float = hit.score * 100

            header: str = (
                f"### 📄 Auszug {i} (Relevanz: {score_pct:.0f}%)\n"
                f"**Quelle:** {source}, Seite {page}\n"
            )
            body: str = f"\n{hit.document}\n\n---\n"

            part: str = header + body

            if total_chars + len(part) > max_context_chars:
                context_parts.append(
                    f"\n⚠️ *Weitere {len(retrieval.hits) - i + 1} "
                    f"Passagen aus Platzgründen gekürzt.*\n"
                )
                break

            context_parts.append(part)
            total_chars += len(part)

        formatted: str = "".join(context_parts)

        logger.info(
            "RAG-Kontext formatiert: %d/%d Passagen, %d Zeichen "
            "(query=%r, collection=%r).",
            min(len(retrieval.hits), i),
            len(retrieval.hits),
            len(formatted),
            query[:80],
            collection_name,
        )

        return formatted

    async def retrieve_and_inject(
        self,
        query: str,
        system_prompt: str,
        collection_name: str,
        top_k: int | None = None,
    ) -> str:
        """
        Kombiniert System-Prompt mit RAG-Kontext zu einem finalen Prompt.

        Dies ist der einfachste Einstiegspunkt: System-Prompt + Query
        → angereicherter Prompt mit Kontext aus der Vektordatenbank.

        Args:
            query: Die Nutzerfrage.
            system_prompt: Der Basis-System-Prompt.
            collection_name: Zu durchsuchende Collection.
            top_k: Anzahl der Kontextpassagen.

        Returns:
            Vollständiger System-Prompt mit RAG-Kontext.
        """
        context: str = await self.retrieve_formatted_context(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
        )

        if context:
            return f"{system_prompt}\n\n{context}"
        else:
            return system_prompt
