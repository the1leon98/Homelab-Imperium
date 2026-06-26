// FILE: frontend/static/js/views/dashboard.js
/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * Dashboard — Echtzeit-Systemmonitoring des Imperium OS.
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Visualisiert CPU, RAM und Festplattenbelegung des HP-Servers mit
 * animierten SVG-Kreisdiagrammen (Circular Progress Gauges).
 *
 * Features:
 *   • Drei Neon-animierte SVG-Gauges (CPU / RAM / Disk)
 *   • Farbcodierung: Grün (≤50 %) → Orange (≤80 %) → Rot (>80 %)
 *   • Sanfte Animation bei Wertänderungen (stroke-dasharray-Transition)
 *   • Automatische Aktualisierung alle 5 Sekunden (Intervall-Polling)
 *   • Zusatzinfos: CPU-Kerne, Temperatur, Uptime
 *   • Cleanup beim Verlassen der View (Intervall-Stopp)
 *
 * Verwendete API-Endpunkte:
 *   GET /api/system/metrics       — SystemMetricResponse
 *   GET /api/system/temperature   — TemperatureResponse
 */

import { get } from '../api.js';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. KONSTANTEN
// ═══════════════════════════════════════════════════════════════════════════════

/** Polling-Intervall in Millisekunden (5 Sekunden) */
const POLL_INTERVAL_MS = 5000;

/** SVG-Gauge-Abmessungen (Quadrat, w: 180px, h: 180px) */
const GAUGE_SIZE = 180;
const GAUGE_RADIUS = 75;
const GAUGE_STROKE_WIDTH = 10;
const GAUGE_CIRCUMFERENCE = 2 * Math.PI * GAUGE_RADIUS;

/**
 * Farb-Schwellwerte für Gauge-Färbung.
 * 0–50 % → success (grün), 51–80 % → warning (orange), 81–100 % → danger (rot)
 */
const THRESHOLD_SUCCESS = 50;
const THRESHOLD_WARNING = 80;

// ═══════════════════════════════════════════════════════════════════════════════
// 2. SVG-GAUGE-FABRIK
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Erzeugt das HTML-Markup für ein einzelnes SVG-Kreisdiagramm.
 *
 * Aufbau:
 *   - Hintergrund-Kreis (dunkle Spur)
 *   - Vordergrund-Kreis (animierter Fortschrittsbogen mit Neon-Glow)
 *   - Zentrierter Prozent-Text
 *   - Label-Text unterhalb des Gauges
 *
 * @param {string} idPrefix    — Eindeutige ID für SVG-Elemente (z. B. "cpu")
 * @param {string} label       — Anzeigename (z. B. "CPU")
 * @param {string} accentColor — CSS-Variable für die Akzentfarbe (z. B. "var(--color-accent-primary)")
 * @param {string} glowColor   — CSS-Variable für den Glow (z. B. "var(--color-accent-primary-glow)")
 * @returns {string} HTML-String
 */
