"""
Intelligenter Ollama-Lastverteiler und Failover-Router.

Der ``OllamaSmartRouter`` verwaltet zwei Inferenz-Backends:
1. **HP-Server (CPU)** — Lokale Ollama-Instanz, immer verfügbar, langsamer.
2. **Desktop-PC (GPU)** — Remote via Tailscale, GTX 1060, schneller.

Das Routing erfolgt nach folgenden Regeln:
- ``power_mode=False`` → Immer CPU (Strom sparen, leise).
- ``power_mode=True``  → GPU bevorzugt, CPU als Hot-Failover.
- GPU nicht erreichbar → Transparentes, automatisches CPU-Failover.

Zusätzlich wird die GPU-Verfügbarkeit periodisch gecached (TTL-basiert),
um nicht bei jedem Request einen Ping durchführen zu müssen.

Verwendung::

    from app.services.ollama_router import OllamaSmartRouter

    router = OllamaSmartRouter()
    response = await router.route_generate(
        prompt="Erkläre Quantencomputing.",
        system_prompt="Du bist ein Physik-Professor.",
        power_mode=True,
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncGenerator, Optional

from app.config import settings
from app.services.clients.ollama import (
    OllamaChatResponse,
    OllamaClient,
    OllamaGenerateResponse,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger(
    "homelab_imperium.services.ollama_router"
)


# ═══════════════════════════════════════════════════════════════════════════════
# Enums & Datenklassen
# ═══════════════════════════════════════════════════════════════════════════════


class BackendNode(str, Enum):
    """Identifikatoren der Inferenz-Backends."""

    CPU = "HP-Server (CPU)"
    GPU = "Desktop-PC (GTX 1060)"


@dataclass
class BackendHealth:
    """Gesundheitszustand eines Inferenz-Backends."""

    node: BackendNode
    is_healthy: bool = False
    last_ping_ms: float = 0.0
    last_checked_at: float = 0.0  # Unix-Timestamp
    model_count: int = 0
    error_message: str = ""


@dataclass
class RoutingDecision:
    """Ergebnis der Routing-Entscheidung (Debug & Monitoring)."""

    selected_node: BackendNode
    reason: str = ""
    cpu_healthy: bool = False
    gpu_healthy: bool = False
    power_mode_requested: bool = False
    fallback_occurred: bool = False


@dataclass
class RoutedGenerateResponse:
    """Ergebnis einer gerouteten Generate-Anfrage."""

    response: str
    model: str
    node: BackendNode
    execution_time_ms: float
    tokens_per_second: float
    eval_count: int = 0
    routing: RoutingDecision = field(default_factory=RoutingDecision)


@dataclass
class RoutedChatResponse:
    """Ergebnis einer gerouteten Chat-Anfrage."""

    message: dict
    model: str
    node: BackendNode
    execution_time_ms: float
    tokens_per_second: float
    eval_count: int = 0
    routing: RoutingDecision = field(default_factory=RoutingDecision)


# ═══════════════════════════════════════════════════════════════════════════════
# OllamaSmartRouter
# ═══════════════════════════════════════════════════════════════════════════════


class OllamaSmartRouter:
    """
    Intelligenter Router für Ollama-Inferenz-Anfragen.

    Features:
    - Dual-Backend: Lokaler CPU-Server + Remote GPU-Desktop
    - Power-Mode: Stromsparend (CPU) vs. Performance (GPU)
    - Hot-Failover: GPU-Ausfall → transparentes CPU-Fallback
    - Health-Caching: GPU-Ping wird TTL-basiert gecached
    - Streaming-Unterstützung: Beide Backends, beide Modi
    - Agent-spezifische Modellauswahl (aus YAML-Konfiguration)
    """

    # TTL für GPU-Health-Cache in Sekunden
    _HEALTH_CACHE_TTL: float = 30.0

    # Timeout für Health-Check-Pings (kurz!)
    _HEALTH_PING_TIMEOUT: int = 5

    def __init__(self) -> None:
        """
        Initialisiert beide Ollama-Clients und den Health-Cache.

        Liest Endpunkte, Modelle und Timeouts aus ``app.config.settings``.
        """
        # ── CPU-Backend (HP-Server, immer lokal) ──
        self._cpu_client: OllamaClient = OllamaClient(
            host=settings.ollama_local_endpoint,
            timeout=settings.ollama_local_timeout,
        )
        self._cpu_model: str = settings.ollama_local_default_model
        self._cpu_temperature: float = settings.ollama_local_temperature
        self._cpu_max_tokens: int = settings.ollama_local_max_tokens

        logger.info(
            "CPU-Backend: %s (Modell: %s, timeout=%ds)",
            settings.ollama_local_endpoint,
            self._cpu_model,
            settings.ollama_local_timeout,
        )

        # ── GPU-Backend (Desktop-PC via Tailscale) ──
        self._gpu_client: OllamaClient = OllamaClient(
            host=settings.ollama_desktop_endpoint,
            timeout=settings.ollama_desktop_timeout,
        )
        self._gpu_model: str = settings.ollama_desktop_default_model
        self._gpu_temperature: float = settings.ollama_desktop_temperature
        self._gpu_max_tokens: int = settings.ollama_desktop_max_tokens

        logger.info(
            "GPU-Backend: %s (Modell: %s, timeout=%ds)",
            settings.ollama_desktop_endpoint,
            self._gpu_model,
            settings.ollama_desktop_timeout,
        )

        # ── Health-Cache ──
        self._cpu_health: BackendHealth = BackendHealth(node=BackendNode.CPU)
        self._gpu_health: BackendHealth = BackendHealth(node=BackendNode.GPU)

        # Lock für Health-Check (verhindert parallele Pings)
        self._health_lock: asyncio.Lock = asyncio.Lock()

        # ── Ping-Parameter ──
        self._ping_interval: int = settings.ollama_desktop_ping_interval
        self._max_ping_failures: int = (
            settings.ollama_desktop_max_ping_failures
        )
        self._consecutive_gpu_failures: int = 0

        # ── Feature-Flags ──
        self._power_mode_default: bool = settings.feature_power_mode_default

        # ── Agent-Modell-Mapping (kann dynamisch erweitert werden) ──
        self._agent_models: dict[str, dict[str, str]] = {
            "it_tutor": {
                "cpu": "qwen2.5-coder:1.5b",
                "gpu": "qwen2.5-coder:7b",
            },
            "auto_engineer": {
                "cpu": "phi3:3.8b",
                "gpu": "llama3.1:8b",
            },
            "medical_health": {
                "cpu": "qwen2.5:3b",
                "gpu": "llama3.1:8b",
            },
            "brainstorm_agent": {
                "cpu": "llama3.2:3b",
                "gpu": "llama3.1:8b",
            },
        }

        logger.info(
            "OllamaSmartRouter initialisiert. "
            "Power-Mode-Default: %s, GPU-Ping-Intervall: %ds, "
            "Max-Ping-Failures: %d",
            self._power_mode_default,
            self._ping_interval,
            self._max_ping_failures,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Health-Check-System
    # ──────────────────────────────────────────────────────────────────────

    async def check_health(self) -> dict[str, BackendHealth]:
        """
        Führt einen vollständigen Health-Check BEIDER Backends durch.

        Aktualisiert die internen Health-Caches und gibt den Zustand
        beider Backends zurück.

        Returns:
            Dictionary mit ``{"cpu": BackendHealth, "gpu": BackendHealth}``.
        """
        async with self._health_lock:
            # CPU-Check (fast immer healthy, da lokal)
            self._cpu_health = await self._ping_backend(
                client=self._cpu_client,
                node=BackendNode.CPU,
            )

            # GPU-Check (kann fehlschlagen — remote)
            self._gpu_health = await self._ping_backend(
                client=self._gpu_client,
                node=BackendNode.GPU,
            )

            # Consecutive-Failure-Counter aktualisieren
            if self._gpu_health.is_healthy:
                self._consecutive_gpu_failures = 0
            else:
                self._consecutive_gpu_failures += 1

        return {
            "cpu": self._cpu_health,
            "gpu": self._gpu_health,
        }

    async def _ping_backend(
        self,
        client: OllamaClient,
        node: BackendNode,
    ) -> BackendHealth:
        """
        Führt einen Health-Check-Ping gegen EIN Backend durch.

        Nutzt ``/api/tags`` als Health-Check (listet Modelle auf).

        Args:
            client: Der OllamaClient für dieses Backend.
            node: Welcher Knoten (CPU/GPU).

        Returns:
            ``BackendHealth`` mit aktuellem Zustand.
        """
        health: BackendHealth = BackendHealth(
            node=node,
            last_checked_at=time.time(),
        )

        start: float = time.monotonic()
        try:
            # Ping mit kurzem Timeout
            is_alive: bool = await client.ping()
            elapsed: float = (time.monotonic() - start) * 1000

            if is_alive:
                # Zusätzliche Info: Wie viele Modelle sind verfügbar?
                try:
                    models: list = await client.list_models()
                    health.model_count = len(models)
                except Exception:
                    health.model_count = -1  # unbekannt

                health.is_healthy = True
                health.last_ping_ms = round(elapsed, 2)
                logger.debug(
                    "%s-Health-Check: ✅ gesund (%.1f ms, %d Modelle)",
                    node.value,
                    elapsed,
                    health.model_count,
                )
            else:
                health.is_healthy = False
                health.last_ping_ms = round(elapsed, 2)
                health.error_message = "Ping gab False zurück."
                logger.warning(
                    "%s-Health-Check: ❌ nicht erreichbar (%.1f ms)",
                    node.value,
                    elapsed,
                )

        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            health.is_healthy = False
            health.last_ping_ms = round(elapsed, 2)
            health.error_message = str(exc)[:200]
            logger.warning(
                "%s-Health-Check: ❌ Fehler — %s (%.1f ms)",
                node.value,
                exc,
                elapsed,
            )

        return health

    async def is_gpu_healthy(self, force_check: bool = False) -> bool:
        """
        Prüft, ob die GPU derzeit erreichbar ist.

        Nutzt den TTL-Cache, um nicht bei jedem Request einen Ping
        durchführen zu müssen.

        Args:
            force_check: ``True`` = Cache ignorieren, sofort pingen.

        Returns:
            ``True`` wenn die GPU erreichbar ist.
        """
        now: float = time.time()

        # Cache noch gültig?
        if (
            not force_check
            and self._gpu_health.last_checked_at > 0
            and (now - self._gpu_health.last_checked_at)
            < self._HEALTH_CACHE_TTL
        ):
            return self._gpu_health.is_healthy

        # Cache abgelaufen → neu pingen
        await self.check_health()
        return self._gpu_health.is_healthy

    async def is_gpu_degraded(self) -> bool:
        """
        Prüft, ob die GPU als "degradiert" gilt (zu viele Ping-Failures).

        Wenn die GPU mehr als ``_max_ping_failures`` Mal hintereinander
        nicht erreichbar war, wird sie als degradiert markiert und es
        wird für eine Weile nicht erneut versucht.

        Returns:
            ``True`` wenn die GPU degradiert ist (nicht benutzen).
        """
        return self._consecutive_gpu_failures >= self._max_ping_failures

    # ──────────────────────────────────────────────────────────────────────
    # Routing-Logik
    # ──────────────────────────────────────────────────────────────────────

    async def _decide_backend(
        self,
        power_mode: bool,
        agent_name: str | None = None,
    ) -> tuple[OllamaClient, str, RoutingDecision]:
        """
        Trifft die Routing-Entscheidung: CPU oder GPU?

        Entscheidungslogik:
        1. ``power_mode=False`` → Immer CPU.
        2. ``power_mode=True``:
           a. GPU gesund? → GPU.
           b. GPU degradiert? → CPU (mit Log-Warnung).
           c. GPU tot? → CPU (transparentes Failover).

        Args:
            power_mode: Vom Nutzer angeforderter Power-Mode.
            agent_name: Optionaler Agent-Name für Modellauswahl.

        Returns:
            Tuple aus (OllamaClient, model_name, RoutingDecision).
        """
        decision: RoutingDecision = RoutingDecision(
            selected_node=BackendNode.CPU,
            power_mode_requested=power_mode,
        )

        # CPU ist immer das gesunde Fallback
        cpu_ok: bool = self._cpu_health.is_healthy or await self.is_gpu_healthy(
            force_check=False
        )  # CPU nicht extra checken
        # CPU-Health explizit prüfen (Fallback muss gehen)
        if not self._cpu_health.is_healthy:
            await self.check_health()
        decision.cpu_healthy = self._cpu_health.is_healthy

        if not power_mode:
            # ── Stromsparmodus: IMMER CPU ──
            decision.selected_node = BackendNode.CPU
            decision.reason = "power_mode=False → CPU gewählt (Stromsparmodus)."
            logger.debug(decision.reason)
        else:
            # ── Power-Mode: GPU bevorzugt ──
            gpu_ok: bool = await self.is_gpu_healthy()
            gpu_degraded: bool = await self.is_gpu_degraded()
            decision.gpu_healthy = gpu_ok

            if gpu_ok and not gpu_degraded:
                # GPU ist fit → nehmen!
                decision.selected_node = BackendNode.GPU
                decision.reason = (
                    "power_mode=True, GPU gesund → GPU gewählt."
                )
                logger.debug(decision.reason)
            elif gpu_ok and gpu_degraded:
                # GPU erreichbar, aber zu viele Failures → erstmal CPU
                decision.selected_node = BackendNode.CPU
                decision.fallback_occurred = True
                decision.reason = (
                    f"GPU degradiert ({self._consecutive_gpu_failures} "
                    f"Ping-Failures) → CPU-Failover."
                )
                logger.warning(decision.reason)
            else:
                # GPU tot → transparentes Failover auf CPU
                decision.selected_node = BackendNode.CPU
                decision.fallback_occurred = True
                decision.reason = (
                    "GPU nicht erreichbar → transparentes CPU-Failover."
                )
                logger.warning(decision.reason)

        # Modell basierend auf Knoten und Agent auswählen
        model: str = self._select_model(decision.selected_node, agent_name)

        client: OllamaClient = (
            self._gpu_client
            if decision.selected_node == BackendNode.GPU
            else self._cpu_client
        )

        temperature: float = (
            self._gpu_temperature
            if decision.selected_node == BackendNode.GPU
            else self._cpu_temperature
        )

        max_tokens: int = (
            self._gpu_max_tokens
            if decision.selected_node == BackendNode.GPU
            else self._cpu_max_tokens
        )

        logger.info(
            "Routing-Entscheidung: %s | Modell: %s | Power: %s | "
            "Failover: %s",
            decision.selected_node.value,
            model,
            power_mode,
            decision.fallback_occurred,
        )

        return client, model, decision

    def _select_model(
        self,
        node: BackendNode,
        agent_name: str | None,
    ) -> str:
        """
        Wählt das passende Modell für Agent und Knoten aus.

        Priorität:
        1. Agent-spezifisches Mapping (aus ``_agent_models``).
        2. Globales Default-Modell für den Knoten.

        Args:
            node: CPU oder GPU.
            agent_name: Agent-Name (z.B. "it_tutor").

        Returns:
            Modellname (z.B. ``"qwen2.5-coder:7b"``).
        """
        if agent_name and agent_name in self._agent_models:
            key: str = "gpu" if node == BackendNode.GPU else "cpu"
            return self._agent_models[agent_name][key]

        # Fallback: Globales Modell
        return (
            self._gpu_model
            if node == BackendNode.GPU
            else self._cpu_model
        )

    # ──────────────────────────────────────────────────────────────────────
    # Öffentliche Routing-API — Generate
    # ──────────────────────────────────────────────────────────────────────

    async def route_generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        power_mode: bool | None = None,
        agent_name: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> RoutedGenerateResponse:
        """
        Führt eine nicht-streamende Textgenerierung über das beste
        verfügbare Backend durch.

        Args:
            prompt: Nutzer-Prompt.
            system_prompt: Optionaler System-Prompt.
            power_mode: ``True`` = GPU bevorzugt, ``None`` = Default aus Settings.
            agent_name: Optionaler Agent für Modellauswahl.
            temperature: Override für Temperatur.
            max_tokens: Override für max_tokens.

        Returns:
            ``RoutedGenerateResponse`` mit Antworttext, Metriken und
            Routing-Entscheidung.
        """
        pmode: bool = (
            power_mode
            if power_mode is not None
            else self._power_mode_default
        )

        client, model, decision = await self._decide_backend(
            power_mode=pmode,
            agent_name=agent_name,
        )

        start_time: float = time.monotonic()

        try:
            result: OllamaGenerateResponse = await client.generate(
                model=model,
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            # GPU-Fehler während der Generate-Anfrage → CPU-Failover
            if (
                decision.selected_node == BackendNode.GPU
                and not decision.fallback_occurred
            ):
                logger.warning(
                    "GPU-Generate fehlgeschlagen: %s → "
                    "Transparentes CPU-Failover.",
                    exc,
                )
                decision.selected_node = BackendNode.CPU
                decision.fallback_occurred = True
                decision.reason += (
                    f" (GPU-Fehler beim Generate: {exc})"
                )
                client = self._cpu_client
                model = self._select_model(BackendNode.CPU, agent_name)

                result = await client.generate(
                    model=model,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            else:
                raise

        elapsed: float = (time.monotonic() - start_time) * 1000

        return RoutedGenerateResponse(
            response=result.response,
            model=model,
            node=decision.selected_node,
            execution_time_ms=round(elapsed, 2),
            tokens_per_second=result.tokens_per_second,
            eval_count=result.eval_count,
            routing=decision,
        )

    async def route_generate_stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        power_mode: bool | None = None,
        agent_name: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Führt eine SSE-Streaming-Textgenerierung durch.

        Yields:
            Text-Tokens (Strings), die direkt an den Client
            weitergestreamt werden können.
        """
        pmode: bool = (
            power_mode
            if power_mode is not None
            else self._power_mode_default
        )

        client, model, decision = await self._decide_backend(
            power_mode=pmode,
            agent_name=agent_name,
        )

        logger.info(
            "Starte Generate-Stream: node=%s, model=%s, power=%s",
            decision.selected_node.value,
            model,
            pmode,
        )

        try:
            async for token in client.generate_stream(
                model=model,
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield token

        except Exception as exc:
            # GPU-Fehler während Stream → CPU-Failover
            if (
                decision.selected_node == BackendNode.GPU
                and not decision.fallback_occurred
            ):
                logger.warning(
                    "GPU-Stream fehlgeschlagen: %s → "
                    "Transparentes CPU-Failover.",
                    exc,
                )
                client = self._cpu_client
                model = self._select_model(BackendNode.CPU, agent_name)

                async for token in client.generate_stream(
                    model=model,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    yield token
            else:
                raise

    # ──────────────────────────────────────────────────────────────────────
    # Öffentliche Routing-API — Chat
    # ──────────────────────────────────────────────────────────────────────

    async def route_chat(
        self,
        messages: list[dict[str, str]],
        power_mode: bool | None = None,
        agent_name: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> RoutedChatResponse:
        """
        Führt eine nicht-streamende Chat-Generierung durch.

        Args:
            messages: Message-Liste (OpenAI-Format).
            power_mode, agent_name, temperature, max_tokens:
                Siehe ``route_generate``.

        Returns:
            ``RoutedChatResponse`` mit Antwort-Message und Metriken.
        """
        pmode: bool = (
            power_mode
            if power_mode is not None
            else self._power_mode_default
        )

        client, model, decision = await self._decide_backend(
            power_mode=pmode,
            agent_name=agent_name,
        )

        start_time: float = time.monotonic()

        try:
            result: OllamaChatResponse = await client.chat(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            if (
                decision.selected_node == BackendNode.GPU
                and not decision.fallback_occurred
            ):
                logger.warning(
                    "GPU-Chat fehlgeschlagen: %s → CPU-Failover.", exc
                )
                decision.selected_node = BackendNode.CPU
                decision.fallback_occurred = True
                client = self._cpu_client
                model = self._select_model(BackendNode.CPU, agent_name)

                result = await client.chat(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            else:
                raise

        elapsed: float = (time.monotonic() - start_time) * 1000

        return RoutedChatResponse(
            message=result.message,
            model=model,
            node=decision.selected_node,
            execution_time_ms=round(elapsed, 2),
            tokens_per_second=result.tokens_per_second,
            eval_count=result.eval_count,
            routing=decision,
        )

    async def route_chat_stream(
        self,
        messages: list[dict[str, str]],
        power_mode: bool | None = None,
        agent_name: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Führt eine SSE-Streaming-Chat-Generierung durch.

        Yields:
            Text-Tokens aus der Chat-Antwort.
        """
        pmode: bool = (
            power_mode
            if power_mode is not None
            else self._power_mode_default
        )

        client, model, decision = await self._decide_backend(
            power_mode=pmode,
            agent_name=agent_name,
        )

        logger.info(
            "Starte Chat-Stream: node=%s, model=%s, power=%s",
            decision.selected_node.value,
            model,
            pmode,
        )

        try:
            async for token in client.chat_stream(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield token

        except Exception as exc:
            if (
                decision.selected_node == BackendNode.GPU
                and not decision.fallback_occurred
            ):
                logger.warning(
                    "GPU-Chat-Stream fehlgeschlagen: %s → "
                    "CPU-Failover.",
                    exc,
                )
                client = self._cpu_client
                model = self._select_model(BackendNode.CPU, agent_name)

                async for token in client.chat_stream(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    yield token
            else:
                raise

    # ──────────────────────────────────────────────────────────────────────
    # Statistiken & Status
    # ──────────────────────────────────────────────────────────────────────

    async def get_status(self) -> dict:
        """
        Liefert den aktuellen Status des Routers (für Dashboard/Monitoring).

        Returns:
            Dictionary mit Health-Status beider Backends, Routing-Statistiken
            und Konfiguration.
        """
        await self.check_health()

        return {
            "cpu": {
                "endpoint": settings.ollama_local_endpoint,
                "default_model": self._cpu_model,
                "healthy": self._cpu_health.is_healthy,
                "last_ping_ms": self._cpu_health.last_ping_ms,
                "models_available": self._cpu_health.model_count,
                "error": self._cpu_health.error_message,
            },
            "gpu": {
                "endpoint": settings.ollama_desktop_endpoint,
                "default_model": self._gpu_model,
                "healthy": self._gpu_health.is_healthy,
                "degraded": await self.is_gpu_degraded(),
                "consecutive_failures": self._consecutive_gpu_failures,
                "last_ping_ms": self._gpu_health.last_ping_ms,
                "models_available": self._gpu_health.model_count,
                "error": self._gpu_health.error_message,
            },
            "config": {
                "power_mode_default": self._power_mode_default,
                "gpu_ping_interval_s": self._ping_interval,
                "max_ping_failures": self._max_ping_failures,
                "health_cache_ttl_s": self._HEALTH_CACHE_TTL,
            },
        }
