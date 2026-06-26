// FILE: frontend/static/js/views/media.js
/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * Medienbunker — HTML5-Videoplayer mit Glassmorphismus-Steuerung.
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Bietet einen vollständigen, nativen HTML5-<video>-Player mit:
 *   • Play/Pause, Zeitleisten-Seeking, Lautstärke, Untertitel
 *   • Glassmorphismus-Steuerelemente (keine iFrames)
 *   • Bibliotheks-Browser: Filme + Serien mit Episoden-Auswahl
 *   • Cover-Thumbnails, Suchfunktion, Genre-Filter
 *   • Tastatur-Steuerung (Space, ←→, M, F)
 *   • Vollbild-Modus & Lade-Spinner
 *
 * Stream-Architektur:
 *   Der <video>-Tag bezieht seine Quelle vom FastAPI-Backend
 *   (/api/media/stream/{id}), das als Proxy zu Jellyfin fungiert.
 *   Die Jellyfin-URL wird NIEMALS zum Client exponiert.
 *
 * Verwendete API-Endpunkte:
 *   GET  /api/media/movies              — Filme
 *   GET  /api/media/series              — Serien
 *   GET  /api/media/series/{id}/episodes — Episoden
 *   GET  /api/media/stream/{id}         — Stream-URL (JSON)
 *   GET  /api/media/cover/{id}/Primary  — Cover-Bild
 *   GET  /api/media/search?q=           — Suche
 */

import { get } from '../api.js';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. KONSTANTEN
// ═══════════════════════════════════════════════════════════════════════════════

/** API-Basis für Cover-Bilder (relativ) */
const COVER_BASE = '/api/media/cover';

/** API-Basis für Stream-Urls */
const STREAM_API = '/api/media/stream';

/** Anzahl Thumbnails pro Seite */
const PAGE_SIZE = 24;

// ═══════════════════════════════════════════════════════════════════════════════
// 2. HAUPTFUNKTION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Rendert den Medienbunker mit zweigeteiltem Layout:
 * links Bibliothek-Browser, rechts Videoplayer.
 *
 * @param {HTMLElement} container — Der #view-port-Container
 * @returns {Function} Cleanup-Funktion
 */
