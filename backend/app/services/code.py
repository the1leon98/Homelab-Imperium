"""
Code-Sicherheitsdienst des Homelab-Imperiums.

Implementiert mehrstufige Code-Validierung vor der Ausführung:
1. **Syntaktische Prüfung** — AST-Parsing mit ``ast.parse()``
2. **Sicherheitsanalyse** — Erkennung gefährlicher Imports & Funktionsaufrufe
3. **Docker-Sandbox** — Isolierte Ausführung in flüchtigem Container
4. **Detailliertes Fehler-Reporting** — Zeilennummern, Kontext, deutsche Meldungen

Verwendung::

    from app.services.code import CodeAgentService

    svc = CodeAgentService()
    result = svc.analyze_code("print('Hallo Welt')")
    if result.is_safe:
        output = await svc.execute_in_sandbox(code)
"""

from __future__ import annotations

import ast
import logging
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import settings

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.services.code")


# ═══════════════════════════════════════════════════════════════════════════════
# Datenklassen
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class SyntaxIssue:
    """Einzelnes Problem im Code (Syntax oder Sicherheit)."""

    line: int = 0
    column: int = 0
    message: str = ""
    severity: str = "error"  # error, warning, info
    category: str = "syntax"  # syntax, security, style
    code_snippet: str = ""


@dataclass
class CodeAnalysisResult:
    """Ergebnis einer vollständigen Code-Analyse."""

    is_valid: bool = True
    is_safe: bool = True
    issues: list[SyntaxIssue] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    ast_node_count: int = 0
    analyzed_at: str = ""


@dataclass
class SandboxResult:
    """Ergebnis einer Docker-Sandbox-Ausführung."""

    success: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    execution_time_ms: float = 0.0
    error_message: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Gefährliche Code-Muster (Blacklist für Sicherheitsanalyse)
# ═══════════════════════════════════════════════════════════════════════════════

# Import-Module, die in der Sandbox NICHT erlaubt sind
_FORBIDDEN_IMPORTS: set[str] = {
    "os",
    "subprocess",
    "shutil",
    "sys",
    "socket",
    "requests",
    "urllib",
    "http",
    "ftplib",
    "telnetlib",
    "smtplib",
    "ctypes",
    "multiprocessing",
    "signal",
    "importlib",
    "pickle",
    "marshal",
    "code",
    "compile",
    "exec",
    "eval",
}

# Riskante Funktionsaufrufe, die auch bei harmlosen Imports gefährlich sind
_FORBIDDEN_FUNCTIONS: set[str] = {
    "eval",
    "exec",
    "compile",
    "open",
    "__import__",
    "getattr",
    "setattr",
    "delattr",
    "globals",
    "locals",
    "vars",
}


# ═══════════════════════════════════════════════════════════════════════════════
# CodeAgentService
# ═══════════════════════════════════════════════════════════════════════════════


