// FILE: frontend/static/js/views/ai_chat.js
/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * AI Studio — Multi-Agenten-Chat mit SSE-Streaming & Neon-Bubbles.
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Bietet eine Google-AI-Studio-ähnliche Chat-Oberfläche mit:
 *   • Sidebar mit 4 Agenten-Profilen (Gradient-Avatare, Beschreibungen)
 *   • Scrollbares Chatfenster mit getrennten Bubble-Stilen:
 *     - System: zentriert, dezent
 *     - User: rechtsbündig, Lila-Neon-Glow
 *     - AI: linksbündig, Cyan-Neon-Glow, Markdown-ähnlich
 *   • SSE-Streaming mit Echtzeit-Token-Rendering
 *   • Tokens/Sekunde-Metrik während der Inferenz
 *   • Power-Mode-Anzeige (CPU/GPU) & RAG-Toggle
 *   • Check for pending prompts from Code Workbench
 *
 * Verwendete API-Endpunkte:
 *   POST /api/ai/chat/stream     — SSE-Streaming-Chat
 *   GET  /api/ai/agents          — Agenten-Liste
 *   GET  /api/ai/status          — Router-Status
 */

import { get, post } from '../api.js';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. KONSTANTEN — Agenten-Definitionen
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Agenten-Metadaten passend zu den YAML-Konfigurationen.
 */
const AGENTS = {
  it_tutor: {
    name: 'Fachinformatiker Tutor',
    persona: 'Prof. Dr. Aris Thales',
    desc: 'IHK-Curriculum, sokratische Methode, Prüfungsvorbereitung.',
    gradient: 'var(--agent-gradient-it)',
    icon: '🎓',
    glow: 'var(--glow-primary)',
  },
  auto_engineer: {
    name: 'Motorentechnik & CAD',
    persona: 'Dr.-Ing. Viktor Eisenhardt',
    desc: 'Berechnungen, CadQuery/OpenSCAD, Turbo-Auslegung.',
    gradient: 'var(--agent-gradient-auto)',
    icon: '⚙',
    glow: 'var(--glow-warning)',
  },
  medical_health: {
    name: 'Medical & Health Coach',
    persona: 'Dr. med. Elena Voss',
    desc: 'Ernährung, Training, 3D-Hologramm-Diagnose.',
    gradient: 'var(--agent-gradient-health)',
    icon: '♡',
    glow: 'var(--glow-cyan)',
  },
  brainstorm_agent: {
    name: 'Kreativ-Brainstormer',
    persona: 'Nova',
    desc: '4-Phasen-Prozess, SCAMPER, Walt-Disney-Methode.',
    gradient: 'var(--agent-gradient-brainstorm)',
    icon: '✦',
    glow: 'var(--glow-primary)',
  },
};

// ═══════════════════════════════════════════════════════════════════════════════
// 2. HAUPTFUNKTION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Rendert das AI Studio mit Agenten-Sidebar und Streaming-Chat.
 *
 * @param {HTMLElement} container
 * @returns {Function} Cleanup
 */
