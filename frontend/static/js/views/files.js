// FILE: frontend/static/js/views/files.js
/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * Dateibunker — Interaktiver Dateimanager mit Drag-and-Drop-Upload.
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Bietet ein vollständiges Explorer-Interface mit:
 *   • Ordner-Navigation & Breadcrumb-Pfad
 *   • Datei-/Ordner-Grid mit Typ-Icons, Größen, Datum
 *   • Drag-and-Drop-Upload mit asynchroner Fortschrittsanzeige
 *   • Such- und Filterfunktion
 *   • Neuer-Ordner-Dialog, Löschen mit Bestätigung
 *   • Datei-Download & Umbenennen (Inline)
 *   • Speicherplatz-Übersicht (Balken + Zahlen)
 *
 * Verwendete API-Endpunkte:
 *   GET    /api/files/list?path=         — Verzeichnisinhalt
 *   POST   /api/files/directory?path=    — Ordner erstellen
 *   DELETE /api/files/directory?path=    — Ordner löschen
 *   DELETE /api/files/delete?path=       — Datei löschen
 *   POST   /api/files/upload?path=       — Datei-Upload
 *   GET    /api/files/download?path=     — Datei-Download
 *   PUT    /api/files/move               — Verschieben/Umbenennen
 *   GET    /api/files/storage            — Speicherplatz-Info
 */

import { get, post, del, put } from '../api.js';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. KONSTANTEN
// ═══════════════════════════════════════════════════════════════════════════════

/** MIME-Type → Icon-Mapping */
const FILE_ICONS = {
  'image': '🖼',
  'video': '🎬',
  'audio': '🎵',
  'pdf': '📕',
  'text': '📝',
  'code': '⟨⟩',
  'archive': '📦',
  'spreadsheet': '📊',
  'document': '📄',
  'font': '🔤',
  'executable': '⚙',
};

/** Datei-Endung → Kategorie */
const EXT_CATEGORY = {
  jpg: 'image', jpeg: 'image', png: 'image', gif: 'image', svg: 'image',
  webp: 'image', bmp: 'image', ico: 'image', tiff: 'image',
  mp4: 'video', mkv: 'video', avi: 'video', mov: 'video', webm: 'video',
  mp3: 'audio', flac: 'audio', wav: 'audio', ogg: 'audio', m4a: 'audio',
  pdf: 'pdf',
  txt: 'text', md: 'text', log: 'text', csv: 'text',
  js: 'code', py: 'code', ts: 'code', rs: 'code', go: 'code',
  java: 'code', c: 'code', cpp: 'code', h: 'code', css: 'code',
  html: 'code', xml: 'code', json: 'code', yaml: 'code', toml: 'code',
  sh: 'code', sql: 'code', php: 'code', rb: 'code', swift: 'code',
  zip: 'archive', tar: 'archive', gz: 'archive', rar: 'archive', '7z': 'archive',
  xlsx: 'spreadsheet', xls: 'spreadsheet', ods: 'spreadsheet',
  docx: 'document', doc: 'document', odt: 'document', pptx: 'document',
  ttf: 'font', otf: 'font', woff: 'font', woff2: 'font',
  exe: 'executable', bin: 'executable',
};

/**
 * Dateigröße formatieren: Bytes → menschenlesbar.
 * @param {number} bytes
 * @returns {string} Z. B. "42.3 MB"
 */
function formatSize(bytes) {
  if (bytes == null || bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
}

/**
 * Datum formatieren (ISO → lesbar).
 * @param {string} iso
 * @returns {string}
 */
function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' }) +
    ' ' + d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
}

/**
 * Icon für eine Datei anhand Erweiterung oder MIME-Type ermitteln.
 */
function fileIcon(entry) {
  if (entry.is_dir) return '📁';
  const ext = (entry.name || '').split('.').pop()?.toLowerCase() || '';
  const cat = EXT_CATEGORY[ext] || 'document';
  return FILE_ICONS[cat] || '📄';
}

// ═══════════════════════════════════════════════════════════════════════════════
// 2. HAUPTFUNKTION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Rendert den Dateibunker-Explorer.
 *
 * @param {HTMLElement} container — Der #view-port-Container
 * @returns {Function} Cleanup-Funktion
 */
