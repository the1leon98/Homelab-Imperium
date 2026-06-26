// FILE: frontend/static/js/views/code.js
/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * Code Workbench — code-server-IDE + KI-Code-Assistent.
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Bietet eine zweigeteilte Entwicklungsumgebung:
 *   • Links: Sicheres Iframe mit code-server (VS Code im Browser)
 *     — Dies ist die EINZIGE zulässige iFrame-Integration im Imperium OS.
 *     — Wird nur nach erfolgreicher Token-Autorisierung geladen.
 *   • Rechts: KI-Code-Assistent (Analyse, Refactoring, Sandbox-Ausführung)
 *     — Syntax- & Sicherheits-Check via Backend
 *     — Docker-Sandbox-Ausführung mit Timeout
 *     — Schnelles Senden von Codefragmenten an den AI-Studio-Agenten
 *
 * Sicherheit:
 *   — Session-Token via POST /api/ide/session (secrets.token_hex(32))
 *   — Iframe-Autorisierung via GET /api/ide/authorize?token=
 *   — Container-Health-Check vor Iframe-Rendering
 *   — Sandbox: kein Netzwerk, read-only FS, no-new-privileges, 512MB Limit
 *
 * Verwendete API-Endpunkte:
 *   POST /api/ide/session        — Sitzungs-Token erzeugen
 *   GET  /api/ide/authorize      — Iframe-Zugriff prüfen
 *   POST /api/code/analyze       — Syntaktische + Sicherheitsanalyse
 *   POST /api/code/execute       — Sandbox-Ausführung
 *   GET  /api/code/status        — Sandbox-Verfügbarkeit
 */

import { get, post } from '../api.js';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. KONSTANTEN
// ═══════════════════════════════════════════════════════════════════════════════

/** Beispiel-Code für den Editor-Start */
const DEFAULT_CODE = `# Willkommen in der Code Workbench
# Schreibe Python-Code und analysiere ihn live.

def fibonacci(n: int) -> int:
    """Berechnet die n-te Fibonacci-Zahl."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

print(fibonacci(10))`;

// ═══════════════════════════════════════════════════════════════════════════════
// 2. HAUPTFUNKTION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Rendert die Code Workbench mit code-server-Iframe und KI-Assistent.
 *
 * @param {HTMLElement} container
 * @returns {Function} Cleanup
 */