function _createGaugeHTML(idPrefix, label, accentColor, glowColor) {
  const center = GAUGE_SIZE / 2;
  const dashOffset = GAUGE_CIRCUMFERENCE; // Start: 0 % (voller Offset = leer)

  return `
    <div class="gauge-container" style="display:flex; flex-direction:column; align-items:center; gap:var(--space-3);">
      <svg class="gauge-svg"
           width="${GAUGE_SIZE}" height="${GAUGE_SIZE}"
           viewBox="0 0 ${GAUGE_SIZE} ${GAUGE_SIZE}"
           aria-label="${label}-Auslastung" role="img">
        <!-- Hintergrund-Spur -->
        <circle class="gauge-track"
                cx="${center}" cy="${center}" r="${GAUGE_RADIUS}"
                fill="none"
                stroke="rgba(255,255,255,0.06)"
                stroke-width="${GAUGE_STROKE_WIDTH}" />
        <!-- Fortschrittsbogen (wird via JS aktualisiert) -->
        <circle class="gauge-fill"
                id="${idPrefix}-gauge-fill"
                cx="${center}" cy="${center}" r="${GAUGE_RADIUS}"
                fill="none"
                stroke="${accentColor}"
                stroke-width="${GAUGE_STROKE_WIDTH}"
                stroke-linecap="round"
                stroke-dasharray="${GAUGE_CIRCUMFERENCE}"
                stroke-dashoffset="${dashOffset}"
                transform="rotate(-90 ${center} ${center})"
                style="transition: stroke-dashoffset 0.8s cubic-bezier(0.34, 1.56, 0.64, 1),
                       stroke 0.6s ease;
                       filter: drop-shadow(0 0 6px ${glowColor});" />
        <!-- Prozent-Text zentriert -->
        <text class="gauge-value"
              id="${idPrefix}-gauge-value"
              x="${center}" y="${center}"
              text-anchor="middle" dominant-baseline="central"
              fill="var(--color-text-primary)"
              font-size="28" font-weight="700"
              style="font-family: var(--font-sans);">
          0%
        </text>
        <!-- Zusatzinfo (GB / Kerne) -->
        <text class="gauge-detail"
              id="${idPrefix}-gauge-detail"
              x="${center}" y="${center + 26}"
              text-anchor="middle" dominant-baseline="central"
              fill="var(--color-text-tertiary)"
              font-size="12" font-weight="500"
              style="font-family: var(--font-sans);">
          --
        </text>
      </svg>
      <!-- Label unterhalb des Gauges -->
      <span class="gauge-label"
            style="font-size:var(--text-sm); font-weight:var(--font-semibold);
                   color:var(--color-text-secondary); text-transform:uppercase;
                   letter-spacing:var(--tracking-wide);">
        ${label}
      </span>
    </div>`;
}

/**
 * Aktualisiert Füllstand, Farbe und Detailtext eines SVG-Gauges.
 *
 * @param {string} idPrefix  — ID-Präfix des Gauges (z. B. "cpu")
 * @param {number} percent   — Prozentwert (0–100)
 * @param {string} detail    — Zusatztext unter dem Prozentwert (z. B. "8 Kerne")
 * @param {string} accentOk  — CSS-Farbe für ≤50 %
 * @param {string} accentWarn — CSS-Farbe für 51–80 %
 * @param {string} accentCrit — CSS-Farbe für >80 %
 * @param {string} glowOk    — CSS-Glow für ≤50 %
 * @param {string} glowWarn  — CSS-Glow für 51–80 %
 * @param {string} glowCrit  — CSS-Glow für >80 %
 */
