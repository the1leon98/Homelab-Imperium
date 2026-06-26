// FILE: frontend/static/js/views/music.js
/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * Musikarchiv — HTML5-Audio-Player mit Cover-Art & Playlist.
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Bietet:
 *   • Großes Album-Cover mit Neon-Glow & Spiegelungseffekt
 *   • Maßgeschneiderte Audio-Steuerelemente (Play/Pause/Prev/Next/Seek)
 *   • Zeitleiste mit Hover-Vergrößerung & Drag-Seeking
 *   • Scrollbare Playlist (Tracks aus der Datenbank)
 *   • Interpreten- & Alben-Navigation
 *   • Bibliothek-Scan-Trigger mit Fortschritts-Feedback
 *
 * Verwendete API-Endpunkte:
 *   GET  /api/music/tracks          — Alle Tracks
 *   GET  /api/music/artists         — Interpreten
 *   GET  /api/music/albums          — Alben
 *   GET  /api/music/search?q=       — Suche
 *   GET  /api/music/stats           — Statistiken
 *   GET  /api/music/stream/{id}     — Audio-Stream
 *   GET  /api/music/cover/{id}      — Cover-Bild
 *   POST /api/music/scan            — Bibliothek-Scan
 */

import { get, post } from '../api.js';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. KONSTANTEN
// ═══════════════════════════════════════════════════════════════════════════════

/** API-Basis für Cover & Stream */
const COVER_BASE = '/api/music/cover';
const STREAM_BASE = '/api/music/stream';

/** Cover-URL generieren */
function coverUrl(trackId) {
  return `${COVER_BASE}/${trackId}`;
}

/** Stream-URL generieren */
function streamUrl(trackId) {
  return `${STREAM_BASE}/${trackId}`;
}

