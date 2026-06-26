// FILE: frontend/static/js/api.js
/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * Zentraler HTTP-Fetch-Wrapper für das Homelab-Imperium.
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Sämtliche Frontend↔Backend-Kommunikation läuft ausschließlich über dieses
 * Modul. Es bietet:
 *
 *   • Automatische JSON-Serialisierung / Deserialisierung
 *   • Konfigurierbare Request-Timeouts via AbortController
 *   • Globalen Ladeindikator (Progress-Bar) während aktiver Requests
 *   • Strukturiertes Error-Handling mit ApiError-Klasse
 *   • Automatische Toast-Benachrichtigungen bei Fehlern
 *   • Exponentielles Backoff-Retry für GET-Requests
 *   • Convenience-Methoden: get(), post(), put(), delete()
 *   • Spezialmethoden: upload(), download(), stream()
 *   • Request-Deduplizierung für identische GET-Requests (kurzzeitig)
 *
 * Verwendung:
 *   import { api, get, post, put, del } from './api.js';
 *   const data = await get('/media/movies');
 *   const result = await post('/finance/transactions', { amount: 42 });
 */

// ═══════════════════════════════════════════════════════════════════════════════
// 1. KONFIGURATION & KONSTANTEN
// ═══════════════════════════════════════════════════════════════════════════════

/** Basis-URL des FastAPI-Backends (relativ, via Caddy-Reverse-Proxy) */
const BASE_URL = '/api';

/** Standard-Timeout für Requests in Millisekunden (15 Sekunden) */
const DEFAULT_TIMEOUT_MS = 15_000;

/** Längeres Timeout für Datei-Uploads und KI-Streaming (5 Minuten) */
const LONG_TIMEOUT_MS = 300_000;

/** Maximale Anzahl an Retry-Versuchen bei Netzwerkfehlern */
const MAX_RETRIES = 2;

/** Basis-Verzögerung für exponentielles Backoff (ms) */
const RETRY_BASE_DELAY_MS = 500;

/** TTL für Request-Deduplizierungs-Cache (ms) */
const DEDUPE_TTL_MS = 300;

// ═══════════════════════════════════════════════════════════════════════════════
// 2. API-ERROR-KLASSE
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Strukturierter API-Fehler mit HTTP-Status, Nachricht und optionalem
 * Server-seitigem Detail (FastAPI-Validation-Errors etc.).
 */
