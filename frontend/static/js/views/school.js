// FILE: frontend/static/js/views/school.js
/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * Ausbildungs-Portal — Noten, Fächer, Agenda & RAG-Skript-Upload.
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Bietet:
 *   • GPA-Übersichtskarten (Gesamt, Prüfungsfächer, Trend-Indikator)
 *   • Fächerliste mit Einzel-GPA, Notenverteilung & Farbcodierung
 *   • Interaktive Notenerfassung pro Fach
 *   • Agenda/Deadline-Liste mit Überfällig-Indikator
 *   • Semester-GPA-Trend als Mini-Chart
 *   • PDF-Upload mit RAG-Indizierungs-Feedback
 *
 * Verwendete API-Endpunkte:
 *   GET    /api/school/dashboard         — Kombi-Übersicht
 *   GET    /api/school/subjects          — Alle Fächer
 *   POST   /api/school/subjects          — Fach erstellen
 *   POST   /api/school/grades            — Note erfassen
 *   GET    /api/school/gpa/trend         — GPA-Verlauf
 *   GET    /api/school/deadlines         — Terminliste
 *   POST   /api/school/deadlines         — Termin erstellen
 *   POST   /api/school/documents/ingest  — PDF → RAG
 */

import { get, post, del } from '../api.js';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. KONSTANTEN
// ═══════════════════════════════════════════════════════════════════════════════

/** Deutsche Notenstufen mit Farbcodierung (1,0 = sehr gut → 6,0 = ungenügend) */
const GRADE_COLORS = {
  1.0: 'var(--color-success)',
  1.5: 'var(--color-success)',
  2.0: 'var(--color-accent-cyan)',
  2.5: 'var(--color-accent-cyan)',
  3.0: 'var(--color-accent-blue)',
  3.5: 'var(--color-warning)',
  4.0: 'var(--color-warning)',
  4.5: 'var(--color-danger)',
  5.0: 'var(--color-danger)',
  5.5: 'var(--color-danger)',
  6.0: 'var(--color-danger)',
};

/** Note → Farbe */
function gradeColor(value) {
  if (value == null) return 'var(--color-text-tertiary)';
  const rounded = Math.round(value * 2) / 2; // Auf 0.5 runden
  return GRADE_COLORS[Math.min(6, Math.max(1, rounded))] || 'var(--color-text-tertiary)';
}

/** Note → Label */
function gradeLabel(value) {
  if (value == null) return '—';
  const map = { 1.0: 'Sehr gut', 1.5: 'Sehr gut+', 2.0: 'Gut', 2.5: 'Gut+',
                3.0: 'Befriedigend', 3.5: 'Befriedigend+', 4.0: 'Ausreichend',
                4.5: 'Ausreichend+', 5.0: 'Mangelhaft', 5.5: 'Mangelhaft+',
                6.0: 'Ungenügend' };
  const rounded = Math.round(value * 2) / 2;
  return map[Math.min(6, Math.max(1, rounded))] || value.toFixed(1);
}

/** Deutsche Notenskala für Select */
const GERMAN_GRADES = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0];

// ═══════════════════════════════════════════════════════════════════════════════
// 2. HAUPTFUNKTION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Rendert das Ausbildungs-Portal.
 *
 * @param {HTMLElement} container
 * @returns {Function} Cleanup
 */
