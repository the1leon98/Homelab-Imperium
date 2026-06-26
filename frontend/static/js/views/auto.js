// FILE: frontend/static/js/views/auto.js
/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * Automotive Workbench — 3D-Drahtgitter-Diagnose & Wartungs-Cockpit.
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Bietet:
 *   • Interaktive 3D-Drahtgitter-SVGs: PKW & Motorrad (Hologramm-Stil)
 *   • Aufschwenkbare Motorhaube (flüssige CSS-Transition)
 *   • Rot/Orange glühende Komponenten bei Wartungsbedarf:
 *     - Motor (oil_change_overdue)
 *     - Bremsen (brake_change_overdue)
 *     - Reifen (tire_change_overdue)
 *     - HU/TÜV (inspection_overdue)
 *   • Fahrzeugliste mit Kilometerstand & Wartungskosten
 *   • Motorberechnungs-Widget (Hubraum, Verdichtung, Leistung)
 *
 * Verwendete API-Endpunkte:
 *   GET /api/auto/vehicles                  — Fahrzeugliste
 *   GET /api/auto/vehicles/{id}/maintenance  — Wartungsstatus
 *   GET /api/auto/maintenance/check-all      — Alle Wartungsbedarfe
 *   GET /api/auto/calculate/engine           — Motorberechnung
 */

import { get } from '../api.js';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. SVG-DRAHTGITTER-MODELLE
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Erzeugt das PKW-Drahtgitter-SVG (300×200 ViewBox).
 * Enthält interaktive Komponenten:
 *   #hood-group    — Motorhaube (schwenkbar)
 *   #part-engine   — Motorblock
 *   #part-brakes-FL/FR/RL/RR — Bremsen
 *   #part-tires-FL/FR/RL/RR  — Reifen
 *   #part-inspection — HU-Plakette
 *
 * @returns {string} SVG-HTML
 */
