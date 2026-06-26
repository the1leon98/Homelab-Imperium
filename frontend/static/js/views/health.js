// FILE: frontend/static/js/views/health.js
/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * Bio-Tracking — Interaktives 3D-Körper-Hologramm & Vital-Dashboard.
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Bietet:
 *   • Hochauflösendes SVG-Körpermodell (Hologramm-Ästhetik)
 *   • 51 anatomische Lokationen als interaktive SVG-Elemente
 *   • Pulsierende Rot/Orange/Gelb-Animation bei aktiven Symptomen
 *   • Vitaldaten-Karten: Ernährung, Gewicht, Training, Schlaf
 *   • Symptom-Tabelle mit Intensität & Lokation
 *   • Polling alle 30s für Hologramm-Updates
 *
 * Hologramm-Farbcodierung:
 *   high   → 🔴 Rot, pulsierend (akut)
 *   medium → 🟠 Orange, langsam pulsierend (subakut)
 *   low    → 🟡 Gelb, statisch (chronisch/abklingend)
 *
 * Verwendete API-Endpunkte:
 *   GET /api/health/hologram          — Aktive Symptome (3D-Modell)
 *   GET /api/health/nutrition         — Ernährungsstatus
 *   GET /api/health/weight            — Gewichts-Trend
 *   GET /api/health/exercise          — Trainingsstatus
 *   GET /api/health/sleep             — Schlafdaten
 *   GET /api/health/vitals            — Vitalwerte
 */

import { get } from '../api.js';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. KONSTANTEN — Anatomische Lokationen → SVG-Element-ID-Mapping
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Mapping: Hologramm-Lokation → SVG-Element-ID.
 * Jede der 51 Lokationen wird im SVG als <circle> oder <ellipse> dargestellt.
 */
const LOCATION_JOINT_MAP = {
  head:             'joint-head',
  temple_L:         'joint-temple_L',   temple_R:         'joint-temple_R',
  jaw_L:            'joint-jaw_L',      jaw_R:            'joint-jaw_R',
  neck:             'joint-neck',
  throat:           'joint-throat',
  chest:            'joint-chest',
  chest_L:          'joint-chest_L',    chest_R:          'joint-chest_R',
  abdomen:          'joint-abdomen',
  abdomen_UL:       'joint-abdomen_UL', abdomen_UR:       'joint-abdomen_UR',
  abdomen_LL:       'joint-abdomen_LL', abdomen_LR:       'joint-abdomen_LR',
  groin_L:          'joint-groin_L',    groin_R:          'joint-groin_R',
  upper_back:       'joint-upper_back',
  lower_back:       'joint-lower_back',
  spine_cervical:   'joint-spine_cervical',
  spine_thoracic:   'joint-spine_thoracic',
  spine_lumbar:     'joint-spine_lumbar',
  shoulder_L:       'joint-shoulder_L', shoulder_R:       'joint-shoulder_R',
  upper_arm_L:      'joint-upper_arm_L', upper_arm_R:     'joint-upper_arm_R',
  elbow_L:          'joint-elbow_L',    elbow_R:          'joint-elbow_R',
  forearm_L:        'joint-forearm_L',  forearm_R:        'joint-forearm_R',
  wrist_L:          'joint-wrist_L',    wrist_R:          'joint-wrist_R',
  hand_L:           'joint-hand_L',     hand_R:           'joint-hand_R',
  hip_L:            'joint-hip_L',      hip_R:            'joint-hip_R',
  thigh_L:          'joint-thigh_L',    thigh_R:          'joint-thigh_R',
  knee_L:           'joint-knee_L',     knee_R:           'joint-knee_R',
  shin_L:           'joint-shin_L',     shin_R:           'joint-shin_R',
  calf_L:           'joint-calf_L',     calf_R:           'joint-calf_R',
  ankle_L:          'joint-ankle_L',    ankle_R:          'joint-ankle_R',
  foot_L:           'joint-foot_L',     foot_R:           'joint-foot_R',
  systemic:         'joint-systemic',
};

/**
 * Intensität → Farb-Mapping.
 */
const INTENSITY_COLORS = {
  high:   { fill: 'var(--color-danger)',  glow: 'var(--color-danger-glow)',  pulse: 'status-pulse' },
  medium: { fill: 'var(--color-warning)', glow: 'var(--color-warning-glow)', pulse: 'status-pulse-slow' },
  low:    { fill: 'var(--color-accent-amber)', glow: 'var(--color-accent-amber-glow)', pulse: '' },
};