export async function showCode(container) {
  // ── 2.1 Grundlayout ────────────────────────────────────────────────────
  container.innerHTML = `
    <div class="section-header">
      <div>
        <h1>Code Workbench</h1>
        <p class="section-subtitle">VS Code im Browser + KI-gestützte Analyse & Refactoring</p>
      </div>
      <div class="flex items-center gap-3">
        <span class="text-xs" style="color:var(--color-text-tertiary);" id="sandbox-status">Sandbox: Prüfe…</span>
        <span class="status-dot inactive" id="sandbox-dot"></span>
      </div>
    </div>

    <div style="display:grid; grid-template-columns:1fr 420px; gap:var(--space-6); height:calc(100vh - 160px); min-height:500px;">

      <!-- Linke Spalte: code-server Iframe -->
      <div style="display:flex; flex-direction:column; min-height:0;">
        <div class="glass-card" style="flex:1; display:flex; flex-direction:column; overflow:hidden; padding:0; position:relative;">
          <!-- Toolbar -->
          <div style="display:flex; align-items:center; justify-content:space-between;
                      padding:var(--space-2) var(--space-4);
                      border-bottom:1px solid var(--color-glass-border);
                      background:var(--color-glass-bg);">
            <div style="display:flex; align-items:center; gap:var(--space-3);">
              <span class="status-dot healthy" id="ide-status-dot" style="flex-shrink:0;"></span>
              <span class="text-xs" style="color:var(--color-text-secondary);" id="ide-status-text">code-server</span>
            </div>
            <div style="display:flex; gap:var(--space-2);">
              <button class="btn-icon btn-ghost" id="btn-refresh-ide" title="IDE neu laden" style="font-size:14px;">🔄</button>
              <button class="btn-icon btn-ghost" id="btn-new-session" title="Neue Sitzung" style="font-size:14px;">🔑</button>
            </div>
          </div>

          <!-- Iframe-Container -->
          <div id="ide-iframe-container" style="flex:1; position:relative; background:#1e1e1e;">
            <!-- Platzhalter während der Autorisierung -->
            <div id="ide-placeholder" style="display:flex; flex-direction:column; align-items:center;
                        justify-content:center; height:100%; color:var(--color-text-tertiary);">
              <div class="spinner" style="margin-bottom:var(--space-4);"></div>
              <span class="text-sm">Verbinde mit code-server…</span>
              <span class="text-xs" style="margin-top:var(--space-2);">Sitzungs-Token wird angefordert</span>
            </div>

            <!-- Das Iframe (wird nach Autorisierung eingeblendet) -->
            <iframe id="code-server-iframe"
                    style="display:none; width:100%; height:100%; border:none;"
                    sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
                    allow="clipboard-read; clipboard-write"
                    title="code-server Web-IDE"
                    referrerpolicy="no-referrer">
            </iframe>

            <!-- Fehler-Anzeige -->
            <div id="ide-error" style="display:none; flex-direction:column; align-items:center;
                        justify-content:center; height:100%; text-align:center; padding:var(--space-6);">
              <span style="font-size:2rem; margin-bottom:var(--space-3);">⚠</span>
              <h3 class="text-sm" style="color:var(--color-text-primary);">code-server nicht erreichbar</h3>
              <p class="text-xs" style="color:var(--color-text-tertiary); margin-top:var(--space-2);" id="ide-error-msg">
                Der Container konnte nicht gestartet werden.
              </p>
              <button class="btn btn-primary btn-pill" id="btn-retry-ide" style="margin-top:var(--space-4);">
                Erneut versuchen
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Rechte Spalte: KI-Assistent -->
      <div style="display:flex; flex-direction:column; gap:var(--space-4); min-height:0; overflow-y:auto;">

        <!-- Code-Editor (Textarea) -->
        <div class="glass-card">
          <div class="card-header">
            <h3>📝 Code-Editor</h3>
            <div style="display:flex; gap:var(--space-2);">
              <button class="btn btn-ghost btn-icon" id="btn-clear-editor" title="Leeren" style="font-size:14px;">🗑</button>
              <button class="btn btn-ghost btn-icon" id="btn-reset-editor" title="Beispiel-Code" style="font-size:14px;">📋</button>
            </div>
          </div>
          <textarea id="code-editor"
                    style="width:100%; height:200px; background:rgba(0,0,0,0.4);
                           color:var(--color-accent-cyan); font-family:var(--font-mono);
                           font-size:var(--text-xs); line-height:1.6;
                           border:1px solid var(--color-glass-border);
                           border-radius:var(--radius-md); padding:var(--space-4);
                           resize:vertical; tab-size:4;"
                    spellcheck="false"
                    placeholder="# Python-Code hier eingeben…">${DEFAULT_CODE}</textarea>

          <!-- Aktions-Buttons -->
          <div class="btn-group" style="margin-top:var(--space-3); width:100%;">
            <button class="btn btn-primary" id="btn-analyze" style="flex:1;">🔍 Analysieren</button>
            <button class="btn btn-success" id="btn-execute" style="flex:1;">▶ Ausführen</button>
            <button class="btn btn-secondary" id="btn-send-to-ai" style="flex:1;">✦ An KI senden</button>
          </div>
        </div>

        <!-- Analyse-Ergebnisse -->
        <div class="glass-card" style="flex:1;">
          <div class="card-header">
            <h3>📊 Analyse & Ausgabe</h3>
            <button class="btn btn-ghost btn-icon" id="btn-clear-output" title="Ausgabe leeren" style="font-size:14px;">🗑</button>
          </div>
          <div id="output-panel"
               style="font-family:var(--font-mono); font-size:var(--text-xs);
                      line-height:1.6; color:var(--color-text-primary);
                      max-height:300px; overflow-y:auto; white-space:pre-wrap;
                      word-break:break-word;">
            <span style="color:var(--color-text-tertiary);">Ergebnisse erscheinen hier…
Drücke "Analysieren" für Syntax- & Sicherheits-Check.
Drücke "Ausführen" für Docker-Sandbox-Test.</span>
          </div>
        </div>

        <!-- Schnell-Refactoring-Vorschläge -->
        <div class="glass-card subtle">
          <div class="card-header"><h3>💡 Refactoring-Vorschläge</h3></div>
          <div id="refactor-suggestions" class="text-xs" style="color:var(--color-text-tertiary);">
            Führe eine Analyse durch, um Vorschläge zu erhalten.
          </div>
        </div>
      </div>
    </div>
  `;

  // ── 2.2 IDE-Session & Iframe-Autorisierung ──────────────────────────────

  const idePlaceholder = document.getElementById('ide-placeholder');
  const ideIframe = document.getElementById('code-server-iframe');
  const ideError = document.getElementById('ide-error');
  const ideErrorMsg = document.getElementById('ide-error-msg');
  const ideStatusDot = document.getElementById('ide-status-dot');
  const ideStatusText = document.getElementById('ide-status-text');

  /**
   * Erzeugt eine neue IDE-Sitzung und autorisiert das Iframe.
   */
  async function _initIDESession() {
    // UI: Ladezustand
    idePlaceholder.style.display = 'flex';
    ideIframe.style.display = 'none';
    ideError.style.display = 'none';
    ideStatusDot.className = 'status-dot degraded';
    ideStatusText.textContent = 'Autorisiere…';

    try {
      // Schritt 1: Token anfordern
      const session = await post('/ide/session', {}, { skipToast: true });

      // Schritt 2: Iframe-Zugriff prüfen
      const auth = await get('/ide/authorize', { token: session.token }, { skipToast: true });

      if (auth.authorized && auth.iframe_url) {
        // Erfolg: Iframe laden
        ideIframe.src = auth.iframe_url;
        ideIframe.style.display = 'block';
        idePlaceholder.style.display = 'none';
        ideStatusDot.className = 'status-dot healthy';
        ideStatusText.textContent = 'code-server · verbunden';
      } else {
        // Nicht autorisiert
        _showIDEError(auth.reason || 'Autorisierung fehlgeschlagen.');
      }
    } catch (err) {
      _showIDEError(err.message || 'Verbindung zum IDE-Server fehlgeschlagen.');
    }
  }

  function _showIDEError(message) {
    ideIframe.style.display = 'none';
    idePlaceholder.style.display = 'none';
    ideError.style.display = 'flex';
    ideErrorMsg.textContent = message;
    ideStatusDot.className = 'status-dot critical';
    ideStatusText.textContent = 'code-server · offline';
  }

  // Event-Listener für IDE-Buttons
  document.getElementById('btn-refresh-ide').addEventListener('click', () => {
    if (ideIframe.style.display !== 'none') {
      ideIframe.src = ideIframe.src; // Iframe neu laden
    } else {
      _initIDESession();
    }
  });

  document.getElementById('btn-new-session').addEventListener('click', _initIDESession);
  document.getElementById('btn-retry-ide').addEventListener('click', _initIDESession);

  // ── 2.3 Code-Editor ────────────────────────────────────────────────────

  const codeEditor = document.getElementById('code-editor');
  const outputPanel = document.getElementById('output-panel');
  const refactorSuggestions = document.getElementById('refactor-suggestions');

  // Editor leeren
  document.getElementById('btn-clear-editor').addEventListener('click', () => {
    codeEditor.value = '';
  });

  // Beispiel-Code wiederherstellen
  document.getElementById('btn-reset-editor').addEventListener('click', () => {
    codeEditor.value = DEFAULT_CODE;
  });

  // Ausgabe leeren
  document.getElementById('btn-clear-output').addEventListener('click', () => {
    outputPanel.innerHTML = `<span style="color:var(--color-text-tertiary);">Ausgabe geleert.</span>`;
  });

  // ── 2.4 Code-Analyse ───────────────────────────────────────────────────

  document.getElementById('btn-analyze').addEventListener('click', async () => {
    const code = codeEditor.value.trim();
    if (!code) {
      outputPanel.innerHTML = `<span style="color:var(--color-warning);">⚠ Kein Code zum Analysieren.</span>`;
      return;
    }

    outputPanel.innerHTML = `<span style="color:var(--color-text-tertiary);">Analysiere Code…</span>`;
    refactorSuggestions.innerHTML = `<span style="color:var(--color-text-tertiary);">Analysiere…</span>`;

    try {
      const result = await post('/code/analyze', { code }, { skipToast: true });

      // Syntax-Ergebnis
      const syntaxOk = result.syntax_valid;
      const issues = result.issues || [];
      const securityIssues = result.security_issues || [];
      const suggestions = result.suggestions || [];

      let output = '';

      // Syntax-Status
      output += syntaxOk
        ? `<span style="color:var(--color-success);">✓ Syntax: Gültig</span>\n`
        : `<span style="color:var(--color-danger);">✕ Syntax-Fehler gefunden:</span>\n`;

      issues.forEach(issue => {
        output += `  <span style="color:var(--color-warning);">• Zeile ${issue.line || '?'}: ${_escapeHtml(issue.message || 'Unbekannter Fehler')}</span>\n`;
      });

      // Sicherheits-Check
      if (securityIssues.length > 0) {
        output += `\n<span style="color:var(--color-danger);">🔒 Sicherheitswarnungen (${securityIssues.length}):</span>\n`;
        securityIssues.forEach(si => {
          output += `  <span style="color:var(--color-danger);">• ${_escapeHtml(si.message || si)}</span>\n`;
        });
      } else {
        output += `\n<span style="color:var(--color-success);">✓ Sicherheits-Check: Bestanden</span>\n`;
      }

      // Komplexität
      if (result.complexity) {
        output += `\n<span style="color:var(--color-text-secondary);">📐 Komplexität: ${result.complexity}</span>\n`;
      }

      outputPanel.innerHTML = output || `<span style="color:var(--color-success);">✓ Keine Auffälligkeiten.</span>`;

      // Refactoring-Vorschläge
      if (suggestions.length > 0) {
        refactorSuggestions.innerHTML = suggestions.map((s, i) =>
          `<div style="padding:var(--space-2) 0; border-bottom:1px solid var(--color-border-subtle);">
            <span style="color:var(--color-accent-primary);">💡 ${_escapeHtml(s.title || s)}:</span>
            <span style="color:var(--color-text-secondary);">${_escapeHtml(s.description || '')}</span>
          </div>`
        ).join('');
      } else {
        refactorSuggestions.innerHTML = `<span style="color:var(--color-success);">✓ Keine Refactoring-Vorschläge — Code sieht gut aus.</span>`;
      }

    } catch (err) {
      outputPanel.innerHTML = `<span style="color:var(--color-danger);">✕ Analyse fehlgeschlagen: ${_escapeHtml(err.message)}</span>`;
      refactorSuggestions.innerHTML = `<span style="color:var(--color-text-tertiary);">Keine Vorschläge verfügbar.</span>`;
    }
  });

  // ── 2.5 Sandbox-Ausführung ─────────────────────────────────────────────

  document.getElementById('btn-execute').addEventListener('click', async () => {
    const code = codeEditor.value.trim();
    if (!code) {
      outputPanel.innerHTML = `<span style="color:var(--color-warning);">⚠ Kein Code zum Ausführen.</span>`;
      return;
    }

    outputPanel.innerHTML = `
      <div style="display:flex; align-items:center; gap:var(--space-3);">
        <div class="spinner"></div>
        <span style="color:var(--color-text-secondary);">Führe in Docker-Sandbox aus…</span>
      </div>`;

    try {
      const result = await post('/code/execute', { code }, { skipToast: true });

      let output = '';

      // Exit-Code
      const exitOk = result.exit_code === 0;
      output += exitOk
        ? `<span style="color:var(--color-success);">✓ Ausführung erfolgreich (Exit-Code 0)</span>\n`
        : `<span style="color:var(--color-danger);">✕ Fehlgeschlagen (Exit-Code ${result.exit_code})</span>\n`;

      // stdout
      if (result.stdout) {
        output += `\n<span style="color:var(--color-text-secondary);">── stdout ──</span>\n`;
        output += `<span style="color:var(--color-accent-cyan);">${_escapeHtml(result.stdout)}</span>`;
      }

      // stderr
      if (result.stderr) {
        output += `\n<span style="color:var(--color-text-secondary);">── stderr ──</span>\n`;
        output += `<span style="color:var(--color-danger);">${_escapeHtml(result.stderr)}</span>`;
      }

      // Laufzeit
      if (result.execution_time_ms != null) {
        output += `\n<span style="color:var(--color-text-tertiary);">⏱ ${result.execution_time_ms} ms</span>`;
      }

      outputPanel.innerHTML = output || `<span style="color:var(--color-text-tertiary);">Keine Ausgabe.</span>`;

    } catch (err) {
      outputPanel.innerHTML = `<span style="color:var(--color-danger);">✕ Ausführung fehlgeschlagen: ${_escapeHtml(err.message)}</span>`;
    }
  });

  // ── 2.6 An KI senden ───────────────────────────────────────────────────

  document.getElementById('btn-send-to-ai').addEventListener('click', () => {
    const code = codeEditor.value.trim();
    if (!code) {
      outputPanel.innerHTML = `<span style="color:var(--color-warning);">⚠ Kein Code zum Senden.</span>`;
      return;
    }

    // Navigiere zum AI Studio und übergib den Code als Prompt
    const prompt = `Analysiere und verbessere folgenden Code:\n\n\`\`\`python\n${code}\n\`\`\``;
    // Speichere den Prompt temporär, damit das AI Studio ihn aufgreifen kann
    sessionStorage.setItem('imperium:ai-prompt', prompt);
    window.location.hash = '#/ai-studio';

    outputPanel.innerHTML = `<span style="color:var(--color-accent-primary);">✦ Code wurde an das AI Studio übergeben.</span>`;
  });

  // ── 2.7 Sandbox-Status prüfen ──────────────────────────────────────────

  async function _checkSandboxStatus() {
    try {
      const status = await get('/code/status', {}, { skipToast: true, skipLoader: true });
      const dot = document.getElementById('sandbox-dot');
      const label = document.getElementById('sandbox-status');
      if (status.available) {
        dot.className = 'status-dot healthy';
        label.textContent = 'Sandbox: Bereit';
      } else {
        dot.className = 'status-dot critical';
        label.textContent = 'Sandbox: Nicht verfügbar';
      }
    } catch {
      document.getElementById('sandbox-dot').className = 'status-dot inactive';
      document.getElementById('sandbox-status').textContent = 'Sandbox: Unbekannt';
    }
  }

  // ── 2.8 Initialisierung ────────────────────────────────────────────────

  // IDE-Session starten
  _initIDESession();

  // Sandbox-Status prüfen
  _checkSandboxStatus();

  // ── 2.9 Cleanup ────────────────────────────────────────────────────────

  return () => {
    // Iframe zurücksetzen (verhindert Weiterlaufen im Hintergrund)
    const iframe = document.getElementById('code-server-iframe');
    if (iframe) iframe.src = 'about:blank';
  };
}

// ═══════════════════════════════════════════════════════════════════════════════
// 3. HILFSFUNKTIONEN
// ═══════════════════════════════════════════════════════════════════════════════

function _escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(str)));
  return div.innerHTML;
}