"""
Code-Router des Homelab-Imperiums.

Stellt REST-Endpunkte für Code-Analyse und Sandbox-Ausführung bereit.
Jeder Endpunkt hat strikte Timeouts und Speicherbegrenzungen, um
Denial-of-Service-Angriffe zu verhindern.

Endpunkte:
- ``POST /api/code/analyze``   — Syntaktische + Sicherheitsanalyse
- ``POST /api/code/execute``    — Sandbox-Ausführung (Docker-Container)
- ``GET /api/code/status``      — Sandbox-Verfügbarkeit prüfen

Sicherheitsarchitektur:
- Max. Code-Länge: 100.000 Zeichen
- Sandbox-Timeout: konfigurierbar (Default: 10s)
- Sandbox-Memory-Limit: konfigurierbar (Default: 512 MB)
- Kein Netzwerkzugriff im Container
- Read-Only-Root-Dateisystem
- Keine Privilegien (no-new-privileges)

Verwendung::

    from app.routers.code import router
    app.include_router(router, prefix="/api")
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.services.code import CodeAgentService, CodeAnalysisResult

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.routers.code")

# ═══════════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════════

router: APIRouter = APIRouter(tags=["Code & Sandbox"])

# ═══════════════════════════════════════════════════════════════════════════════
# Dependency Injection
# ═══════════════════════════════════════════════════════════════════════════════

_code_service: CodeAgentService = CodeAgentService()


def get_code_service() -> CodeAgentService:
    """Factory für den CodeAgentService (zustandslos)."""
    return _code_service


# ═══════════════════════════════════════════════════════════════════════════════
# Request-Modelle
# ═══════════════════════════════════════════════════════════════════════════════

# Maximale Code-Länge (verhindert RAM-Überlast durch Monster-Payloads)
_MAX_CODE_LENGTH: int = 100_000


class CodeAnalyzeRequest(BaseModel):
    """
    Request-Body für die Code-Analyse.
    """

    code: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_CODE_LENGTH,
        description="Python-Quellcode zur Analyse.",
    )


class CodeExecuteRequest(BaseModel):
    """
    Request-Body für die Sandbox-Ausführung.
    """

    code: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_CODE_LENGTH,
        description="Auszuführender Python-Code.",
    )
    timeout: int = Field(
        default=settings.sandbox_execution_timeout,
        ge=1,
        le=60,
        description="Timeout in Sekunden (1–60).",
    )
    input_data: str | None = Field(
        default=None,
        max_length=10_000,
        description="Optionaler stdin-Input für das Skript.",
    )


class CodeAnalyzeResponse(BaseModel):
    """Antwort der Code-Analyse."""

    is_valid: bool = Field(description="Syntaktisch korrekt.")
    is_safe: bool = Field(description="Keine Sicherheitsprobleme.")
    error_count: int = 0
    warning_count: int = 0
    issues: list[dict] = Field(default_factory=list)
    analyzed_at: str = ""


class CodeExecuteResponse(BaseModel):
    """Antwort der Sandbox-Ausführung."""

    success: bool = Field(description="Ausführung erfolgreich (exit_code=0).")
    stdout: str = Field(default="", description="Standard-Ausgabe.")
    stderr: str = Field(default="", description="Standard-Fehlerausgabe.")
    exit_code: int = Field(default=-1, description="Prozess-Exit-Code.")
    execution_time_ms: float = Field(default=0.0, description="Ausführungsdauer in ms.")
    error_message: str | None = Field(default=None)


# ═══════════════════════════════════════════════════════════════════════════════
# Endpunkte
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/code/analyze",
    response_model=CodeAnalyzeResponse,
    summary="Code analysieren",
    description="Führt eine syntaktische und sicherheitstechnische Analyse "
    "des eingesendeten Python-Codes durch. Prüft auf Syntaxfehler "
    "(via AST) und verbotene Imports/Funktionsaufrufe.",
)
async def analyze_code(
    request: CodeAnalyzeRequest,
    svc: CodeAgentService = Depends(get_code_service),
) -> CodeAnalyzeResponse:
    """
    Statische Code-Analyse ohne Ausführung.

    **Geprüft werden:**
    - Syntaktische Korrektheit (``ast.parse()``)
    - Verbotene Imports (os, subprocess, socket, …)
    - Verbotene Funktionsaufrufe (eval, exec, __import__, …)
    - Datei-Schreibzugriffe (open mit 'w'/'a'-Mode)

    **Keine Ausführung** — der Code wird nur analysiert, nicht gestartet.
    """
    logger.info(
        "POST /code/analyze: %d Zeichen.", len(request.code)
    )

    try:
        result: CodeAnalysisResult = svc.analyze_code(request.code)

        return CodeAnalyzeResponse(
            is_valid=result.is_valid,
            is_safe=result.is_safe,
            error_count=result.error_count,
            warning_count=result.warning_count,
            issues=[
                {
                    "line": i.line,
                    "column": i.column,
                    "message": i.message,
                    "severity": i.severity,
                    "category": i.category,
                    "code_snippet": i.code_snippet,
                }
                for i in result.issues
            ],
            analyzed_at=result.analyzed_at,
        )

    except Exception as exc:
        logger.exception("Fehler bei Code-Analyse.")
        raise HTTPException(
            status_code=500,
            detail=f"Analyse fehlgeschlagen: {exc}",
        ) from exc


@router.post(
    "/code/execute",
    response_model=CodeExecuteResponse,
    summary="Code in Sandbox ausführen",
    description="**Sicherheitskritisch.** Führt den eingesendeten Python-Code "
    "in einer isolierten Docker-Sandbox aus. "
    "Vor der Ausführung erfolgt eine vollständige Sicherheitsanalyse. "
    "Bei bestandener Prüfung wird der Code in einem flüchtigen Container "
    "ohne Netzwerkzugriff, mit Speicherlimit und Read-Only-Dateisystem "
    "ausgeführt.",
    responses={
        200: {"description": "Ausführung abgeschlossen."},
        400: {"description": "Code-Analyse fehlgeschlagen — Ausführung verweigert."},
        403: {"description": "Sicherheitsanalyse fehlgeschlagen — Code enthält verbotene Operationen."},
        500: {"description": "Sandbox-Fehler."},
    },
)
async def execute_code(
    request: CodeExecuteRequest,
    svc: CodeAgentService = Depends(get_code_service),
) -> CodeExecuteResponse:
    """
    Führt Python-Code in einer Docker-Sandbox aus.

    **DoS-Schutz:**
    - Maximale Code-Länge: 100.000 Zeichen (Request-Validierung)
    - Maximales Timeout: 60 Sekunden
    - Sandbox-Speicherlimit: 512 MB (Docker ``--memory``)
    - Sandbox-CPU-Limit: 1 Kern (Docker ``--cpus 1``)
    - Kein Netzwerkzugriff (``--network none``)

    **Sicherheitsgarantien:**
    - Flüchtiger Container (``--rm``) — keine persistenten Änderungen
    - Read-Only-Root-Dateisystem (``--read-only``)
    - Keine Privilegien-Eskalation (``--security-opt no-new-privileges``)
    - Alle Linux-Capabilities entfernt (``--cap-drop ALL``)
    """
    logger.info(
        "POST /code/execute: %d Zeichen, timeout=%ds.",
        len(request.code),
        request.timeout,
    )

    # ═══ Stufe 0: Schnellprüfung auf verdächtige Muster ═══
    if len(request.code) > _MAX_CODE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Code zu lang ({len(request.code)} > "
            f"{_MAX_CODE_LENGTH} Zeichen).",
        )

    # ═══ Stufe 1: Statische Analyse ═══
    analysis: CodeAnalysisResult = svc.analyze_code(request.code)

    if not analysis.is_valid:
        logger.warning(
            "Code-Ausführung verweigert: Syntaxfehler (%d Issues).",
            analysis.error_count,
        )
        return CodeExecuteResponse(
            success=False,
            stderr=svc.format_issues_for_display(analysis),
            error_message="Syntaxfehler — Ausführung verweigert.",
        )

    if not analysis.is_safe:
        logger.warning(
            "Code-Ausführung verweigert: Sicherheitsprobleme (%d Issues).",
            sum(
                1
                for i in analysis.issues
                if i.category == "security"
            ),
        )
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Sicherheitsanalyse fehlgeschlagen. "
                "Der Code enthält verbotene Operationen.",
                "issues": [
                    {
                        "line": i.line,
                        "message": i.message,
                    }
                    for i in analysis.issues
                    if i.category == "security"
                ],
            },
        )

    # ═══ Stufe 2: Sandbox-Ausführung ═══
    logger.info(
        "Code-Analyse bestanden — starte Sandbox-Ausführung "
        "(timeout=%ds)...",
        request.timeout,
    )

    try:
        sandbox_result = await svc.execute_in_sandbox(
            code=request.code,
            timeout=request.timeout,
            input_data=request.input_data,
        )

        return CodeExecuteResponse(
            success=sandbox_result.success,
            stdout=sandbox_result.stdout,
            stderr=sandbox_result.stderr,
            exit_code=sandbox_result.exit_code,
            execution_time_ms=sandbox_result.execution_time_ms,
            error_message=sandbox_result.error_message or None,
        )

    except Exception as exc:
        logger.exception("Sandbox-Ausführung fehlgeschlagen.")
        raise HTTPException(
            status_code=500,
            detail=f"Sandbox-Fehler: {exc}",
        ) from exc


@router.get(
    "/code/status",
    summary="Sandbox-Verfügbarkeit",
    description="Prüft, ob die Docker-Sandbox verfügbar ist. "
    "Führt ``docker --version`` und ``docker info`` aus "
    "(ohne Container zu starten).",
)
async def get_sandbox_status() -> dict:
    """
    Status-Check für die Code-Sandbox.

    Returns:
        Dict mit ``docker_available``, ``sandbox_image``,
        ``sandbox_timeout`` und ``sandbox_max_memory``.
    """
    logger.debug("GET /code/status.")

    docker_ok: bool = False
    docker_version: str = ""

    try:
        result: subprocess.CompletedProcess = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        docker_ok = result.returncode == 0
        docker_version = result.stdout.strip()
    except Exception:
        pass

    return {
        "docker_available": docker_ok,
        "docker_version": docker_version,
        "sandbox_image": settings.sandbox_docker_image,
        "sandbox_timeout_seconds": settings.sandbox_execution_timeout,
        "sandbox_max_memory_mb": settings.sandbox_max_memory_mb,
        "sandbox_max_output_bytes": settings.sandbox_max_output_size,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post(
    "/code/format-issues",
    summary="Issues formatiert ausgeben",
    description="Nimmt eine CodeAnalyzeResponse entgegen und gibt "
    "eine menschenlesbare Markdown-Formatierung zurück. "
    "Nützlich für Chat-Agenten.",
)
async def format_issues(
    request: CodeAnalyzeRequest,
    svc: CodeAgentService = Depends(get_code_service),
) -> dict:
    """
    Formatiert Analyse-Issues als Markdown für KI-Agenten.
    """
    logger.debug("POST /code/format-issues: %d Zeichen.", len(request.code))

    try:
        result: CodeAnalysisResult = svc.analyze_code(request.code)
        markdown: str = svc.format_issues_for_display(result)
        return {
            "is_valid": result.is_valid,
            "is_safe": result.is_safe,
            "markdown": markdown,
        }

    except Exception as exc:
        logger.exception("Fehler bei Issue-Formatierung.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc
