"""
Zentraler KI-Router des Homelab-Imperiums.

Stellt den Eintrittspunkt für alle KI-Chat-Interaktionen bereit.
Integriert RAG-Kontext-Injektion, Agent-Routing und duales
CPU/GPU-Inferenz-Backend mit transparentem Failover.

Pipeline:
1. ChatRequest validieren (Pydantic)
2. Agent-Konfiguration laden (YAML → System-Prompt, Modell)
3. RAG-Kontext abrufen (wenn ``rag_enabled=True``)
4. Prompt mit System-Prompt + RAG-Kontext anreichern
5. Inferenz über ``OllamaSmartRouter`` (CPU/GPU-Routing)
6. Response mit Metriken zurückgeben

Endpunkte:
- ``POST /api/ai/chat``        — Einmalige Chat-Generierung
- ``POST /api/ai/chat/stream`` — SSE-Streaming-Chat
- ``GET /api/ai/agents``        — Verfügbare Agenten auflisten
- ``GET /api/ai/status``        — Router-Status (CPU/GPU-Health)

Verwendung::

    from app.routers.ai import router
    app.include_router(router, prefix="/api")
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.schemas import (
    AgentInfo,
    ChatRequest,
    ChatResponse,
)
from app.services.ollama_router import OllamaSmartRouter
from app.services.rag_engine import RAGEngine

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.routers.ai")

# ═══════════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════════

router: APIRouter = APIRouter(tags=["KI & Agenten"])

# ═══════════════════════════════════════════════════════════════════════════════
# Globale Services (Singleton)
# ═══════════════════════════════════════════════════════════════════════════════

_ollama_router: OllamaSmartRouter = OllamaSmartRouter()
_rag_engine: RAGEngine = RAGEngine()

# ═══════════════════════════════════════════════════════════════════════════════
# Agent-Konfiguration (YAML → Dict)
# ═══════════════════════════════════════════════════════════════════════════════

_AGENTS_DIR: Path = Path(__file__).resolve().parents[3] / "config" / "agents"

# Cache für geladene Agent-Konfigurationen
_agent_cache: dict[str, dict] = {}


def _load_agent_config(agent_name: str) -> dict:
    """
    Lädt die YAML-Konfiguration eines Agenten.

    Sucht in ``config/agents/{agent_name}.yaml`` und cached
    das Ergebnis im Speicher.

    Args:
        agent_name: Agent-Name (z.B. ``"it_tutor"``).

    Returns:
        Dictionary mit system_prompt, default_model, temperature, etc.

    Raises:
        FileNotFoundError: Wenn die YAML-Datei nicht existiert.
    """
    if agent_name in _agent_cache:
        return _agent_cache[agent_name]

    config_path: Path = _AGENTS_DIR / f"{agent_name}.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Agent-Konfiguration nicht gefunden: {config_path}"
        )

    with open(config_path, "r", encoding="utf-8") as f:
        config: dict = yaml.safe_load(f)

    _agent_cache[agent_name] = config
    logger.debug("Agent-Konfiguration geladen: %s.", agent_name)
    return config


# ═══════════════════════════════════════════════════════════════════════════════
# Endpunkte
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/ai/chat",
    response_model=ChatResponse,
    summary="KI-Chat (einmalig)",
    description="**Zentraler KI-Endpunkt.** Sendet einen Prompt an den "
    "gewählten Agenten, reichert ihn optional mit RAG-Kontext an und "
    "führt die Inferenz über CPU oder GPU aus (je nach power_mode).",
)
async def chat(
    request: ChatRequest,
) -> ChatResponse:
    """
    Einmalige, nicht-streamende Chat-Generierung.

    **Pipeline:**
    1. Agent-Konfiguration laden (YAML → System-Prompt)
    2. RAG-Kontext aus ChromaDB abrufen (wenn ``rag_enabled=True``)
    3. System-Prompt + RAG-Kontext + User-Prompt kombinieren
    4. ``OllamaSmartRouter.route_generate()`` → CPU oder GPU
    5. Response mit Metriken (backend_used, model, tok/s, …)
    """
    logger.info(
        "POST /ai/chat: agent=%s, prompt_len=%d, power=%s, rag=%s.",
        request.agent_name,
        len(request.prompt),
        request.power_mode,
        request.rag_enabled,
    )

    try:
        # ── Schritt 1: Agent-Konfiguration laden ──
        agent_config: dict = _load_agent_config(request.agent_name)
        system_prompt: str = agent_config.get("system_prompt", "")
        rag_collection: str = agent_config.get("rag_collection", "")

        # ── Schritt 2: RAG-Kontext anreichern ──
        rag_sources: list[str] = []
        if request.rag_enabled and rag_collection:
            try:
                context: str = await _rag_engine.retrieve_formatted_context(
                    query=request.prompt,
                    collection_name=rag_collection,
                    top_k=4,
                )
                if context:
                    system_prompt = f"{system_prompt}\n\n{context}"
                    # Quellen extrahieren (aus "Quelle: dateiname.pdf")
                    import re

                    rag_sources = re.findall(
                        r"\*\*Quelle:\*\*\s*(.+?)(?:\n|$)", context
                    )
                    logger.debug(
                        "RAG-Kontext injiziert: %d Quellen, %d Zeichen.",
                        len(rag_sources),
                        len(context),
                    )
            except Exception as exc:
                logger.warning(
                    "RAG-Kontext-Abruf fehlgeschlagen (nicht kritisch): %s",
                    exc,
                )

        # ── Schritt 3: Inferenz über SmartRouter ──
        response = await _ollama_router.route_generate(
            prompt=request.prompt,
            system_prompt=system_prompt,
            power_mode=request.power_mode,
            agent_name=request.agent_name,
        )

        logger.info(
            "Chat abgeschlossen: node=%s, model=%s, "
            "%.1f tok/s, %.0f ms.",
            response.node.value,
            response.model,
            response.tokens_per_second,
            response.execution_time_ms,
        )

        return ChatResponse(
            response=response.response,
            backend_used=response.node.value,
            model=response.model,
            execution_time_ms=response.execution_time_ms,
            tokens_per_second=response.tokens_per_second,
            sources=rag_sources,
            total_tokens=response.eval_count,
        )

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Agent nicht gefunden: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Fehler bei KI-Chat.")
        raise HTTPException(
            status_code=500,
            detail=f"Chat-Verarbeitung fehlgeschlagen: {exc}",
        ) from exc


@router.post(
    "/ai/chat/stream",
    summary="KI-Chat (SSE-Streaming)",
    description="**Streaming-Endpunkt.** Sendet Tokens einzeln via "
    "Server-Sent Events (SSE) an den Client. Ideal für "
    "Chat-Oberflächen mit Echtzeit-Textausgabe.",
    responses={
        200: {
            "description": "SSE-Stream mit Text-Tokens.",
            "content": {"text/event-stream": {}},
        },
    },
)
async def chat_stream(
    request: ChatRequest,
) -> StreamingResponse:
    """
    SSE-Streaming-Chat — jedes Token wird sofort an den Client gesendet.

    Die Response nutzt ``text/event-stream`` mit ``Transfer-Encoding: chunked``.
    Jedes Token wird als ``data: {"token": "..."}`` gesendet.
    """
    logger.info(
        "POST /ai/chat/stream: agent=%s, prompt_len=%d, power=%s, rag=%s.",
        request.agent_name,
        len(request.prompt),
        request.power_mode,
        request.rag_enabled,
    )

    async def event_generator():
        """Generator für SSE-Events."""
        import json

        try:
            # ── Agent-Konfiguration laden ──
            agent_config: dict = _load_agent_config(request.agent_name)
            system_prompt: str = agent_config.get("system_prompt", "")
            rag_collection: str = agent_config.get("rag_collection", "")

            # ── RAG-Kontext ──
            if request.rag_enabled and rag_collection:
                try:
                    context: str = (
                        await _rag_engine.retrieve_formatted_context(
                            query=request.prompt,
                            collection_name=rag_collection,
                            top_k=4,
                        )
                    )
                    if context:
                        system_prompt = f"{system_prompt}\n\n{context}"
                except Exception as exc:
                    logger.warning(
                        "RAG-Kontext im Stream fehlgeschlagen: %s", exc
                    )

            # ── Inferenz-Stream ──
            start_time: float = time.monotonic()
            token_count: int = 0

            async for token in _ollama_router.route_generate_stream(
                prompt=request.prompt,
                system_prompt=system_prompt,
                power_mode=request.power_mode,
                agent_name=request.agent_name,
            ):
                token_count += 1
                yield f"data: {json.dumps({'token': token})}\n\n"

            # Abschluss-Event mit Metriken
            elapsed_ms: float = (time.monotonic() - start_time) * 1000
            done_msg: str = json.dumps(
                {
                    "done": True,
                    "tokens": token_count,
                    "elapsed_ms": round(elapsed_ms, 1),
                }
            )
            yield f"data: {done_msg}\n\n"

        except Exception as exc:
            logger.exception("Stream-Fehler.")
            yield (
                f"data: {json.dumps({'error': str(exc)})}\n\n"
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Nginx: kein Buffering
        },
    )


@router.get(
    "/ai/agents",
    summary="Verfügbare Agenten",
    description="Listet alle konfigurierten KI-Agenten mit "
    "Metadaten (Name, Beschreibung, Modell, Temperatur).",
)
async def list_agents() -> list[dict]:
    """
    Agent-Übersicht für die Chat-UI.

    Liest alle ``config/agents/*.yaml``-Dateien und extrahiert
    die Anzeige-Metadaten.
    """
    logger.debug("GET /ai/agents.")

    agents: list[dict] = []

    if not _AGENTS_DIR.exists():
        logger.warning("Agent-Verzeichnis nicht gefunden: %s.", _AGENTS_DIR)
        return agents

    for yaml_file in sorted(_AGENTS_DIR.glob("*.yaml")):
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                config: dict = yaml.safe_load(f)

            agents.append(
                {
                    "agent_name": config.get("agent_name", yaml_file.stem),
                    "display_name": config.get(
                        "display_name", yaml_file.stem
                    ),
                    "description": config.get("description", ""),
                    "avatar_gradient": config.get(
                        "avatar_gradient",
                        "linear-gradient(135deg, #667, #889)",
                    ),
                    "default_model": config.get("default_model", ""),
                    "temperature": config.get("temperature", 0.7),
                }
            )
        except Exception as exc:
            logger.warning(
                "Fehler beim Laden von %s: %s", yaml_file.name, exc
            )

    logger.info("%d Agenten geladen.", len(agents))
    return agents


@router.get(
    "/ai/status",
    summary="KI-Router-Status",
    description="Health-Status der Inferenz-Backends (CPU + GPU) "
    "mit Erreichbarkeit und verfügbaren Modellen.",
)
async def get_router_status() -> dict:
    """
    Status beider Ollama-Backends.

    Returns:
        Dict mit CPU- und GPU-Health, verfügbaren Modellen
        und Konfiguration.
    """
    logger.debug("GET /ai/status.")

    try:
        return await _ollama_router.get_status()

    except Exception as exc:
        logger.exception("Fehler bei Router-Status.")
        raise HTTPException(
            status_code=500,
            detail=f"Statusabfrage fehlgeschlagen: {exc}",
        ) from exc