export async function showFiles(container) {
  // ── 2.1 Grundlayout ────────────────────────────────────────────────────
  container.innerHTML = `
    <div class="section-header">
      <div>
        <h1>Dateibunker</h1>
        <p class="section-subtitle">Sicheres Dateimanagement mit Drag-and-Drop-Upload</p>
      </div>
      <div class="btn-group">
        <button class="btn btn-primary" id="btn-new-folder">📁 Neuer Ordner</button>
        <button class="btn btn-secondary" id="btn-upload-trigger">⬆ Datei hochladen</button>
        <input type="file" id="file-input-hidden" multiple style="display:none;">
      </div>
    </div>

    <!-- Breadcrumb-Navigation -->
    <nav class="breadcrumb glass-card subtle" id="file-breadcrumb"
         style="display:flex; align-items:center; gap:var(--space-2); padding:var(--space-3) var(--space-4); margin-bottom:var(--space-4); overflow-x:auto;">
    </nav>

    <div style="display:grid; grid-template-columns:1fr 260px; gap:var(--space-6);">

      <!-- Hauptbereich: Drag-Drop-Zone + Dateigitter -->
      <div style="display:flex; flex-direction:column; gap:var(--space-4); min-height:0;">

        <!-- Drag-and-Drop-Upload-Zone -->
        <div id="drop-zone"
             class="glass-card"
             style="display:flex; align-items:center; justify-content:center;
                    gap:var(--space-3); padding:var(--space-6);
                    border:2px dashed var(--color-glass-border);
                    border-radius:var(--radius-lg); cursor:pointer;
                    transition:border-color 0.2s ease, background 0.2s ease;
                    text-align:center;">
          <span style="font-size:2rem;" aria-hidden="true">📤</span>
          <div>
            <div style="font-weight:var(--font-semibold); color:var(--color-text-secondary);">
              Dateien hierher ziehen
            </div>
            <div class="text-xs" style="color:var(--color-text-tertiary);">
              oder klicken zum Auswählen
            </div>
          </div>
        </div>

        <!-- Upload-Fortschritt (versteckt) -->
        <div id="upload-progress-container" style="display:none;"></div>

        <!-- Suchleiste -->
        <div style="display:flex; gap:var(--space-3);">
          <input type="text" id="file-search-input" class="form-input"
                 placeholder="Dateien filtern…" style="flex:1;">
          <button class="btn btn-ghost btn-icon" id="btn-clear-search" title="Filter löschen">✕</button>
        </div>

        <!-- Datei-Grid -->
        <div id="file-grid"
             style="display:grid; grid-template-columns:repeat(auto-fill, minmax(170px, 1fr));
                    gap:var(--space-3); overflow-y:auto; flex:1; min-height:300px;
                    align-content:start;">
        </div>

        <!-- Leerzustand -->
        <div id="file-empty-state" class="empty-state" style="display:none;">
          <div class="empty-icon">📭</div>
          <h3>Dieser Ordner ist leer</h3>
          <p>Ziehe Dateien hierher oder klicke „Neuer Ordner".</p>
        </div>
      </div>

      <!-- Seitenleiste: Speicherplatz-Info -->
      <div style="display:flex; flex-direction:column; gap:var(--space-4);">
        <div class="glass-card" id="storage-card">
          <div class="card-header"><h3>Speicherplatz</h3></div>
          <div id="storage-info" style="text-align:center; padding:var(--space-4) 0;">
            <div class="spinner" style="margin:0 auto;"></div>
          </div>
        </div>

        <!-- Aktionen für ausgewählte Datei -->
        <div class="glass-card" id="file-actions-card" style="display:none;">
          <div class="card-header"><h3>Aktionen</h3></div>
          <div id="selected-file-info" class="text-xs" style="color:var(--color-text-tertiary); margin-bottom:var(--space-3);"></div>
          <div class="flex flex-col gap-2">
            <button class="btn btn-secondary btn-pill" id="btn-download-file" style="width:100%;">⬇ Herunterladen</button>
            <button class="btn btn-ghost btn-pill" id="btn-rename-file" style="width:100%;">✎ Umbenennen</button>
            <button class="btn btn-ghost btn-pill" id="btn-delete-file" style="width:100%; color:var(--color-danger);">🗑 Löschen</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Neuer-Ordner-Modal (versteckt) -->
    <div id="new-folder-modal" class="modal-overlay" style="display:none;">
      <div class="modal-dialog">
        <div class="modal-header">
          <h3>Neuer Ordner</h3>
          <button class="btn-icon btn-ghost" id="btn-close-modal" title="Schließen">✕</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label">Ordnername</label>
            <input type="text" id="new-folder-name" class="form-input"
                   placeholder="z. B. Dokumente" autofocus>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-ghost" id="btn-cancel-folder">Abbrechen</button>
          <button class="btn btn-primary" id="btn-create-folder">Erstellen</button>
        </div>
      </div>
    </div>

    <!-- Umbenennen-Modal -->
    <div id="rename-modal" class="modal-overlay" style="display:none;">
      <div class="modal-dialog">
        <div class="modal-header">
          <h3>Umbenennen</h3>
          <button class="btn-icon btn-ghost" id="btn-close-rename-modal">✕</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label">Neuer Name</label>
            <input type="text" id="rename-input" class="form-input" autofocus>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-ghost" id="btn-cancel-rename">Abbrechen</button>
          <button class="btn btn-primary" id="btn-confirm-rename">Umbenennen</button>
        </div>
      </div>
    </div>

    <!-- Bestätigungs-Modal (Löschen) -->
    <div id="confirm-modal" class="modal-overlay" style="display:none;">
      <div class="modal-dialog">
        <div class="modal-header">
          <h3>Löschen bestätigen</h3>
          <button class="btn-icon btn-ghost" id="btn-close-confirm-modal">✕</button>
        </div>
        <div class="modal-body">
          <p id="confirm-message" class="text-sm" style="color:var(--color-text-secondary);"></p>
          <div class="form-check" style="margin-top:var(--space-3);">
            <input type="checkbox" id="confirm-secure-delete">
            <label for="confirm-secure-delete" class="text-xs" style="color:var(--color-text-tertiary);">
              Sicheres Löschen (3-faches Überschreiben)
            </label>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-ghost" id="btn-cancel-delete">Abbrechen</button>
          <button class="btn btn-danger" id="btn-confirm-delete">Löschen</button>
        </div>
      </div>
    </div>
  `;

  // ── 2.2 DOM-Referenzen ─────────────────────────────────────────────────
  const fileGrid = document.getElementById('file-grid');
  const fileEmpty = document.getElementById('file-empty-state');
  const breadcrumb = document.getElementById('file-breadcrumb');
  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input-hidden');
  const uploadProgressContainer = document.getElementById('upload-progress-container');
  const searchInput = document.getElementById('file-search-input');
  const storageInfo = document.getElementById('storage-info');
  const fileActionsCard = document.getElementById('file-actions-card');
  const selectedFileInfo = document.getElementById('selected-file-info');

  // ── 2.3 Zustand ────────────────────────────────────────────────────────
  let currentPath = '';           // Aktueller Pfad relativ zum Basisverzeichnis
  let currentEntries = [];        // Geladene Verzeichniseinträge
  let selectedEntry = null;      // Ausgewählter Eintrag
  let filterQuery = '';          // Such-/Filter-Text

  // ── 2.4 Verzeichnis laden ──────────────────────────────────────────────

  async function loadDirectory(path) {
    currentPath = path;
    fileGrid.innerHTML = `<div class="spinner" style="margin:var(--space-8) auto; grid-column:1/-1;"></div>`;
    fileEmpty.style.display = 'none';
    fileActionsCard.style.display = 'none';
    selectedEntry = null;

    try {
      const entries = await get('/files/list', { path }, { skipToast: true });
      currentEntries = entries || [];
      _renderBreadcrumbs(path);
      _renderEntries();
      _updateStorage();
    } catch (err) {
      fileGrid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
        <div class="empty-icon">⚠</div>
        <h3>Fehler beim Laden</h3>
        <p>${err.message || 'Verzeichnis nicht lesbar.'}</p>
      </div>`;
    }
  }

  // ── 2.5 Breadcrumbs rendern ────────────────────────────────────────────

  function _renderBreadcrumbs(path) {
    const parts = path ? path.split('/').filter(Boolean) : [];
    let link = '';

    breadcrumb.innerHTML = `
      <a href="#" class="breadcrumb-link text-sm" data-path=""
         style="color:var(--color-accent-primary); text-decoration:none; white-space:nowrap;">
        🏠 Dateibunker
      </a>
    ` + parts.map((part, i) => {
      link += (link ? '/' : '') + part;
      const isLast = i === parts.length - 1;
      return `
        <span class="breadcrumb-sep text-tertiary">/</span>
        <a href="#" class="breadcrumb-link text-sm" data-path="${link}"
           style="color:${isLast ? 'var(--color-text-primary)' : 'var(--color-accent-primary)'};
                  text-decoration:none; white-space:nowrap; font-weight:${isLast ? 'var(--font-semibold)' : 'var(--font-normal)'};">
          ${part}
        </a>
      `;
    }).join('');
  }

  // ── 2.6 Einträge rendern ───────────────────────────────────────────────

  function _renderEntries() {
    let entries = currentEntries;

    // Filtern
    if (filterQuery) {
      const q = filterQuery.toLowerCase();
      entries = entries.filter(e => e.name.toLowerCase().includes(q));
    }

    // Ordner zuerst, dann alphabetisch
    entries.sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      return (a.name || '').localeCompare(b.name || '');
    });

    if (entries.length === 0) {
      fileGrid.innerHTML = '';
      fileEmpty.style.display = 'flex';
      return;
    }

    fileEmpty.style.display = 'none';
    fileGrid.innerHTML = entries.map(entry => `
      <div class="file-entry glass-card subtle"
           data-name="${_escapeAttr(entry.name)}"
           data-is-dir="${entry.is_dir || false}"
           style="display:flex; align-items:center; gap:var(--space-3);
                  padding:var(--space-3) var(--space-4); cursor:pointer;
                  transition:border-color 0.15s ease, transform 0.15s ease;
                  ${selectedEntry && selectedEntry.name === entry.name ? 'border-color:var(--color-accent-primary);' : ''}"
           title="${_escapeAttr(entry.name)}${entry.size != null ? ' · ' + formatSize(entry.size) : ''}">
        <span style="font-size:1.5rem; flex-shrink:0;">${fileIcon(entry)}</span>
        <div style="min-width:0; flex:1;">
          <div class="text-sm" style="color:var(--color-text-primary); font-weight:var(--font-medium);
                      overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
            ${_escapeHtml(entry.name)}
          </div>
          <div class="text-xs" style="color:var(--color-text-tertiary);">
            ${entry.is_dir ? 'Ordner' : formatSize(entry.size)}
            ${!entry.is_dir && entry.modified ? ' · ' + formatDate(entry.modified) : ''}
          </div>
        </div>
      </div>
    `).join('');
  }

  // ── 2.7 Speicherplatz aktualisieren ────────────────────────────────────

  async function _updateStorage() {
    try {
      const data = await get('/files/storage', {}, { skipToast: true, skipLoader: true });
      const pct = data.percent || 0;
      const color = pct > 90 ? 'var(--color-danger)' :
                    pct > 70 ? 'var(--color-warning)' : 'var(--color-success)';

      storageInfo.innerHTML = `
        <div class="stat-widget" style="align-items:center;">
          <div class="stat-value" style="font-size:var(--text-2xl); color:${color};">
            ${pct.toFixed(1)}%
          </div>
          <div class="stat-label">belegt</div>
        </div>
        <div class="progress-bar" style="margin-top:var(--space-3);">
          <div class="progress-fill ${pct > 90 ? 'danger' : pct > 70 ? 'warning' : 'success'}"
               style="width:${pct}%;"></div>
        </div>
        <div style="display:flex; justify-content:space-between; margin-top:var(--space-2);
                    font-size:var(--text-xs); color:var(--color-text-tertiary);">
          <span>${formatSize((data.free_gb || 0) * 1e9)} frei</span>
          <span>${formatSize((data.total_gb || 0) * 1e9)} gesamt</span>
        </div>
      `;
    } catch {
      storageInfo.innerHTML = `<p class="text-xs text-tertiary" style="text-align:center;">Nicht verfügbar</p>`;
    }
  }

  // ── 2.8 Event-Handler ──────────────────────────────────────────────────

  // Breadcrumb-Navigation
  breadcrumb.addEventListener('click', (e) => {
    const link = e.target.closest('.breadcrumb-link');
    if (!link) return;
    e.preventDefault();
    loadDirectory(link.dataset.path || '');
  });

  // Eintrag auswählen / Ordner öffnen
  fileGrid.addEventListener('click', (e) => {
    const entryEl = e.target.closest('.file-entry');
    if (!entryEl) return;

    const name = entryEl.dataset.name;
    const isDir = entryEl.dataset.isDir === 'true';
    const entry = currentEntries.find(e => e.name === name);
    if (!entry) return;

    if (isDir) {
      // Ordner betreten
      const newPath = currentPath ? `${currentPath}/${name}` : name;
      loadDirectory(newPath);
    } else {
      // Datei auswählen
      selectedEntry = entry;
      _renderEntries();
      _showFileActions(entry);
    }
  });

  // Doppelklick auf Datei → Download
  fileGrid.addEventListener('dblclick', (e) => {
    const entryEl = e.target.closest('.file-entry');
    if (!entryEl) return;
    const isDir = entryEl.dataset.isDir === 'true';
    if (isDir) return;

    const name = entryEl.dataset.name;
    const entry = currentEntries.find(e => e.name === name);
    if (entry) _downloadFile(entry);
  });

  function _showFileActions(entry) {
    fileActionsCard.style.display = 'block';
    selectedFileInfo.textContent = `${fileIcon(entry)} ${entry.name} · ${formatSize(entry.size || 0)}`;
  }

  // Download
  document.getElementById('btn-download-file').addEventListener('click', () => {
    if (!selectedEntry) return;
    _downloadFile(selectedEntry);
  });

  function _downloadFile(entry) {
    const path = currentPath ? `${currentPath}/${entry.name}` : entry.name;
    const a = document.createElement('a');
    a.href = `/api/files/download?path=${encodeURIComponent(path)}`;
    a.download = entry.name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  // Löschen
  document.getElementById('btn-delete-file').addEventListener('click', () => {
    if (!selectedEntry) return;
    _showConfirmModal(
      `${selectedEntry.is_dir ? 'Ordner' : 'Datei'} „${selectedEntry.name}” wirklich löschen?`,
      async (secure) => {
        const path = currentPath ? `${currentPath}/${selectedEntry.name}` : selectedEntry.name;
        if (selectedEntry.is_dir) {
          await del(`/files/directory?path=${encodeURIComponent(path)}&recursive=true`, { skipToast: false });
        } else {
          await del(`/files/delete?path=${encodeURIComponent(path)}&secure=${secure}`, { skipToast: false });
        }
        selectedEntry = null;
        fileActionsCard.style.display = 'none';
        loadDirectory(currentPath);
      }
    );
  });

  // ── 2.9 Modals ─────────────────────────────────────────────────────────

  // Neuer Ordner
  document.getElementById('btn-new-folder').addEventListener('click', () => {
    document.getElementById('new-folder-modal').style.display = 'flex';
    document.getElementById('new-folder-name').value = '';
    setTimeout(() => document.getElementById('new-folder-name').focus(), 100);
  });

  document.getElementById('btn-close-modal').addEventListener('click', () => {
    document.getElementById('new-folder-modal').style.display = 'none';
  });
  document.getElementById('btn-cancel-folder').addEventListener('click', () => {
    document.getElementById('new-folder-modal').style.display = 'none';
  });

  document.getElementById('btn-create-folder').addEventListener('click', async () => {
    const name = document.getElementById('new-folder-name').value.trim();
    if (!name) return;
    const path = currentPath ? `${currentPath}/${name}` : name;
    try {
      await post(`/files/directory?path=${encodeURIComponent(path)}`);
      document.getElementById('new-folder-modal').style.display = 'none';
      loadDirectory(currentPath);
    } catch (err) {
      alert(`Fehler: ${err.message}`);
    }
  });

  // Umbenennen
  document.getElementById('btn-rename-file').addEventListener('click', () => {
    if (!selectedEntry) return;
    document.getElementById('rename-modal').style.display = 'flex';
    document.getElementById('rename-input').value = selectedEntry.name;
    setTimeout(() => document.getElementById('rename-input').focus(), 100);
  });

  document.getElementById('btn-close-rename-modal').addEventListener('click', () => {
    document.getElementById('rename-modal').style.display = 'none';
  });
  document.getElementById('btn-cancel-rename').addEventListener('click', () => {
    document.getElementById('rename-modal').style.display = 'none';
  });

  document.getElementById('btn-confirm-rename').addEventListener('click', async () => {
    if (!selectedEntry) return;
    const newName = document.getElementById('rename-input').value.trim();
    if (!newName || newName === selectedEntry.name) {
      document.getElementById('rename-modal').style.display = 'none';
      return;
    }
    const oldPath = currentPath ? `${currentPath}/${selectedEntry.name}` : selectedEntry.name;
    const newPath = currentPath ? `${currentPath}/${newName}` : newName;
    try {
      await put('/files/move', { source: oldPath, destination: newPath });
      document.getElementById('rename-modal').style.display = 'none';
      selectedEntry = null;
      fileActionsCard.style.display = 'none';
      loadDirectory(currentPath);
    } catch (err) {
      alert(`Fehler: ${err.message}`);
    }
  });

  // Bestätigungs-Modal
  let _confirmCallback = null;

  function _showConfirmModal(message, callback) {
    document.getElementById('confirm-message').textContent = message;
    document.getElementById('confirm-secure-delete').checked = false;
    document.getElementById('confirm-modal').style.display = 'flex';
    _confirmCallback = callback;
  }

  document.getElementById('btn-close-confirm-modal').addEventListener('click', () => {
    document.getElementById('confirm-modal').style.display = 'none';
    _confirmCallback = null;
  });
  document.getElementById('btn-cancel-delete').addEventListener('click', () => {
    document.getElementById('confirm-modal').style.display = 'none';
    _confirmCallback = null;
  });
  document.getElementById('btn-confirm-delete').addEventListener('click', async () => {
    const secure = document.getElementById('confirm-secure-delete').checked;
    document.getElementById('confirm-modal').style.display = 'none';
    if (_confirmCallback) {
      await _confirmCallback(secure);
      _confirmCallback = null;
    }
  });

  // Modal-Overlay-Klick schließen
  document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) overlay.style.display = 'none';
    });
  });

  // ── 2.10 Suche/Filter ──────────────────────────────────────────────────

  searchInput.addEventListener('input', () => {
    filterQuery = searchInput.value.trim();
    _renderEntries();
  });

  document.getElementById('btn-clear-search').addEventListener('click', () => {
    searchInput.value = '';
    filterQuery = '';
    _renderEntries();
  });

  // ── 2.11 Drag-and-Drop-Upload ──────────────────────────────────────────

  // Trigger-Upload-Button
  document.getElementById('btn-upload-trigger').addEventListener('click', () => {
    fileInput.click();
  });

  // Klick auf Drop-Zone
  dropZone.addEventListener('click', () => {
    fileInput.click();
  });

  // Datei-Auswahl via Input
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) {
      _uploadFiles(fileInput.files);
      fileInput.value = '';
    }
  });

  // Drag-Events auf Drop-Zone
  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropZone.style.borderColor = 'var(--color-accent-primary)';
    dropZone.style.background = 'var(--color-accent-primary-soft)';
  });

  dropZone.addEventListener('dragleave', (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropZone.style.borderColor = 'var(--color-glass-border)';
    dropZone.style.background = '';
  });

  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropZone.style.borderColor = 'var(--color-glass-border)';
    dropZone.style.background = '';

    if (e.dataTransfer.files.length > 0) {
      _uploadFiles(e.dataTransfer.files);
    }
  });

  // ── 2.12 Upload-Logik ──────────────────────────────────────────────────

  async function _uploadFiles(fileList) {
    const files = Array.from(fileList);
    if (files.length === 0) return;

    uploadProgressContainer.style.display = 'block';
    uploadProgressContainer.innerHTML = '';

    for (const file of files) {
      const progressId = `upload-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

      // Fortschritts-Widget pro Datei
      const widget = document.createElement('div');
      widget.className = 'glass-card subtle';
      widget.id = progressId;
      widget.style.cssText = 'padding:var(--space-3) var(--space-4); margin-bottom:var(--space-2);';
      widget.innerHTML = `
        <div style="display:flex; align-items:center; gap:var(--space-3);">
          <span style="font-size:1.2rem;">📄</span>
          <div style="flex:1; min-width:0;">
            <div class="text-xs" style="color:var(--color-text-primary); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
              ${_escapeHtml(file.name)}
            </div>
            <div class="text-xs" style="color:var(--color-text-tertiary);">${formatSize(file.size)}</div>
          </div>
          <span class="text-xs" id="${progressId}-pct" style="color:var(--color-accent-primary); min-width:40px; text-align:right;">0%</span>
        </div>
        <div class="progress-bar" style="margin-top:var(--space-2);">
          <div class="progress-fill primary" id="${progressId}-bar" style="width:0%;"></div>
        </div>
      `;
      uploadProgressContainer.appendChild(widget);

      // Upload ausführen
      const formData = new FormData();
      formData.append('file', file);

      const url = `/api/files/upload?path=${encodeURIComponent(currentPath)}`;

      try {
        await _xhrUpload(url, formData, (pct) => {
          const barEl = document.getElementById(`${progressId}-bar`);
          const pctEl = document.getElementById(`${progressId}-pct`);
          if (barEl) barEl.style.width = `${pct}%`;
          if (pctEl) pctEl.textContent = `${pct}%`;
          if (pct >= 100) {
            if (pctEl) { pctEl.style.color = 'var(--color-success)'; pctEl.textContent = '✓'; }
          }
        });
      } catch (err) {
        const pctEl = document.getElementById(`${progressId}-pct`);
        if (pctEl) { pctEl.style.color = 'var(--color-danger)'; pctEl.textContent = '✕'; }
        console.error(`Upload-Fehler für ${file.name}:`, err);
      }
    }

    // Nach allen Uploads: Verzeichnis neu laden
    setTimeout(() => {
      loadDirectory(currentPath);
      // Upload-Widgets nach 3s ausblenden
      setTimeout(() => {
        uploadProgressContainer.style.display = 'none';
      }, 3000);
    }, 500);
  }

  /**
   * XHR-basierter Upload mit Progress-Tracking.
   *
   * @param {string} url
   * @param {FormData} formData
   * @param {(pct:number)=>void} onProgress
   * @returns {Promise<any>}
   */
  function _xhrUpload(url, formData, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);

      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable && onProgress) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      });

      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          if (onProgress) onProgress(100);
          try { resolve(JSON.parse(xhr.responseText)); } catch { resolve(xhr.responseText); }
        } else {
          reject(new Error(`Upload fehlgeschlagen [${xhr.status}]`));
        }
      });

      xhr.addEventListener('error', () => reject(new Error('Netzwerkfehler')));
      xhr.addEventListener('abort', () => reject(new Error('Abgebrochen')));

      xhr.send(formData);
    });
  }

  // ── 2.13 Initial laden ─────────────────────────────────────────────────
  loadDirectory('');

  // ── 2.14 Cleanup ───────────────────────────────────────────────────────
  return () => {
    // Keine persistenten Listener außerhalb des Containers
  };
}

// ═══════════════════════════════════════════════════════════════════════════════
// 3. HILFSFUNKTIONEN (privat)
// ═══════════════════════════════════════════════════════════════════════════════

/** HTML-Escaping für Dateinamen */
function _escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}

/** Attribut-Escaping */
function _escapeAttr(str) {
  return str.replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}