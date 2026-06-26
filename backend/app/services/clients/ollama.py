"""
Ollama-Client-Wrapper für das Homelab-Imperium.

Asynchroner HTTP-Client (httpx) für die Ollama-REST-API. Unterstützt:
- Textgenerierung (``/api/generate``) — synchron & SSE-Streaming
- Chat-Generierung (``/api/chat``) — mit Message-Historie
- Modell-Management (``/api/tags``, ``/api/show``, ``/api/pull``)
- Statusdiagnose (``/api/ps``, Heartbeat)

Verwendung::

    from app.services.clients.ollama import OllamaClient

    client = OllamaClient(host="http://127.0.0.1:11434")
    if await client.ping():
        async for token in client.generate_stream("qwen2.5:3b", "Hallo!"):
            print(token, end="")
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional

import httpx

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.clients.ollama")


# ═══════════════════════════════════════════════════════════════════════════════
# Datenklassen für API-Responses
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class OllamaModelInfo:
    """Metadaten eines Ollama-Modells."""

    name: str
    modified_at: str = ""
    size_bytes: int = 0
    digest: str = ""
    format: str = ""
    family: str = ""
    parameter_size: str = ""
    quantization_level: str = ""


@dataclass
class OllamaGenerateResponse:
    """Nicht-Streaming-Ergebnis einer ``/api/generate``-Anfrage."""

    response: str
    model: str = ""
    total_duration_ms: float = 0.0
    load_duration_ms: float = 0.0
    prompt_eval_count: int = 0
    prompt_eval_duration_ms: float = 0.0
    eval_count: int = 0
    eval_duration_ms: float = 0.0
    done: bool = True
    tokens_per_second: float = 0.0


@dataclass
class OllamaChatResponse:
    """Nicht-Streaming-Ergebnis einer ``/api/chat``-Anfrage."""

    message: dict[str, str] = field(default_factory=dict)
    model: str = ""
    total_duration_ms: float = 0.0
    eval_count: int = 0
    eval_duration_ms: float = 0.0
    tokens_per_second: float = 0.0
    done: bool = True


@dataclass
class OllamaRunningModel:
    """Informationen über ein aktuell geladenes Modell."""

    name: str
    model: str = ""
    size_bytes: int = 0
    digest: str = ""
    expires_at: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# OllamaClient
# ═══════════════════════════════════════════════════════════════════════════════


class OllamaClient:
    """
    Asynchroner Client für die Ollama-REST-API.

    Unterstützt lokale und remote Ollama-Instanzen. Alle Methoden sind
    ``async`` und nutzen ``httpx.AsyncClient`` für HTTP/1.1-Kommunikation.

    API-Referenz: https://github.com/ollama/ollama/blob/main/docs/api.md
    """

    # Standard-Timeout für nicht-streaming Requests (Sekunden)
    _DEFAULT_TIMEOUT: int = 120

    # Timeout für Ping-Requests (kurz)
    _PING_TIMEOUT: int = 5

    # Maximale Wiederholungsversuche bei Verbindungsfehlern
    _MAX_RETRIES: int = 2

    def __init__(
        self,
        host: str,
        timeout: int | None = None,
    ) -> None:
        """
        Initialisiert den Ollama-Client.

        Args:
            host: Basis-URL der Ollama-Instanz (z.B. ``"http://127.0.0.1:11434"``).
            timeout: Timeout in Sekunden für Generate-/Chat-Requests.
                     ``None`` = Default (120 s).
        """
        self.host: str = host.rstrip("/")
        self.timeout: int = timeout or self._DEFAULT_TIMEOUT
        logger.info(
            "Ollama-Client initialisiert: host=%s, timeout=%ds",
            self.host,
            self.timeout,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Private Hilfsmethoden
    # ──────────────────────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        """Baut eine vollständige Ollama-API-URL zusammen."""
        return f"{self.host}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict | None = None,
        timeout: int | None = None,
        stream: bool = False,
    ) -> httpx.Response:
        """
        Führt einen HTTP-Request gegen die Ollama-API aus.

        Beinhaltet automatische Retry-Logik für Verbindungsfehler.

        Args:
            method: HTTP-Methode (GET, POST).
            path: API-Pfad (z.B. ``"/api/generate"``).
            json_data: JSON-Body für POST-Requests.
            timeout: Timeout in Sekunden (Default: self.timeout).
            stream: ``True`` für SSE-Streaming-Responses.

        Returns:
            ``httpx.Response``-Objekt.

        Raises:
            httpx.ConnectError: Bei Verbindungsfehlern nach allen Retries.
            httpx.HTTPStatusError: Bei HTTP-Fehlern (4xx, 5xx).
        """
        url: str = self._url(path)
        req_timeout: int = timeout or self.timeout

        last_exc: Exception | None = None

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(req_timeout),
                ) as client:
                    response: httpx.Response = await client.request(
                        method=method,
                        url=url,
                        json=json_data,
                    )
                    response.raise_for_status()
                    return response

            except httpx.ConnectError as exc:
                logger.warning(
                    "Ollama-Verbindungsfehler (%s) — Versuch %d/%d: %s",
                    url,
                    attempt,
                    self._MAX_RETRIES,
                    exc,
                )
                last_exc = exc
                if attempt < self._MAX_RETRIES:
                    await asyncio.sleep(0.5 * attempt)

            except httpx.HTTPStatusError as exc:
                # Keine Wiederholung bei Client/Server-HTTP-Fehlern
                logger.error(
                    "Ollama-HTTP-Fehler: %s %s → HTTP %d: %s",
                    method,
                    url,
                    exc.response.status_code,
                    exc.response.text[:300],
                )
                raise

            except httpx.ReadTimeout as exc:
                logger.error(
                    "Ollama-Timeout nach %ds: %s %s",
                    req_timeout,
                    method,
                    url,
                )
                raise

        raise last_exc or httpx.ConnectError(
            f"Ollama nicht erreichbar nach {self._MAX_RETRIES} Versuchen: "
            f"{self.host}"
        )

    # ──────────────────────────────────────────────────────────────────────
    # Health-Check & Status
    # ──────────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """
        Prüft die Erreichbarkeit der Ollama-Instanz.

        Ruft ``/api/tags`` auf — der leichteste Endpunkt, der eine
        funktionierende Ollama-Installation bestätigt.

        Returns:
            ``True`` wenn die Instanz erreichbar ist und antwortet.
        """
        try:
            response: httpx.Response = await self._request(
                "GET", "/api/tags", timeout=self._PING_TIMEOUT
            )
            data: dict = response.json()
            models: list[dict] = data.get("models", [])
            logger.debug(
                "Ollama-Ping erfolgreich: %d Modelle verfügbar auf %s.",
                len(models),
                self.host,
            )
            return True
        except Exception as exc:
            logger.debug(
                "Ollama-Ping fehlgeschlagen für %s: %s", self.host, exc
            )
            return False

    # ──────────────────────────────────────────────────────────────────────
    # Modell-Management
    # ──────────────────────────────────────────────────────────────────────

    async def list_models(self) -> list[OllamaModelInfo]:
        """
        Listet alle lokal verfügbaren Ollama-Modelle auf.

        Endpunkt: ``GET /api/tags``

        Returns:
            Liste von ``OllamaModelInfo``-Objekten, sortiert nach Name.
        """
        logger.info("Rufe Modell-Liste ab: %s", self.host)
        response: httpx.Response = await self._request("GET", "/api/tags")
        data: dict = response.json()

        models: list[OllamaModelInfo] = []
        for raw in data.get("models", []):
            details: dict = raw.get("details", {})
            models.append(
                OllamaModelInfo(
                    name=raw.get("name", "unbekannt"),
                    modified_at=raw.get("modified_at", ""),
                    size_bytes=raw.get("size", 0),
                    digest=raw.get("digest", ""),
                    format=details.get("format", ""),
                    family=details.get("family", ""),
                    parameter_size=details.get("parameter_size", ""),
                    quantization_level=details.get(
                        "quantization_level", ""
                    ),
                )
            )

        models.sort(key=lambda m: m.name)
        logger.info(
            "%d Modelle auf %s gefunden: %s",
            len(models),
            self.host,
            [m.name for m in models],
        )
        return models

    async def show_model(self, model_name: str) -> dict:
        """
        Zeigt detaillierte Informationen zu einem Modell.

        Endpunkt: ``POST /api/show``

        Args:
            model_name: Name des Modells (z.B. ``"qwen2.5-coder:3b"``).

        Returns:
            Dictionary mit Modell-Parametern, Template, Lizenz etc.
        """
        logger.debug("Rufe Modell-Info ab: %s", model_name)
        response: httpx.Response = await self._request(
            "POST",
            "/api/show",
            json_data={"name": model_name},
        )
        return response.json()

    async def pull_model(
        self,
        model_name: str,
        insecure: bool = False,
    ) -> AsyncGenerator[str, None]:
        """
        Lädt ein Modell aus der Ollama-Registry herunter (Streaming).

        Endpunkt: ``POST /api/pull`` — liefert SSE-Stream mit Fortschritt.

        Args:
            model_name: Name des Modells (z.B. ``"llama3.2:3b"``).
            insecure: ``True`` = TLS-Verifikation deaktivieren.

        Yields:
            Status-JSON-Strings: ``{"status": "pulling manifest", ...}``
        """
        logger.info("Lade Modell herunter: %s", model_name)
        url: str = self._url("/api/pull")

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(600.0),  # 10 min für große Modelle
            ) as client:
                async with client.stream(
                    "POST",
                    url,
                    json={
                        "name": model_name,
                        "insecure": insecure,
                        "stream": True,
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.strip():
                            yield line
        except Exception as exc:
            logger.error(
                "Fehler beim Herunterladen von Modell %r: %s",
                model_name,
                exc,
            )
            raise

    async def list_running(self) -> list[OllamaRunningModel]:
        """
        Listet alle aktuell in den RAM geladenen Modelle.

        Endpunkt: ``GET /api/ps``

        Returns:
            Liste von ``OllamaRunningModel``-Objekten.
        """
        logger.debug("Rufe laufende Modelle ab: %s", self.host)
        response: httpx.Response = await self._request("GET", "/api/ps")
        data: dict = response.json()

        running: list[OllamaRunningModel] = []
        for raw in data.get("models", []):
            running.append(
                OllamaRunningModel(
                    name=raw.get("name", ""),
                    model=raw.get("model", ""),
                    size_bytes=raw.get("size", 0),
                    digest=raw.get("digest", ""),
                    expires_at=raw.get("expires_at", ""),
                )
            )

        logger.debug(
            "%d Modelle aktuell geladen auf %s.", len(running), self.host
        )
        return running

    # ──────────────────────────────────────────────────────────────────────
    # Textgenerierung — /api/generate
    # ──────────────────────────────────────────────────────────────────────

    async def generate(
        self,
        model: str,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        stop: list[str] | None = None,
        timeout: int | None = None,
    ) -> OllamaGenerateResponse:
        """
        Führt eine einmalige, nicht-streamende Textgenerierung durch.

        Endpunkt: ``POST /api/generate`` mit ``"stream": false``.

        Args:
            model: Modellname (z.B. ``"qwen2.5-coder:7b"``).
            prompt: Der Nutzer-Prompt.
            system_prompt: Optionaler System-Prompt (Kontext).
            temperature: Kreativität (0,0–2,0).
            max_tokens: Maximale Antwortlänge in Tokens.
            top_p: Nucleus-Sampling (0,0–1,0).
            top_k: Top-K Sampling.
            stop: Liste von Stop-Sequenzen.
            timeout: Timeout für diesen Request.

        Returns:
            ``OllamaGenerateResponse`` mit Antworttext und Inferenz-Metriken.
        """
        logger.info(
            "Ollama-Generate: model=%s, prompt_len=%d, system=%s",
            model,
            len(prompt),
            bool(system_prompt),
        )
        body: dict = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            body["system"] = system_prompt
        if temperature is not None:
            body["options"] = body.get("options", {})
            body["options"]["temperature"] = temperature
        if max_tokens is not None:
            body["options"] = body.get("options", {})
            body["options"]["num_predict"] = max_tokens
        if top_p is not None:
            body["options"] = body.get("options", {})
            body["options"]["top_p"] = top_p
        if top_k is not None:
            body["options"] = body.get("options", {})
            body["options"]["top_k"] = top_k
        if stop:
            body["options"] = body.get("options", {})
            body["options"]["stop"] = stop

        start_time: float = time.monotonic()
        response: httpx.Response = await self._request(
            "POST",
            "/api/generate",
            json_data=body,
            timeout=timeout,
        )
        data: dict = response.json()
        elapsed: float = (time.monotonic() - start_time) * 1000

        result: OllamaGenerateResponse = OllamaGenerateResponse(
            response=data.get("response", ""),
            model=model,
            total_duration_ms=data.get("total_duration", 0) / 1_000_000,
            load_duration_ms=data.get("load_duration", 0) / 1_000_000,
            prompt_eval_count=data.get("prompt_eval_count", 0),
            prompt_eval_duration_ms=data.get("prompt_eval_duration", 0)
            / 1_000_000,
            eval_count=data.get("eval_count", 0),
            eval_duration_ms=data.get("eval_duration", 0) / 1_000_000,
        )
        # Tokens/s berechnen
        if result.eval_duration_ms > 0:
            result.tokens_per_second = round(
                result.eval_count / (result.eval_duration_ms / 1000), 1
            )

        logger.info(
            "Ollama-Generate abgeschlossen: %d Tokens, %.1f tok/s, "
            "%.0f ms (Client-Roundtrip: %.0f ms)",
            result.eval_count,
            result.tokens_per_second,
            result.total_duration_ms,
            elapsed,
        )
        return result

    async def generate_stream(
        self,
        model: str,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        stop: list[str] | None = None,
        timeout: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Führt eine SSE-Streaming-Textgenerierung durch.

        Endpunkt: ``POST /api/generate`` mit ``"stream": true``.
        Die Antwort wird als Server-Sent Events (SSE) gestreamt, wobei
        jedes JSON-Objekt ein ``response``-Feld mit einem Text-Token
        enthält.

        Args:
            model: Modellname.
            prompt: Nutzer-Prompt.
            system_prompt: Optionaler System-Prompt.
            temperature, max_tokens, top_p, top_k, stop: Inferenz-Parameter.
            timeout: Timeout.

        Yields:
            Text-Tokens (Strings), die direkt an den Client
            weitergestreamt werden können.
        """
        logger.info(
            "Ollama-Generate-Stream: model=%s, prompt_len=%d",
            model,
            len(prompt),
        )
        url: str = self._url("/api/generate")

        body: dict = {
            "model": model,
            "prompt": prompt,
            "stream": True,
        }
        if system_prompt:
            body["system"] = system_prompt
        options: dict = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if top_p is not None:
            options["top_p"] = top_p
        if top_k is not None:
            options["top_k"] = top_k
        if stop:
            options["stop"] = stop
        if options:
            body["options"] = options

        token_count: int = 0
        start_time: float = time.monotonic()

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout or self.timeout),
            ) as client:
                async with client.stream(
                    "POST", url, json=body
                ) as response:
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue

                        try:
                            chunk: dict = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning(
                                "Ungültiger SSE-Chunk: %s", line[:100]
                            )
                            continue

                        # Text-Token extrahieren und yielden
                        token: str = chunk.get("response", "")
                        if token:
                            token_count += 1
                            yield token

                        # Prüfen, ob der Stream abgeschlossen ist
                        if chunk.get("done", False):
                            elapsed: float = (
                                time.monotonic() - start_time
                            ) * 1000
                            eval_count: int = chunk.get("eval_count", token_count)
                            eval_duration: float = (
                                chunk.get("eval_duration", 0) / 1_000_000
                            )
                            tps: float = (
                                eval_count / (eval_duration / 1000)
                                if eval_duration > 0
                                else 0.0
                            )
                            logger.info(
                                "Ollama-Stream abgeschlossen: %d Tokens, "
                                "%.1f tok/s, %.0f ms Client-Zeit",
                                eval_count,
                                tps,
                                elapsed,
                            )
                            return

        except Exception as exc:
            logger.error(
                "Ollama-Stream-Fehler (model=%s, %d Tokens bisher): %s",
                model,
                token_count,
                exc,
            )
            raise

    # ──────────────────────────────────────────────────────────────────────
    # Chat-Generierung — /api/chat
    # ──────────────────────────────────────────────────────────────────────

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ) -> OllamaChatResponse:
        """
        Führt eine einmalige, nicht-streamende Chat-Generierung durch.

        Endpunkt: ``POST /api/chat`` mit ``"stream": false``.

        Args:
            model: Modellname.
            messages: Liste von Message-Dicts im OpenAI-Format:
                      ``[{"role": "system", "content": "..."},
                         {"role": "user", "content": "..."}]``
            temperature: Kreativität.
            max_tokens: Maximale Antwortlänge.
            timeout: Timeout.

        Returns:
            ``OllamaChatResponse`` mit Antwort-Message und Metriken.
        """
        logger.info(
            "Ollama-Chat: model=%s, messages=%d",
            model,
            len(messages),
        )
        body: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if temperature is not None or max_tokens is not None:
            options: dict = {}
            if temperature is not None:
                options["temperature"] = temperature
            if max_tokens is not None:
                options["num_predict"] = max_tokens
            body["options"] = options

        response: httpx.Response = await self._request(
            "POST",
            "/api/chat",
            json_data=body,
            timeout=timeout,
        )
        data: dict = response.json()

        eval_count: int = data.get("eval_count", 0)
        eval_duration_ms: float = data.get("eval_duration", 0) / 1_000_000
        tps: float = (
            eval_count / (eval_duration_ms / 1000)
            if eval_duration_ms > 0
            else 0.0
        )

        logger.info(
            "Ollama-Chat abgeschlossen: %d Tokens, %.1f tok/s",
            eval_count,
            tps,
        )
        return OllamaChatResponse(
            message=data.get("message", {}),
            model=model,
            total_duration_ms=data.get("total_duration", 0) / 1_000_000,
            eval_count=eval_count,
            eval_duration_ms=eval_duration_ms,
            tokens_per_second=round(tps, 1),
        )

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Führt eine SSE-Streaming-Chat-Generierung durch.

        Endpunkt: ``POST /api/chat`` mit ``"stream": true``.

        Args:
            model: Modellname.
            messages: Message-Liste (OpenAI-Format).
            temperature, max_tokens: Inferenz-Parameter.
            timeout: Timeout.

        Yields:
            Text-Tokens aus dem ``message.content``-Feld jedes SSE-Chunks.
        """
        logger.info(
            "Ollama-Chat-Stream: model=%s, messages=%d",
            model,
            len(messages),
        )
        url: str = self._url("/api/chat")

        body: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        options: dict = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if options:
            body["options"] = options

        token_count: int = 0
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout or self.timeout),
            ) as client:
                async with client.stream(
                    "POST", url, json=body
                ) as response:
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue

                        try:
                            chunk: dict = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Chat-Antwort: Token steckt in message.content
                        msg: dict = chunk.get("message", {})
                        token: str = msg.get("content", "")
                        if token:
                            token_count += 1
                            yield token

                        if chunk.get("done", False):
                            logger.info(
                                "Ollama-Chat-Stream abgeschlossen: "
                                "%d Tokens.",
                                token_count,
                            )
                            return

        except Exception as exc:
            logger.error(
                "Ollama-Chat-Stream-Fehler (model=%s, %d Tokens): %s",
                model,
                token_count,
                exc,
            )
            raise