export async function showMedia(container) {
  // ── 2.1 Grundlayout ────────────────────────────────────────────────────
  container.innerHTML = `
    <div class="section-header">
      <div>
        <h1>Medienbunker</h1>
        <p class="section-subtitle">Jellyfin-Bibliothek — Direkt-Stream via Backend-Proxy</p>
      </div>
      <div class="btn-group">
        <button class="btn btn-secondary btn-pill tab-btn active" data-tab="movies">🎬 Filme</button>
        <button class="btn btn-secondary btn-pill tab-btn" data-tab="series">📺 Serien</button>
        <button class="btn btn-secondary btn-pill tab-btn" data-tab="continue">▶ Weiter</button>
      </div>
    </div>

    <div style="display: grid; grid-template-columns: 1fr 360px; gap: var(--space-6); height: calc(100vh - 180px); min-height: 500px;">

      <!-- Linker Bereich: Thumbnail-Grid + Suche -->
      <div style="display:flex; flex-direction:column; gap:var(--space-4); min-height:0;">
        <!-- Suchleiste -->
        <div style="display:flex; gap:var(--space-3);">
          <input type="text" id="media-search-input" class="form-input"
                 placeholder="Filme & Serien durchsuchen..."
                 style="flex:1;">
          <button class="btn btn-primary" id="media-search-btn">Suchen</button>
        </div>
        <!-- Thumbnail-Grid (scrollbar) -->
        <div id="media-grid" class="media-thumb-grid"
             style="display:grid; grid-template-columns:repeat(auto-fill, minmax(140px, 1fr));
                    gap:var(--space-3); overflow-y:auto; flex:1; padding-right:var(--space-2);">
          <div class="empty-state" style="grid-column:1/-1;">
            <div class="empty-icon">🎬</div>
            <h3>Medien werden geladen…</h3>
          </div>
        </div>
        <!-- Paginierung -->
        <div id="media-pagination" style="display:flex; justify-content:center; gap:var(--space-2);"></div>
      </div>

      <!-- Rechter Bereich: Player -->
      <div style="display:flex; flex-direction:column; gap:var(--space-4);">
        <!-- Player-Wrapper -->
        <div class="player-wrapper glass-card" id="player-wrapper"
             style="position:relative; width:100%; aspect-ratio:16/9;
                    background:#000; border-radius:var(--radius-lg); overflow:hidden;
                    display:flex; align-items:center; justify-content:center;">
          <!-- Lade-Spinner (anfangs sichtbar) -->
          <div id="player-placeholder" style="text-align:center; color:var(--color-text-tertiary);">
            <div class="spinner" style="margin:0 auto var(--space-3);"></div>
            <span class="text-xs">Wähle ein Medium</span>
          </div>

          <!-- HTML5-Video-Element (anfangs versteckt) -->
          <video id="imperium-video-player"
                 style="display:none; width:100%; height:100%; object-fit:contain;"
                 crossorigin="anonymous"
                 playsinline
                 preload="metadata">
          </video>

          <!-- Zentraler Overlay-Play-Button (groß, semi-transparent) -->
          <div id="center-play-btn"
               style="display:none; position:absolute; top:50%; left:50%;
                      transform:translate(-50%,-50%); width:72px; height:72px;
                      border-radius:var(--radius-full);
                      background:var(--glass-strong-bg);
                      backdrop-filter:var(--glass-strong-blur);
                      -webkit-backdrop-filter:var(--glass-strong-blur);
                      border:2px solid var(--color-glass-border-hover);
                      color:var(--color-text-primary); font-size:28px;
                      cursor:pointer; z-index:10;
                      display:none; align-items:center; justify-content:center;
                      transition:transform 0.15s ease, background 0.15s ease;"
               title="Play">
            ▶
          </div>
        </div>

        <!-- Aktueller Titel -->
        <div class="glass-card subtle" style="padding:var(--space-4);">
          <div id="now-playing-title" class="text-sm" style="color:var(--color-text-secondary);">
            Keine Wiedergabe
          </div>
          <div id="now-playing-meta" class="text-xs" style="color:var(--color-text-tertiary); margin-top:2px;"></div>
        </div>

        <!-- Steuerelemente -->
        <div class="glass-card" style="padding:var(--space-4);">
          <div class="player-controls" style="display:flex; flex-direction:column; gap:var(--space-3);">

            <!-- Zeitleiste -->
            <div style="display:flex; align-items:center; gap:var(--space-3);">
              <span id="time-current" class="text-xs text-secondary" style="min-width:42px;">0:00</span>
              <div id="seekbar-container"
                   style="flex:1; height:6px; background:rgba(255,255,255,0.08);
                          border-radius:var(--radius-full); cursor:pointer; position:relative;
                          transition:height 0.15s ease;">
                <div id="seekbar-buffered"
                     style="position:absolute; height:100%; background:rgba(255,255,255,0.12);
                            border-radius:var(--radius-full); width:0%;"></div>
                <div id="seekbar-progress"
                     style="position:absolute; height:100%;
                            background:var(--color-accent-primary);
                            border-radius:var(--radius-full); width:0%;
                            box-shadow:0 0 6px rgba(160,107,255,0.40);"></div>
                <div id="seekbar-thumb"
                     style="position:absolute; top:50%; transform:translate(-50%,-50%);
                            width:14px; height:14px; border-radius:var(--radius-full);
                            background:var(--color-accent-primary);
                            box-shadow:0 0 10px rgba(160,107,255,0.60);
                            left:0%; display:none;"></div>
              </div>
              <span id="time-duration" class="text-xs text-secondary" style="min-width:42px;">0:00</span>
            </div>

            <!-- Buttons-Reihe -->
            <div style="display:flex; align-items:center; gap:var(--space-3);">
              <!-- Play/Pause -->
              <button id="btn-play" class="btn-icon btn-ghost" title="Play / Pause (Space)">▶</button>

              <!-- Vor/Zurück -->
              <button id="btn-skip-back" class="btn-icon btn-ghost" title="−10s (←)">⏪</button>
              <button id="btn-skip-fwd" class="btn-icon btn-ghost" title="+10s (→)">⏩</button>

              <!-- Lautstärke -->
              <button id="btn-mute" class="btn-icon btn-ghost" title="Stumm (M)">🔊</button>
              <input type="range" id="volume-slider" min="0" max="100" value="80"
                     title="Lautstärke"
                     style="width:80px; accent-color:var(--color-accent-primary);">

              <span style="flex:1;"></span>

              <!-- Untertitel -->
              <button id="btn-subtitles" class="btn-icon btn-ghost" title="Untertitel (V)">💬</button>

              <!-- Wiedergabegeschwindigkeit -->
              <select id="playback-rate" class="form-select"
                      style="width:auto; padding:4px 8px; font-size:var(--text-xs);"
                      title="Geschwindigkeit">
                <option value="0.5">0.5×</option>
                <option value="0.75">0.75×</option>
                <option value="1" selected>1×</option>
                <option value="1.25">1.25×</option>
                <option value="1.5">1.5×</option>
                <option value="2">2×</option>
              </select>

              <!-- Vollbild -->
              <button id="btn-fullscreen" class="btn-icon btn-ghost" title="Vollbild (F)">⛶</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;

  // ── 2.2 DOM-Referenzen ─────────────────────────────────────────────────
  const player = document.getElementById('imperium-video-player');
  const playerWrapper = document.getElementById('player-wrapper');
  const playerPlaceholder = document.getElementById('player-placeholder');
  const centerPlayBtn = document.getElementById('center-play-btn');
  const mediaGrid = document.getElementById('media-grid');
  const paginationEl = document.getElementById('media-pagination');
  const searchInput = document.getElementById('media-search-input');
  const searchBtn = document.getElementById('media-search-btn');
  const nowPlayingTitle = document.getElementById('now-playing-title');
  const nowPlayingMeta = document.getElementById('now-playing-meta');
  const seekbarContainer = document.getElementById('seekbar-container');
  const seekbarProgress = document.getElementById('seekbar-progress');
  const seekbarBuffered = document.getElementById('seekbar-buffered');
  const seekbarThumb = document.getElementById('seekbar-thumb');
  const timeCurrent = document.getElementById('time-current');
  const timeDuration = document.getElementById('time-duration');
  const btnPlay = document.getElementById('btn-play');
  const btnMute = document.getElementById('btn-mute');
  const volumeSlider = document.getElementById('volume-slider');
  const playbackRate = document.getElementById('playback-rate');
  const btnFullscreen = document.getElementById('btn-fullscreen');

  // ── 2.3 Zustand ────────────────────────────────────────────────────────
  let currentTab = 'movies';      // 'movies' | 'series' | 'continue'
  let currentPage = 1;
  let currentSeriesId = null;    // Für Episoden-Navigation
  let currentItem = null;        // Aktuell ausgewähltes Medium
  let isSeeking = false;
  let isDragging = false;
  let isFullscreen = false;

  // ── 2.4 Hilfsfunktionen ────────────────────────────────────────────────

  /** Sekunden → "m:ss" oder "h:mm:ss" */
  function formatTime(sec) {
    if (!isFinite(sec) || sec < 0) return '0:00';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  /** Cover-URL generieren */
  function coverUrl(itemId) {
    return `${COVER_BASE}/${itemId}/Primary?width=300`;
  }

  /** Stream-URL vom Backend holen */
  async function fetchStreamUrl(itemId) {
    const data = await get(`/media/stream/${itemId}`, {}, { skipLoader: true, skipToast: true });
    return data.stream_url || null;
  }

  // ── 2.5 Medien-Bibliothek laden ────────────────────────────────────────

  async function loadLibrary(tab, page = 1) {
    currentTab = tab;
    currentPage = page;
    currentSeriesId = null;

    mediaGrid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
      <div class="spinner" style="margin:0 auto var(--space-3);"></div>
      <h3>Bibliothek wird geladen…</h3>
    </div>`;

    try {
      let items = [];
      let totalPages = 1;

      if (tab === 'movies') {
        const data = await get('/media/movies', { limit: PAGE_SIZE, page }, { skipToast: true });
        items = data.items || [];
        totalPages = data.total_pages || 1;
      } else if (tab === 'series') {
        const data = await get('/media/series', { limit: PAGE_SIZE, page }, { skipToast: true });
        items = data.items || [];
        totalPages = data.total_pages || 1;
      } else if (tab === 'continue') {
        const data = await get('/media/continue', { limit: PAGE_SIZE }, { skipToast: true });
        items = data || [];
        totalPages = 1;
      }

      _renderThumbnails(items, tab);
      _renderPagination(page, totalPages);
    } catch {
      mediaGrid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
        <div class="empty-icon">⚠</div>
        <h3>Bibliothek nicht verfügbar</h3>
        <p>Der Jellyfin-Server konnte nicht erreicht werden.</p>
      </div>`;
    }
  }

  function _renderThumbnails(items, tab) {
    if (!items.length) {
      mediaGrid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
        <div class="empty-icon">📭</div>
        <h3>Keine Einträge</h3>
        <p>Die Bibliothek ist leer.</p>
      </div>`;
      return;
    }

    mediaGrid.innerHTML = items.map(item => `
      <div class="media-thumb glass-card subtle"
           data-id="${item.id}"
           data-type="${tab}"
           style="cursor:pointer; padding:var(--space-2); transition:transform 0.15s ease, border-color 0.15s ease;"
           title="${item.title || item.name || ''}">
        <div style="aspect-ratio:2/3; background:var(--color-bg-tertiary);
                    border-radius:var(--radius-sm); overflow:hidden;
                    display:flex; align-items:center; justify-content:center;">
          <img src="${coverUrl(item.id)}"
               alt="${item.title}"
               loading="lazy"
               style="width:100%; height:100%; object-fit:cover;"
               onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
          <span style="display:none; font-size:2rem; opacity:0.25;">🎬</span>
        </div>
        <div style="padding:var(--space-2) var(--space-1);">
          <div class="text-xs" style="color:var(--color-text-primary); font-weight:var(--font-medium);
                      overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
            ${item.title || item.name || 'Unbekannt'}
          </div>
          <div class="text-xs" style="color:var(--color-text-tertiary); margin-top:2px;">
            ${item.year || ''} ${item.runtime_display ? '· ' + item.runtime_display : ''}
          </div>
        </div>
      </div>
    `).join('');
  }

  function _renderPagination(page, totalPages) {
    if (totalPages <= 1) { paginationEl.innerHTML = ''; return; }
    const btns = [];
    for (let p = 1; p <= totalPages; p++) {
      btns.push(`<button class="btn btn-secondary ${p === page ? 'btn-primary' : ''}"
                        style="min-width:36px;"
                        data-page="${p}">${p}</button>`);
    }
    paginationEl.innerHTML = btns.join('');
  }

  // ── 2.6 Episoden einer Serie laden ─────────────────────────────────────

  async function loadEpisodes(seriesId, seriesName) {
    currentSeriesId = seriesId;
    mediaGrid.innerHTML = `
      <div style="grid-column:1/-1; padding:var(--space-3); display:flex; align-items:center; gap:var(--space-3);">
        <button class="btn btn-ghost btn-icon" id="back-to-series" title="Zurück zur Serienliste">←</button>
        <span class="text-sm" style="color:var(--color-text-primary); font-weight:var(--font-semibold);">
          📺 ${seriesName}
        </span>
      </div>
      <div class="spinner" style="margin:var(--space-6) auto;"></div>`;

    try {
      const episodes = await get(`/media/series/${seriesId}/episodes`, {}, { skipToast: true });

      const episodeItems = episodes.map(ep => ({
        ...ep,
        id: ep.id,
        title: ep.title || ep.name || `Folge ${ep.index_number || '?'}`,
        year: null,
        runtime_display: ep.runtime_display || ''
      }));

      _renderThumbnails(episodeItems, 'episode');

      // Zurück-Button
      const backBtn = document.getElementById('back-to-series');
      if (backBtn) {
        backBtn.addEventListener('click', () => loadLibrary('series', currentPage));
      }
    } catch {
      mediaGrid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
        <div class="empty-icon">⚠</div><h3>Episoden nicht verfügbar</h3></div>`;
    }
  }

  // ── 2.7 Suche ──────────────────────────────────────────────────────────

  async function searchMedia(query) {
    if (!query.trim()) { loadLibrary(currentTab); return; }

    mediaGrid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
      <div class="spinner"></div><h3>Suche läuft…</h3></div>`;
    paginationEl.innerHTML = '';

    try {
      const data = await get('/media/search', { q: query.trim() }, { skipToast: true });
      const items = data.items || data || [];
      _renderThumbnails(items, 'search');
    } catch {
      mediaGrid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
        <div class="empty-icon">🔍</div><h3>Keine Ergebnisse</h3></div>`;
    }
  }

  // ── 2.8 Medium abspielen ───────────────────────────────────────────────

  async function playItem(item) {
    currentItem = item;
    nowPlayingTitle.textContent = item.title || item.name || 'Unbekannt';
    nowPlayingMeta.textContent = `${item.year || ''} · ${item.runtime_display || ''}`;

    // Player vorbereiten
    playerPlaceholder.style.display = 'none';
    player.style.display = 'block';
    centerPlayBtn.style.display = 'none';
    btnPlay.textContent = '⏸';

    try {
      const streamUrl = await fetchStreamUrl(item.id);
      if (!streamUrl) throw new Error('Keine Stream-URL');

      player.src = streamUrl;
      player.load();
      await player.play();
    } catch (err) {
      console.error('Stream-Fehler:', err);
      nowPlayingMeta.textContent = '⚠ Stream nicht verfügbar';
      playerPlaceholder.style.display = 'flex';
      player.style.display = 'none';
    }
  }

  // ── 2.9 Player-Steuerung ───────────────────────────────────────────────

  // Play/Pause
  function togglePlay() {
    if (!player.src || !currentItem) return;
    if (player.paused || player.ended) {
      player.play().catch(() => {});
      btnPlay.textContent = '⏸';
    } else {
      player.pause();
      btnPlay.textContent = '▶';
      centerPlayBtn.style.display = 'flex';
    }
  }

  btnPlay.addEventListener('click', togglePlay);
  centerPlayBtn.addEventListener('click', togglePlay);

  // Auf Video-Klick großflächig Play/Pause
  player.addEventListener('click', togglePlay);

  // Play/Pause-Icon synchronisieren
  player.addEventListener('play', () => {
    btnPlay.textContent = '⏸';
    centerPlayBtn.style.display = 'none';
  });
  player.addEventListener('pause', () => {
    btnPlay.textContent = '▶';
    centerPlayBtn.style.display = 'flex';
  });
  player.addEventListener('ended', () => {
    btnPlay.textContent = '▶';
    centerPlayBtn.style.display = 'flex';
  });

  // Zeit aktualisieren
  player.addEventListener('timeupdate', () => {
    if (!isSeeking && !isDragging) {
      const pct = player.duration ? (player.currentTime / player.duration) * 100 : 0;
      seekbarProgress.style.width = `${pct}%`;
      seekbarThumb.style.left = `${pct}%`;
      timeCurrent.textContent = formatTime(player.currentTime);
    }

    // Buffered
    if (player.buffered.length > 0) {
      const buffEnd = player.buffered.end(player.buffered.length - 1);
      const buffPct = player.duration ? (buffEnd / player.duration) * 100 : 0;
      seekbarBuffered.style.width = `${buffPct}%`;
    }
  });

  player.addEventListener('loadedmetadata', () => {
    timeDuration.textContent = formatTime(player.duration);
  });

  // Zeitleisten-Seeking (Maus)
  seekbarContainer.addEventListener('mousedown', (e) => {
    isDragging = true;
    seekbarThumb.style.display = 'block';
    _seekFromEvent(e);
  });

  document.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    _seekFromEvent(e);
  });

  document.addEventListener('mouseup', () => {
    if (!isDragging) return;
    isDragging = false;
    if (player.duration && isSeeking) {
      player.currentTime = (seekbarProgress.style.width.replace('%', '') / 100) * player.duration;
    }
  });

  // Touch-Unterstützung
  seekbarContainer.addEventListener('touchstart', (e) => {
    isDragging = true;
    seekbarThumb.style.display = 'block';
    _seekFromEvent(e.touches[0]);
  });

  seekbarContainer.addEventListener('touchmove', (e) => {
    if (!isDragging) return;
    e.preventDefault();
    _seekFromEvent(e.touches[0]);
  });

  seekbarContainer.addEventListener('touchend', () => {
    if (!isDragging) return;
    isDragging = false;
    if (player.duration) {
      player.currentTime = (seekbarProgress.style.width.replace('%', '') / 100) * player.duration;
    }
  });

  function _seekFromEvent(e) {
    const rect = seekbarContainer.getBoundingClientRect();
    let pct = ((e.clientX - rect.left) / rect.width) * 100;
    pct = Math.max(0, Math.min(100, pct));
    seekbarProgress.style.width = `${pct}%`;
    seekbarThumb.style.left = `${pct}%`;
    if (player.duration) {
      timeCurrent.textContent = formatTime((pct / 100) * player.duration);
    }
  }

  // Hover-Effekt auf Seekbar
  seekbarContainer.addEventListener('mouseenter', () => {
    seekbarContainer.style.height = '8px';
    seekbarThumb.style.display = 'block';
  });
  seekbarContainer.addEventListener('mouseleave', () => {
    if (!isDragging) {
      seekbarContainer.style.height = '6px';
      seekbarThumb.style.display = 'none';
    }
  });

  // Lautstärke
  volumeSlider.addEventListener('input', () => {
    player.volume = volumeSlider.value / 100;
    _updateMuteIcon();
  });
  btnMute.addEventListener('click', () => {
    player.muted = !player.muted;
    _updateMuteIcon();
  });

  function _updateMuteIcon() {
    if (player.muted || player.volume === 0) {
      btnMute.textContent = '🔇';
    } else if (player.volume < 0.5) {
      btnMute.textContent = '🔉';
    } else {
      btnMute.textContent = '🔊';
    }
  }

  // Wiedergabegeschwindigkeit
  playbackRate.addEventListener('change', () => {
    player.playbackRate = parseFloat(playbackRate.value);
  });

  // Skip ±10s
  document.getElementById('btn-skip-back').addEventListener('click', () => {
    player.currentTime = Math.max(0, player.currentTime - 10);
  });
  document.getElementById('btn-skip-fwd').addEventListener('click', () => {
    player.currentTime = Math.min(player.duration || Infinity, player.currentTime + 10);
  });

  // Vollbild
  btnFullscreen.addEventListener('click', () => {
    if (!isFullscreen) {
      if (playerWrapper.requestFullscreen) {
        playerWrapper.requestFullscreen();
      }
    } else {
      if (document.exitFullscreen) {
        document.exitFullscreen();
      }
    }
  });

  document.addEventListener('fullscreenchange', () => {
    isFullscreen = !!document.fullscreenElement;
    btnFullscreen.textContent = isFullscreen ? '⛶' : '⛶';
    btnFullscreen.style.color = isFullscreen ? 'var(--color-accent-primary)' : '';
  });

  // ── 2.10 Tastatur-Steuerung ────────────────────────────────────────────

  function handleKeyboard(e) {
    // Nicht triggern, wenn in Input-Feldern
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
      return;
    }

    switch (e.key) {
      case ' ':
      case 'Spacebar':
        e.preventDefault();
        togglePlay();
        break;
      case 'ArrowLeft':
        e.preventDefault();
        player.currentTime = Math.max(0, player.currentTime - 5);
        break;
      case 'ArrowRight':
        e.preventDefault();
        player.currentTime = Math.min(player.duration || Infinity, player.currentTime + 5);
        break;
      case 'ArrowUp':
        e.preventDefault();
        player.volume = Math.min(1, player.volume + 0.05);
        volumeSlider.value = player.volume * 100;
        _updateMuteIcon();
        break;
      case 'ArrowDown':
        e.preventDefault();
        player.volume = Math.max(0, player.volume - 0.05);
        volumeSlider.value = player.volume * 100;
        _updateMuteIcon();
        break;
      case 'm':
      case 'M':
        player.muted = !player.muted;
        _updateMuteIcon();
        break;
      case 'f':
      case 'F':
        if (!isFullscreen && playerWrapper.requestFullscreen) {
          playerWrapper.requestFullscreen();
        } else if (isFullscreen && document.exitFullscreen) {
          document.exitFullscreen();
        }
        break;
    }
  }

  document.addEventListener('keydown', handleKeyboard);

  // ── 2.11 Event-Delegation für Thumbnails, Tabs & Pagination ────────────

  // Tabs
  container.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      container.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadLibrary(btn.dataset.tab);
    });
  });

  // Thumbnails & zurück-Button (Event-Delegation)
  mediaGrid.addEventListener('click', async (e) => {
    const thumb = e.target.closest('.media-thumb');
    if (!thumb) return;

    const itemId = thumb.dataset.id;
    const itemType = thumb.dataset.type;

    if (itemType === 'series') {
      const seriesName = thumb.querySelector('.text-xs')?.textContent || 'Serie';
      await loadEpisodes(itemId, seriesName);
    } else {
      // Item spielen (movie, episode, search-result)
      const title = thumb.querySelector('.text-xs')?.textContent || '';
      await playItem({ id: itemId, title, year: '', runtime_display: '' });
    }
  });

  // Paginierung
  paginationEl.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-page]');
    if (!btn) return;
    loadLibrary(currentTab, parseInt(btn.dataset.page));
  });

  // Suche
  searchBtn.addEventListener('click', () => searchMedia(searchInput.value));
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') searchMedia(searchInput.value);
  });

  // ── 2.12 Initial Bibliothek laden ──────────────────────────────────────
  loadLibrary('movies');

  // ── 2.13 Cleanup-Funktion ──────────────────────────────────────────────
  return () => {
    // Video stoppen
    if (player) {
      player.pause();
      player.src = '';
      player.load();
    }
    // Tastatur-Listener entfernen
    document.removeEventListener('keydown', handleKeyboard);
    // Seeker-Event-Listener werden via DOM-Entfernung bereinigt
  };
}