export async function showAIChat(container) {
  // ── 2.1 Grundlayout ────────────────────────────────────────────────────
  container.innerHTML = `
    <div class="section-header">
      <div>
        <h1>AI Studio</h1>
        <p class="section-subtitle">Multi-Agenten-Chat mit SSE-Streaming & dualem CPU/GPU-Backend</p>
      </div>
      <div class="flex items-center gap-3">
        <span class="text-xs" style="color:var(--color-text-tertiary);" id="router-status">Router: Prüfe…</span>
        <span class="status-dot inactive" id="router-dot"></span>
      </div>
    </div>

    <div style="display:flex; gap:var(--space-6); height:calc(100vh - 160px); min-height:500px;">

      <!-- ═══ AGENTEN-SIDEBAR ═══ -->
      <div class="glass-card" style="width:280px; display:flex; flex-direction:column; gap:var(--space-3); overflow-y:auto; flex-shrink:0;">
        <div class="card-header"><h3>🤖 Agenten</h3></div>

        <div id="agent-list" style="display:flex; flex-direction:column; gap:var(--space-2);">
          ${Object.entries(AGENTS).map(([key, agent]) => `
            <div class="agent-card-select" data-agent="${key}"
                 style="cursor:pointer; padding:var(--space-3); border-radius:var(--radius-md);
                        border:1px solid transparent;
                        transition:border-color 0.2s ease, background 0.2s ease;
                        ${key === 'it_tutor' ? 'border-color:rgba(160,107,255,0.35); background:var(--color-accent-primary-soft);' : ''}">
              <div style="display:flex; align-items:center; gap:var(--space-3);">
                <div style="width:40px; height:40px; border-radius:var(--radius-md);
                            background:${agent.gradient};
                            display:flex; align-items:center; justify-content:center;
                            font-size:18px; color:#fff; flex-shrink:0;">
                  ${agent.icon}
                </div>
                <div style="min-width:0;">
                  <div class="text-xs" style="color:var(--color-text-primary); font-weight:var(--font-semibold);">
                    ${agent.name}
                  </div>
                  <div class="text-xs" style="color:var(--color-text-tertiary); font-size:10px;">
                    ${agent.persona}
                  </div>
                </div>
              </div>
              <div class="text-xs" style="color:var(--color-text-tertiary); margin-top:var(--space-2); line-height:1.4;">
                ${agent.desc}
              </div>
            </div>
          `).join('')}
        </div>

        <!-- Power Mode & RAG Toggles -->
        <div style="margin-top:auto; padding-top:var(--space-4); border-top:1px solid var(--color-glass-border);">
          <div class="flex justify-between items-center" style="margin-bottom:var(--space-3);">
            <span class="text-xs text-secondary">Power Mode</span>
            <label class="toggle-switch">
              <input type="checkbox" id="power-mode-toggle" aria-label="GPU Power Mode">
              <span class="toggle-slider"></span>
            </label>
          </div>
          <div class="flex justify-between items-center" style="margin-bottom:var(--space-3);">
            <span class="text-xs text-secondary">RAG (Dokumente)</span>
            <label class="toggle-switch">
              <input type="checkbox" id="rag-toggle" checked aria-label="RAG aktivieren">
              <span class="toggle-slider"></span>
            </label>
          </div>
        </div>
      </div>

      <!-- ═══ CHAT-FENSTER ═══ -->
      <div class="glass-card" style="flex:1; display:flex; flex-direction:column; min-width:0;">
        <!-- Chat-Header mit Agent-Info -->
        <div id="chat-header" style="display:flex; align-items:center; gap:var(--space-3);
                    padding:var(--space-3) var(--space-4);
                    border-bottom:1px solid var(--color-glass-border);">
          <div id="active-agent-avatar"
               style="width:36px; height:36px; border-radius:var(--radius-md);
                      background:var(--agent-gradient-it);
                      display:flex; align-items:center; justify-content:center;
                      font-size:16px; color:#fff; flex-shrink:0;">🎓</div>
          <div>
            <div id="active-agent-name" class="text-sm" style="color:var(--color-text-primary); font-weight:var(--font-semibold);">
              Fachinformatiker Tutor
            </div>
            <div id="active-agent-persona" class="text-xs" style="color:var(--color-text-tertiary);">
              Prof. Dr. Aris Thales
            </div>
          </div>
          <span style="flex:1;"></span>
          <button class="btn-icon btn-ghost" id="btn-clear-chat" title="Chat leeren" style="font-size:14px;">🗑</button>
        </div>

        <!-- Chat-Nachrichten -->
        <div id="chat-scroller"
             style="flex:1; overflow-y:auto; padding:var(--space-4);
                    display:flex; flex-direction:column; gap:var(--space-3);">
          <div class="message-system">
            Willkommen im AI Studio. Wähle einen Agenten und stelle deine Frage.
          </div>
        </div>

        <!-- Streaming-Indikator -->
        <div id="streaming-indicator"
             style="display:none; padding:var(--space-2) var(--space-4);
                    border-top:1px solid var(--color-glass-border);
                    background:var(--color-glass-bg);">
          <div style="display:flex; align-items:center; gap:var(--space-3);">
            <div class="spinner" style="width:16px; height:16px; border-width:2px;"></div>
            <span class="text-xs" style="color:var(--color-accent-primary);" id="stream-status">Generiert…</span>
            <span style="flex:1;"></span>
            <span class="text-xs" style="color:var(--color-text-tertiary);" id="token-metrics">— tok/s</span>
          </div>
        </div>

        <!-- Eingabebereich -->
        <div style="padding:var(--space-3) var(--space-4); border-top:1px solid var(--color-glass-border);">
          <div style="display:flex; gap:var(--space-3); align-items:flex-end;">
            <textarea id="chat-input-text"
                      rows="1"
                      placeholder="Nachricht an den Agenten… (Shift+Enter für neue Zeile)"
                      style="flex:1; background:var(--glass-input-bg);
                             border:var(--glass-input-border);
                             border-radius:var(--radius-xl); padding:var(--space-3) var(--space-5);
                             color:var(--color-text-primary); font-family:var(--font-sans);
                             font-size:var(--text-sm); resize:none;
                             max-height:120px; line-height:1.5;"
                      ></textarea>
            <button id="send-chat-btn"
                    style="width:44px; height:44px; border-radius:var(--radius-full);
                           background:var(--color-accent-primary); border:none;
                           color:#fff; font-size:18px; cursor:pointer; flex-shrink:0;
                           box-shadow:0 0 14px rgba(160,107,255,0.30);
                           transition:transform 0.15s ease, box-shadow 0.15s ease;
                           display:flex; align-items:center; justify-content:center;"
                    title="Senden (Enter)">
              ↑
            </button>
          </div>
          <div class="text-xs" style="color:var(--color-text-tertiary); margin-top:var(--space-2); text-align:center;">
            Enter = Senden · Shift+Enter = Neue Zeile
          </div>
        </div>
      </div>
    </div>
  `;

  // ── 2.2 DOM-Referenzen ─────────────────────────────────────────────────
  const chatScroller = document.getElementById('chat-scroller');
  const chatInput = document.getElementById('chat-input-text');
  const sendBtn = document.getElementById('send-chat-btn');
  const streamingIndicator = document.getElementById('streaming-indicator');
  const streamStatus = document.getElementById('stream-status');
  const tokenMetrics = document.getElementById('token-metrics');
  const routerDot = document.getElementById('router-dot');
  const routerStatus = document.getElementById('router-status');

  // ── 2.3 Zustand ────────────────────────────────────────────────────────
  let activeAgent = 'it_tutor';
  let isStreaming = false;
  let abortController = null;

  // ── 2.4 Agenten-Wechsel ────────────────────────────────────────────────

  const agentCards = container.querySelectorAll('.agent-card-select');
  agentCards.forEach(card => {
    card.addEventListener('click', () => {
      const agentKey = card.dataset.agent;
      if (!agentKey || isStreaming) return;
      activeAgent = agentKey;
      const agent = AGENTS[agentKey];

      // Sidebar-Styling
      agentCards.forEach(c => {
        c.style.borderColor = 'transparent';
        c.style.background = '';
      });
      card.style.borderColor = 'rgba(160,107,255,0.35)';
      card.style.background = 'var(--color-accent-primary-soft)';

      // Header aktualisieren
      document.getElementById('active-agent-avatar').style.background = agent.gradient;
      document.getElementById('active-agent-avatar').textContent = agent.icon;
      document.getElementById('active-agent-name').textContent = agent.name;
      document.getElementById('active-agent-persona').textContent = agent.persona;

      // System-Nachricht
      _addMessage('system', `Agent gewechselt zu: **${agent.name}** (${agent.persona})`);
    });
  });

  // ── 2.5 Nachrichten-Rendering ──────────────────────────────────────────

  /**
   * Fügt eine Nachricht zum Chat hinzu.
   * @param {'system'|'user'|'ai'} role
   * @param {string} content — Plain-Text oder einfaches Markdown
   * @param {string} [model] — Optionales Modell-Label für AI-Nachrichten
   * @returns {HTMLElement} Das erzeugte Nachrichten-Element
   */
  function _addMessage(role, content, model = null) {
    const bubble = document.createElement('div');

    if (role === 'system') {
      bubble.className = 'message-system';
      bubble.innerHTML = `<span>${_formatContent(content)}</span>`;
    } else if (role === 'user') {
      bubble.className = 'message-user';
      bubble.innerHTML = `
        <div class="msg-bubble user-bubble">
          <div class="msg-label">Du</div>
          <div class="msg-content">${_formatContent(content)}</div>
        </div>`;
    } else if (role === 'ai') {
      bubble.className = 'message-ai';
      const agent = AGENTS[activeAgent];
      bubble.innerHTML = `
        <div class="msg-bubble ai-bubble">
          <div class="msg-label">
            <span style="display:inline-block; width:18px; height:18px; border-radius:4px;
                         background:${agent.gradient}; vertical-align:middle; margin-right:4px;"></span>
            ${agent.persona}${model ? ` <span style="color:var(--color-text-tertiary); font-weight:var(--font-normal);">· ${model}</span>` : ''}
          </div>
          <div class="msg-content">${_formatContent(content)}</div>
        </div>`;
    }

    chatScroller.appendChild(bubble);
    _scrollToBottom();
    return bubble;
  }

  /**
   * Einfaches Content-Formatting (Zeilenumbrüche, `**bold**`, `*italic*`, `\`code\``).
   */
  function _formatContent(text) {
    let html = _escapeHtml(text);
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\n/g, '<br>');
    return html;
  }

  function _scrollToBottom() {
    requestAnimationFrame(() => {
      chatScroller.scrollTop = chatScroller.scrollHeight;
    });
  }

  // ── 2.6 Auto-Resize Textarea ───────────────────────────────────────────

  chatInput.addEventListener('input', () => {
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
  });

  // ── 2.7 Nachricht senden (SSE-Streaming) ───────────────────────────────

  async function _sendMessage() {
    const text = chatInput.value.trim();
    if (!text || isStreaming) return;

    // User-Nachricht anzeigen
    _addMessage('user', text);
    chatInput.value = '';
    chatInput.style.height = 'auto';
    isStreaming = true;
    sendBtn.style.opacity = '0.5';

    // AI-Antwort-Bubble vorbereiten
    const aiBubble = _addMessage('ai', '');
    const aiContent = aiBubble.querySelector('.msg-content');
    let fullResponse = '';
    let tokenCount = 0;
    const startTime = performance.now();

    // Streaming-Indikator einblenden
    streamingIndicator.style.display = 'flex';
    streamStatus.textContent = 'Generiert…';
    tokenMetrics.textContent = '— tok/s';

    // Power Mode & RAG
    const powerMode = document.getElementById('power-mode-toggle').checked;
    const ragEnabled = document.getElementById('rag-toggle').checked;

    // AbortController für Abbruch
    abortController = new AbortController();

    try {
      const response = await fetch('/api/ai/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
        body: JSON.stringify({
          message: text,
          agent: activeAgent,
          power_mode: powerMode,
          rag_enabled: ragEnabled,
        }),
        signal: abortController.signal,
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();
            if (data === '[DONE]') break;

            try {
              const parsed = JSON.parse(data);
              const token = parsed.token || parsed.content || '';
              if (token) {
                fullResponse += token;
                tokenCount++;
                aiContent.innerHTML = _formatContent(fullResponse);
                _scrollToBottom();

                // Tokens/s berechnen
                const elapsed = (performance.now() - startTime) / 1000;
                const tps = elapsed > 0 ? (tokenCount / elapsed).toFixed(1) : '0';
                tokenMetrics.textContent = `${tps} tok/s · ${tokenCount} Tokens`;
                streamStatus.textContent = 'Generiert…';
              }

              if (parsed.model) {
                const agent = AGENTS[activeAgent];
                const labelEl = aiBubble.querySelector('.msg-label');
                if (labelEl) {
                  labelEl.innerHTML = `
                    <span style="display:inline-block; width:18px; height:18px; border-radius:4px;
                                 background:${agent.gradient}; vertical-align:middle; margin-right:4px;"></span>
                    ${agent.persona} <span style="color:var(--color-text-tertiary); font-weight:var(--font-normal);">· ${parsed.model}</span>
                  `;
                }
              }
            } catch {
              // Plain-Text Token
              fullResponse += data;
              tokenCount++;
              aiContent.innerHTML = _formatContent(fullResponse);
              _scrollToBottom();
            }
          }
        }
      }

      streamStatus.textContent = 'Fertig ✓';
      tokenMetrics.textContent = `${tokenCount} Tokens`;
      setTimeout(() => { streamingIndicator.style.display = 'none'; }, 2000);

    } catch (err) {
      if (err.name === 'AbortError') {
        // Benutzer hat abgebrochen
        if (!fullResponse) fullResponse = '[Abgebrochen]';
        aiContent.innerHTML = _formatContent(fullResponse);
        streamStatus.textContent = 'Abgebrochen';
      } else {
        aiContent.innerHTML = _formatContent(fullResponse || `⚠ Fehler: ${err.message}`);
        streamStatus.textContent = 'Fehler';
      }
      tokenMetrics.textContent = `${tokenCount} Tokens`;
      setTimeout(() => { streamingIndicator.style.display = 'none'; }, 3000);
    } finally {
      isStreaming = false;
      sendBtn.style.opacity = '1';
      abortController = null;
      chatInput.focus();
    }
  }

  // Send-Button
  sendBtn.addEventListener('click', _sendMessage);

  // Enter/Shift+Enter
  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      _sendMessage();
    }
  });

  // ── 2.8 Chat leeren ────────────────────────────────────────────────────

  document.getElementById('btn-clear-chat').addEventListener('click', () => {
    chatScroller.innerHTML = `
      <div class="message-system">
        Chat geleert. Stelle eine neue Frage an den Agenten.
      </div>`;
  });

  // ── 2.9 Router-Status prüfen ───────────────────────────────────────────

  async function _checkRouterStatus() {
    try {
      const status = await get('/ai/status', {}, { skipToast: true, skipLoader: true });
      if (status.cpu_healthy || status.gpu_healthy) {
        routerDot.className = 'status-dot healthy';
        routerStatus.textContent = status.gpu_healthy ? 'Router: CPU + GPU' : 'Router: CPU';
      } else {
        routerDot.className = 'status-dot critical';
        routerStatus.textContent = 'Router: Offline';
      }
    } catch {
      routerDot.className = 'status-dot inactive';
      routerStatus.textContent = 'Router: Unbekannt';
    }
  }

  // ── 2.10 Prüfen auf Pending-Prompt aus Code Workbench ──────────────────

  const pendingPrompt = sessionStorage.getItem('imperium:ai-prompt');
  if (pendingPrompt) {
    sessionStorage.removeItem('imperium:ai-prompt');
    chatInput.value = pendingPrompt;
    // Kurz warten, dann automatisch senden
    setTimeout(() => {
      if (chatInput.value === pendingPrompt) {
        _sendMessage();
      }
    }, 300);
  }

  // ── 2.11 Chat-Bubble-Styles (inline, da dynamisch erzeugt) ─────────────

  _injectChatStyles();

  // ── 2.12 Initialisierung ───────────────────────────────────────────────
  _checkRouterStatus();
  chatInput.focus();

  // ── 2.13 Cleanup ───────────────────────────────────────────────────────
  return () => {
    // Laufenden Stream abbrechen
    if (abortController) {
      abortController.abort();
      abortController = null;
    }
    isStreaming = false;
  };
}