function _updateGauge(idPrefix, percent, detail,
                       accentOk, accentWarn, accentCrit,
                       glowOk, glowWarn, glowCrit) {
  const fill = document.getElementById(`${idPrefix}-gauge-fill`);
  const value = document.getElementById(`${idPrefix}-gauge-value`);
  const detailEl = document.getElementById(`${idPrefix}-gauge-detail`);

  if (!fill || !value) return;

  // Prozentwert clampen und Dash-Offset berechnen
  const pct = Math.max(0, Math.min(100, percent));
  const offset = GAUGE_CIRCUMFERENCE * (1 - pct / 100);

  fill.setAttribute('stroke-dashoffset', offset);

  // Farbe nach Schwellwert wählen
  let color, glow;
  if (pct <= THRESHOLD_SUCCESS) {
    color = accentOk;
    glow = glowOk;
  } else if (pct <= THRESHOLD_WARNING) {
    color = accentWarn;
    glow = glowWarn;
  } else {
    color = accentCrit;
    glow = glowCrit;
  }

  fill.setAttribute('stroke', color);
  fill.style.filter = `drop-shadow(0 0 6px ${glow})`;

  // Prozent-Text
  value.textContent = `${Math.round(pct)}%`;

  // Detail-Text
  if (detailEl && detail) {
    detailEl.textContent = detail;
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// 3. DASHBOARD-HAUPTFUNKTION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Rendert das vollständige Dashboard und startet das Metrik-Polling.
 *
 * Gibt eine Cleanup-Funktion zurück, die der Router beim Verlassen
 * der View automatisch aufruft (stoppt das Polling-Intervall).
 *
 * @param {HTMLElement} container — Der #view-port-Container
 * @returns {Function} Cleanup-Funktion (stoppt Intervall)
 */
export function showDashboard(container) {
  // ── 3.1 Grundlayout rendern ────────────────────────────────────────────
  container.innerHTML = `
    <!-- Section-Header -->
    <div class="section-header">
      <div>
        <h1>Dashboard</h1>
        <p class="section-subtitle">Echtzeit-Systemmetriken des HP-Servers</p>
      </div>
      <div class="flex items-center gap-3">
        <span class="text-xs text-tertiary" id="poll-indicator">
          ● Live — Aktualisierung alle 5s
        </span>
      </div>
    </div>

    <!-- Gauges-Reihe: CPU | RAM | Disk -->
    <div class="dashboard-grid cols-3" style="margin-bottom: var(--space-6);">
      <div class="glass-card" style="display:flex; justify-content:center; padding:var(--space-8) var(--space-6);">
        ${_createGaugeHTML('cpu', 'CPU', 'var(--color-accent-primary)', 'rgba(160, 107, 255, 0.40)')}
      </div>
      <div class="glass-card" style="display:flex; justify-content:center; padding:var(--space-8) var(--space-6);">
        ${_createGaugeHTML('ram', 'Arbeitsspeicher', 'var(--color-accent-cyan)', 'rgba(0, 245, 212, 0.40)')}
      </div>
      <div class="glass-card" style="display:flex; justify-content:center; padding:var(--space-8) var(--space-6);">
        ${_createGaugeHTML('disk', 'Festplatte', 'var(--color-accent-blue)', 'rgba(56, 225, 255, 0.40)')}
      </div>
    </div>

    <!-- Schnell-Statistiken -->
    <div class="dashboard-grid cols-4" style="margin-bottom: var(--space-6);">
      <div class="glass-card subtle stat-widget">
        <span class="stat-label">CPU-Kerne</span>
        <span class="stat-value" id="stat-cores">--</span>
      </div>
      <div class="glass-card subtle stat-widget">
        <span class="stat-label">CPU-Temperatur</span>
        <span class="stat-value" id="stat-temp">--°C</span>
      </div>
      <div class="glass-card subtle stat-widget">
        <span class="stat-label">RAM Gesamt</span>
        <span class="stat-value" id="stat-ram-total">-- GB</span>
      </div>
      <div class="glass-card subtle stat-widget">
        <span class="stat-label">Uptime</span>
        <span class="stat-value" id="stat-uptime" style="font-size:var(--text-xl);">--</span>
      </div>
    </div>

    <!-- Modul-Zusammenfassung -->
    <div class="section-header" style="margin-top: var(--space-4);">
      <h2 style="font-size: var(--text-xl);">Modul-Übersicht</h2>
    </div>
    <div class="dashboard-grid cols-3" id="module-summary-grid">
      <!-- Wird dynamisch mit Status-Karten befüllt -->
    </div>
  `;

  // ── 3.2 Farbreferenzen für Gauge-Updates ──────────────────────────────
  const CPU_OK = 'var(--color-accent-primary)';
  const CPU_WARN = 'var(--color-warning)';
  const CPU_CRIT = 'var(--color-danger)';
  const CPU_GLOW_OK = 'rgba(160, 107, 255, 0.40)';
  const CPU_GLOW_WARN = 'rgba(255, 159, 10, 0.40)';
  const CPU_GLOW_CRIT = 'rgba(255, 64, 129, 0.50)';

  const RAM_OK = 'var(--color-accent-cyan)';
  const RAM_WARN = 'var(--color-warning)';
  const RAM_CRIT = 'var(--color-danger)';
  const RAM_GLOW_OK = 'rgba(0, 245, 212, 0.40)';
  const RAM_GLOW_WARN = 'rgba(255, 159, 10, 0.40)';
  const RAM_GLOW_CRIT = 'rgba(255, 64, 129, 0.50)';

  const DISK_OK = 'var(--color-accent-blue)';
  const DISK_WARN = 'var(--color-warning)';
  const DISK_CRIT = 'var(--color-danger)';
  const DISK_GLOW_OK = 'rgba(56, 225, 255, 0.40)';
  const DISK_GLOW_WARN = 'rgba(255, 159, 10, 0.40)';
  const DISK_GLOW_CRIT = 'rgba(255, 64, 129, 0.50)';

  // ── 3.3 Polling-Funktion ──────────────────────────────────────────────
  /**
   * Ruft Systemmetriken und Temperatur ab und aktualisiert alle UI-Elemente.
   * Fehler werden stumm behandelt — die Anzeige bleibt beim letzten Stand.
   */
  async function _pollMetrics() {
    try {
      // Parallele Requests für Metriken und Temperatur
      const [metrics, temp] = await Promise.all([
        get('/system/metrics', {}, { skipLoader: true, skipToast: true }),
        get('/system/temperature', {}, { skipLoader: true, skipToast: true })
      ]);

      // ── CPU-Gauge ────────────────────────────────────────────────────
      _updateGauge(
        'cpu', metrics.cpu_percent,
        `${metrics.cpu_count} Kerne`,
        CPU_OK, CPU_WARN, CPU_CRIT,
        CPU_GLOW_OK, CPU_GLOW_WARN, CPU_GLOW_CRIT
      );

      // ── RAM-Gauge ────────────────────────────────────────────────────
      _updateGauge(
        'ram', metrics.ram_percent,
        `${metrics.ram_used_gb?.toFixed(1) || '--'} / ${metrics.ram_total_gb?.toFixed(0) || '--'} GB`,
        RAM_OK, RAM_WARN, RAM_CRIT,
        RAM_GLOW_OK, RAM_GLOW_WARN, RAM_GLOW_CRIT
      );

      // ── Disk-Gauge ───────────────────────────────────────────────────
      _updateGauge(
        'disk', metrics.disk_percent,
        `${metrics.disk_free_gb?.toFixed(0) || '--'} GB frei`,
        DISK_OK, DISK_WARN, DISK_CRIT,
        DISK_GLOW_OK, DISK_GLOW_WARN, DISK_GLOW_CRIT
      );

      // ── Stat-Widgets ─────────────────────────────────────────────────
      const coresEl = document.getElementById('stat-cores');
      const tempEl = document.getElementById('stat-temp');
      const ramTotalEl = document.getElementById('stat-ram-total');
      const uptimeEl = document.getElementById('stat-uptime');

      if (coresEl) coresEl.textContent = metrics.cpu_count ?? '--';
      if (ramTotalEl) ramTotalEl.textContent = `${(metrics.ram_total_gb ?? 0).toFixed(0)} GB`;

      if (tempEl && temp) {
        if (temp.is_available && temp.temperature_celsius != null) {
          const t = temp.temperature_celsius;
          tempEl.textContent = `${t.toFixed(1)}°C`;
          // Temperatur-Färbung
          if (t > 80) {
            tempEl.style.color = 'var(--color-danger)';
          } else if (t > 60) {
            tempEl.style.color = 'var(--color-warning)';
          } else {
            tempEl.style.color = 'var(--color-success)';
          }
        } else {
          tempEl.textContent = 'n. v.';
          tempEl.style.color = 'var(--color-text-tertiary)';
        }
      }

      if (uptimeEl) uptimeEl.textContent = metrics.uptime || '--';

      // ── Poll-Indikator kurz grün blinken lassen ──────────────────────
      const indicator = document.getElementById('poll-indicator');
      if (indicator) {
        indicator.style.color = 'var(--color-success)';
        setTimeout(() => {
          indicator.style.color = 'var(--color-text-tertiary)';
        }, 600);
      }

    } catch {
      // Stumm — Anzeige bleibt beim letzten erfolgreichen Stand
      const indicator = document.getElementById('poll-indicator');
      if (indicator) {
        indicator.style.color = 'var(--color-danger)';
        indicator.textContent = '⚠ Verbindungsfehler — wiederhole...';
        setTimeout(() => {
          indicator.textContent = '● Live — Aktualisierung alle 5s';
          indicator.style.color = 'var(--color-text-tertiary)';
        }, 3000);
      }
    }
  }

  // ── 3.4 Modul-Status-Karten rendern ───────────────────────────────────
  _renderModuleSummary();

  // ── 3.5 Initiale Metriken laden & Polling starten ─────────────────────
  _pollMetrics();
  const pollInterval = setInterval(_pollMetrics, POLL_INTERVAL_MS);

  // ── 3.6 Cleanup-Funktion (wird vom Router beim Verlassen aufgerufen) ──
  return () => {
    clearInterval(pollInterval);
  };
}

// ═══════════════════════════════════════════════════════════════════════════════
// 4. MODUL-ÜBERSICHT
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Rendert statische Status-Karten für jedes Imperium-OS-Modul.
 * Dient als Schnellzugriff-Übersicht auf dem Dashboard.
 */
function _renderModuleSummary() {
  const grid = document.getElementById('module-summary-grid');
  if (!grid) return;

  /** @type {Array<{name:string, icon:string, desc:string, route:string, gradient:string}>} */
  const modules = [
    {
      name: 'Medienbunker',
      icon: '▶',
      desc: 'Jellyfin — Filme, Serien, Live-TV',
      route: '#/media',
      gradient: 'var(--agent-gradient-health)'
    },
    {
      name: 'Musikarchiv',
      icon: '♫',
      desc: 'MP3/FLAC — Mutagen-Indizierung',
      route: '#/music',
      gradient: 'linear-gradient(135deg, #38e1ff, #a06bff)'
    },
    {
      name: 'Dateibunker',
      icon: '📁',
      desc: 'Sicheres Dateimanagement',
      route: '#/files',
      gradient: 'linear-gradient(135deg, #00f5d4, #38e1ff)'
    },
    {
      name: 'Finanzen',
      icon: '◎',
      desc: 'Transaktionen, Budgets, Trends',
      route: '#/finance',
      gradient: 'linear-gradient(135deg, #ffd60a, #ff9f0a)'
    },
    {
      name: 'Bio-Tracking',
      icon: '♡',
      desc: 'Gesundheit, Ernährung, Hologramm',
      route: '#/health',
      gradient: 'var(--agent-gradient-health)'
    },
    {
      name: 'Ausbildung',
      icon: '✎',
      desc: 'Noten, Fristen, IHK-Stoff',
      route: '#/school',
      gradient: 'var(--agent-gradient-it)'
    },
    {
      name: 'Automotive',
      icon: '⚙',
      desc: 'Fahrzeuge, CAD, Wartung',
      route: '#/auto',
      gradient: 'var(--agent-gradient-auto)'
    },
    {
      name: 'Code Workbench',
      icon: '⟨⟩',
      desc: 'Sandbox, Analyse, Docker',
      route: '#/code',
      gradient: 'linear-gradient(135deg, #00f5d4, #a06bff)'
    },
    {
      name: 'AI Studio',
      icon: '✦',
      desc: '4 Agenten, RAG, Ollama',
      route: '#/ai-studio',
      gradient: 'var(--agent-gradient-brainstorm)'
    }
  ];

  grid.innerHTML = modules.map(mod => `
    <a href="${mod.route}" class="glass-card subtle"
       style="display:flex; align-items:center; gap:var(--space-4);
              padding:var(--space-5); text-decoration:none; cursor:pointer;
              transition: border-color var(--duration-fast) var(--ease-default),
                          box-shadow var(--duration-fast) var(--ease-default),
                          transform var(--duration-fast) var(--ease-default);">
      <div style="width:42px; height:42px; border-radius:var(--radius-md);
                  background:${mod.gradient};
                  display:flex; align-items:center; justify-content:center;
                  font-size:var(--text-lg); color:#fff; flex-shrink:0;">
        ${mod.icon}
      </div>
      <div>
        <div style="font-size:var(--text-sm); font-weight:var(--font-semibold);
                    color:var(--color-text-primary);">
          ${mod.name}
        </div>
        <div style="font-size:var(--text-xs); color:var(--color-text-tertiary);
                    margin-top:2px;">
          ${mod.desc}
        </div>
      </div>
    </a>
  `).join('');
}