export class ApiError extends Error {
  /**
   * @param {string} message  — Lesbare Fehlermeldung
   * @param {number} status   — HTTP-Statuscode (0 = Netzwerkfehler/Timeout)
   * @param {object} [detail] — Vom Server zurückgegebener Detail-Payload
   */
  constructor(message, status = 0, detail = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// 3. GLOBALER LADEINDIKATOR
// ═══════════════════════════════════════════════════════════════════════════════

/** Zähler für aktuell laufende Requests */
let _activeRequests = 0;

/** Referenz auf das Progress-Bar-Element (wird lazy erzeugt) */
let _loaderBar = null;

/**
 * Erzeugt oder gibt die globale Ladeleiste zurück.
 * Eine dünne Neon-lila Linie am oberen Rand des Viewports,
 * die bei aktiven Requests animiert wird.
 *
 * @returns {HTMLElement}
 */
function _getLoaderBar() {
  if (!_loaderBar) {
    _loaderBar = document.createElement('div');
    _loaderBar.id = 'global-loader-bar';
    _loaderBar.setAttribute('role', 'progressbar');
    _loaderBar.setAttribute('aria-label', 'Ladevorgang aktiv');
    _loaderBar.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      height: 3px;
      z-index: 9999;
      pointer-events: none;
      background: linear-gradient(90deg,
        var(--color-accent-primary, #a06bff),
        var(--color-accent-cyan, #00f5d4),
        var(--color-accent-primary, #a06bff)
      );
      background-size: 200% 100%;
      animation: loader-bar-slide 1.8s ease-in-out infinite;
      opacity: 0;
      transition: opacity 200ms ease;
    `;
    document.body.appendChild(_loaderBar);

    // Keyframe für die Ladebalken-Animation (nur einmal injizieren)
    if (!document.getElementById('loader-bar-keyframes')) {
      const style = document.createElement('style');
      style.id = 'loader-bar-keyframes';
      style.textContent = `
        @keyframes loader-bar-slide {
          0%   { width: 0%; left: 0; }
          40%  { width: 40%; left: 30%; }
          70%  { width: 25%; left: 60%; }
          100% { width: 0%; left: 100%; }
        }
      `;
      document.head.appendChild(style);
    }
  }
  return _loaderBar;
}

/**
 * Erhöht den Zähler aktiver Requests und blendet die Ladeleiste ein.
 * Wird automatisch vor jedem Request aufgerufen.
 */
function _requestStarted() {
  _activeRequests++;
  if (_activeRequests === 1) {
    const bar = _getLoaderBar();
    bar.style.opacity = '1';
  }
}

/**
 * Verringert den Zähler aktiver Requests und blendet die Ladeleiste aus,
 * sobald keine Requests mehr aktiv sind.
 * Wird automatisch nach Abschluss jedes Requests aufgerufen (finally-Block).
 */
function _requestFinished() {
  _activeRequests = Math.max(0, _activeRequests - 1);
  if (_activeRequests === 0) {
    const bar = _getLoaderBar();
    bar.style.opacity = '0';
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// 4. TOAST-BENACHRICHTIGUNGEN
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Zeigt eine Toast-Benachrichtigung im #toast-container an.
 * Der Toast verschwindet automatisch nach `duration` Millisekunden.
 *
 * @param {string} message — Anzuzeigende Nachricht
 * @param {'error'|'success'|'warning'|'info'} type — Toast-Typ (bestimmt die Farbe)
 * @param {number} [duration=5000] — Anzeigedauer in ms
 */
export function showToast(message, type = 'info', duration = 5000) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.setAttribute('role', 'alert');

  // Icon je nach Typ
  const icons = { error: '✕', success: '✓', warning: '⚠', info: 'ℹ' };
  toast.innerHTML = `
    <span class="toast-icon">${icons[type] || icons.info}</span>
    <span class="toast-message">${_escapeHtml(message)}</span>
  `;

  container.appendChild(toast);

  // Automatisches Ausblenden mit Slide-Out-Animation
  setTimeout(() => {
    toast.style.animation = 'slideOutRight 300ms ease-in both';
    toast.addEventListener('animationend', () => toast.remove());
  }, duration);
}

/**
 * Escape HTML-Sonderzeichen, um XSS in Toast-Nachrichten zu verhindern.
 *
 * @param {string} str — Rohstring
 * @returns {string} — Bereinigter String
 */
function _escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 5. REQUEST-DEDUPLIZIERUNG (flüchtiger Cache für identische GET-Requests)
// ═══════════════════════════════════════════════════════════════════════════════

/** @type {Map<string, {promise: Promise, timestamp: number}>} */
const _dedupeCache = new Map();

/**
 * Prüft, ob ein identischer GET-Request noch in-flight ist, und gibt
 * dessen Promise zurück, um doppelte Netzwerkanfragen zu vermeiden.
 *
 * @param {string} cacheKey — Eindeutiger Schlüssel (URL+serialisierte Optionen)
 * @returns {Promise|null} — Das laufende Promise oder null
 */
function _getDeduped(cacheKey) {
  const entry = _dedupeCache.get(cacheKey);
  if (entry && Date.now() - entry.timestamp < DEDUPE_TTL_MS) {
    return entry.promise;
  }
  return null;
}

/**
 * Registriert ein laufendes GET-Request-Promise im Deduplizierungs-Cache.
 *
 * @param {string} cacheKey
 * @param {Promise} promise
 */
function _setDeduped(cacheKey, promise) {
  _dedupeCache.set(cacheKey, { promise, timestamp: Date.now() });
}

/**
 * Entfernt einen Eintrag aus dem Deduplizierungs-Cache.
 *
 * @param {string} cacheKey
 */
function _clearDeduped(cacheKey) {
  _dedupeCache.delete(cacheKey);
}

// ═══════════════════════════════════════════════════════════════════════════════
// 6. KERN-FUNKTION: apiRequest()
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Führt einen HTTP-Request gegen das FastAPI-Backend aus.
 *
 * Features:
 *  - Automatisches JSON-Encoding des Bodys
 *  - AbortController-basiertes Timeout
 *  - Globaler Ladeindikator
 *  - Strukturierte ApiError-Instanzen
 *  - Exponentielles Backoff-Retry bei GET-Requests
 *  - Request-Deduplizierung für identische GET-Requests
 *
 * @param {string} endpoint — API-Pfad relativ zu /api (z. B. "/media/movies")
 * @param {object} [options={}] — Fetch-Optionen (method, body, headers, signal, …)
 * @param {object} [config={}] — Erweiterte Konfiguration
 * @param {number} [config.timeout] — Timeout in ms (default: 15000)
 * @param {boolean} [config.skipLoader] — Ladebalken für diesen Request unterdrücken
 * @param {boolean} [config.rawResponse] — Gibt das nackte Response-Objekt zurück (kein JSON-Parsing)
 * @param {boolean} [config.skipToast] — Keine Fehler-Toasts anzeigen
 * @param {number} [config.retries] — Anzahl Retry-Versuche (nur GET, default: 2)
 * @returns {Promise<any>} — Geparste JSON-Antwort oder raw Response
 * @throws {ApiError} — Bei Netzwerkfehlern, Timeouts oder HTTP-Fehlern
 */
export async function apiRequest(endpoint, options = {}, config = {}) {
  const {
    timeout = DEFAULT_TIMEOUT_MS,
    skipLoader = false,
    rawResponse = false,
    skipToast = false,
    retries = MAX_RETRIES
  } = config;

  const url = `${BASE_URL}${endpoint}`;
  const method = (options.method || 'GET').toUpperCase();

  // ── Request-Body serialisieren ─────────────────────────────────────────
  // Wenn kein Content-Type gesetzt und der Body kein FormData/Blob ist → JSON
  if (options.body !== undefined &&
      typeof options.body !== 'string' &&
      !(options.body instanceof FormData) &&
      !(options.body instanceof Blob) &&
      !(options.body instanceof URLSearchParams)) {
    options.body = JSON.stringify(options.body);
  }

  // ── Header zusammenbauen ──────────────────────────────────────────────
  const headers = new Headers(options.headers || {});

  // JSON-Content-Type nur setzen, wenn nicht explizit anders angegeben
  // und kein FormData/Blob-Body vorliegt
  if (!headers.has('Content-Type') &&
      !(options.body instanceof FormData) &&
      !(options.body instanceof Blob)) {
    headers.set('Content-Type', 'application/json');
  }

  // Accept-Header
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json');
  }

  // ── Timeout via AbortController ────────────────────────────────────────
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  // Externes Signal mit dem Timeout-Signal kombinieren
  if (options.signal) {
    options.signal.addEventListener('abort', () => controller.abort());
  }

  // ── Deduplizierung für GET-Requests ───────────────────────────────────
  const cacheKey = (method === 'GET')
    ? `${url}|${headers.get('Accept') || ''}`
    : '';

  if (cacheKey) {
    const deduped = _getDeduped(cacheKey);
    if (deduped) return deduped;
  }

  // ── Request ausführen ──────────────────────────────────────────────────
  const executeRequest = async () => {
    if (!skipLoader) _requestStarted();

    try {
      const response = await fetch(url, {
        ...options,
        headers,
        signal: controller.signal
      });

      clearTimeout(timeoutId);

      // Rohantwort zurückgeben (für SSE, Downloads etc.)
      if (rawResponse) return response;

      // 204 No Content — Erfolg ohne Body
      if (response.status === 204) return null;

      // ── Fehlerhafte HTTP-Statuscodes ──────────────────────────────────
      if (!response.ok) {
        let detail = null;
        try {
          // Versuche, den FastAPI-Fehler-Detail zu parsen
          detail = await response.json();
        } catch {
          // Kein JSON-Body — Text als Fallback
          try {
            detail = { message: await response.text() };
          } catch {
            detail = { message: response.statusText };
          }
        }

        const serverMsg = detail?.detail || detail?.message || response.statusText;
        throw new ApiError(
          `[${response.status}] ${serverMsg}`,
          response.status,
          detail
        );
      }

      // ── Erfolgreiche Antwort parsen ───────────────────────────────────
      const contentType = response.headers.get('Content-Type') || '';

      if (contentType.includes('application/json')) {
        return await response.json();
      }

      // Fallback: Text
      return await response.text();

    } catch (error) {
      clearTimeout(timeoutId);

      // AbortController-Timeout als eigenen Fehlertyp behandeln
      if (error.name === 'AbortError' && !options.signal?.aborted) {
        throw new ApiError(
          `Request-Timeout nach ${timeout}ms: ${method} ${endpoint}`,
          408
        );
      }

      // ApiError unverändert durchreichen
      if (error instanceof ApiError) throw error;

      // Netzwerkfehler
      throw new ApiError(
        `Netzwerkfehler: ${error.message || 'Unbekannter Fehler'}`,
        0
      );

    } finally {
      if (!skipLoader) _requestFinished();
    }
  };

  // ── Retry-Logik (nur für GET-Requests) ────────────────────────────────
  const requestPromise = (async () => {
    let lastError = null;

    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        return await executeRequest();
      } catch (error) {
        lastError = error;

        // Nur bei Netzwerkfehlern (status === 0) oder 5xx wiederholen
        const shouldRetry = method === 'GET' &&
                            attempt < retries &&
                            (error.status === 0 || (error.status >= 500 && error.status < 600));

        if (!shouldRetry) break;

        // Exponentielles Backoff: 500ms, 1000ms, 2000ms, …
        const delay = RETRY_BASE_DELAY_MS * Math.pow(2, attempt);
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }

    throw lastError;
  })();

  // ── Im Deduplizierungs-Cache registrieren ────────────────────────────
  if (cacheKey) {
    _setDeduped(cacheKey, requestPromise);
    // Promise nach Abschluss aus Cache entfernen (Erfolg oder Fehler)
    requestPromise.finally(() => _clearDeduped(cacheKey));
  }

  // Fehler-Toast anzeigen (außerhalb des Promises, damit Caller es trotzdem catchen kann)
  return requestPromise.catch(error => {
    if (!skipToast && error instanceof ApiError) {
      const toastMsg = error.status === 0
        ? 'Verbindung zum Server fehlgeschlagen'
        : error.message;
      showToast(toastMsg, 'error');
    }
    throw error; // Weiterwerfen für den Caller
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// 7. CONVENIENCE-METHODEN
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * HTTP GET — JSON-Daten vom Backend abrufen.
 *
 * @param {string} endpoint — API-Pfad (z. B. "/media/movies")
 * @param {object} [params] — Query-Parameter als Key-Value-Objekt
 * @param {object} [config] — Erweiterte Konfiguration (siehe apiRequest)
 * @returns {Promise<any>}
 *
 * @example
 *   const movies = await get('/media/movies', { limit: 10 });
 */
export async function get(endpoint, params = {}, config = {}) {
  let url = endpoint;

  // Query-String aus params-Objekt bauen
  if (params && Object.keys(params).length > 0) {
    const searchParams = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) {
        searchParams.append(key, value);
      }
    }
    const qs = searchParams.toString();
    if (qs) url += `?${qs}`;
  }

  return apiRequest(url, { method: 'GET' }, config);
}

/**
 * HTTP POST — Daten an das Backend senden und JSON-Antwort erhalten.
 *
 * @param {string} endpoint — API-Pfad
 * @param {object} [body] — Request-Body (wird als JSON serialisiert)
 * @param {object} [config] — Erweiterte Konfiguration
 * @returns {Promise<any>}
 *
 * @example
 *   const tx = await post('/finance/transactions', { amount: 42, category: 'Essen' });
 */
export async function post(endpoint, body = {}, config = {}) {
  return apiRequest(endpoint, { method: 'POST', body }, config);
}

/**
 * HTTP PUT — Vorhandene Ressource vollständig aktualisieren.
 *
 * @param {string} endpoint — API-Pfad
 * @param {object} [body] — Request-Body
 * @param {object} [config] — Erweiterte Konfiguration
 * @returns {Promise<any>}
 */
export async function put(endpoint, body = {}, config = {}) {
  return apiRequest(endpoint, { method: 'PUT', body }, config);
}

/**
 * HTTP DELETE — Ressource löschen.
 *
 * @param {string} endpoint — API-Pfad
 * @param {object} [config] — Erweiterte Konfiguration
 * @returns {Promise<any>}
 */
export async function del(endpoint, config = {}) {
  return apiRequest(endpoint, { method: 'DELETE' }, config);
}

/**
 * HTTP PATCH — Teilweise Aktualisierung einer Ressource.
 *
 * @param {string} endpoint — API-Pfad
 * @param {object} [body] — Teil-Daten
 * @param {object} [config] — Erweiterte Konfiguration
 * @returns {Promise<any>}
 */
export async function patch(endpoint, body = {}, config = {}) {
  return apiRequest(endpoint, { method: 'PATCH', body }, config);
}

// ═══════════════════════════════════════════════════════════════════════════════
// 8. SPEZIALMETHODEN
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Datei-Upload via multipart/form-data.
 *
 * @param {string} endpoint — API-Pfad (z. B. "/files/upload")
 * @param {FormData} formData — FormData mit Datei(en) und Metadaten
 * @param {object} [config] — Erweiterte Konfiguration
 * @param {function} [onProgress] — Progress-Callback (0–100)
 * @returns {Promise<any>}
 *
 * @example
 *   const fd = new FormData();
 *   fd.append('file', fileInput.files[0]);
 *   fd.append('path', '/dokumente');
 *   const result = await upload('/files/upload', fd, {}, (pct) => console.log(`${pct}%`));
 */
export async function upload(endpoint, formData, config = {}, onProgress = null) {
  const uploadConfig = {
    timeout: LONG_TIMEOUT_MS,
    ...config
  };

  // XMLHttpRequest-basierter Upload für echte Progress-Events
  // (fetch bietet keinen Upload-Fortschritt)
  if (onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const url = `${BASE_URL}${endpoint}`;

      xhr.open('POST', url);

      // Progress-Tracking
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      });

      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch {
            resolve(xhr.responseText);
          }
        } else {
          let detail = null;
          try { detail = JSON.parse(xhr.responseText); } catch { /* ignore */ }
          reject(new ApiError(
            `Upload fehlgeschlagen [${xhr.status}]`,
            xhr.status,
            detail
          ));
        }
      });

      xhr.addEventListener('error', () => {
        reject(new ApiError('Upload-Netzwerkfehler', 0));
      });

      xhr.addEventListener('abort', () => {
        reject(new ApiError('Upload abgebrochen', 0));
      });

      // Timeout
      xhr.timeout = uploadConfig.timeout;
      xhr.addEventListener('timeout', () => {
        reject(new ApiError(`Upload-Timeout nach ${uploadConfig.timeout}ms`, 408));
      });

      if (!uploadConfig.skipLoader) _requestStarted();
      xhr.addEventListener('loadend', () => {
        if (!uploadConfig.skipLoader) _requestFinished();
      });

      xhr.send(formData);
    });
  }

  // Fallback: Einfacher fetch-basierter Upload (kein Progress)
  return apiRequest(endpoint, {
    method: 'POST',
    body: formData
    // Kein Content-Type! Browser setzt multipart/form-data mit Boundary automatisch
  }, uploadConfig);
}

/**
 * Datei-Download — Gibt ein Blob zurück und löst den Browser-Download aus.
 *
 * @param {string} endpoint — API-Pfad
 * @param {string} [filename] — Vorgeschlagener Dateiname für den Download
 * @param {object} [config] — Erweiterte Konfiguration
 * @returns {Promise<Blob>} — Die heruntergeladenen Daten als Blob
 *
 * @example
 *   await download('/files/download?path=/doku.pdf', 'doku.pdf');
 */
export async function download(endpoint, filename = null, config = {}) {
  const downloadConfig = {
    timeout: LONG_TIMEOUT_MS,
    rawResponse: true,
    ...config
  };

  // Accept-Header für beliebigen Inhalt
  const response = await apiRequest(endpoint, {
    method: 'GET',
    headers: { 'Accept': '*/*' }
  }, downloadConfig);

  const blob = await response.blob();

  // Dateiname aus Content-Disposition-Header oder Parameter extrahieren
  const contentDisposition = response.headers.get('Content-Disposition') || '';
  const headerMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
  const resolvedFilename = filename || (headerMatch ? headerMatch[1].replace(/['"]/g, '') : 'download');

  // Browser-Download auslösen
  const blobUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = blobUrl;
  a.download = resolvedFilename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(blobUrl);

  return blob;
}

/**
 * SSE-Stream (Server-Sent Events) für KI-Chat-Streaming.
 * Verarbeitet einen text/event-stream und ruft callbacks für jedes Event auf.
 *
 * @param {string} endpoint — API-Pfad (z. B. "/ai/chat/stream")
 * @param {object} body — Request-Body (Chat-Nachricht etc.)
 * @param {object} callbacks — Event-Handler
 * @param {function} [callbacks.onToken] — Wird für jedes Text-Token aufgerufen
 * @param {function} [callbacks.onDone] — Wird nach vollständigem Stream aufgerufen
 * @param {function} [callbacks.onError] — Wird bei Fehlern aufgerufen
 * @param {AbortSignal} [callbacks.signal] — Zum Abbrechen des Streams
 * @returns {Promise<void>}
 *
 * @example
 *   await stream('/ai/chat/stream', { message: 'Hallo' }, {
 *     onToken: (token) => appendToChat(token),
 *     onDone: () => console.log('Fertig'),
 *     onError: (err) => showToast(err.message, 'error')
 *   });
 */
export async function stream(endpoint, body, callbacks = {}) {
  const { onToken, onDone, onError, signal } = callbacks;
  const streamConfig = {
    timeout: LONG_TIMEOUT_MS,
    rawResponse: true,
    skipToast: true
  };

  try {
    const response = await apiRequest(endpoint, {
      method: 'POST',
      body
    }, streamConfig);

    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      const err = new ApiError(
        detail.detail || `Stream-Fehler [${response.status}]`,
        response.status,
        detail
      );
      if (onError) onError(err);
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      // Prüfen, ob extern abgebrochen wurde
      if (signal?.aborted) {
        reader.cancel();
        break;
      }

      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE-Events parsen (Zeilen-basiert: "data: ...\n\n")
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // Unvollständige Zeile zurück in den Buffer

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6).trim();
          if (data === '[DONE]') {
            if (onDone) onDone();
            return;
          }
          try {
            const parsed = JSON.parse(data);
            if (onToken && parsed.token !== undefined) {
              onToken(parsed.token);
            } else if (onToken && parsed.content !== undefined) {
              onToken(parsed.content);
            }
          } catch {
            // Plain-Text-Token (kein JSON)
            if (onToken) onToken(data);
          }
        }
      }
    }

    if (onDone) onDone();

  } catch (error) {
    if (error.name === 'AbortError' && signal?.aborted) {
      // Absichtlicher Abbruch — kein Fehler
      if (onDone) onDone();
      return;
    }
    if (onError) {
      onError(error instanceof ApiError ? error : new ApiError(error.message, 0));
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// 9. DIENST-SPEZIFISCHE API-HELFER
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Namespace-Objekt mit dienstspezifischen API-Methoden für jede Domäne.
 * Ermöglicht aufrufe wie: api.media.getMovies() statt get('/media/movies').
 *
 * @type {object}
 */
export const api = {
  /** @namespace api.health — System-Health-Endpunkte */
  health: {
    /** Health-Check (einfach) */
    check: () => get('/health'),
    /** Liveness-Probe (Kubernetes-kompatibel) */
    live: () => get('/health/live'),
    /** Readiness-Probe (DB-Check inklusive) */
    ready: () => get('/health/ready'),
    /** Detaillierter Status aller Subsysteme */
    detailed: () => get('/health/detailed')
  },

  /** @namespace api.system — System-Metriken (CPU, RAM, Disk) */
  system: {
    /** Kompakte Metriken */
    metrics: () => get('/system/metrics'),
    /** Vollständige Metriken */
    metricsFull: () => get('/system/metrics/full'),
    /** CPU-Temperatur */
    temperature: () => get('/system/temperature'),
    /** Power-Mode setzen */
    setPowerMode: (mode) => post('/system/power-mode', { mode })
  },

  /** @namespace api.media — Jellyfin-Medienbunker */
  media: {
    movies: (params) => get('/media/movies', params),
    series: (params) => get('/media/series', params),
    episodes: (seriesId, params) => get(`/media/series/${seriesId}/episodes`, params),
    continue: () => get('/media/continue'),
    recent: () => get('/media/recent'),
    search: (query) => get('/media/search', { query }),
    item: (id) => get(`/media/item/${id}`),
    genres: () => get('/media/genres'),
    stats: () => get('/media/stats'),
    /** Cover-URL (relativ) */
    coverUrl: (id) => `${BASE_URL}/media/cover/${id}`,
    /** Stream-URL (relativ) — Direkt-Play oder HLS */
    streamUrl: (id) => `${BASE_URL}/media/stream/${id}`
  },

  /** @namespace api.music — Musikarchiv */
  music: {
    scan: () => post('/music/scan'),
    tracks: (params) => get('/music/tracks', params),
    artists: () => get('/music/artists'),
    albums: () => get('/music/albums'),
    genres: () => get('/music/genres'),
    search: (query) => get('/music/search', { query }),
    stats: () => get('/music/stats'),
    /** Stream-URL */
    streamUrl: (id) => `${BASE_URL}/music/stream/${id}`,
    /** Cover-URL */
    coverUrl: (id) => `${BASE_URL}/music/cover/${id}`
  },

  /** @namespace api.files — Dateibunker */
  files: {
    list: (path) => get('/files/list', { path }),
    info: (path) => get('/files/info', { path }),
    createDir: (path) => post('/files/directory', { path }),
    delete: (path, secure = false) => del(`/files/delete?path=${encodeURIComponent(path)}&secure=${secure}`),
    move: (source, dest) => post('/files/move', { source, destination: dest }),
    copy: (source, dest) => post('/files/copy', { source, destination: dest }),
    storage: () => get('/files/storage'),
    /** Download-URL */
    downloadUrl: (path) => `${BASE_URL}/files/download?path=${encodeURIComponent(path)}`
  },

  /** @namespace api.finance — Finanztransaktionen */
  finance: {
    transactions: (params) => get('/finance/transactions', params),
    create: (data) => post('/finance/transactions', data),
    update: (id, data) => put(`/finance/transactions/${id}`, data),
    delete: (id) => del(`/finance/transactions/${id}`),
    balance: () => get('/finance/balance'),
    monthlySummary: (year, month) => get('/finance/summary/monthly', { year, month }),
    categories: () => get('/finance/categories'),
    trends: () => get('/finance/trends'),
    budgetComparison: () => get('/finance/budget/comparison'),
    yearlySummary: (year) => get('/finance/summary/yearly', { year })
  },

  /** @namespace api.healthBio — Bio-Tracking & Gesundheit */
  healthBio: {
    records: (params) => get('/health-bio/records', params),
    create: (data) => post('/health-bio/records', data),
    update: (id, data) => put(`/health-bio/records/${id}`, data),
    delete: (id) => del(`/health-bio/records/${id}`),
    hologram: () => get('/health-bio/hologram'),
    hologramLocation: (location) => get(`/health-bio/hologram/${encodeURIComponent(location)}`),
    nutrition: () => get('/health-bio/nutrition'),
    weight: () => get('/health-bio/weight'),
    exercise: () => get('/health-bio/exercise'),
    sleep: () => get('/health-bio/sleep'),
    vitals: () => get('/health-bio/vitals'),
    dashboard: () => get('/health-bio/dashboard')
  },

  /** @namespace api.school — Ausbildung (Noten, Fristen) */
  school: {
    subjects: () => get('/school/subjects'),
    createSubject: (data) => post('/school/subjects', data),
    grades: (subjectId) => get('/school/grades', { subject_id: subjectId }),
    createGrade: (data) => post('/school/grades', data),
    deadlines: () => get('/school/deadlines'),
    createDeadline: (data) => post('/school/deadlines', data),
    gpa: () => get('/school/gpa'),
    gpaTrend: () => get('/school/gpa/trend'),
    uploadPdf: (formData, onProgress) => upload('/school/upload-pdf', formData, {}, onProgress)
  },

  /** @namespace api.auto — Automotive (Fahrzeuge, Berechnungen) */
  auto: {
    vehicles: () => get('/auto/vehicles'),
    createVehicle: (data) => post('/auto/vehicles', data),
    updateVehicle: (id, data) => put(`/auto/vehicles/${id}`, data),
    maintenance: (vehicleId) => get(`/auto/maintenance/${vehicleId}`),
    checkAllMaintenance: () => get('/auto/maintenance/check-all'),
    engineCalc: (params) => get('/auto/engine', params),
    performance: (params) => get('/auto/performance', params),
    turboCalc: (params) => get('/auto/turbo', params),
    generateCad: (data) => post('/auto/cad/generate', data),
    cadStatus: (jobId) => get(`/auto/cad/status/${jobId}`)
  },

  /** @namespace api.code — Code Workbench */
  code: {
    analyze: (code) => post('/code/analyze', { code }),
    execute: (code, language = 'python') => post('/code/execute', { code, language }),
    status: (jobId) => get(`/code/status/${jobId}`),
    formatIssues: (code) => post('/code/format-issues', { code })
  },

  /** @namespace api.ai — AI Studio (Chat, Agenten) */
  ai: {
    chat: (message, agent) => post('/ai/chat', { message, agent }),
    /** SSE-Stream für Echtzeit-Chat */
    chatStream: (message, agent, callbacks) =>
      stream('/ai/chat/stream', { message, agent }, callbacks),
    agents: () => get('/ai/agents'),
    status: () => get('/ai/status')
  },

  /** @namespace api.ide — Web-IDE (code-server) */
  ide: {
    createSession: () => post('/ide/session'),
    authorize: (sessionId) => post('/ide/authorize', { session_id: sessionId }),
    logout: (sessionId) => post('/ide/logout', { session_id: sessionId }),
    status: (sessionId) => get(`/ide/status/${sessionId}`),
    listSessions: () => get('/ide/sessions'),
    restart: (sessionId) => post(`/ide/restart/${sessionId}`)
  }
};

// ═══════════════════════════════════════════════════════════════════════════════
// 10. DEFAULT-EXPORT
// ═══════════════════════════════════════════════════════════════════════════════

export default apiRequest;