// ═══════════════════════════════════════════════════════════════════════════════
// 3. CHAT-BUBBLE-CSS (einmalige Injektion)
// ═══════════════════════════════════════════════════════════════════════════════

function _injectChatStyles() {
  if (document.getElementById('ai-chat-styles')) return;
  const style = document.createElement('style');
  style.id = 'ai-chat-styles';
  style.textContent = `
    /* System-Nachricht */
    .message-system {
      text-align: center;
      padding: var(--space-2) var(--space-4);
    }
    .message-system span {
      display: inline-block;
      font-size: var(--text-xs);
      color: var(--color-text-tertiary);
      background: var(--color-glass-bg);
      border: 1px solid var(--color-glass-border);
      border-radius: var(--radius-full);
      padding: var(--space-1) var(--space-4);
      max-width: 80%;
    }

    /* User-Nachricht (rechtsbündig) */
    .message-user {
      display: flex;
      justify-content: flex-end;
      padding: 0 var(--space-2);
    }
    .user-bubble {
      background: linear-gradient(135deg, rgba(160,107,255,0.15), rgba(160,107,255,0.06));
      border: 1px solid rgba(160,107,255,0.25);
      border-radius: var(--radius-lg) var(--radius-lg) var(--radius-sm) var(--radius-lg);
      box-shadow: 0 0 12px rgba(160,107,255,0.10);
      max-width: 75%;
    }

    /* AI-Nachricht (linksbündig) */
    .message-ai {
      display: flex;
      justify-content: flex-start;
      padding: 0 var(--space-2);
    }
    .ai-bubble {
      background: var(--glass-bg);
      border: var(--glass-border);
      border-radius: var(--radius-lg) var(--radius-lg) var(--radius-lg) var(--radius-sm);
      max-width: 85%;
    }

    /* Bubble-Basis */
    .msg-bubble {
      padding: var(--space-3) var(--space-4);
      animation: fadeSlideIn 250ms cubic-bezier(0,0,0.2,1) both;
    }
    .msg-label {
      font-size: var(--text-xs);
      font-weight: var(--font-semibold);
      margin-bottom: var(--space-2);
      color: var(--color-text-secondary);
    }
    .user-bubble .msg-label {
      color: var(--color-accent-primary);
    }
    .msg-content {
      font-size: var(--text-sm);
      line-height: 1.7;
      color: var(--color-text-primary);
      word-break: break-word;
    }
    .msg-content code {
      background: rgba(0,0,0,0.35);
      padding: 1px 6px;
      border-radius: 4px;
      font-family: var(--font-mono);
      font-size: 0.9em;
      color: var(--color-accent-cyan);
    }
    .msg-content strong {
      color: #ffffff;
      font-weight: var(--font-semibold);
    }
  `;
  document.head.appendChild(style);
}

// ═══════════════════════════════════════════════════════════════════════════════
// 4. HILFSFUNKTIONEN
// ═══════════════════════════════════════════════════════════════════════════════

function _escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(str)));
  return div.innerHTML;
}