// ═══════════════════════════════════════════════════════════════════════════════
// 2. SVG-KÖRPERMODELL — HTML-Template
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Generiert das vollständige SVG-Körper-Hologramm.
 * 420×680px ViewBox, cyan-farbene Umrisse, 51 Joint-Marker.
 *
 * @returns {string} SVG-HTML
 */
function _createHologramSVG() {
  return `
  <svg id="human-hologram" viewBox="0 0 420 680"
       style="width:100%; max-width:420px; height:auto;"
       xmlns="http://www.w3.org/2000/svg">
    <defs>
      <!-- Neon-Glow-Filter -->
      <filter id="glow-cyan" x="-40%" y="-40%" width="180%" height="180%">
        <feGaussianBlur stdDeviation="3" result="blur" />
        <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
      <filter id="glow-red" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur stdDeviation="6" result="blur" />
        <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
      <!-- Pulsier-Animationen -->
      <style>
        @keyframes status-pulse {
          0%, 100% { opacity: 1; r: 5; }
          50% { opacity: 0.35; r: 7; }
        }
        @keyframes status-pulse-slow {
          0%, 100% { opacity: 0.9; r: 4.5; }
          50% { opacity: 0.4; r: 6; }
        }
        .pulse-fast { animation: status-pulse 1.2s ease-in-out infinite; }
        .pulse-slow { animation: status-pulse-slow 2.2s ease-in-out infinite; }
      </style>
    </defs>

    <!-- Hintergrund-Gitter (Hologramm-Raster) -->
    <g opacity="0.06">
      <line x1="0" y1="0" x2="420" y2="680" stroke="#38e1ff" stroke-width="0.5" />
      <line x1="420" y1="0" x2="0" y2="680" stroke="#38e1ff" stroke-width="0.5" />
      <line x1="210" y1="0" x2="210" y2="680" stroke="#38e1ff" stroke-width="0.3" />
      <line x1="0" y1="340" x2="420" y2="340" stroke="#38e1ff" stroke-width="0.3" />
      <circle cx="210" cy="340" r="200" fill="none" stroke="#38e1ff" stroke-width="0.3" />
    </g>

    <!-- ═══ KOPF ═══ -->
    <ellipse cx="210" cy="48" rx="32" ry="38" fill="none" stroke="#38e1ff"
             stroke-width="1.2" filter="url(#glow-cyan)" />
    <!-- Augen -->
    <ellipse cx="198" cy="38" rx="5" ry="3" fill="none" stroke="#38e1ff" stroke-width="0.7" />
    <ellipse cx="222" cy="38" rx="5" ry="3" fill="none" stroke="#38e1ff" stroke-width="0.7" />
    <!-- Kopf-Gelenkpunkt -->
    <circle id="joint-head" cx="210" cy="86" r="4" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1" />
    <!-- Schläfen -->
    <circle id="joint-temple_L" cx="188" cy="40" r="3" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />
    <circle id="joint-temple_R" cx="232" cy="40" r="3" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />
    <!-- Kiefer -->
    <circle id="joint-jaw_L" cx="195" cy="68" r="2.5" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />
    <circle id="joint-jaw_R" cx="225" cy="68" r="2.5" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />

    <!-- ═══ HALS ═══ -->
    <line x1="210" y1="86" x2="210" y2="110" stroke="#38e1ff" stroke-width="1.2"
          filter="url(#glow-cyan)" />
    <circle id="joint-neck" cx="210" cy="98" r="3.5" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-throat" cx="210" cy="108" r="2.5" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />

    <!-- ═══ SCHULTERN ═══ -->
    <line x1="140" y1="120" x2="280" y2="120" stroke="#38e1ff" stroke-width="1"
          filter="url(#glow-cyan)" />
    <circle id="joint-shoulder_L" cx="140" cy="120" r="5" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1.2" />
    <circle id="joint-shoulder_R" cx="280" cy="120" r="5" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1.2" />

    <!-- ═══ BRUSTKORB ═══ -->
    <path d="M 155 130 Q 210 150 265 130 L 265 220 Q 210 245 155 220 Z"
          fill="rgba(56,225,255,0.03)" stroke="#38e1ff" stroke-width="1"
          filter="url(#glow-cyan)" />
    <!-- Rippen -->
    <path d="M 162 155 Q 210 172 258 155" fill="none" stroke="rgba(56,225,255,0.15)" stroke-width="0.5" />
    <path d="M 158 180 Q 210 200 262 180" fill="none" stroke="rgba(56,225,255,0.15)" stroke-width="0.5" />
    <path d="M 155 205 Q 210 225 265 205" fill="none" stroke="rgba(56,225,255,0.15)" stroke-width="0.5" />

    <circle id="joint-chest" cx="210" cy="175" r="4" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-chest_L" cx="172" cy="165" r="3" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />
    <circle id="joint-chest_R" cx="248" cy="165" r="3" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />

    <!-- ═══ ARME ═══ -->
    <!-- Links -->
    <line x1="140" y1="120" x2="105" y2="190" stroke="#38e1ff" stroke-width="1.2"
          filter="url(#glow-cyan)" />
    <circle id="joint-upper_arm_L" cx="122" cy="155" r="3" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-elbow_L" cx="105" cy="190" r="4" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1" />
    <line x1="105" y1="190" x2="70" y2="260" stroke="#38e1ff" stroke-width="1"
          filter="url(#glow-cyan)" />
    <circle id="joint-forearm_L" cx="88" cy="225" r="3" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-wrist_L" cx="70" cy="260" r="3.5" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1" />
    <circle id="joint-hand_L" cx="60" cy="280" r="3" fill="rgba(56,225,255,0.35)"
            stroke="#38e1ff" stroke-width="0.8" />
    <!-- Hand-Silhouette -->
    <ellipse cx="58" cy="288" rx="10" ry="12" fill="rgba(56,225,255,0.03)"
             stroke="rgba(56,225,255,0.2)" stroke-width="0.6" />

    <!-- Rechts -->
    <line x1="280" y1="120" x2="315" y2="190" stroke="#38e1ff" stroke-width="1.2"
          filter="url(#glow-cyan)" />
    <circle id="joint-upper_arm_R" cx="298" cy="155" r="3" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-elbow_R" cx="315" cy="190" r="4" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1" />
    <line x1="315" y1="190" x2="350" y2="260" stroke="#38e1ff" stroke-width="1"
          filter="url(#glow-cyan)" />
    <circle id="joint-forearm_R" cx="332" cy="225" r="3" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-wrist_R" cx="350" cy="260" r="3.5" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1" />
    <circle id="joint-hand_R" cx="360" cy="280" r="3" fill="rgba(56,225,255,0.35)"
            stroke="#38e1ff" stroke-width="0.8" />
    <ellipse cx="362" cy="288" rx="10" ry="12" fill="rgba(56,225,255,0.03)"
             stroke="rgba(56,225,255,0.2)" stroke-width="0.6" />

    <!-- ═══ WIRBELSÄULE ═══ -->
    <line x1="210" y1="110" x2="210" y2="380" stroke="rgba(56,225,255,0.3)"
          stroke-width="0.8" stroke-dasharray="4,4" />
    <circle id="joint-spine_cervical" cx="210" cy="125" r="3" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.7" />
    <circle id="joint-spine_thoracic" cx="210" cy="200" r="3" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.7" />
    <circle id="joint-spine_lumbar" cx="210" cy="280" r="3" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.7" />

    <!-- ═══ BAUCH / ABDOMEN ═══ -->
    <ellipse cx="210" cy="270" rx="48" ry="42" fill="rgba(56,225,255,0.02)"
             stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-abdomen" cx="210" cy="270" r="4.5" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.9" />
    <circle id="joint-abdomen_UL" cx="185" cy="252" r="3" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />
    <circle id="joint-abdomen_UR" cx="235" cy="252" r="3" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />
    <circle id="joint-abdomen_LL" cx="185" cy="292" r="3" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />
    <circle id="joint-abdomen_LR" cx="235" cy="292" r="3" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />

    <!-- ═══ RÜCKEN ═══ -->
    <ellipse cx="210" cy="220" rx="38" ry="50" fill="none" stroke="rgba(56,225,255,0.12)"
             stroke-width="0.6" stroke-dasharray="3,6" />
    <circle id="joint-upper_back" cx="210" cy="190" r="3" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />
    <circle id="joint-lower_back" cx="210" cy="310" r="3.5" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />

    <!-- ═══ HÜFTE / BECKEN ═══ -->
    <path d="M 162 320 Q 210 340 258 320" fill="none" stroke="#38e1ff"
          stroke-width="1.2" filter="url(#glow-cyan)" />
    <circle id="joint-hip_L" cx="160" cy="325" r="5" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1.2" />
    <circle id="joint-hip_R" cx="260" cy="325" r="5" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1.2" />
    <!-- Leiste -->
    <circle id="joint-groin_L" cx="190" cy="335" r="3" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />
    <circle id="joint-groin_R" cx="230" cy="335" r="3" fill="rgba(56,225,255,0.25)"
            stroke="#38e1ff" stroke-width="0.7" />

    <!-- ═══ BEINE ═══ -->
    <!-- Links -->
    <line x1="160" y1="325" x2="145" y2="460" stroke="#38e1ff" stroke-width="1.2"
          filter="url(#glow-cyan)" />
    <circle id="joint-thigh_L" cx="152" cy="390" r="3.5" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-knee_L" cx="145" cy="460" r="5" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1.2" />
    <line x1="145" y1="460" x2="130" y2="560" stroke="#38e1ff" stroke-width="1"
          filter="url(#glow-cyan)" />
    <circle id="joint-shin_L" cx="137" cy="510" r="3" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-calf_L" cx="137" cy="535" r="3" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-ankle_L" cx="130" cy="560" r="4" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1" />
    <circle id="joint-foot_L" cx="118" cy="578" r="3" fill="rgba(56,225,255,0.35)"
            stroke="#38e1ff" stroke-width="0.8" />
    <!-- Fuß-Silhouette -->
    <ellipse cx="115" cy="582" rx="16" ry="8" fill="rgba(56,225,255,0.02)"
             stroke="rgba(56,225,255,0.18)" stroke-width="0.6" />

    <!-- Rechts -->
    <line x1="260" y1="325" x2="275" y2="460" stroke="#38e1ff" stroke-width="1.2"
          filter="url(#glow-cyan)" />
    <circle id="joint-thigh_R" cx="268" cy="390" r="3.5" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-knee_R" cx="275" cy="460" r="5" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1.2" />
    <line x1="275" y1="460" x2="290" y2="560" stroke="#38e1ff" stroke-width="1"
          filter="url(#glow-cyan)" />
    <circle id="joint-shin_R" cx="283" cy="510" r="3" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-calf_R" cx="283" cy="535" r="3" fill="rgba(56,225,255,0.3)"
            stroke="#38e1ff" stroke-width="0.8" />
    <circle id="joint-ankle_R" cx="290" cy="560" r="4" fill="rgba(56,225,255,0.4)"
            stroke="#38e1ff" stroke-width="1" />
    <circle id="joint-foot_R" cx="302" cy="578" r="3" fill="rgba(56,225,255,0.35)"
            stroke="#38e1ff" stroke-width="0.8" />
    <ellipse cx="305" cy="582" rx="16" ry="8" fill="rgba(56,225,255,0.02)"
             stroke="rgba(56,225,255,0.18)" stroke-width="0.6" />

    <!-- ═══ SYSTEMIC ═══ -->
    <circle id="joint-systemic" cx="210" cy="620" r="5" fill="rgba(56,225,255,0.15)"
            stroke="#38e1ff" stroke-width="0.7" stroke-dasharray="3,3" />

    <!-- Legende unten -->
    <g transform="translate(10, 650)">
      <circle cx="6" cy="0" r="5" fill="var(--color-danger)" opacity="0.85">
        <animate attributeName="opacity" values="0.85;0.3;0.85" dur="1.2s" repeatCount="indefinite" />
      </circle>
      <text x="16" y="4" fill="var(--color-text-tertiary)" font-size="9" font-family="var(--font-sans)">Akut</text>
      <circle cx="60" cy="0" r="5" fill="var(--color-warning)" opacity="0.85">
        <animate attributeName="opacity" values="0.85;0.4;0.85" dur="2.2s" repeatCount="indefinite" />
      </circle>
      <text x="70" y="4" fill="var(--color-text-tertiary)" font-size="9" font-family="var(--font-sans)">Subakut</text>
      <circle cx="130" cy="0" r="5" fill="var(--color-accent-amber)" opacity="0.7" />
      <text x="140" y="4" fill="var(--color-text-tertiary)" font-size="9" font-family="var(--font-sans)">Chronisch</text>
    </g>
  </svg>`;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 3. HAUPTFUNKTION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Rendert das Bio-Tracking-Dashboard mit 3D-Hologramm.
 *
 * @param {HTMLElement} container — Der #view-port-Container
 * @returns {Function} Cleanup-Funktion
 */
export async function showHealth(container) {
  // ── 3.1 Grundlayout ────────────────────────────────────────────────────
  container.innerHTML = `
    <div class="section-header">
      <div>
        <h1>Bio-Tracking</h1>
        <p class="section-subtitle">Holografische Körperanalyse & Vital-Monitoring</p>
      </div>
      <span class="text-xs text-tertiary" id="hologram-poll-indicator">● Live</span>
    </div>

    <div style="display:grid; grid-template-columns:1fr 360px; gap:var(--space-6);">

      <!-- Linke Spalte: Hologramm + Symptom-Tabelle -->
      <div style="display:flex; flex-direction:column; gap:var(--space-6);">
        <!-- Hologramm -->
        <div class="glass-card" style="display:flex; flex-direction:column; align-items:center; padding:var(--space-6);">
          <div class="card-header" style="width:100%;"><h3>🧬 3D-Körper-Hologramm</h3></div>
          <div id="hologram-container" style="margin:var(--space-4) 0;
                       background:radial-gradient(ellipse at center, rgba(56,225,255,0.04) 0%, transparent 70%);
                       border-radius:var(--radius-lg); padding:var(--space-4);">
            ${_createHologramSVG()}
          </div>
          <!-- Hover-Info-Tooltip -->
          <div id="hologram-tooltip"
               style="display:none; position:fixed; z-index:var(--z-tooltip);
                      background:var(--glass-strong-bg); border:var(--glass-strong-border);
                      border-radius:var(--radius-md); padding:var(--space-3) var(--space-4);
                      font-size:var(--text-xs); color:var(--color-text-primary);
                      backdrop-filter:var(--glass-strong-blur);
                      -webkit-backdrop-filter:var(--glass-strong-blur);
                      pointer-events:none; max-width:220px;">
          </div>
        </div>

        <!-- Aktive Symptome -->
        <div class="glass-card" style="flex:1;">
          <div class="card-header"><h3>📋 Aktive Symptome</h3></div>
          <div id="symptoms-list" style="max-height:300px; overflow-y:auto;">
            <div class="text-xs text-tertiary" style="text-align:center; padding:var(--space-6);">
              Keine aktiven Symptome — Hologramm ist klar.
            </div>
          </div>
        </div>
      </div>

      <!-- Rechte Spalte: Vital-Karten -->
      <div style="display:flex; flex-direction:column; gap:var(--space-6);">
        <!-- Gewicht -->
        <div class="glass-card subtle">
          <div class="card-header"><h3>⚖ Gewicht</h3></div>
          <div class="stat-widget" style="align-items:center;">
            <span class="stat-value" id="weight-value" style="font-size:var(--text-2xl);">— kg</span>
            <span class="stat-change" id="weight-trend" style="margin-top:var(--space-1);">—</span>
          </div>
          <div class="progress-bar" style="margin-top:var(--space-3);">
            <div class="progress-fill primary" id="weight-bar" style="width:0%;"></div>
          </div>
        </div>

        <!-- Ernährung -->
        <div class="glass-card subtle">
          <div class="card-header"><h3>🍎 Ernährung (heute)</h3></div>
          <div id="nutrition-info" style="display:flex; flex-direction:column; gap:var(--space-2);">
            <div class="flex justify-between text-sm">
              <span class="text-secondary">Kalorien</span>
              <span id="nut-calories" style="color:var(--color-text-primary);">— / — kcal</span>
            </div>
            <div class="flex justify-between text-sm">
              <span class="text-secondary">Protein</span>
              <span id="nut-protein" style="color:var(--color-accent-primary);">— g</span>
            </div>
            <div class="flex justify-between text-sm">
              <span class="text-secondary">Kohlenhydrate</span>
              <span id="nut-carbs" style="color:var(--color-accent-cyan);">— g</span>
            </div>
            <div class="flex justify-between text-sm">
              <span class="text-secondary">Fett</span>
              <span id="nut-fat" style="color:var(--color-warning);">— g</span>
            </div>
            <div class="flex justify-between text-sm">
              <span class="text-secondary">Wasser</span>
              <span id="nut-water" style="color:var(--color-accent-blue);">— / — ml</span>
            </div>
          </div>
        </div>

        <!-- Training -->
        <div class="glass-card subtle">
          <div class="card-header"><h3>🏋 Training (heute)</h3></div>
          <div class="flex justify-between text-sm">
            <span class="text-secondary">Workouts</span>
            <span id="ex-count" style="color:var(--color-text-primary);">—</span>
          </div>
          <div class="flex justify-between text-sm" style="margin-top:var(--space-1);">
            <span class="text-secondary">Dauer</span>
            <span id="ex-duration" style="color:var(--color-accent-cyan);">— min</span>
          </div>
          <div class="flex justify-between text-sm" style="margin-top:var(--space-1);">
            <span class="text-secondary">Kalorien verbrannt</span>
            <span id="ex-calories" style="color:var(--color-danger);">— kcal</span>
          </div>
        </div>

        <!-- Schlaf -->
        <div class="glass-card subtle">
          <div class="card-header"><h3>😴 Schlaf (letzte Nacht)</h3></div>
          <div class="stat-widget" style="align-items:center;">
            <span class="stat-value" id="sleep-duration" style="font-size:var(--text-2xl);">— h</span>
            <span class="stat-label" id="sleep-quality">—</span>
          </div>
        </div>

        <!-- Vitalwerte -->
        <div class="glass-card subtle">
          <div class="card-header"><h3>💓 Vitalwerte</h3></div>
          <div id="vitals-info" class="text-sm text-tertiary" style="text-align:center;">
            Keine aktuellen Daten
          </div>
        </div>
      </div>
    </div>
  `;

  // ── 3.2 Hologramm-Interaktivität ────────────────────────────────────────
  const hologramSvg = document.getElementById('human-hologram');
  const tooltip = document.getElementById('hologram-tooltip');

  // Hover-Tooltip für Joint-Marker
  if (hologramSvg) {
    hologramSvg.addEventListener('mouseover', (e) => {
      const circle = e.target.closest('circle[id^="joint-"]');
      if (!circle) { tooltip.style.display = 'none'; return; }
      const locationName = circle.id.replace('joint-', '');
      const anomaly = _currentAnomalies?.find(a => a.location === locationName);
      tooltip.innerHTML = anomaly
        ? `<strong>${_formatLocation(locationName)}</strong><br>
           <span style="color:${INTENSITY_COLORS[anomaly.intensity]?.fill || '#fff'};">${_intensityLabel(anomaly.intensity)}</span>
           — ${_escapeHtml(anomaly.cause)}`
        : `<strong>${_formatLocation(locationName)}</strong><br><span style="color:var(--color-text-tertiary);">Keine Auffälligkeiten</span>`;
      tooltip.style.display = 'block';
      tooltip.style.left = (e.clientX + 14) + 'px';
      tooltip.style.top = (e.clientY - 10) + 'px';
    });

    hologramSvg.addEventListener('mousemove', (e) => {
      if (tooltip.style.display === 'block') {
        tooltip.style.left = (e.clientX + 14) + 'px';
        tooltip.style.top = (e.clientY - 10) + 'px';
      }
    });

    hologramSvg.addEventListener('mouseout', () => {
      tooltip.style.display = 'none';
    });
  }

  // ── 3.3 Daten laden ────────────────────────────────────────────────────

  let _currentAnomalies = [];
  let _pollInterval = null;

  async function _loadAllData() {
    try {
      const [hologram, nutrition, weight, exercise, sleep, vitals] = await Promise.all([
        get('/health/hologram', {}, { skipToast: true, skipLoader: true }),
        get('/health/nutrition', {}, { skipToast: true, skipLoader: true }),
        get('/health/weight', { days: 30 }, { skipToast: true, skipLoader: true }),
        get('/health/exercise', {}, { skipToast: true, skipLoader: true }),
        get('/health/sleep', {}, { skipToast: true, skipLoader: true }),
        get('/health/vitals', {}, { skipToast: true, skipLoader: true })
      ]);

      _currentAnomalies = hologram.anomalies || [];
      _updateHologram(_currentAnomalies);
      _updateSymptomsList(_currentAnomalies);
      _updateNutrition(nutrition);
      _updateWeight(weight);
      _updateExercise(exercise);
      _updateSleep(sleep);
      _updateVitals(vitals);

      // Poll-Indikator kurz grün blinken
      const indicator = document.getElementById('hologram-poll-indicator');
      if (indicator) {
        indicator.style.color = 'var(--color-success)';
        setTimeout(() => { indicator.style.color = 'var(--color-text-tertiary)'; }, 600);
      }
    } catch {
      const indicator = document.getElementById('hologram-poll-indicator');
      if (indicator) {
        indicator.style.color = 'var(--color-danger)';
        indicator.textContent = '⚠ Verbindungsfehler';
        setTimeout(() => {
          indicator.textContent = '● Live';
          indicator.style.color = 'var(--color-text-tertiary)';
        }, 3000);
      }
    }
  }

  // ── 3.4 Hologramm-Updater ──────────────────────────────────────────────

  function _updateHologram(anomalies) {
    // Alle Joints zurücksetzen
    Object.values(LOCATION_JOINT_MAP).forEach(jointId => {
      const el = document.getElementById(jointId);
      if (!el) return;
      el.setAttribute('fill', 'rgba(56,225,255,0.25)');
      el.setAttribute('stroke', '#38e1ff');
      el.setAttribute('r', el.id.includes('shoulder') || el.id.includes('hip') ||
                              el.id.includes('knee') || el.id.includes('elbow') ? '5' :
                              el.id.includes('head') || el.id.includes('chest') ||
                              el.id.includes('abdomen') && !el.id.includes('_') ? '4.5' : '3');
      el.classList.remove('pulse-fast', 'pulse-slow');
      el.style.filter = '';
    });

    // Anomalien einzeichnen
    anomalies.forEach(a => {
      const jointId = LOCATION_JOINT_MAP[a.location];
      if (!jointId) return;
      const el = document.getElementById(jointId);
      if (!el) return;

      const color = INTENSITY_COLORS[a.intensity] || INTENSITY_COLORS.medium;
      el.setAttribute('fill', color.fill);
      el.setAttribute('stroke', color.fill);
      el.setAttribute('r', '7');
      el.style.filter = `drop-shadow(0 0 10px ${color.glow})`;

      if (color.pulse) {
        el.classList.add(a.intensity === 'high' ? 'pulse-fast' : 'pulse-slow');
      }
    });
  }

  // ── 3.5 Symptom-Tabelle ────────────────────────────────────────────────

  function _updateSymptomsList(anomalies) {
    const listEl = document.getElementById('symptoms-list');
    if (!anomalies.length) {
      listEl.innerHTML = `<div class="text-xs text-tertiary" style="text-align:center; padding:var(--space-6);">
        Keine aktiven Symptome — Hologramm ist klar.</div>`;
      return;
    }

    listEl.innerHTML = anomalies.map(a => {
      const color = INTENSITY_COLORS[a.intensity]?.fill || 'var(--color-text-primary)';
      return `
        <div style="display:flex; align-items:center; gap:var(--space-3);
                    padding:var(--space-3) var(--space-4);
                    border-bottom:1px solid var(--color-border-subtle);">
          <span class="status-dot ${a.intensity === 'high' ? 'critical' : a.intensity === 'medium' ? 'degraded' : ''}"
                style="flex-shrink:0;"></span>
          <div style="flex:1; min-width:0;">
            <div class="text-xs" style="color:var(--color-text-primary); font-weight:var(--font-medium);">
              ${_formatLocation(a.location)}
            </div>
            <div class="text-xs" style="color:var(--color-text-tertiary); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
              ${_escapeHtml(a.cause)}
            </div>
          </div>
          <span class="text-xs" style="color:${color}; font-weight:var(--font-semibold); white-space:nowrap;">
            ${_intensityLabel(a.intensity)}
          </span>
        </div>`;
    }).join('');
  }

  // ── 3.6 Vital-Karten-Updater ───────────────────────────────────────────

  function _updateNutrition(data) {
    if (!data) return;
    document.getElementById('nut-calories').textContent =
      `${data.calories_consumed ?? '—'} / ${data.calories_target ?? '—'} kcal`;
    document.getElementById('nut-protein').textContent = `${data.protein_grams ?? '—'} g`;
    document.getElementById('nut-carbs').textContent = `${data.carbs_grams ?? '—'} g`;
    document.getElementById('nut-fat').textContent = `${data.fat_grams ?? '—'} g`;
    document.getElementById('nut-water').textContent =
      `${data.water_ml ?? '—'} / ${data.water_target_ml ?? '—'} ml`;
  }

  function _updateWeight(data) {
    if (!data) return;
    const current = data.current_weight_kg;
    document.getElementById('weight-value').textContent =
      current != null ? `${Number(current).toFixed(1)} kg` : '— kg';

    const trend7 = data.trend_7d_kg;
    const trendEl = document.getElementById('weight-trend');
    if (trend7 != null && trend7 !== 0) {
      const sign = trend7 > 0 ? '+' : '';
      trendEl.textContent = `${sign}${Number(trend7).toFixed(1)} kg / Woche`;
      trendEl.className = `stat-change ${trend7 > 0 ? 'negative' : 'positive'}`;
    } else {
      trendEl.textContent = 'stabil';
      trendEl.className = 'stat-change';
      trendEl.style.color = 'var(--color-text-tertiary)';
    }

    // Fortschrittsbalken: angenommenes Zielgewicht 80kg, aktuelles Gewicht
    if (current != null) {
      const target = 80;
      const pct = Math.min(100, Math.max(0, (current / target) * 100));
      document.getElementById('weight-bar').style.width = `${pct}%`;
    }
  }

  function _updateExercise(data) {
    if (!data) return;
    document.getElementById('ex-count').textContent = data.workout_count ?? '0';
    document.getElementById('ex-duration').textContent = `${data.total_duration_min ?? 0} min`;
    document.getElementById('ex-calories').textContent = `${data.calories_burned ?? 0} kcal`;
  }

  function _updateSleep(data) {
    if (!data) return;
    document.getElementById('sleep-duration').textContent =
      data.duration_hours != null ? `${Number(data.duration_hours).toFixed(1)} h` : '— h';
    const quality = data.quality_score;
    const qEl = document.getElementById('sleep-quality');
    if (quality != null) {
      qEl.textContent = quality >= 80 ? 'Erholsam' : quality >= 50 ? 'Mittel' : 'Schlecht';
      qEl.style.color = quality >= 80 ? 'var(--color-success)' :
                        quality >= 50 ? 'var(--color-warning)' : 'var(--color-danger)';
    } else {
      qEl.textContent = '—';
      qEl.style.color = 'var(--color-text-tertiary)';
    }
  }

  function _updateVitals(data) {
    if (!data || !data.entries || !data.entries.length) {
      document.getElementById('vitals-info').innerHTML =
        '<span class="text-tertiary">Keine aktuellen Daten</span>';
      return;
    }
    const v = data.entries[0];
    document.getElementById('vitals-info').innerHTML = `
      <div class="flex justify-between text-sm">
        <span class="text-secondary">Herzfrequenz</span>
        <span style="color:var(--color-text-primary);">${v.val1 ?? '—'} bpm</span>
      </div>
      <div class="flex justify-between text-sm" style="margin-top:var(--space-1);">
        <span class="text-secondary">Blutdruck</span>
        <span style="color:var(--color-text-primary);">${v.val2 ?? '—'} / ${v.val3 ?? '—'} mmHg</span>
      </div>
    `;
  }

  // ── 3.7 Initialisierung & Polling ──────────────────────────────────────
  await _loadAllData();
  _pollInterval = setInterval(_loadAllData, 30_000);

  // ── 3.8 Cleanup ────────────────────────────────────────────────────────
  return () => {
    if (_pollInterval) clearInterval(_pollInterval);
  };
}

// ═══════════════════════════════════════════════════════════════════════════════
// 4. HILFSFUNKTIONEN
// ═══════════════════════════════════════════════════════════════════════════════

/** Anatomische Lokation → lesbarer deutscher Name */
function _formatLocation(loc) {
  const MAP = {
    head: 'Kopf', temple_L: 'Linke Schläfe', temple_R: 'Rechte Schläfe',
    jaw_L: 'Linker Kiefer', jaw_R: 'Rechter Kiefer', neck: 'Nacken',
    throat: 'Hals', chest: 'Brustkorb', chest_L: 'Linke Brust', chest_R: 'Rechte Brust',
    abdomen: 'Bauch', abdomen_UL: 'Oberbauch links', abdomen_UR: 'Oberbauch rechts',
    abdomen_LL: 'Unterbauch links', abdomen_LR: 'Unterbauch rechts',
    groin_L: 'Linke Leiste', groin_R: 'Rechte Leiste',
    upper_back: 'Oberer Rücken', lower_back: 'Unterer Rücken',
    spine_cervical: 'HWS', spine_thoracic: 'BWS', spine_lumbar: 'LWS',
    shoulder_L: 'Linke Schulter', shoulder_R: 'Rechte Schulter',
    upper_arm_L: 'Linker Oberarm', upper_arm_R: 'Rechter Oberarm',
    elbow_L: 'Linker Ellbogen', elbow_R: 'Rechter Ellbogen',
    forearm_L: 'Linker Unterarm', forearm_R: 'Rechter Unterarm',
    wrist_L: 'Linkes Handgelenk', wrist_R: 'Rechtes Handgelenk',
    hand_L: 'Linke Hand', hand_R: 'Rechte Hand',
    hip_L: 'Linke Hüfte', hip_R: 'Rechte Hüfte',
    thigh_L: 'Linker Oberschenkel', thigh_R: 'Rechter Oberschenkel',
    knee_L: 'Linkes Knie', knee_R: 'Rechtes Knie',
    shin_L: 'Linkes Schienbein', shin_R: 'Rechtes Schienbein',
    calf_L: 'Linke Wade', calf_R: 'Rechte Wade',
    ankle_L: 'Linker Knöchel', ankle_R: 'Rechter Knöchel',
    foot_L: 'Linker Fuß', foot_R: 'Rechter Fuß',
    systemic: 'Systemisch',
  };
  return MAP[loc] || loc.replace(/_/g, ' ');
}

/** Intensität → Label */
function _intensityLabel(intensity) {
  return intensity === 'high' ? 'Akut' : intensity === 'medium' ? 'Subakut' : 'Chronisch';
}

function _escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}