function _createCarWireframe() {
  return `
  <svg id="car-wireframe" viewBox="0 0 300 200"
       style="width:100%; max-width:600px; display:block;"
       xmlns="http://www.w3.org/2000/svg">
    <defs>
      <filter id="glow-cyan-car" x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="2" result="blur" />
        <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
      <style>
        @keyframes part-pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
        .part-overdue { animation: part-pulse 1.2s ease-in-out infinite; }
        .part-warning { animation: part-pulse 2.2s ease-in-out infinite; }
      </style>
    </defs>

    <!-- ═══ RÄDER ═══ -->
    <g id="wheel-FL">
      <circle cx="55" cy="155" r="22" fill="none" stroke="#38e1ff" stroke-width="1.2"
              filter="url(#glow-cyan-car)" />
      <circle cx="55" cy="155" r="12" fill="none" stroke="#38e1ff" stroke-width="0.6" />
      <line x1="55" y1="133" x2="55" y2="177" stroke="#38e1ff" stroke-width="0.5" opacity="0.4" />
      <line x1="33" y1="155" x2="77" y2="155" stroke="#38e1ff" stroke-width="0.5" opacity="0.4" />
    </g>
    <g id="wheel-FR">
      <circle cx="245" cy="155" r="22" fill="none" stroke="#38e1ff" stroke-width="1.2"
              filter="url(#glow-cyan-car)" />
      <circle cx="245" cy="155" r="12" fill="none" stroke="#38e1ff" stroke-width="0.6" />
      <line x1="245" y1="133" x2="245" y2="177" stroke="#38e1ff" stroke-width="0.5" opacity="0.4" />
      <line x1="223" y1="155" x2="267" y2="155" stroke="#38e1ff" stroke-width="0.5" opacity="0.4" />
    </g>

    <!-- ═══ KAROSSERIE (Unterbau) ═══ -->
    <path d="M 20 155 L 35 100 L 65 75 L 110 50 L 150 42 L 190 50 L 235 75 L 265 100 L 280 155"
          fill="rgba(56,225,255,0.03)" stroke="#38e1ff" stroke-width="1.2"
          filter="url(#glow-cyan-car)" />
    <!-- Stoßstange vorne -->
    <path d="M 22 155 Q 15 150 22 145" fill="none" stroke="#38e1ff" stroke-width="0.8" />
    <!-- Stoßstange hinten -->
    <path d="M 278 155 Q 285 150 278 145" fill="none" stroke="#38e1ff" stroke-width="0.8" />

    <!-- ═══ FAHRERKABINE ═══ -->
    <path d="M 105 52 L 130 15 Q 150 8 170 15 L 195 52"
          fill="rgba(56,225,255,0.04)" stroke="#38e1ff" stroke-width="1"
          filter="url(#glow-cyan-car)" />
    <!-- Lenkrad-Andeutung -->
    <ellipse cx="140" cy="42" rx="8" ry="4" fill="none" stroke="#38e1ff" stroke-width="0.5"
             transform="rotate(-15 140 42)" />

    <!-- ═══ SCHEINWERFER ═══ -->
    <ellipse cx="30" cy="130" rx="6" ry="4" fill="rgba(56,225,255,0.1)" stroke="#38e1ff" stroke-width="0.7" />
    <ellipse cx="270" cy="130" rx="4" ry="3" fill="rgba(255,64,129,0.1)" stroke="#38e1ff" stroke-width="0.7" />

    <!-- ═══ MOTORHAUBE (Gruppe — schwenkbar) ═══ -->
    <g id="hood-group" style="transform-origin:65px 75px; transition:transform 0.7s cubic-bezier(0.34,1.56,0.64,1);">
      <path id="hood-element" d="M 65 75 L 105 52"
            fill="none" stroke="#38e1ff" stroke-width="2" stroke-linecap="round"
            filter="url(#glow-cyan-car)" style="cursor:pointer;" />
      <line x1="70" y1="78" x2="110" y2="55" stroke="#38e1ff" stroke-width="0.4" opacity="0.3" />
    </g>

    <!-- ═══ MOTORBLOCK (unter der Haube) ═══ -->
    <g id="engine-group">
      <rect id="part-engine" x="55" y="82" width="35" height="18" rx="2"
            fill="rgba(56,225,255,0.04)" stroke="#38e1ff" stroke-width="0.8" />
      <!-- Zylinder -->
      <circle cx="65" cy="88" r="4" fill="none" stroke="#38e1ff" stroke-width="0.5" />
      <circle cx="75" cy="88" r="4" fill="none" stroke="#38e1ff" stroke-width="0.5" />
      <circle cx="85" cy="88" r="4" fill="none" stroke="#38e1ff" stroke-width="0.5" />
      <!-- Kurbelwelle -->
      <line x1="60" y1="96" x2="88" y2="96" stroke="#38e1ff" stroke-width="0.6" />
    </g>

    <!-- ═══ BREMSEN ═══ -->
    <g id="part-brakes-FL">
      <circle cx="55" cy="155" r="15" fill="none" stroke="#38e1ff" stroke-width="0.5"
              stroke-dasharray="3,3" />
    </g>
    <g id="part-brakes-FR">
      <circle cx="245" cy="155" r="15" fill="none" stroke="#38e1ff" stroke-width="0.5"
              stroke-dasharray="3,3" />
    </g>

    <!-- ═══ REIFEN ═══ -->
    <g id="part-tires-FL">
      <circle cx="55" cy="155" r="20" fill="none" stroke="#38e1ff" stroke-width="0.6"
              stroke-dasharray="2,4" />
    </g>
    <g id="part-tires-FR">
      <circle cx="245" cy="155" r="20" fill="none" stroke="#38e1ff" stroke-width="0.6"
              stroke-dasharray="2,4" />
    </g>

    <!-- ═══ HU-PLAKETTE ═══ -->
    <g id="part-inspection">
      <circle cx="270" cy="145" r="8" fill="none" stroke="#38e1ff" stroke-width="0.7" />
      <text x="270" y="148" text-anchor="middle" fill="#38e1ff" font-size="6"
            font-family="var(--font-sans)">HU</text>
    </g>

    <!-- ═══ AUSPUFF ═══ -->
    <path d="M 270 155 L 290 155 L 290 148" fill="none" stroke="#38e1ff" stroke-width="0.7" opacity="0.5" />

    <!-- Fahrzeug-Label -->
    <text x="150" y="192" text-anchor="middle" fill="#38e1ff" font-size="9"
          font-family="var(--font-sans)" opacity="0.4">PKW — Hologramm-Diagnose</text>
  </svg>`;
}

/**
 * Erzeugt das Motorrad-Drahtgitter-SVG (200×200 ViewBox).
 * @returns {string}
 */