export async function showSchool(container) {
  // ── 2.1 Grundlayout ────────────────────────────────────────────────────
  container.innerHTML = `
    <div class="section-header">
      <div>
        <h1>Ausbildungs-Portal</h1>
        <p class="section-subtitle">Fachinformatiker AE — Noten, Fristen & Skripte</p>
      </div>
      <button class="btn btn-primary" id="btn-add-subject">📘 Neues Fach</button>
    </div>

    <!-- GPA-Übersichtskarten -->
    <div class="dashboard-grid cols-3" style="margin-bottom:var(--space-6);" id="gpa-cards">
      <div class="glass-card stat-widget" style="align-items:center; text-align:center;">
        <span class="stat-label">Gesamt-GPA</span>
        <span class="stat-value" id="gpa-overall" style="font-size:var(--text-3xl);">—</span>
      </div>
      <div class="glass-card stat-widget" style="align-items:center; text-align:center;">
        <span class="stat-label">Prüfungsfächer-GPA</span>
        <span class="stat-value" id="gpa-exam" style="font-size:var(--text-3xl);">—</span>
      </div>
      <div class="glass-card stat-widget" style="align-items:center; text-align:center;">
        <span class="stat-label">Trend</span>
        <span class="stat-value" id="gpa-trend" style="font-size:var(--text-2xl);">—</span>
      </div>
    </div>

    <div style="display:grid; grid-template-columns:1fr 380px; gap:var(--space-6);">

      <!-- Linke Spalte: Fächer & Noten -->
      <div style="display:flex; flex-direction:column; gap:var(--space-6);">

        <!-- Fächerliste -->
        <div id="subjects-container">
          <div class="glass-card"><div class="skeleton skeleton-card"></div></div>
        </div>

        <!-- GPA-Trend-Chart -->
        <div class="glass-card">
          <div class="card-header"><h3>📈 Semester-GPA-Verlauf</h3></div>
          <div id="gpa-trend-chart" style="height:200px; display:flex; align-items:flex-end; gap:var(--space-2); padding:var(--space-4) 0;">
            <span class="text-xs text-tertiary" style="width:100%; text-align:center;">Lade Trenddaten…</span>
          </div>
        </div>
      </div>

      <!-- Rechte Spalte: Agenda + PDF-Upload -->
      <div style="display:flex; flex-direction:column; gap:var(--space-6);">

        <!-- Agenda / Deadlines -->
        <div class="glass-card">
          <div class="card-header">
            <h3>📅 Agenda</h3>
            <button class="btn btn-ghost btn-icon" id="btn-add-deadline" title="Neuer Termin">+</button>
          </div>
          <div id="deadlines-list" style="max-height:350px; overflow-y:auto;">
            <span class="text-xs text-tertiary" style="display:block; text-align:center; padding:var(--space-6);">Keine anstehenden Termine</span>
          </div>
          <div id="deadline-stats" class="text-xs text-tertiary" style="padding:var(--space-3) var(--space-4); border-top:1px solid var(--color-border-subtle);"></div>
        </div>

        <!-- PDF-Upload (RAG) -->
        <div class="glass-card">
          <div class="card-header"><h3>📄 Skript-Upload (RAG)</h3></div>
          <div id="pdf-upload-area"
               style="border:2px dashed var(--color-glass-border); border-radius:var(--radius-lg);
                      padding:var(--space-6); text-align:center; cursor:pointer;
                      transition:border-color 0.2s ease, background 0.2s ease;">
            <span style="font-size:2rem;" aria-hidden="true">📤</span>
            <div style="margin-top:var(--space-2); color:var(--color-text-secondary); font-weight:var(--font-medium);">
              PDF-Skript hierher ziehen
            </div>
            <div class="text-xs" style="color:var(--color-text-tertiary); margin-top:var(--space-1);">
              oder klicken zum Auswählen
            </div>
            <input type="file" id="pdf-file-input" accept=".pdf" style="display:none;">
          </div>
          <!-- Upload-Status -->
          <div id="pdf-upload-status" style="margin-top:var(--space-3);"></div>
          <!-- Indizierte Dokumente -->
          <div id="pdf-documents-list" style="margin-top:var(--space-4);"></div>
        </div>
      </div>
    </div>

    <!-- Modal: Neue Note -->
    <div id="grade-modal" class="modal-overlay" style="display:none;">
      <div class="modal-dialog">
        <div class="modal-header">
          <h3>Note erfassen</h3>
          <button class="btn-icon btn-ghost" id="btn-close-grade-modal">✕</button>
        </div>
        <div class="modal-body">
          <input type="hidden" id="grade-subject-id">
          <div class="form-group">
            <label class="form-label">Fach</label>
            <span id="grade-subject-name" class="text-sm" style="color:var(--color-text-primary);"></span>
          </div>
          <div class="form-group">
            <label class="form-label">Note</label>
            <select id="grade-value" class="form-select">
              ${GERMAN_GRADES.map(g => `<option value="${g}">${g.toFixed(1)} — ${gradeLabel(g)}</option>`).join('')}
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Typ</label>
            <select id="grade-type" class="form-select">
              <option value="klassenarbeit">Klassenarbeit (2×)</option>
              <option value="klausur">Klausur (2×)</option>
              <option value="test" selected>Test (1×)</option>
              <option value="mündlich">Mündlich (1×)</option>
              <option value="projekt">Projekt (1.5×)</option>
              <option value="referat">Referat (1×)</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Beschreibung</label>
            <input type="text" id="grade-description" class="form-input" placeholder="z. B. Netzwerktechnik Kapitel 3">
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-ghost" id="btn-cancel-grade">Abbrechen</button>
          <button class="btn btn-primary" id="btn-save-grade">Speichern</button>
        </div>
      </div>
    </div>

    <!-- Modal: Neues Fach -->
    <div id="subject-modal" class="modal-overlay" style="display:none;">
      <div class="modal-dialog">
        <div class="modal-header">
          <h3>Neues Fach</h3>
          <button class="btn-icon btn-ghost" id="btn-close-subject-modal">✕</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label">Fachname</label>
            <input type="text" id="subject-name" class="form-input" placeholder="z. B. IT-Systeme">
          </div>
          <div class="form-group">
            <label class="form-label">Lehrkraft</label>
            <input type="text" id="subject-teacher" class="form-input" placeholder="z. B. Hr. Müller">
          </div>
          <div class="form-group">
            <label class="form-label">Raum</label>
            <input type="text" id="subject-room" class="form-input" placeholder="z. B. A204">
          </div>
          <div class="form-check">
            <input type="checkbox" id="subject-exam">
            <label for="subject-exam" class="text-sm" style="color:var(--color-text-secondary);">Prüfungsfach</label>
          </div>
          <div class="form-group" style="margin-top:var(--space-3);">
            <label class="form-label">Farbe</label>
            <input type="color" id="subject-color" value="#a06bff" style="width:60px; height:32px; border:none; border-radius:var(--radius-sm); cursor:pointer;">
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-ghost" id="btn-cancel-subject">Abbrechen</button>
          <button class="btn btn-primary" id="btn-save-subject">Fach erstellen</button>
        </div>
      </div>
    </div>

    <!-- Modal: Neuer Termin -->
    <div id="deadline-modal" class="modal-overlay" style="display:none;">
      <div class="modal-dialog">
        <div class="modal-header">
          <h3>Neuer Termin</h3>
          <button class="btn-icon btn-ghost" id="btn-close-deadline-modal">✕</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label">Titel</label>
            <input type="text" id="deadline-title" class="form-input" placeholder="z. B. Klausur Wirtschaftskunde">
          </div>
          <div class="form-group">
            <label class="form-label">Fach</label>
            <select id="deadline-subject" class="form-select"><option value="">Kein Fach</option></select>
          </div>
          <div class="form-group">
            <label class="form-label">Fällig am</label>
            <input type="date" id="deadline-date" class="form-input">
          </div>
          <div class="form-group">
            <label class="form-label">Priorität</label>
            <select id="deadline-priority" class="form-select">
              <option value="high">Hoch</option>
              <option value="medium" selected>Mittel</option>
              <option value="low">Niedrig</option>
            </select>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-ghost" id="btn-cancel-deadline">Abbrechen</button>
          <button class="btn btn-primary" id="btn-save-deadline">Speichern</button>
        </div>
      </div>
    </div>
  `;

  // ── 2.2 Zustand ────────────────────────────────────────────────────────
  let subjects = [];

  // ── 2.3 Daten laden ────────────────────────────────────────────────────

  async function _loadDashboard() {
    try {
      const [dash, trend] = await Promise.all([
        get('/school/dashboard', {}, { skipToast: true }),
        get('/school/gpa/trend', {}, { skipToast: true })
      ]);
      _renderGPACards(dash.gpa);
      _renderSubjects(dash.subjects);
      _renderTrendChart(trend);
      _renderDeadlines(dash.deadlines);
      subjects = dash.subjects || [];
      _populateDeadlineSubjects();
    } catch (err) {
      console.error('Schul-Dashboard-Fehler:', err);
    }
  }

  // ── 2.4 GPA-Karten ────────────────────────────────────────────────────

  function _renderGPACards(gpa) {
    if (!gpa) return;
    const overall = gpa.overall;
    const exam = gpa.exam_subjects;
    const trend = gpa.trend;

    const overallEl = document.getElementById('gpa-overall');
    const examEl = document.getElementById('gpa-exam');
    const trendEl = document.getElementById('gpa-trend');

    if (overall != null) {
      overallEl.textContent = Number(overall).toFixed(1);
      overallEl.style.color = gradeColor(Number(overall));
    }
    if (exam != null) {
      examEl.textContent = Number(exam).toFixed(1);
      examEl.style.color = gradeColor(Number(exam));
    }
    if (trendEl) {
      trendEl.textContent = trend === 'improving' ? '↗ Besser' :
                             trend === 'declining' ? '↘ Schlechter' : '→ Stabil';
      trendEl.style.color = trend === 'improving' ? 'var(--color-success)' :
                             trend === 'declining' ? 'var(--color-danger)' : 'var(--color-text-secondary)';
    }
  }

  // ── 2.5 Fächerliste ───────────────────────────────────────────────────

  function _renderSubjects(subjectList) {
    const container = document.getElementById('subjects-container');
    if (!subjectList || !subjectList.length) {
      container.innerHTML = `<div class="glass-card empty-state"><div class="empty-icon">📘</div>
        <h3>Keine Fächer</h3><p>Erstelle dein erstes Fach.</p></div>`;
      return;
    }

    container.innerHTML = subjectList.map(s => {
      const gpa = s.gpa != null ? Number(s.gpa).toFixed(1) : '—';
      const color = s.gpa != null ? gradeColor(Number(s.gpa)) : 'var(--color-text-tertiary)';
      const gradeCount = s.grade_count || 0;
      const examBadge = s.is_exam_subject ? ' <span style="background:var(--color-accent-primary-soft); color:var(--color-accent-primary); padding:1px 6px; border-radius:var(--radius-xs); font-size:10px;">AP</span>' : '';
      const hex = s.color_hex || '#a06bff';

      // Noten-Balken generieren
      let gradeDots = '';
      if (s.recent_grades && s.recent_grades.length) {
        gradeDots = s.recent_grades.slice(-5).map(g => {
          const gColor = gradeColor(Number(g.value));
          return `<span style="display:inline-block; width:8px; height:8px; border-radius:50%;
                        background:${gColor}; margin-right:3px; box-shadow:0 0 4px ${gColor};"
                       title="${Number(g.value).toFixed(1)} — ${g.grade_type || 'Note'}"></span>`;
        }).join('');
      }

      return `
        <div class="glass-card subtle subject-card" data-id="${s.id}"
             style="margin-bottom:var(--space-3); cursor:pointer; transition:border-color 0.15s ease;">
          <div style="display:flex; align-items:center; gap:var(--space-4);">
            <!-- Farb-Indikator -->
            <div style="width:4px; height:48px; border-radius:2px; background:${hex}; flex-shrink:0;"></div>
            <!-- Info -->
            <div style="flex:1; min-width:0;">
              <div style="display:flex; align-items:center; gap:var(--space-2);">
                <span class="text-sm" style="color:var(--color-text-primary); font-weight:var(--font-semibold);">
                  ${_escapeHtml(s.name)}
                </span>
                ${examBadge}
              </div>
              <div class="text-xs" style="color:var(--color-text-tertiary); margin-top:2px;">
                ${s.teacher ? _escapeHtml(s.teacher) + ' · ' : ''}${gradeCount} Noten
                ${gradeDots ? `<span style="margin-left:var(--space-2);">${gradeDots}</span>` : ''}
              </div>
            </div>
            <!-- GPA -->
            <div style="text-align:right; flex-shrink:0;">
              <div style="font-size:var(--text-2xl); font-weight:var(--font-bold); color:${color};">${gpa}</div>
            </div>
            <!-- Note-hinzufügen-Button -->
            <button class="btn-icon btn-ghost add-grade-btn" data-subject-id="${s.id}"
                    data-subject-name="${_escapeAttr(s.name)}"
                    title="Note hinzufügen" style="flex-shrink:0;">+</button>
          </div>
        </div>`;
    }).join('');

    // Event-Listener: Note hinzufügen
    container.querySelectorAll('.add-grade-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        _openGradeModal(btn.dataset.subjectId, btn.dataset.subjectName);
      });
    });

    // Event-Listener: Fach-Details (später erweiterbar)
    container.querySelectorAll('.subject-card').forEach(card => {
      card.addEventListener('click', () => {
        // Für jetzt: Note-Modal öffnen
        const btn = card.querySelector('.add-grade-btn');
        if (btn) _openGradeModal(btn.dataset.subjectId, btn.dataset.subjectName);
      });
    });
  }

  // ── 2.6 GPA-Trend-Chart (Mini-Balken) ──────────────────────────────────

  function _renderTrendChart(trend) {
    const chartEl = document.getElementById('gpa-trend-chart');
    if (!trend || !trend.points || !trend.points.length) {
      chartEl.innerHTML = `<span class="text-xs text-tertiary" style="width:100%; text-align:center;">Keine Trenddaten</span>`;
      return;
    }

    const points = trend.points.slice(-8);
    const maxGPA = 6.0;
    const minGPA = 1.0;
    const range = maxGPA - minGPA;

    chartEl.innerHTML = points.map(p => {
      const gpa = Number(p.gpa) || 0;
      const heightPct = gpa > 0 ? ((gpa - minGPA) / range) * 100 : 0;
      const color = gradeColor(gpa);
      const label = p.period || '';
      return `
        <div style="flex:1; display:flex; flex-direction:column; align-items:center; gap:4px; height:100%;">
          <span class="text-xs" style="color:${color}; font-weight:var(--font-bold);">${gpa > 0 ? gpa.toFixed(1) : '—'}</span>
          <div style="flex:1; width:100%; display:flex; align-items:flex-end; justify-content:center;">
            <div style="width:70%; height:${heightPct}%; min-height:4px;
                        background:${color}; border-radius:4px 4px 0 0;
                        box-shadow:0 0 6px ${color}88;
                        transition:height 0.5s var(--ease-bounce);"></div>
          </div>
          <span class="text-xs" style="color:var(--color-text-tertiary); transform:rotate(-45deg); transform-origin:top left; white-space:nowrap; margin-top:4px;">${label}</span>
        </div>`;
    }).join('');
  }

  // ── 2.7 Deadlines ──────────────────────────────────────────────────────

  function _renderDeadlines(deadlines) {
    if (!deadlines) return;
    const listEl = document.getElementById('deadlines-list');
    const upcoming = deadlines.upcoming || [];
    const overdue = deadlines.overdue_count || 0;
    const pending = deadlines.pending_count || 0;

    if (!upcoming.length) {
      listEl.innerHTML = `<span class="text-xs text-tertiary" style="display:block; text-align:center; padding:var(--space-6);">Keine anstehenden Termine</span>`;
    } else {
      listEl.innerHTML = upcoming.map(d => {
        const dueDate = new Date(d.due_date);
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const isOverdue = dueDate < today && !d.is_completed;
        const daysLeft = Math.ceil((dueDate - today) / (1000 * 60 * 60 * 24));
        const priorityColor = d.priority === 'high' ? 'var(--color-danger)' :
                              d.priority === 'medium' ? 'var(--color-warning)' : 'var(--color-text-tertiary)';

        return `
          <div style="display:flex; align-items:center; gap:var(--space-3);
                      padding:var(--space-3) var(--space-4);
                      border-bottom:1px solid var(--color-border-subtle);
                      ${isOverdue ? 'background:rgba(255,64,129,0.06);' : ''}">
            <span style="font-size:1.2rem;">${d.deadline_type === 'klausur' ? '📝' : d.deadline_type === 'projekt' ? '📂' : '📋'}</span>
            <div style="flex:1; min-width:0;">
              <div class="text-xs" style="color:var(--color-text-primary); font-weight:var(--font-medium);
                          overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                ${_escapeHtml(d.title)}
              </div>
              <div class="text-xs" style="color:var(--color-text-tertiary);">
                ${dueDate.toLocaleDateString('de-DE')}
                ${isOverdue ? ' ⚠ Überfällig' : daysLeft <= 3 ? ` · ${daysLeft} Tag${daysLeft !== 1 ? 'e' : ''}` : ''}
              </div>
            </div>
            <span class="status-dot ${isOverdue ? 'critical' : d.priority === 'high' ? 'degraded' : 'healthy'}"
                  style="flex-shrink:0;"></span>
          </div>`;
      }).join('');
    }

    document.getElementById('deadline-stats').textContent =
      `${pending} ausstehend · ${overdue} überfällig`;
  }

  function _populateDeadlineSubjects() {
    const select = document.getElementById('deadline-subject');
    if (!select) return;
    select.innerHTML = '<option value="">Kein Fach</option>' +
      (subjects || []).map(s => `<option value="${s.id}">${_escapeHtml(s.name)}</option>`).join('');
  }

  // ── 2.8 Modals ─────────────────────────────────────────────────────────

  // Note-Modal
  function _openGradeModal(subjectId, subjectName) {
    document.getElementById('grade-subject-id').value = subjectId;
    document.getElementById('grade-subject-name').textContent = subjectName;
    document.getElementById('grade-value').value = '2.0';
    document.getElementById('grade-description').value = '';
    document.getElementById('grade-modal').style.display = 'flex';
  }

  document.getElementById('btn-close-grade-modal').addEventListener('click', () => {
    document.getElementById('grade-modal').style.display = 'none';
  });
  document.getElementById('btn-cancel-grade').addEventListener('click', () => {
    document.getElementById('grade-modal').style.display = 'none';
  });

  document.getElementById('btn-save-grade').addEventListener('click', async () => {
    const subjectId = parseInt(document.getElementById('grade-subject-id').value);
    const value = parseFloat(document.getElementById('grade-value').value);
    const gradeType = document.getElementById('grade-type').value;
    const description = document.getElementById('grade-description').value.trim();

    // Gewichtung je nach Typ
    const weightMap = { klassenarbeit: 2.0, klausur: 2.0, test: 1.0, mündlich: 1.0, projekt: 1.5, referat: 1.0 };
    const weight = weightMap[gradeType] || 1.0;

    try {
      await post('/school/grades', {
        subject_id: subjectId,
        value,
        weight,
        grade_type: gradeType,
        description: description || null
      });
      document.getElementById('grade-modal').style.display = 'none';
      await _loadDashboard();
    } catch (err) {
      alert(`Fehler: ${err.message}`);
    }
  });

  // Fach-Modal
  document.getElementById('btn-add-subject').addEventListener('click', () => {
    document.getElementById('subject-name').value = '';
    document.getElementById('subject-teacher').value = '';
    document.getElementById('subject-room').value = '';
    document.getElementById('subject-exam').checked = false;
    document.getElementById('subject-modal').style.display = 'flex';
  });

  document.getElementById('btn-close-subject-modal').addEventListener('click', () => {
    document.getElementById('subject-modal').style.display = 'none';
  });
  document.getElementById('btn-cancel-subject').addEventListener('click', () => {
    document.getElementById('subject-modal').style.display = 'none';
  });

  document.getElementById('btn-save-subject').addEventListener('click', async () => {
    const name = document.getElementById('subject-name').value.trim();
    const teacher = document.getElementById('subject-teacher').value.trim();
    const room = document.getElementById('subject-room').value.trim();
    const isExam = document.getElementById('subject-exam').checked;
    const color = document.getElementById('subject-color').value;

    if (!name) return;
    try {
      await post('/school/subjects', {
        name, teacher: teacher || null, room: room || null,
        is_exam_subject: isExam, color_hex: color
      });
      document.getElementById('subject-modal').style.display = 'none';
      await _loadDashboard();
    } catch (err) {
      alert(`Fehler: ${err.message}`);
    }
  });

  // Deadline-Modal
  document.getElementById('btn-add-deadline').addEventListener('click', () => {
    _populateDeadlineSubjects();
    document.getElementById('deadline-title').value = '';
    document.getElementById('deadline-date').value = new Date().toISOString().split('T')[0];
    document.getElementById('deadline-modal').style.display = 'flex';
  });

  document.getElementById('btn-close-deadline-modal').addEventListener('click', () => {
    document.getElementById('deadline-modal').style.display = 'none';
  });
  document.getElementById('btn-cancel-deadline').addEventListener('click', () => {
    document.getElementById('deadline-modal').style.display = 'none';
  });

  document.getElementById('btn-save-deadline').addEventListener('click', async () => {
    const title = document.getElementById('deadline-title').value.trim();
    const subjectId = document.getElementById('deadline-subject').value;
    const dueDate = document.getElementById('deadline-date').value;
    const priority = document.getElementById('deadline-priority').value;

    if (!title || !dueDate) return;
    try {
      await post('/school/deadlines', {
        subject_id: subjectId ? parseInt(subjectId) : null,
        title, due_date: dueDate, priority
      });
      document.getElementById('deadline-modal').style.display = 'none';
      await _loadDashboard();
    } catch (err) {
      alert(`Fehler: ${err.message}`);
    }
  });

  // Modal-Overlay-Klick → schließen
  document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) overlay.style.display = 'none';
    });
  });

  // ── 2.9 PDF-Upload (RAG) ───────────────────────────────────────────────

  const pdfUploadArea = document.getElementById('pdf-upload-area');
  const pdfFileInput = document.getElementById('pdf-file-input');
  const pdfStatusEl = document.getElementById('pdf-upload-status');

  pdfUploadArea.addEventListener('click', () => pdfFileInput.click());
  pdfFileInput.addEventListener('change', () => {
    if (pdfFileInput.files.length > 0) _uploadPDF(pdfFileInput.files[0]);
  });

  // Drag-and-Drop
  pdfUploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    pdfUploadArea.style.borderColor = 'var(--color-accent-primary)';
    pdfUploadArea.style.background = 'var(--color-accent-primary-soft)';
  });
  pdfUploadArea.addEventListener('dragleave', () => {
    pdfUploadArea.style.borderColor = 'var(--color-glass-border)';
    pdfUploadArea.style.background = '';
  });
  pdfUploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    pdfUploadArea.style.borderColor = 'var(--color-glass-border)';
    pdfUploadArea.style.background = '';
    if (e.dataTransfer.files.length > 0) _uploadPDF(e.dataTransfer.files[0]);
  });

  async function _uploadPDF(file) {
    if (file.type !== 'application/pdf' && !file.name.endsWith('.pdf')) {
      pdfStatusEl.innerHTML = `<div class="text-xs" style="color:var(--color-danger);">Nur PDF-Dateien werden akzeptiert.</div>`;
      return;
    }

    pdfStatusEl.innerHTML = `
      <div class="glass-card subtle" style="padding:var(--space-3) var(--space-4);">
        <div style="display:flex; align-items:center; gap:var(--space-3);">
          <span style="font-size:1.2rem;">📄</span>
          <div style="flex:1;">
            <div class="text-xs" style="color:var(--color-text-primary);">${_escapeHtml(file.name)}</div>
            <div class="text-xs" style="color:var(--color-text-tertiary);">
              ${(file.size / 1024).toFixed(0)} KB — Wird analysiert…
            </div>
          </div>
          <div class="spinner"></div>
        </div>
      </div>`;

    const formData = new FormData();
    formData.append('file', file);

    try {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/school/documents/ingest');

      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          const pct = Math.round((e.loaded / e.total) * 100);
          pdfStatusEl.querySelector('.text-xs:last-child').textContent =
            `${(file.size / 1024).toFixed(0)} KB — Upload ${pct}%`;
        }
      });

      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          let result;
          try { result = JSON.parse(xhr.responseText); } catch { result = {}; }
          pdfStatusEl.innerHTML = `
            <div class="glass-card subtle" style="padding:var(--space-3) var(--space-4); border-color:var(--color-success);">
              <div style="display:flex; align-items:center; gap:var(--space-3);">
                <span style="color:var(--color-success); font-size:1.2rem;">✓</span>
                <div>
                  <div class="text-xs" style="color:var(--color-success); font-weight:var(--font-medium);">
                    Erfolgreich indiziert
                  </div>
                  <div class="text-xs" style="color:var(--color-text-tertiary);">
                    ${result.chunks_count ? `${result.chunks_count} Chunks in RAG-Datenbank` : 'Dokument wurde in die Wissensdatenbank aufgenommen.'}
                  </div>
                </div>
              </div>
            </div>`;
        } else {
          pdfStatusEl.innerHTML = `<div class="text-xs" style="color:var(--color-danger);">Fehler beim Indizieren [${xhr.status}]</div>`;
        }
      });

      xhr.addEventListener('error', () => {
        pdfStatusEl.innerHTML = `<div class="text-xs" style="color:var(--color-danger);">Netzwerkfehler beim Upload</div>`;
      });

      xhr.send(formData);
    } catch (err) {
      pdfStatusEl.innerHTML = `<div class="text-xs" style="color:var(--color-danger);">${_escapeHtml(err.message)}</div>`;
    }
  }

  // ── 2.10 Initialisierung ───────────────────────────────────────────────
  await _loadDashboard();

  // ── 2.11 Cleanup ───────────────────────────────────────────────────────
  return () => {
    // Keine persistenten Listener außerhalb des Containers
  };
}

// ═══════════════════════════════════════════════════════════════════════════════
// 3. HILFSFUNKTIONEN
// ═══════════════════════════════════════════════════════════════════════════════

function _escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}

function _escapeAttr(str) {
  return str.replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}