/** Sekunden → "m:ss" */
function formatTime(sec) {
  if (!isFinite(sec) || sec < 0) return '0:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 2. HAUPTFUNKTION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Rendert den Musikplayer mit Cover-Art, Steuerelementen und Playlist.
 *
 * @param {HTMLElement} container
 * @returns {Function} Cleanup
 */
export async function showMusic(container) {
  // ── 2.1 Grundlayout ────────────────────────────────────────────────────
  container.innerHTML = `
    <div class="section-header">
      <div>
        <h1>Musikarchiv</h1>
        <p class="section-subtitle">Lokale MP3/FLAC-Sammlung — HTML5-Audio-Player</p>
      </div>
      <div class="btn-group">
        <button class="btn btn-secondary btn-pill tab-btn active" data-tab="tracks">🎵 Tracks</button>
        <button class="btn btn-secondary btn-pill tab-btn" data-tab="albums">💿 Alben</button>
        <button class="btn btn-secondary btn-pill tab-btn" data-tab="artists">🎤 Interpreten</button>
        <button class="btn btn-ghost" id="btn-scan-library" title="Bibliothek neu scannen">🔄 Scan</button>
      </div>
    </div>

    <div style="display:grid; grid-template-columns:1fr 340px; gap:var(--space-6);">

      <!-- Linke Spalte: Player + Playlist -->
      <div style="display:flex; flex-direction:column; gap:var(--space-6);">

        <!-- Player-Bereich -->
        <div class="glass-card" style="display:flex; flex-direction:column; align-items:center; padding:var(--space-8) var(--space-6);">
          <!-- Cover-Art mit Glow & Spiegelung -->
          <div style="position:relative; margin-bottom:var(--space-6);">
            <div id="cover-container"
                 style="width:260px; height:260px; border-radius:var(--radius-lg);
                        overflow:hidden; box-shadow:0 0 40px rgba(160,107,255,0.25),
                        0 0 80px rgba(160,107,255,0.10);
                        background:var(--color-bg-tertiary);
                        display:flex; align-items:center; justify-content:center;">
              <!-- Default-Placeholder -->
              <span id="cover-placeholder" style="font-size:4rem; opacity:0.2;">🎵</span>
              <img id="cover-image" src="" alt="Album Cover"
                   style="display:none; width:100%; height:100%; object-fit:cover;">
            </div>
            <!-- Spiegelung -->
            <div style="margin-top:4px; width:260px; height:40px;
                        background:linear-gradient(to bottom, rgba(160,107,255,0.08), transparent);
                        border-radius:0 0 var(--radius-lg) var(--radius-lg);
                        filter:blur(4px); transform:scaleY(-1); opacity:0.3;">
            </div>
          </div>

          <!-- Track-Info -->
          <div style="text-align:center; margin-bottom:var(--space-6); min-height:50px;">
            <h3 id="music-title" style="font-size:var(--text-xl); color:var(--color-text-primary);
                       font-weight:var(--font-bold); margin-bottom:var(--space-1);">
              Kein Track geladen
            </h3>
            <span id="music-artist" style="font-size:var(--text-sm); color:var(--color-text-secondary);">
              —
            </span>
            <span id="music-album" style="display:block; font-size:var(--text-xs); color:var(--color-text-tertiary); margin-top:2px;">
            </span>
          </div>

          <!-- Zeitleiste -->
          <div style="width:100%; max-width:400px; display:flex; align-items:center; gap:var(--space-3); margin-bottom:var(--space-4);">
            <span id="time-current" class="text-xs" style="color:var(--color-text-tertiary); min-width:36px;">0:00</span>
            <div id="seekbar-container"
                 style="flex:1; height:5px; background:rgba(255,255,255,0.08);
                        border-radius:var(--radius-full); cursor:pointer; position:relative;
                        transition:height 0.15s ease;">
              <div id="seekbar-progress"
                   style="position:absolute; height:100%;
                          background:var(--color-accent-primary);
                          border-radius:var(--radius-full); width:0%;
                          box-shadow:0 0 6px rgba(160,107,255,0.40);"></div>
              <div id="seekbar-thumb"
                   style="position:absolute; top:50%; transform:translate(-50%,-50%);
                          width:13px; height:13px; border-radius:var(--radius-full);
                          background:var(--color-accent-primary);
                          box-shadow:0 0 10px rgba(160,107,255,0.60);
                          left:0%; display:none;"></div>
            </div>
            <span id="time-duration" class="text-xs" style="color:var(--color-text-tertiary); min-width:36px;">0:00</span>
          </div>

          <!-- Steuerelemente -->
          <div style="display:flex; align-items:center; gap:var(--space-4);">
            <button id="btn-shuffle" class="btn-icon btn-ghost" title="Zufallswiedergabe">🔀</button>
            <button id="btn-prev" class="btn-icon btn-ghost" title="Vorheriger Track">⏮</button>
            <button id="btn-play"
                    style="width:56px; height:56px; border-radius:var(--radius-full);
                           background:var(--color-accent-primary);
                           border:none; color:#fff; font-size:22px; cursor:pointer;
                           display:flex; align-items:center; justify-content:center;
                           box-shadow:0 0 20px rgba(160,107,255,0.35);
                           transition:transform 0.15s ease, box-shadow 0.15s ease;"
                    title="Play / Pause">
              ▶
            </button>
            <button id="btn-next" class="btn-icon btn-ghost" title="Nächster Track">⏭</button>
            <button id="btn-repeat" class="btn-icon btn-ghost" title="Wiederholen">🔁</button>
          </div>

          <!-- Lautstärke -->
          <div style="display:flex; align-items:center; gap:var(--space-3); margin-top:var(--space-4); width:100%; max-width:300px;">
            <span style="font-size:14px; color:var(--color-text-tertiary);">🔈</span>
            <input type="range" id="volume-slider" min="0" max="100" value="80"
                   style="flex:1; accent-color:var(--color-accent-primary);">
            <span style="font-size:14px; color:var(--color-text-tertiary);">🔊</span>
          </div>
        </div>

        <!-- HTML5-Audio-Element (versteckt) -->
        <audio id="audio-player" preload="metadata"></audio>

        <!-- Playlist / Grid -->
        <div class="glass-card" style="flex:1; min-height:0; display:flex; flex-direction:column;">
          <div class="card-header">
            <h3 id="playlist-title">🎵 Alle Tracks</h3>
            <div style="display:flex; gap:var(--space-2);">
              <input type="text" id="music-search-input" class="form-input"
                     placeholder="Suchen…" style="width:180px; font-size:var(--text-xs); padding:4px 10px;">
            </div>
          </div>
          <div id="playlist-container" style="overflow-y:auto; flex:1; min-height:200px;">
            <span class="text-xs text-tertiary" style="display:block; text-align:center; padding:var(--space-8);">
              Lade Musikbibliothek…
            </span>
          </div>
        </div>
      </div>

      <!-- Rechte Spalte: Statistiken & Scan-Status -->
      <div style="display:flex; flex-direction:column; gap:var(--space-4);">
        <!-- Bibliotheks-Statistiken -->
        <div class="glass-card subtle">
          <div class="card-header"><h3>📊 Bibliothek</h3></div>
          <div id="library-stats" style="display:flex; flex-direction:column; gap:var(--space-2);">
            <span class="text-xs text-tertiary">Wird geladen…</span>
          </div>
        </div>

        <!-- Scan-Status -->
        <div class="glass-card subtle" id="scan-status-card" style="display:none;">
          <div class="card-header"><h3>🔄 Scan-Status</h3></div>
          <div id="scan-status-content" class="text-xs" style="color:var(--color-text-tertiary);"></div>
        </div>

        <!-- Gerade gespielter Track Details -->
        <div class="glass-card subtle" id="now-playing-details" style="display:none;">
          <div class="card-header"><h3>📋 Details</h3></div>
          <div id="track-details-content" class="text-xs" style="display:flex; flex-direction:column; gap:var(--space-1);">
          </div>
        </div>
      </div>
    </div>
  `;

  // ── 2.2 DOM-Referenzen ─────────────────────────────────────────────────
  const audio = document.getElementById('audio-player');
  const coverImage = document.getElementById('cover-image');
  const coverPlaceholder = document.getElementById('cover-placeholder');
  const musicTitle = document.getElementById('music-title');
  const musicArtist = document.getElementById('music-artist');
  const musicAlbum = document.getElementById('music-album');
  const seekbarContainer = document.getElementById('seekbar-container');
  const seekbarProgress = document.getElementById('seekbar-progress');
  const seekbarThumb = document.getElementById('seekbar-thumb');
  const timeCurrent = document.getElementById('time-current');
  const timeDuration = document.getElementById('time-duration');
  const btnPlay = document.getElementById('btn-play');
  const volumeSlider = document.getElementById('volume-slider');
  const playlistContainer = document.getElementById('playlist-container');
  const playlistTitle = document.getElementById('playlist-title');
  const searchInput = document.getElementById('music-search-input');

  // ── 2.3 Zustand ────────────────────────────────────────────────────────
  let currentTrackIndex = -1;
  let currentPlaylist = [];
  let currentTab = 'tracks';
  let isDragging = false;
  let shuffle = false;
  let repeat = false;

  // ── 2.4 Playlist laden ─────────────────────────────────────────────────

  async function loadPlaylist(tab = 'tracks', query = '') {
    currentTab = tab;

    try {
      let data;

      if (query) {
        data = await get('/music/search', { q: query }, { skipToast: true });
        currentPlaylist = data.tracks || data || [];
        playlistTitle.textContent = `🔍 Suchergebnisse: "${query}"`;
      } else if (tab === 'tracks') {
        data = await get('/music/tracks', { limit: 100 }, { skipToast: true });
        currentPlaylist = data.tracks || data || [];
        playlistTitle.textContent = '🎵 Alle Tracks';
      } else if (tab === 'albums') {
        data = await get('/music/albums', {}, { skipToast: true });
        currentPlaylist = data.albums || data || [];
        playlistTitle.textContent = '💿 Alben';
      } else if (tab === 'artists') {
        data = await get('/music/artists', {}, { skipToast: true });
        currentPlaylist = data.artists || data || [];
        playlistTitle.textContent = '🎤 Interpreten';
      }

      _renderPlaylist(currentPlaylist, tab);
    } catch {
      playlistContainer.innerHTML = `<span class="text-xs" style="color:var(--color-danger); display:block; text-align:center; padding:var(--space-6);">Fehler beim Laden der Bibliothek</span>`;
    }
  }

  function _renderPlaylist(items, tab) {
    if (!items.length) {
      playlistContainer.innerHTML = `<span class="text-xs text-tertiary" style="display:block; text-align:center; padding:var(--space-6);">Keine Einträge</span>`;
      return;
    }

    if (tab === 'tracks' || tab === 'search') {
      // Track-Liste
      playlistContainer.innerHTML = items.map((t, i) => `
        <div class="track-row" data-index="${i}"
             style="display:flex; align-items:center; gap:var(--space-3);
                    padding:var(--space-2) var(--space-4); cursor:pointer;
                    border-bottom:1px solid var(--color-border-subtle);
                    transition:background 0.1s ease;
                    ${i === currentTrackIndex ? 'background:rgba(160,107,255,0.08);' : ''}">
          <!-- Mini-Cover -->
          <div style="width:36px; height:36px; border-radius:var(--radius-xs);
                      background:var(--color-bg-tertiary); overflow:hidden; flex-shrink:0;
                      display:flex; align-items:center; justify-content:center;">
            <img src="${coverUrl(t.id)}" alt="" loading="lazy"
                 style="width:100%; height:100%; object-fit:cover;"
                 onerror="this.style.display='none';this.nextElementSibling.style.display='block';">
            <span style="display:none; font-size:14px; opacity:0.3;">🎵</span>
          </div>
          <!-- Info -->
          <div style="flex:1; min-width:0;">
            <div class="text-xs" style="color:var(--color-text-primary); font-weight:var(--font-medium);
                        overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
              ${_escapeHtml(t.title || t.name || 'Unbekannt')}
            </div>
            <div class="text-xs" style="color:var(--color-text-tertiary);">
              ${_escapeHtml(t.artist || '—')}${t.album ? ' · ' + _escapeHtml(t.album) : ''}
              ${t.duration_display ? ' · ' + t.duration_display : ''}
            </div>
          </div>
          <!-- Aktuell-spielend-Indikator -->
          ${i === currentTrackIndex && !audio.paused ? '<span style="color:var(--color-accent-primary);">♪</span>' : ''}
        </div>
      `).join('');
    } else if (tab === 'albums') {
      // Album-Grid
      playlistContainer.innerHTML = `<div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(140px, 1fr)); gap:var(--space-3); padding:var(--space-3);">` +
        items.map(a => `
          <div class="glass-card subtle" style="cursor:pointer; padding:var(--space-3); text-align:center;"
               data-album="${_escapeAttr(a.name || '')}">
            <div style="width:100%; aspect-ratio:1; border-radius:var(--radius-sm);
                        background:var(--color-bg-tertiary); overflow:hidden; margin-bottom:var(--space-2);">
              <img src="${coverUrl(a.cover_track_id || a.id)}" alt="" loading="lazy"
                   style="width:100%; height:100%; object-fit:cover;"
                   onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
              <span style="display:none; font-size:2rem; opacity:0.2; align-items:center; justify-content:center; height:100%;">💿</span>
            </div>
            <div class="text-xs" style="color:var(--color-text-primary); font-weight:var(--font-medium);
                        overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
              ${_escapeHtml(a.name || 'Unbekannt')}
            </div>
            <div class="text-xs" style="color:var(--color-text-tertiary);">
              ${_escapeHtml(a.artist || '—')} · ${a.track_count || 0} Tracks
            </div>
          </div>
        `).join('') + '</div>';
    } else if (tab === 'artists') {
      // Interpreten-Liste
      playlistContainer.innerHTML = items.map(a => `
        <div class="track-row" data-artist="${_escapeAttr(a.name || '')}"
             style="display:flex; align-items:center; gap:var(--space-3);
                    padding:var(--space-3) var(--space-4); cursor:pointer;
                    border-bottom:1px solid var(--color-border-subtle);">
          <div style="width:40px; height:40px; border-radius:var(--radius-full);
                      background:linear-gradient(135deg, var(--color-accent-primary), var(--color-accent-cyan));
                      display:flex; align-items:center; justify-content:center;
                      font-size:18px; color:#fff; flex-shrink:0;">🎤</div>
          <div>
            <div class="text-sm" style="color:var(--color-text-primary); font-weight:var(--font-medium);">
              ${_escapeHtml(a.name || 'Unbekannt')}
            </div>
            <div class="text-xs" style="color:var(--color-text-tertiary);">
              ${a.album_count || 0} Alben · ${a.track_count || 0} Tracks
            </div>
          </div>
        </div>
      `).join('');
    }
  }

  // ── 2.5 Playlist-Interaktion ───────────────────────────────────────────

  playlistContainer.addEventListener('click', (e) => {
    const trackRow = e.target.closest('.track-row');
    if (!trackRow) return;

    const index = parseInt(trackRow.dataset.index);
    if (!isNaN(index) && currentTab === 'tracks') {
      _playTrack(index);
    }
  });

  // ── 2.6 Track abspielen ────────────────────────────────────────────────

  function _playTrack(index) {
    if (!currentPlaylist.length || index < 0 || index >= currentPlaylist.length) return;
    currentTrackIndex = index;
    const track = currentPlaylist[index];

    // UI aktualisieren
    musicTitle.textContent = track.title || track.name || 'Unbekannt';
    musicArtist.textContent = track.artist || '—';
    musicAlbum.textContent = track.album || '';

    // Cover laden
    const url = coverUrl(track.id);
    coverImage.src = url;
    coverImage.style.display = 'block';
    coverPlaceholder.style.display = 'none';
    coverImage.onerror = () => {
      coverImage.style.display = 'none';
      coverPlaceholder.style.display = 'flex';
    };

    // Track-Details
    _updateTrackDetails(track);

    // Audio abspielen
    audio.src = streamUrl(track.id);
    audio.load();
    audio.play().catch(() => {});
    btnPlay.textContent = '⏸';

    // Playlist neu rendern für aktiven Indikator
    _renderPlaylist(currentPlaylist, currentTab);
  }

  function _playNext() {
    if (!currentPlaylist.length) return;
    let next = currentTrackIndex + 1;
    if (shuffle) {
      next = Math.floor(Math.random() * currentPlaylist.length);
    } else if (next >= currentPlaylist.length) {
      next = repeat ? 0 : -1;
    }
    if (next >= 0) _playTrack(next);
  }

  function _playPrev() {
    if (!currentPlaylist.length) return;
    let prev = currentTrackIndex - 1;
    if (prev < 0) prev = repeat ? currentPlaylist.length - 1 : 0;
    _playTrack(prev);
  }

  // ── 2.7 Audio-Steuerung ────────────────────────────────────────────────

  btnPlay.addEventListener('click', () => {
    if (!audio.src || !currentPlaylist.length) return;
    if (audio.paused || audio.ended) {
      audio.play().catch(() => {});
    } else {
      audio.pause();
    }
  });

  audio.addEventListener('play', () => { btnPlay.textContent = '⏸'; });
  audio.addEventListener('pause', () => { btnPlay.textContent = '▶'; });
  audio.addEventListener('ended', () => {
    btnPlay.textContent = '▶';
    if (repeat && currentTrackIndex >= 0) {
      _playTrack(currentTrackIndex);
    } else {
      _playNext();
    }
  });

  // Zeit & Seekbar
  audio.addEventListener('timeupdate', () => {
    if (!isDragging) {
      const pct = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
      seekbarProgress.style.width = `${pct}%`;
      seekbarThumb.style.left = `${pct}%`;
      timeCurrent.textContent = formatTime(audio.currentTime);
    }
  });

  audio.addEventListener('loadedmetadata', () => {
    timeDuration.textContent = formatTime(audio.duration);
  });

  // Seekbar-Drag
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
    if (audio.duration) {
      audio.currentTime = (parseFloat(seekbarProgress.style.width) / 100) * audio.duration;
    }
  });

  // Touch
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
    if (audio.duration) {
      audio.currentTime = (parseFloat(seekbarProgress.style.width) / 100) * audio.duration;
    }
  });

  function _seekFromEvent(e) {
    const rect = seekbarContainer.getBoundingClientRect();
    let pct = ((e.clientX - rect.left) / rect.width) * 100;
    pct = Math.max(0, Math.min(100, pct));
    seekbarProgress.style.width = `${pct}%`;
    seekbarThumb.style.left = `${pct}%`;
    if (audio.duration) timeCurrent.textContent = formatTime((pct / 100) * audio.duration);
  }

  // Hover-Effekt
  seekbarContainer.addEventListener('mouseenter', () => {
    seekbarContainer.style.height = '7px';
    seekbarThumb.style.display = 'block';
  });
  seekbarContainer.addEventListener('mouseleave', () => {
    if (!isDragging) {
      seekbarContainer.style.height = '5px';
      seekbarThumb.style.display = 'none';
    }
  });

  // Lautstärke
  volumeSlider.addEventListener('input', () => {
    audio.volume = volumeSlider.value / 100;
  });
  audio.volume = 0.8;

  // Prev / Next
  document.getElementById('btn-prev').addEventListener('click', _playPrev);
  document.getElementById('btn-next').addEventListener('click', _playNext);

  // Shuffle
  document.getElementById('btn-shuffle').addEventListener('click', function () {
    shuffle = !shuffle;
    this.style.color = shuffle ? 'var(--color-accent-primary)' : '';
    this.style.textShadow = shuffle ? '0 0 8px rgba(160,107,255,0.5)' : '';
  });

  // Repeat
  document.getElementById('btn-repeat').addEventListener('click', function () {
    repeat = !repeat;
    this.style.color = repeat ? 'var(--color-accent-primary)' : '';
    this.style.textShadow = repeat ? '0 0 8px rgba(160,107,255,0.5)' : '';
  });

  // Tastatur
  function handleKeyboard(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    switch (e.key) {
      case ' ': e.preventDefault(); btnPlay.click(); break;
      case 'ArrowLeft': e.preventDefault(); audio.currentTime = Math.max(0, audio.currentTime - 5); break;
      case 'ArrowRight': e.preventDefault(); audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 5); break;
      case 'ArrowUp': e.preventDefault(); audio.volume = Math.min(1, audio.volume + 0.05); volumeSlider.value = audio.volume * 100; break;
      case 'ArrowDown': e.preventDefault(); audio.volume = Math.max(0, audio.volume - 0.05); volumeSlider.value = audio.volume * 100; break;
    }
  }
  document.addEventListener('keydown', handleKeyboard);

  // ── 2.8 Track-Details ──────────────────────────────────────────────────

  function _updateTrackDetails(track) {
    const card = document.getElementById('now-playing-details');
    const content = document.getElementById('track-details-content');
    if (!track) { card.style.display = 'none'; return; }
    card.style.display = 'block';
    content.innerHTML = `
      <div class="flex justify-between"><span class="text-tertiary">Titel</span><span style="color:var(--color-text-primary);">${_escapeHtml(track.title || '—')}</span></div>
      <div class="flex justify-between"><span class="text-tertiary">Interpret</span><span style="color:var(--color-text-primary);">${_escapeHtml(track.artist || '—')}</span></div>
      <div class="flex justify-between"><span class="text-tertiary">Album</span><span style="color:var(--color-text-primary);">${_escapeHtml(track.album || '—')}</span></div>
      <div class="flex justify-between"><span class="text-tertiary">Genre</span><span style="color:var(--color-text-primary);">${_escapeHtml(track.genre || '—')}</span></div>
      <div class="flex justify-between"><span class="text-tertiary">Dauer</span><span style="color:var(--color-text-primary);">${track.duration_display || '—'}</span></div>
      ${track.bitrate ? `<div class="flex justify-between"><span class="text-tertiary">Bitrate</span><span style="color:var(--color-text-primary);">${track.bitrate} kbps</span></div>` : ''}
      ${track.format ? `<div class="flex justify-between"><span class="text-tertiary">Format</span><span style="color:var(--color-text-primary); text-transform:uppercase;">${_escapeHtml(track.format)}</span></div>` : ''}
    `;
  }

  // ── 2.9 Tabs ───────────────────────────────────────────────────────────

  container.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      container.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadPlaylist(btn.dataset.tab);
    });
  });

  // ── 2.10 Suche ─────────────────────────────────────────────────────────

  let searchTimeout;
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      const query = searchInput.value.trim();
      loadPlaylist(query ? 'search' : currentTab, query);
    }, 400);
  });

  // ── 2.11 Bibliothek-Scan ───────────────────────────────────────────────

  document.getElementById('btn-scan-library').addEventListener('click', async () => {
    const statusCard = document.getElementById('scan-status-card');
    const statusContent = document.getElementById('scan-status-content');
    statusCard.style.display = 'block';
    statusContent.innerHTML = `<div class="flex items-center gap-2"><div class="spinner"></div><span>Scanne Musikbibliothek…</span></div>`;

    try {
      const result = await post('/music/scan', { force: false }, { skipToast: true });
      statusContent.innerHTML = `<span style="color:var(--color-success);">✓ Scan gestartet — ${result.message || 'Bibliothek wird im Hintergrund indiziert.'}</span>`;
      // Nach 3s Status ausblenden
      setTimeout(() => { statusCard.style.display = 'none'; }, 5000);
    } catch (err) {
      statusContent.innerHTML = `<span style="color:var(--color-danger);">✕ Scan fehlgeschlagen: ${_escapeHtml(err.message)}</span>`;
    }
  });

  // ── 2.12 Bibliotheks-Statistiken ───────────────────────────────────────

  async function _loadStats() {
    try {
      const stats = await get('/music/stats', {}, { skipToast: true, skipLoader: true });
      const el = document.getElementById('library-stats');
      el.innerHTML = `
        <div class="stat-widget" style="align-items:center; padding:var(--space-3) 0;">
          <span class="stat-value" style="font-size:var(--text-2xl); color:var(--color-accent-primary);">${stats.total_tracks || 0}</span>
          <span class="stat-label">Tracks</span>
        </div>
        <div class="flex justify-between text-xs">
          <span class="text-tertiary">Interpreten</span><span style="color:var(--color-text-primary);">${stats.total_artists || 0}</span>
        </div>
        <div class="flex justify-between text-xs">
          <span class="text-tertiary">Alben</span><span style="color:var(--color-text-primary);">${stats.total_albums || 0}</span>
        </div>
        <div class="flex justify-between text-xs">
          <span class="text-tertiary">Genres</span><span style="color:var(--color-text-primary);">${stats.total_genres || 0}</span>
        </div>
        ${stats.total_duration ? `
        <div class="flex justify-between text-xs">
          <span class="text-tertiary">Gesamtdauer</span><span style="color:var(--color-text-primary);">${stats.total_duration}</span>
        </div>` : ''}
      `;
    } catch {
      document.getElementById('library-stats').innerHTML = `<span class="text-xs text-tertiary">Nicht verfügbar</span>`;
    }
  }

  // ── 2.13 Initialisierung ───────────────────────────────────────────────
  await Promise.all([loadPlaylist('tracks'), _loadStats()]);

  // ── 2.14 Cleanup ───────────────────────────────────────────────────────
  return () => {
    document.removeEventListener('keydown', handleKeyboard);
    audio.pause();
    audio.src = '';
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

function _escapeAttr(str) {
  return String(str).replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}