function _createBikeWireframe() {
  return `
  <svg id="bike-wireframe" viewBox="0 0 200 200"
       style="width:100%; max-width:350px; display:block;"
       xmlns="http://www.w3.org/2000/svg">
    <defs>
      <filter id="glow-cyan-bike" x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="2" result="blur" />
        <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
      <style>
        @keyframes part-pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
        .part-overdue { animation: part-pulse 1.2s ease-in-out infinite; }
        .part-warning { animation: part-pulse 2.2s ease-in-out infinite; }
      </style>
    </defs>

    <!-- ═══ RÄDER ═══ -->
    <g id="bike-wheel-F">
      <circle cx="50" cy="155" r="26" fill="none" stroke="#ffd60a" stroke-width="1.2"
              filter="url(#glow-cyan-bike)" />
      <circle cx="50" cy="155" r="16" fill="none" stroke="#ffd60a" stroke-width="0.6" />
      <line x1="50" y1="129" x2="50" y2="181" stroke="#ffd60a" stroke-width="0.5" opacity="0.4" />
      <line x1="24" y1="155" x2="76" y2="155" stroke="#ffd60a" stroke-width="0.5" opacity="0.4" />
    </g>
    <g id="bike-wheel-R">
      <circle cx="155" cy="155" r="26" fill="none" stroke="#ffd60a" stroke-width="1.2"
              filter="url(#glow-cyan-bike)" />
      <circle cx="155" cy="155" r="16" fill="none" stroke="#ffd60a" stroke-width="0.6" />
      <line x1="155" y1="129" x2="155" y2="181" stroke="#ffd60a" stroke-width="0.5" opacity="0.4" />
      <line x1="129" y1="155" x2="181" y2="155" stroke="#ffd60a" stroke-width="0.5" opacity="0.4" />
    </g>

    <!-- ═══ RAHMEN ═══ -->
    <path d="M 70 155 L 80 100 L 60 70 L 50 55 L 80 40 L 120 70 L 140 155"
          fill="none" stroke="#ffd60a" stroke-width="1.2"
          filter="url(#glow-cyan-bike)" />
    <path d="M 80 100 L 130 100 L 140 155" fill="none" stroke="#ffd60a" stroke-width="0.8" />

    <!-- ═══ LENKER ═══ -->
    <line x1="50" y1="55" x2="90" y2="45" stroke="#ffd60a" stroke-width="1" />
    <line x1="80" y1="40" x2="55" y2="30" stroke="#ffd60a" stroke-width="0.8" />
    <!-- Gabel -->
    <line x1="55" y1="30" x2="50" y2="155" stroke="#ffd60a" stroke-width="0.8" />

    <!-- ═══ TANK ═══ -->
    <path d="M 60 70 Q 70 55 80 40 L 120 70 Q 130 80 120 90 L 80 100 Q 70 90 60 70"
          fill="rgba(255,214,10,0.04)" stroke="#ffd60a" stroke-width="0.8" />

    <!-- ═══ MOTOR ═══ -->
    <g id="bike-part-engine">
      <rect x="72" y="85" width="30" height="22" rx="3"
            fill="rgba(255,214,10,0.04)" stroke="#ffd60a" stroke-width="0.8" />
      <circle cx="87" cy="96" r="6" fill="none" stroke="#ffd60a" stroke-width="0.5" />
    </g>

    <!-- ═══ SITZBANK ═══ -->
    <path d="M 105 75 L 140 75 Q 155 75 155 85 L 100 90 Q 95 85 105 75"
          fill="rgba(255,214,10,0.03)" stroke="#ffd60a" stroke-width="0.7" />

    <!-- ═══ BREMSEN ═══ -->
    <g id="bike-part-brakes-F">
      <circle cx="50" cy="155" r="18" fill="none" stroke="#ffd60a" stroke-width="0.5"
              stroke-dasharray="3,3" />
    </g>
    <g id="bike-part-brakes-R">
      <circle cx="155" cy="155" r="18" fill="none" stroke="#ffd60a" stroke-width="0.5"
              stroke-dasharray="3,3" />
    </g>

    <!-- ═══ AUSPUFF ═══ -->
    <path d="M 90 100 L 90 115 Q 90 120 95 120 L 145 120" fill="none"
          stroke="#ffd60a" stroke-width="0.6" opacity="0.4" />

    <!-- Label -->
    <text x="100" y="192" text-anchor="middle" fill="#ffd60a" font-size="9"
          font-family="var(--font-sans)" opacity="0.4">Motorrad — Hologramm-Diagnose</text>
  </svg>`;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 2. HAUPTFUNKTION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Rendert das Automotive-Dashboard.
 *
 * @param {HTMLElement} container
 * @returns {Function} Cleanup
 */
export async function showAuto(container) {
  // ── 2.1 Grundlayout ────────────────────────────────────────────────────
  container.innerHTML = `
    <div class="section-header">
      <div>
        <h1>Automotive Workbench</h1>
        <p class="section-subtitle">3D-Hologramm-Diagnose, Wartung & Motorberechnung</p>
      </div>
    </div>

    <!-- Fahrzeug-Übersicht -->
    <div class="dashboard-grid cols-3" style="margin-bottom:var(--space-6);" id="vehicle-summary-cards">
    </div>

    <div style="display:grid; grid-template-columns:1fr 1fr; gap:var(--space-6); margin-bottom:var(--space-6);">

      <!-- PKW-Hologramm -->
      <div class="glass-card" style="display:flex; flex-direction:column; align-items:center; padding:var(--space-6);">
        <div class="card-header" style="width:100%;"><h3>🚗 PKW-Drahtgitter</h3></div>
        <div id="car-hologram-container" style="margin:var(--space-4) 0;
                    background:radial-gradient(ellipse at center, rgba(56,225,255,0.04) 0%, transparent 70%);
                    border-radius:var(--radius-lg);">
          ${_createCarWireframe()}
        </div>
        <div style="display:flex; gap:var(--space-3); margin-top:var(--space-3);">
          <button class="btn btn-primary btn-pill" id="toggle-hood-btn">🔧 Motorhaube öffnen</button>
        </div>
        <!-- Wartungs-Legende -->
        <div id="car-legend" class="text-xs" style="margin-top:var(--space-3); color:var(--color-text-tertiary);
                    display:flex; gap:var(--space-4);">
          <span style="display:flex; align-items:center; gap:4px;">
            <span class="status-dot healthy"></span> OK
          </span>
          <span style="display:flex; align-items:center; gap:4px;">
            <span class="status-dot degraded"></span> Bald fällig
          </span>
          <span style="display:flex; align-items:center; gap:4px;">
            <span class="status-dot critical"></span> Überfällig
          </span>
        </div>
      </div>

      <!-- Motorrad-Hologramm -->
      <div class="glass-card" style="display:flex; flex-direction:column; align-items:center; padding:var(--space-6);">
        <div class="card-header" style="width:100%;"><h3>🏍 Motorrad-Drahtgitter</h3></div>
        <div id="bike-hologram-container" style="margin:var(--space-4) 0;
                    background:radial-gradient(ellipse at center, rgba(255,214,10,0.04) 0%, transparent 70%);
                    border-radius:var(--radius-lg);">
          ${_createBikeWireframe()}
        </div>
        <div id="bike-legend" class="text-xs" style="margin-top:var(--space-3); color:var(--color-text-tertiary);
                    display:flex; gap:var(--space-4);">
          <span style="display:flex; align-items:center; gap:4px;">
            <span class="status-dot healthy"></span> OK
          </span>
          <span style="display:flex; align-items:center; gap:4px;">
            <span class="status-dot degraded"></span> Bald fällig
          </span>
          <span style="display:flex; align-items:center; gap:4px;">
            <span class="status-dot critical"></span> Überfällig
          </span>
        </div>
      </div>
    </div>

    <!-- Motorberechnung + Wartungsliste -->
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:var(--space-6);">
      <!-- Motorberechnung -->
      <div class="glass-card">
        <div class="card-header"><h3>⚙ Motorberechnung</h3></div>
        <form id="engine-calc-form" style="display:flex; flex-direction:column; gap:var(--space-3);" autocomplete="off">
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:var(--space-3);">
            <div class="form-group">
              <label class="form-label">Bohrung (mm)</label>
              <input type="number" id="calc-bore" class="form-input" value="86" step="0.1" min="40">
            </div>
            <div class="form-group">
              <label class="form-label">Hub (mm)</label>
              <input type="number" id="calc-stroke" class="form-input" value="86" step="0.1" min="30">
            </div>
            <div class="form-group">
              <label class="form-label">Zylinder</label>
              <input type="number" id="calc-cylinders" class="form-input" value="4" min="1" max="16">
            </div>
            <div class="form-group">
              <label class="form-label">Drehzahl (1/min)</label>
              <input type="number" id="calc-rpm" class="form-input" value="6000" min="500">
            </div>
          </div>
          <button type="submit" class="btn btn-primary" style="align-self:flex-start;">Berechnen</button>
        </form>
        <div id="engine-results" style="margin-top:var(--space-4); display:flex; flex-direction:column; gap:var(--space-2);">
          <span class="text-xs text-tertiary">Ergebnisse erscheinen hier…</span>
        </div>
      </div>

      <!-- Wartungs-Liste -->
      <div class="glass-card">
        <div class="card-header"><h3>🔧 Wartungsstatus</h3></div>
        <div id="maintenance-list" style="max-height:300px; overflow-y:auto;">
          <span class="text-xs text-tertiary" style="display:block; text-align:center; padding:var(--space-6);">Lade Wartungsdaten…</span>
        </div>
      </div>
    </div>
  `;

  // ── 2.2 Hauben-Mechanik ────────────────────────────────────────────────
  let hoodOpen = false;
  const hoodGroup = document.getElementById('hood-group');

  document.getElementById('toggle-hood-btn').addEventListener('click', () => {
    hoodOpen = !hoodOpen;
    if (hoodOpen) {
      hoodGroup.style.transform = 'rotate(-38deg)';
      document.getElementById('toggle-hood-btn').textContent = '🔧 Motorhaube schließen';
    } else {
      hoodGroup.style.transform = 'rotate(0deg)';
      document.getElementById('toggle-hood-btn').textContent = '🔧 Motorhaube öffnen';
    }
  });

  // ── 2.3 Daten laden ────────────────────────────────────────────────────

  async function _loadAllData() {
    try {
      const [vehicles, allMaintenance] = await Promise.all([
        get('/auto/vehicles', {}, { skipToast: true }),
        get('/auto/maintenance/check-all', {}, { skipToast: true })
      ]);

      _renderVehicleCards(vehicles);
      _updateHolograms(allMaintenance, vehicles);
      _renderMaintenanceList(allMaintenance);
    } catch (err) {
      console.error('Auto-Dashboard-Fehler:', err);
    }
  }

  // ── 2.4 Fahrzeug-Übersichtskarten ──────────────────────────────────────

  function _renderVehicleCards(vehicles) {
    const container = document.getElementById('vehicle-summary-cards');
    if (!vehicles || !vehicles.length) {
      container.innerHTML = `<div class="glass-card empty-state" style="grid-column:1/-1;">
        <div class="empty-icon">🚗</div><h3>Keine Fahrzeuge</h3></div>`;
      return;
    }

    container.innerHTML = vehicles.map(v => `
      <div class="glass-card stat-widget" style="align-items:center; text-align:center;">
        <span class="stat-label">${_escapeHtml(v.name || v.model || 'Fahrzeug')}</span>
        <span class="stat-value" style="font-size:var(--text-2xl);">${(v.odometer_km || 0).toLocaleString('de-DE')} km</span>
        <span class="text-xs" style="color:var(--color-text-tertiary);">${v.license_plate || ''} · ${v.type || ''}</span>
      </div>
    `).join('');
  }

  // ── 2.5 Hologramm-Updater ──────────────────────────────────────────────

  function _updateHolograms(allMaintenance, vehicles) {
    // Alle Parts zurücksetzen
    _resetPart('part-engine');
    _resetPart('part-brakes-FL'); _resetPart('part-brakes-FR');
    _resetPart('part-tires-FL'); _resetPart('part-tires-FR');
    _resetPart('part-inspection');
    _resetPart('bike-part-engine');
    _resetPart('bike-part-brakes-F'); _resetPart('bike-part-brakes-R');

    if (!allMaintenance || !allMaintenance.length) return;

    allMaintenance.forEach(m => {
      const isCar = !m.vehicle_type || m.vehicle_type === 'car';
      const prefix = isCar ? '' : 'bike-';

      // Motor/Ölwechsel
      if (m.oil_change_overdue_km > 0) {
        _glowPart(prefix + 'part-engine',
          m.oil_change_overdue_km > 500 ? 'critical' : 'warning');
      }

      // Bremsen
      if (m.brake_change_overdue_days > 0 || m.brake_change_overdue_km > 0) {
        const overdue = Math.max(m.brake_change_overdue_days || 0, (m.brake_change_overdue_km || 0) / 100);
        _glowPart(prefix + 'part-brakes-FL', overdue > 30 ? 'critical' : 'warning');
        _glowPart(prefix + 'part-brakes-FR', overdue > 30 ? 'critical' : 'warning');
        if (!isCar) _glowPart('bike-part-brakes-R', overdue > 30 ? 'critical' : 'warning');
      }

      // Reifen (nur PKW)
      if (isCar && m.tire_change_overdue_days > 0) {
        _glowPart('part-tires-FL', m.tire_change_overdue_days > 30 ? 'critical' : 'warning');
        _glowPart('part-tires-FR', m.tire_change_overdue_days > 30 ? 'critical' : 'warning');
      }

      // HU/TÜV
      if (m.inspection_overdue_days > 0) {
        _glowPart('part-inspection',
          m.inspection_overdue_days > 30 ? 'critical' : 'warning');
      }
    });
  }

  function _resetPart(id) {
    const el = document.getElementById(id);
    if (!el) return;
    // Finde Kreise und Rechtecke im Element und setze zurück
    const shapes = el.querySelectorAll('circle, rect, ellipse');
    shapes.forEach(s => {
      const origStroke = s.getAttribute('data-orig-stroke');
      if (origStroke) s.setAttribute('stroke', origStroke);
    });
    el.classList.remove('part-overdue', 'part-warning');
    el.style.filter = '';
    // Spezielle Behandlung für verschiedene Elemente
    if (id === 'part-inspection') {
      const circle = el.querySelector('circle');
      if (circle) circle.setAttribute('stroke', '#38e1ff');
    }
  }

  function _glowPart(id, level) {
    const el = document.getElementById(id);
    if (!el) return;

    const isCritical = level === 'critical';
    const color = isCritical ? '#ff4081' : '#ff9f0a';
    const glowColor = isCritical ? 'rgba(255,64,129,0.60)' : 'rgba(255,159,10,0.50)';

    el.classList.add(isCritical ? 'part-overdue' : 'part-warning');
    el.style.filter = `drop-shadow(0 0 10px ${glowColor})`;

    // Finde shapes und färbe sie
    const shapes = el.querySelectorAll('circle, rect, ellipse, path');
    shapes.forEach(s => {
      if (!s.getAttribute('data-orig-stroke')) {
        s.setAttribute('data-orig-stroke', s.getAttribute('stroke') || '#38e1ff');
      }
      if (s.getAttribute('stroke')?.includes('38e1ff') || s.getAttribute('stroke')?.includes('ffd60a') ||
          !s.getAttribute('data-orig-stroke') || s.getAttribute('data-orig-stroke')?.includes('38e1ff') ||
          s.getAttribute('data-orig-stroke')?.includes('ffd60a')) {
        s.setAttribute('stroke', color);
      }
    });
  }

  // ── 2.6 Wartungsliste ──────────────────────────────────────────────────

  function _renderMaintenanceList(allMaintenance) {
    const listEl = document.getElementById('maintenance-list');
    if (!allMaintenance || !allMaintenance.length) {
      listEl.innerHTML = `<div class="text-xs" style="color:var(--color-success); text-align:center; padding:var(--space-6);">
        ✓ Alle Fahrzeuge sind gewartet.</div>`;
      return;
    }

    listEl.innerHTML = allMaintenance.map(m => {
      const issues = [];
      if (m.oil_change_overdue_km > 0) issues.push({ label: 'Ölwechsel', detail: `${m.oil_change_overdue_km} km überfällig`, critical: m.oil_change_overdue_km > 500 });
      if (m.inspection_overdue_days > 0) issues.push({ label: 'HU/TÜV', detail: `${m.inspection_overdue_days} Tage überfällig`, critical: m.inspection_overdue_days > 30 });
      if (m.brake_change_overdue_km > 0) issues.push({ label: 'Bremsen', detail: `${m.brake_change_overdue_km} km überfällig`, critical: m.brake_change_overdue_km > 500 });
      if (m.tire_change_overdue_days > 0) issues.push({ label: 'Reifen', detail: `${m.tire_change_overdue_days} Tage überfällig`, critical: m.tire_change_overdue_days > 30 });

      return `
        <div style="padding:var(--space-3) var(--space-4); border-bottom:1px solid var(--color-border-subtle);">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:var(--space-2);">
            <span class="text-xs" style="color:var(--color-text-primary); font-weight:var(--font-semibold);">
              ${_escapeHtml(m.vehicle_name || 'Fahrzeug')}
            </span>
            <span class="text-xs" style="color:var(--color-text-tertiary);">${(m.odometer_km || 0).toLocaleString('de-DE')} km</span>
          </div>
          ${issues.map(i => `
            <div style="display:flex; align-items:center; gap:var(--space-2); margin-top:2px;">
              <span class="status-dot ${i.critical ? 'critical' : 'degraded'}" style="flex-shrink:0;"></span>
              <span class="text-xs" style="color:${i.critical ? 'var(--color-danger)' : 'var(--color-warning)'};">${i.label}</span>
              <span class="text-xs" style="color:var(--color-text-tertiary);">${i.detail}</span>
            </div>
          `).join('')}
        </div>`;
    }).join('');
  }

  // ── 2.7 Motorberechnung ────────────────────────────────────────────────

  document.getElementById('engine-calc-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const bore = parseFloat(document.getElementById('calc-bore').value) || 86;
    const stroke = parseFloat(document.getElementById('calc-stroke').value) || 86;
    const cylinders = parseInt(document.getElementById('calc-cylinders').value) || 4;
    const rpm = parseInt(document.getElementById('calc-rpm').value) || 6000;

    try {
      const result = await get('/auto/calculate/engine', {
        bore_mm: bore, stroke_mm: stroke, cylinders, rpm
      }, { skipToast: true });

      const resultsEl = document.getElementById('engine-results');
      resultsEl.innerHTML = `
        <div class="flex justify-between text-sm">
          <span class="text-secondary">Hubraum</span>
          <span style="color:var(--color-accent-primary);">${result.displacement_cc?.toFixed(0) || '—'} cm³</span>
        </div>
        <div class="flex justify-between text-sm">
          <span class="text-secondary">Hubraum pro Zyl.</span>
          <span style="color:var(--color-text-primary);">${result.displacement_per_cyl_cc?.toFixed(0) || '—'} cm³</span>
        </div>
        <div class="flex justify-between text-sm">
          <span class="text-secondary">Kolbenfläche</span>
          <span style="color:var(--color-text-primary);">${result.piston_area_cm2?.toFixed(1) || '—'} cm²</span>
        </div>
        <div class="flex justify-between text-sm">
          <span class="text-secondary">Hub/Bohrung</span>
          <span style="color:var(--color-text-primary);">${result.stroke_bore_ratio?.toFixed(2) || '—'}</span>
        </div>
        <div class="flex justify-between text-sm" style="margin-top:var(--space-2); padding-top:var(--space-2); border-top:1px solid var(--color-border-subtle);">
          <span class="text-secondary">Mittlere Kolbengeschw.</span>
          <span style="color:var(--color-accent-cyan);">${result.piston_speed_ms?.toFixed(1) || '—'} m/s</span>
        </div>
        <div class="flex justify-between text-sm">
          <span class="text-secondary">Hubraumleistung</span>
          <span style="color:var(--color-accent-primary); font-weight:var(--font-semibold);">
            ${result.estimated_power_kw ? (result.estimated_power_kw * 1.36).toFixed(0) + ' PS' : '—'}
          </span>
        </div>
      `;
    } catch {
      document.getElementById('engine-results').innerHTML =
        `<span class="text-xs" style="color:var(--color-danger);">Berechnung fehlgeschlagen</span>`;
    }
  });

  // ── 2.8 Initialisierung ────────────────────────────────────────────────
  await _loadAllData();

  // ── 2.9 Cleanup ────────────────────────────────────────────────────────
  return () => {};
}

// ═══════════════════════════════════════════════════════════════════════════════
// 3. HILFSFUNKTIONEN
// ═══════════════════════════════════════════════════════════════════════════════

function _escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}