// FILE: frontend/static/js/router.js
/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * Hash-Router für das Imperium OS Single-Page-Application-Framework.
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Verantwortlich für:
 *   • Flimmerfreie, animierte Übergänge zwischen Views (#/dashboard, #/media …)
 *   • Automatische Synchronisation der aktiven Sidebar-Navigationsklasse
 *   • View-Lifecycle-Management: mount → cleanup (Event-Listener, Timer, SSE)
 *   • Ladezustand während asynchroner View-Initialisierung
 *   • 404-Fallback-View mit konsistentem Cyber-Dark-Styling
 *   • programmatische Navigation via navigateTo()
 *   • Scroll-Reset und Fokus-Management nach Navigation
 *   • Breadcrumb-Synchronisation via CustomEvent
 *   • Fehlergrenze (Error Boundary) für abstürzende Views
 *
 * Architektur:
 *   Jede View-Funktion kann ein Cleanup-Objekt oder eine Cleanup-Funktion
 *   zurückgeben, die beim Verlassen der Route automatisch aufgerufen wird.
 *
 *   function showMyView(container) {
 *     const timer = setInterval(…);
 *     const handler = (e) => { … };
 *     document.addEventListener('click', handler);
 *
 *     return () => {  // Cleanup-Funktion
 *       clearInterval(timer);
 *       document.removeEventListener('click', handler);
 *     };
 *   }
 */

// ── View-Importe ────────────────────────────────────────────────────────────
import { showDashboard } from './views/dashboard.js';
import { showMedia } from './views/media.js';
import { showFiles } from './views/files.js';
import { showFinance } from './views/finance.js';
import { showHealth } from './views/health.js';
import { showSchool } from './views/school.js';
import { showAuto } from './views/auto.js';
import { showCode } from './views/code.js';
import { showMusic } from './views/music.js';
import { showAIChat } from './views/ai_chat.js';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. ROUTEN-TABELLE
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Routen-Definitionen: Hash → Render-Funktion + Metadaten
 *
 * @type {Record<string, {
 *   render: (container: HTMLElement) => (Promise<void>|void),
 *   label: string,       // Breadcrumb- & Titel-Text
 *   icon: string,        // Unicode-Icon (für etwaige dynamische Sidebar)
 *   guard?: () => boolean  // Optionale Zugriffswächter-Funktion
 * }>}
 */
const routes = {
  '#/dashboard': {
    render: showDashboard,
    label: 'Dashboard',
    icon: '◈'
  },
  '#/media': {
    render: showMedia,
    label: 'Medienbunker',
    icon: '▶'
  },
  '#/music': {
    render: showMusic,
    label: 'Musikarchiv',
    icon: '♫'
  },
  '#/files': {
    render: showFiles,
    label: 'Dateibunker',
    icon: '📁'
  },
  '#/finance': {
    render: showFinance,
    label: 'Finanzen',
    icon: '◎'
  },
  '#/health': {
    render: showHealth,
    label: 'Bio-Tracking',
    icon: '♡'
  },
  '#/school': {
    render: showSchool,
    label: 'Ausbildung',
    icon: '✎'
  },
  '#/auto': {
    render: showAuto,
    label: 'Automotive',
    icon: '⚙'
  },
  '#/code': {
    render: showCode,
    label: 'Code Workbench',
    icon: '⟨⟩'
  },
  '#/ai-studio': {
    render: showAIChat,
    label: 'AI Studio',
    icon: '✦'
  }
};

// ═══════════════════════════════════════════════════════════════════════════════
// 2. LOKALER ZUSTAND
// ═══════════════════════════════════════════════════════════════════════════════

/** Aktuell aktiver Hash (für Cleanup-Referenz) */
let _currentHash = null;

/**
 * Aktive Cleanup-Funktion der momentan angezeigten View.
 * Wird beim Verlassen der Route aufgerufen.
 *
 * @type {Function|null}
 */
let _activeCleanup = null;

/** Verhindert Race-Conditions bei schnellem Hash-Wechsel */
let _transitionId = 0;

/** DOM-Referenz auf den View-Port (wird einmalig gecacht) */
let _viewPort = null;

// ═══════════════════════════════════════════════════════════════════════════════
// 3. HILFSFUNKTIONEN
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Gibt (bzw. cached) das View-Port-DOM-Element.
 * @returns {HTMLElement}
 */
function _getViewPort() {
  if (!_viewPort) {
    _viewPort = document.getElementById('view-port');
    if (!_viewPort) {
      throw new Error('Router: #view-port-Element nicht im DOM gefunden.');
    }
  }
  return _viewPort;
}

/**
 * Synchronisiert die aktive CSS-Klasse in der Sidebar.
 * Entfernt .active von allen .nav-item-Elementen und setzt sie
 * auf das Element, dessen href dem aktuellen Hash entspricht.
 *
 * @param {string} hash — Der neue Hash (z. B. "#/dashboard")
 */
function _syncSidebarActive(hash) {
  const allItems = document.querySelectorAll('#sidebar-nav .nav-item');
  allItems.forEach(item => {
    const isActive = item.getAttribute('href') === hash;
    item.classList.toggle('active', isActive);

    // ARIA-Attribut für Screenreader
    if (isActive) {
      item.setAttribute('aria-current', 'page');
    } else {
      item.removeAttribute('aria-current');
    }
  });
}

/**
 * Sendet ein CustomEvent zur Synchronisation von Breadcrumb & Seitentitel.
 * Wird von index.html (Shell-Script) abgehört.
 *
 * @param {string} label — Anzeigetext für Breadcrumb und Titel
 */
function _syncBreadcrumb(label) {
  window.dispatchEvent(new CustomEvent('imperium:route-changed', {
    detail: { label }
  }));
  document.title = `Imperium OS — ${label}`;
}

/**
 * Setzt die Scroll-Position des View-Ports zurück (nach oben).
 */
function _resetScroll() {
  const vp = _getViewPort();
  if (vp) vp.scrollTop = 0;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 4. 404-FALLBACK-VIEW
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Rendert eine konsistente 404-Fehlerseite im Cyber-Dark-Stil.
 *
 * @param {HTMLElement} container
 * @param {string} [requestedHash] — Der nicht gefundene Hash
 */
function _render404(container, requestedHash = null) {
  const displayPath = requestedHash
    ? requestedHash.replace('#/', '/')
    : 'Unbekannte Route';

  container.innerHTML = `
    <div class="empty-state">
      <div class="empty-icon" aria-hidden="true">⚠</div>
      <h3>404 — Bereich nicht gefunden</h3>
      <p>
        Die angeforderte Route <code>${_escapeHtml(displayPath)}</code>
        existiert nicht im Imperium OS.
      </p>
      <a href="#/dashboard" class="btn btn-primary btn-pill" style="margin-top: var(--space-4);">
        Zurück zum Dashboard
      </a>
    </div>
  `;
}

/**
 * Einfacher HTML-Escape für die 404-Anzeige.
 *
 * @param {string} str
 * @returns {string}
 */
function _escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 5. KERN-ROUTING-LOGIK
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Führt einen flimmerfreien View-Wechsel durch:
 *   1. Vorherige View-Cleanup-Funktion aufrufen
 *   2. Lade-Zustand anzeigen (Skeleton)
 *   3. Neue View rendern (async unterstützt)
 *   4. Cleanup registrieren
 *   5. Sidebar & Breadcrumb synchronisieren
 *   6. Scroll-Position zurücksetzen
 *
 * Race-Condition-Schutz: Jeder Durchlauf bekommt eine Transition-ID.
 * Nur der letzte Durchlauf darf das finale DOM schreiben.
 *
 * @param {string} hash — Ziel-Hash (z. B. "#/dashboard")
 */
async function _transitionTo(hash) {
  const tId = ++_transitionId;
  const route = routes[hash];
  const container = _getViewPort();

  // ── Phase 1: Vorherige View abbauen ─────────────────────────────────
  if (typeof _activeCleanup === 'function') {
    try {
      _activeCleanup();
    } catch (err) {
      console.error('Router: Fehler beim Cleanup der vorherigen View:', err);
    }
    _activeCleanup = null;
  }

  // ── Phase 2: 404-Behandlung ─────────────────────────────────────────
  if (!route) {
    if (tId !== _transitionId) return; // Race-Check
    _render404(container, hash);
    _syncSidebarActive(hash);
    _resetScroll();
    _currentHash = hash;
    return;
  }

  // ── Phase 3: Guard-Prüfung (optionaler Zugriffswächter) ──────────────
  if (typeof route.guard === 'function') {
    try {
      const allowed = route.guard();
      if (!allowed) {
        if (tId !== _transitionId) return;
        container.innerHTML = `
          <div class="empty-state">
            <div class="empty-icon" aria-hidden="true">🔒</div>
            <h3>Zugriff verweigert</h3>
            <p>Du hast keine Berechtigung für diesen Bereich.</p>
          </div>`;
        _syncSidebarActive(hash);
        _resetScroll();
        _currentHash = hash;
        return;
      }
    } catch (err) {
      console.error('Router: Guard-Prüfung fehlgeschlagen:', err);
    }
  }

  // ── Phase 4: Ladezustand (Skeleton) ──────────────────────────────────
  container.innerHTML = `
    <div class="section-header">
      <div>
        <div class="skeleton skeleton-title"></div>
        <div class="skeleton skeleton-text" style="width: 40%;"></div>
      </div>
    </div>
    <div class="dashboard-grid">
      <div class="glass-card"><div class="skeleton skeleton-card"></div></div>
      <div class="glass-card"><div class="skeleton skeleton-card"></div></div>
      <div class="glass-card"><div class="skeleton skeleton-card"></div></div>
    </div>
  `;

  // ── Phase 5: Neue View rendern ───────────────────────────────────────
  try {
    const result = route.render(container);

    // Race-Check nach asynchroner Ausführung
    if (tId !== _transitionId) return;

    // Cleanup-Funktion aus dem Rückgabewert extrahieren
    if (typeof result === 'function') {
      _activeCleanup = result;
    } else if (result && typeof result.then === 'function') {
      // Async-Funktion: warte auf Abschluss, dann Cleanup prüfen
      const resolved = await result;
      if (tId !== _transitionId) return; // Erneuter Race-Check nach await

      if (typeof resolved === 'function') {
        _activeCleanup = resolved;
      } else if (resolved && typeof resolved.cleanup === 'function') {
        _activeCleanup = () => resolved.cleanup();
      }
    } else if (result && typeof result.cleanup === 'function') {
      _activeCleanup = () => result.cleanup();
    }

    // View-Animation (Einblenden)
    container.style.animation = 'none';
    container.offsetHeight; // Reflow-Trigger
    container.style.animation = 'fadeSlideIn 250ms cubic-bezier(0, 0, 0.2, 1) both';

  } catch (err) {
    // ── Fehlergrenze: View-Absturz abfangen ──────────────────────────
    if (tId !== _transitionId) return;

    console.error(`Router: Fehler beim Rendern von ${hash}:`, err);
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon" aria-hidden="true">⚠</div>
        <h3>Fehler beim Laden der Ansicht</h3>
        <p>Die angeforderte Komponente konnte nicht gerendert werden.</p>
        <code class="code-block" style="margin-top: var(--space-4); max-width: 600px;">
${_escapeHtml(err.message || 'Unbekannter Fehler')}
        </code>
        <a href="#/dashboard" class="btn btn-primary btn-pill" style="margin-top: var(--space-4);">
          Zurück zum Dashboard
        </a>
      </div>
    `;
  }

  // ── Phase 6: UI synchronisieren ──────────────────────────────────────
  _syncSidebarActive(hash);
  _syncBreadcrumb(route.label);
  _resetScroll();
  _currentHash = hash;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 6. ÖFFENTLICHE API
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Programmatische Navigation zu einer beliebigen Route.
 * Aktualisiert window.location.hash und löst den Router aus.
 *
 * @param {string} hash — Ziel-Hash mit oder ohne '#' (z. B. "#/media" oder "/media")
 *
 * @example
 *   // Aus einer View heraus:
 *   import { navigateTo } from '../router.js';
 *   navigateTo('#/finance');
 *   navigateTo('/dashboard');  // '#' wird automatisch ergänzt
 */
export function navigateTo(hash) {
  if (!hash.startsWith('#')) {
    hash = '#' + hash;
  }
  window.location.hash = hash;
}

/**
 * Gibt die aktuell aktive Route zurück.
 *
 * @returns {string|null} — Der aktuelle Hash oder null
 */
export function getCurrentRoute() {
  return _currentHash;
}

/**
 * Gibt die Metadaten einer Route zurück (label, icon, guard).
 *
 * @param {string} hash
 * @returns {object|null}
 */
export function getRouteMeta(hash) {
  return routes[hash] || null;
}

/**
 * Registriert eine neue Route zur Laufzeit (für Plugins / dynamische Module).
 *
 * @param {string} hash — Hash-Schlüssel (z. B. "#/plugins/my-plugin")
 * @param {Function} renderFn — Render-Funktion (container) => void|cleanupFn
 * @param {object} [meta] — Optionale Metadaten
 * @param {string} [meta.label] — Anzeigename
 */
export function registerRoute(hash, renderFn, meta = {}) {
  if (routes[hash]) {
    console.warn(`Router: Route "${hash}" wird überschrieben.`);
  }
  routes[hash] = {
    render: renderFn,
    label: meta.label || hash.replace('#/', ''),
    icon: meta.icon || '·',
    guard: meta.guard || null
  };
}

// ═══════════════════════════════════════════════════════════════════════════════
// 7. EVENT-HANDLER & INITIALISIERUNG
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Hash-Change-Listener: Führt den View-Wechsel bei jeder Hash-Änderung durch.
 * Wird NICHT ausgelöst, wenn der Hash sich nicht tatsächlich ändert.
 */
window.addEventListener('hashchange', () => {
  const hash = window.location.hash || '#/dashboard';
  if (hash !== _currentHash) {
    _transitionTo(hash);
  }
});

/**
 * Initialer Seitenaufruf: Rendert die Startroute.
 * `load`-Event stellt sicher, dass das DOM vollständig geparst ist.
 */
window.addEventListener('load', () => {
  const hash = window.location.hash || '#/dashboard';
  _transitionTo(hash);
});

/**
 * Zusätzlicher DOMContentLoaded-Listener als Fallback,
 * falls `load` durch langsame Assets verzögert wird.
 */
document.addEventListener('DOMContentLoaded', () => {
  if (!_currentHash) {
    const hash = window.location.hash || '#/dashboard';
    _transitionTo(hash);
  }
});

// ═══════════════════════════════════════════════════════════════════════════════
// 8. DEFAULT-EXPORT (Router-Instanz)
// ═══════════════════════════════════════════════════════════════════════════════

export default {
  navigateTo,
  getCurrentRoute,
  getRouteMeta,
  registerRoute
};