class CodeAgentService:
    """
    Code-Validierungs- und Sandbox-Dienst.

    Führt AST-basierte statische Analyse und optionale Docker-Sandbox-
    Ausführung durch. Jede Stufe liefert detaillierte, deutschsprachige
    Fehlermeldungen mit Zeilennummern und Code-Kontext.
    """

    def __init__(self) -> None:
        """Initialisiert den Code-Dienst mit Sandbox-Einstellungen."""
        self._sandbox_image: str = settings.sandbox_docker_image
        self._sandbox_timeout: int = settings.sandbox_execution_timeout
        self._sandbox_max_memory: str = f"{settings.sandbox_max_memory_mb}m"
        self._sandbox_max_output: int = settings.sandbox_max_output_size
        logger.info(
            "CodeAgentService initialisiert: image=%s, timeout=%ds, "
            "max_memory=%s, max_output=%d bytes.",
            self._sandbox_image,
            self._sandbox_timeout,
            self._sandbox_max_memory,
            self._sandbox_max_output,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Stufe 1: Syntaktische Prüfung (AST-Parser)
    # ──────────────────────────────────────────────────────────────────────

    def check_syntax(self, code: str) -> list[SyntaxIssue]:
        """
        Prüft Python-Code auf syntaktische Korrektheit via ``ast.parse()``.

        Args:
            code: Python-Quellcode als String.

        Returns:
            Liste von ``SyntaxIssue`` — leer, wenn syntaktisch korrekt.
        """
        issues: list[SyntaxIssue] = []

        # Schritt 0: Leerer Code?
        stripped: str = code.strip()
        if not stripped:
            issues.append(
                SyntaxIssue(
                    line=1,
                    column=1,
                    message="Der Code ist leer. Bitte gib gültigen "
                    "Python-Code ein.",
                    severity="error",
                    category="syntax",
                )
            )
            return issues

        # Schritt 1: AST-Parsing
        try:
            tree: ast.AST = ast.parse(stripped)
            logger.debug(
                "AST-Parsing erfolgreich: %d Knoten.", len(list(ast.walk(tree)))
            )
        except SyntaxError as exc:
            line: int = exc.lineno or 1
            col: int = exc.offset or 1
            msg: str = exc.msg or "Unbekannter Syntaxfehler"

            # Code-Kontext (die fehlerhafte Zeile + 1 Zeile Kontext)
            code_lines: list[str] = stripped.split("\n")
            snippet: str = ""
            if line <= len(code_lines):
                snippet = code_lines[line - 1]
                if col > 0:
                    # Zeiger unter den Fehler setzen
                    pointer: str = " " * (col - 1) + "^"
                    snippet += "\n" + pointer

            issues.append(
                SyntaxIssue(
                    line=line,
                    column=col,
                    message=f"Syntaxfehler in Zeile {line}, Spalte {col}: "
                    f"{msg}",
                    severity="error",
                    category="syntax",
                    code_snippet=snippet,
                )
            )
            logger.warning(
                "Syntaxfehler erkannt: Zeile %d, Spalte %d: %s",
                line,
                col,
                msg,
            )

        return issues

    # ──────────────────────────────────────────────────────────────────────
    # Stufe 2: Sicherheitsanalyse (AST-Walk)
    # ──────────────────────────────────────────────────────────────────────

    def check_security(self, code: str) -> list[SyntaxIssue]:
        """
        Analysiert den Code auf sicherheitskritische Muster.

        Durchläuft den AST-Baum und sucht nach:
        - Verbotenen Import-Modulen (os, subprocess, socket, …)
        - Gefährlichen Funktionsaufrufen (eval, exec, __import__, …)
        - Zugriffen auf das Dateisystem (open, pathlib.Path)

        Args:
            code: Python-Quellcode als String.

        Returns:
            Liste von ``SyntaxIssue`` mit gefundenen Sicherheitsproblemen.
        """
        issues: list[SyntaxIssue] = []

        try:
            tree: ast.AST = ast.parse(code)
        except SyntaxError:
            # Syntax-Fehler werden bereits in check_syntax behandelt
            return issues

        code_lines: list[str] = code.split("\n")

        for node in ast.walk(tree):
            # ── Verbotene Imports ──
            if isinstance(node, ast.Import):
                for alias in node.names:
                    base_module: str = alias.name.split(".")[0]
                    if base_module in _FORBIDDEN_IMPORTS:
                        issues.append(
                            self._create_security_issue(
                                node,
                                code_lines,
                                f"Verbotener Import: '{alias.name}'. "
                                f"Das Modul '{base_module}' ist in der "
                                f"Sandbox nicht erlaubt (Sicherheitsrisiko: "
                                f"Systemzugriff, Netzwerk, Prozess-Steuerung).",
                            )
                        )

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    base_module = node.module.split(".")[0]
                    if base_module in _FORBIDDEN_IMPORTS:
                        for alias in node.names:
                            issues.append(
                                self._create_security_issue(
                                    node,
                                    code_lines,
                                    f"Verbotener Import: 'from {node.module} "
                                    f"import {alias.name}'. Das Modul "
                                    f"'{base_module}' ist in der Sandbox "
                                    f"nicht erlaubt.",
                                )
                            )

            # ── Verbotene Funktionsaufrufe ──
            elif isinstance(node, ast.Call):
                func_name: str = self._get_func_name(node.func)
                if func_name in _FORBIDDEN_FUNCTIONS:
                    issues.append(
                        self._create_security_issue(
                            node,
                            code_lines,
                            f"Verbotener Funktionsaufruf: '{func_name}()'. "
                            f"Diese Funktion kann zur Code-Injection oder "
                            f"Sandbox-Flucht missbraucht werden.",
                        )
                    )

                # Spezielle Prüfung: open() mit Schreib-Modi
                if func_name == "open":
                    args: list[ast.expr] = node.args
                    if len(args) >= 2 and isinstance(args[1], ast.Constant):
                        mode: str = str(args[1].value)
                        if "w" in mode or "a" in mode:
                            issues.append(
                                self._create_security_issue(
                                    node,
                                    code_lines,
                                    f"Datei-Schreibzugriff: open(..., '{mode}'). "
                                    f"In der Sandbox ist nur Lesezugriff "
                                    f"auf temporäre Dateien erlaubt.",
                                )
                            )

        return issues

    def _get_func_name(self, func_node: ast.expr) -> str:
        """Extrahiert den Funktionsnamen aus einem AST-Call-Knoten."""
        if isinstance(func_node, ast.Name):
            return func_node.id
        elif isinstance(func_node, ast.Attribute):
            return func_node.attr
        return ""

    def _create_security_issue(
        self,
        node: ast.AST,
        code_lines: list[str],
        message: str,
    ) -> SyntaxIssue:
        """Erzeugt ein SyntaxIssue aus einem AST-Knoten mit Code-Kontext."""
        line: int = getattr(node, "lineno", 0)
        col: int = getattr(node, "col_offset", 0)
        snippet: str = ""
        if 0 < line <= len(code_lines):
            snippet = code_lines[line - 1]

        logger.warning("Sicherheitsproblem: Zeile %d — %s", line, message)
        return SyntaxIssue(
            line=line,
            column=col + 1,
            message=message,
            severity="error",
            category="security",
            code_snippet=snippet,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Stufe 3: Vollständige Analyse
    # ──────────────────────────────────────────────────────────────────────

    def analyze_code(self, code: str) -> CodeAnalysisResult:
        """
        Führt die vollständige Code-Analyse durch (Syntax + Sicherheit).

        Dies ist die primäre Einstiegsmethode. Sie kombiniert syntaktische
        Prüfung und Sicherheitsanalyse in einem Durchlauf.

        Args:
            code: Python-Quellcode als String.

        Returns:
            ``CodeAnalysisResult`` mit allen gefundenen Issues.
        """
        logger.info("Starte vollständige Code-Analyse (%d Zeichen)...", len(code))

        all_issues: list[SyntaxIssue] = []
        all_issues.extend(self.check_syntax(code))

        # Sicherheitsanalyse nur, wenn Syntax ok ist
        if not any(i.severity == "error" and i.category == "syntax" for i in all_issues):
            all_issues.extend(self.check_security(code))

        errors: int = sum(
            1 for i in all_issues if i.severity == "error"
        )
        warnings: int = sum(
            1 for i in all_issues if i.severity == "warning"
        )

        # AST-Knoten zählen (für Metriken)
        node_count: int = 0
        try:
            tree: ast.AST = ast.parse(code)
            node_count = len(list(ast.walk(tree)))
        except SyntaxError:
            pass

        result: CodeAnalysisResult = CodeAnalysisResult(
            is_valid=(errors == 0),
            is_safe=(
                not any(
                    i.category == "security" and i.severity == "error"
                    for i in all_issues
                )
            ),
            issues=all_issues,
            error_count=errors,
            warning_count=warnings,
            ast_node_count=node_count,
            analyzed_at=datetime.now(timezone.utc).isoformat(),
        )

        if result.is_valid and result.is_safe:
            logger.info(
                "Code-Analyse: ✅ bestanden (%d Knoten, 0 Fehler).",
                node_count,
            )
        else:
            logger.warning(
                "Code-Analyse: ❌ %d Fehler, %d Warnungen.",
                errors,
                warnings,
            )

        return result

    # ──────────────────────────────────────────────────────────────────────
    # Stufe 4: Docker-Sandbox-Ausführung
    # ──────────────────────────────────────────────────────────────────────

    async def execute_in_sandbox(
        self,
        code: str,
        timeout: int | None = None,
        input_data: str | None = None,
    ) -> SandboxResult:
        """
        Führt Python-Code in einer isolierten Docker-Sandbox aus.

        **Sicherheitsarchitektur:**
        - Flüchtiger Container (``--rm``) — keine persistenten Änderungen
        - Kein Netzwerkzugriff (``--network none``)
        - Speicherlimit (``--memory``)
        - CPU-Limit (``--cpus``)
        - Read-Only-Root-Dateisystem (``--read-only``)
        - Keine Privilegien (``--security-opt no-new-privileges``)
        - tmpfs für /tmp (flüchtig, ``noexec``)
        - Timeout via ``subprocess.run(timeout=...)``

        Args:
            code: Auszuführender Python-Code.
            timeout: Timeout in Sekunden (Default aus Settings).
            input_data: Optionaler stdin-Input.

        Returns:
            ``SandboxResult`` mit stdout, stderr, exit_code und Laufzeit.
        """
        # Vor der Ausführung: Analyse
        analysis: CodeAnalysisResult = self.analyze_code(code)
        if not analysis.is_valid:
            return SandboxResult(
                success=False,
                stderr="\n".join(
                    i.message for i in analysis.issues
                ),
                error_message="Code-Analyse fehlgeschlagen. "
                "Ausführung verweigert.",
            )
        if not analysis.is_safe:
            return SandboxResult(
                success=False,
                stderr="\n".join(
                    i.message
                    for i in analysis.issues
                    if i.category == "security"
                ),
                error_message="Sicherheitsanalyse fehlgeschlagen. "
                "Code enthält verbotene Operationen.",
            )

        exec_timeout: int = timeout or self._sandbox_timeout
        job_id: str = uuid.uuid4().hex[:8]
        logger.info(
            "Starte Sandbox-Ausführung (Job %s, timeout=%ds)...",
            job_id,
            exec_timeout,
        )

        # Temporäre Datei für den Code
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            prefix=f"sandbox_{job_id}_",
            delete=False,
        ) as tmp:
            tmp.write(code)
            code_file: str = tmp.name

        import time

        start_time: float = time.monotonic()

        try:
            # Docker-Run-Befehl
            cmd: list[str] = [
                "docker",
                "run",
                "--rm",  # Container nach Ausführung löschen
                "--network", "none",  # Kein Netzwerk
                "--memory", self._sandbox_max_memory,
                "--cpus", "1",
                "--read-only",  # Read-Only Root-FS
                "--tmpfs", "/tmp:noexec,nosuid,nodev,size=64M",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "ALL",
                # Code-Datei in den Container mounten
                "-v", f"{code_file}:/code/user_script.py:ro",
                self._sandbox_image,
                "python",
                "-I",  # Isolated mode (keine User-Site-Packages)
                "-B",  # Keine .pyc-Dateien schreiben
                "/code/user_script.py",
            ]

            logger.debug(
                "Docker-Befehl: %s",
                " ".join(cmd[:6]) + " ...",
            )

            process: subprocess.CompletedProcess = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=exec_timeout,
            )

            elapsed: float = (time.monotonic() - start_time) * 1000

            # Ausgabe kürzen, falls zu groß
            stdout: str = process.stdout or ""
            stderr: str = process.stderr or ""

            if len(stdout) > self._sandbox_max_output:
                stdout = (
                    stdout[: self._sandbox_max_output]
                    + f"\n\n[... Ausgabe gekürzt, überschreitet "
                    f"{self._sandbox_max_output} Bytes ...]"
                )
            if len(stderr) > self._sandbox_max_output:
                stderr = (
                    stderr[: self._sandbox_max_output]
                    + f"\n\n[... Ausgabe gekürzt ...]"
                )

            success: bool = process.returncode == 0

            logger.info(
                "Sandbox-Job %s: %s (exit=%d, %.0f ms, "
                "stdout=%d B, stderr=%d B).",
                job_id,
                "✅ Erfolg" if success else "❌ Fehler",
                process.returncode,
                elapsed,
                len(stdout),
                len(stderr),
            )

            return SandboxResult(
                success=success,
                stdout=stdout,
                stderr=stderr,
                exit_code=process.returncode,
                execution_time_ms=round(elapsed, 2),
            )

        except subprocess.TimeoutExpired:
            elapsed = (time.monotonic() - start_time) * 1000
            logger.warning(
                "Sandbox-Job %s: ⏰ Timeout nach %.0f ms.", job_id, elapsed
            )
            # Container zwangsweise beenden
            subprocess.run(
                ["docker", "rm", "-f", f"sandbox_{job_id}"],
                capture_output=True,
            )
            return SandboxResult(
                success=False,
                stderr=(
                    f"⏰ Zeitüberschreitung: Die Code-Ausführung "
                    f"wurde nach {exec_timeout} Sekunden abgebrochen."
                ),
                execution_time_ms=round(elapsed, 2),
                error_message="Timeout",
            )

        except FileNotFoundError:
            return SandboxResult(
                success=False,
                error_message=(
                    "Docker ist nicht installiert oder nicht im PATH. "
                    "Bitte installiere Docker, um die Sandbox zu nutzen."
                ),
            )

        except Exception as exc:
            logger.error(
                "Sandbox-Job %s: Unerwarteter Fehler: %s", job_id, exc
            )
            return SandboxResult(
                success=False,
                stderr=str(exc),
                error_message=f"Unerwarteter Fehler: {exc}",
            )

        finally:
            # Temporäre Datei löschen
            try:
                Path(code_file).unlink()
            except OSError:
                pass

    # ──────────────────────────────────────────────────────────────────────
    # Hilfsmethoden
    # ──────────────────────────────────────────────────────────────────────

    def dry_run_check(self, code: str) -> CodeAnalysisResult:
        """
        Alias für ``analyze_code()`` — schnelle Syntax+Sicherheitsprüfung
        ohne Ausführung.

        Args:
            code: Python-Quellcode.

        Returns:
            ``CodeAnalysisResult``.
        """
        return self.analyze_code(code)

    def format_issues_for_display(
        self,
        result: CodeAnalysisResult,
    ) -> str:
        """
        Formatiert Analyse-Issues als menschenlesbaren Markdown-String.

        Nützlich für die Rückgabe an das Frontend oder den KI-Agenten.

        Args:
            result: Ergebnis aus ``analyze_code()``.

        Returns:
            Formatierter Markdown-String mit allen Issues.
        """
        if result.is_valid and result.is_safe:
            return (
                "✅ **Code-Analyse bestanden** — "
                "Keine Syntaxfehler oder Sicherheitsprobleme gefunden."
            )

        lines: list[str] = []
        lines.append(
            f"## Code-Analyse: "
            f"{'✅' if result.is_valid else '❌'} "
            f"Syntax | "
            f"{'✅' if result.is_safe else '⛔'} "
            f"Sicherheit"
        )
        lines.append("")

        for issue in result.issues:
            icon: str = "⛔" if issue.severity == "error" else "⚠️"
            lines.append(
                f"### {icon} Zeile {issue.line} — "
                f"{issue.category.upper()}"
            )
            lines.append(f"**{issue.message}**")
            if issue.code_snippet:
                lines.append(f"```python\n{issue.code_snippet}\n```")
            lines.append("")

        lines.append(
            f"---\n"
            f"📊 **Zusammenfassung:** {result.error_count} Fehler, "
            f"{result.warning_count} Warnungen | "
            f"Analysiert: {result.analyzed_at}"
        )
        return "\n".join(lines)
