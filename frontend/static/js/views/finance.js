// FILE: frontend/static/js/views/finance.js
/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * Finanz-Zentrale — Transaktionserfassung & Chart-Visualisierung.
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Bietet:
 *   • Schnellerfassungs-Formular für Einnahmen & Ausgaben
 *   • Saldo-Übersichtskarten (Einnahmen, Ausgaben, Netto)
 *   • Chart.js-Diagramme im Cyber-Neon-Stil:
 *     - Monatliches Budget (Balken: Einnahmen vs. Ausgaben)
 *     - Kategorien-Verteilung (Donut-Chart)
 *     - Umsatzverlauf (Linien-Chart über 6 Monate)
 *   • Letzte Transaktionen als Tabelle mit Löschfunktion
 *
 * Verwendete API-Endpunkte:
 *   GET    /api/finance/balance              — Gesamtsaldo
 *   GET    /api/finance/summary/monthly      — Monatsübersicht
 *   GET    /api/finance/categories           — Kategorie-Aufschlüsselung
 *   GET    /api/finance/trends               — Zeitreihen-Trends
 *   GET    /api/finance/transactions         — Letzte Transaktionen
 *   POST   /api/finance/transactions         — Transaktion erstellen
 *   DELETE /api/finance/transactions/{id}    — Transaktion löschen
 */

import { get, post, del } from '../api.js';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. KONSTANTEN — Cyber-Neon-Chart-Farben
// ═══════════════════════════════════════════════════════════════════════════════

/** Chart.js-Farbpalette passend zu tokens.css */
const CHART_COLORS = {
  primary:     '#a06bff',
  cyan:        '#00f5d4',
  blue:        '#38e1ff',
  success:     '#00ff88',
  warning:     '#ff9f0a',
  danger:      '#ff4081',
  amber:       '#ffd60a',
  gridLines:   'rgba(255,255,255,0.06)',
  tickLabels:  '#606078',
  bgDark:      '#0c0c18',
};

/** Chart.js-Globaleinstellungen (werden nach dem Laden gesetzt) */
const CHART_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 600, easing: 'easeOutQuart' },
};

// ═══════════════════════════════════════════════════════════════════════════════
// 2. HAUPTFUNKTION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Rendert die Finanz-Zentrale und initialisiert alle Charts.
 *
 * @param {HTMLElement} container — Der #view-port-Container
 * @returns {Function} Cleanup-Funktion
 */
export async function showFinance(container) {
  // ── 2.1 Grundlayout ────────────────────────────────────────────────────
  container.innerHTML = `
    <div class="section-header">
      <div>
        <h1>Finanz-Zentrale</h1>
        <p class="section-subtitle">Transaktionen, Budgets & Trend-Analyse</p>
      </div>
    </div>

    <!-- Saldo-Übersichtskarten -->
    <div class="dashboard-grid cols-3" style="margin-bottom:var(--space-6);" id="balance-cards">
      <div class="glass-card stat-widget" style="align-items:center; text-align:center;">
        <span class="stat-label">Einnahmen (gesamt)</span>
        <span class="stat-value" id="bal-income" style="color:var(--color-success);">— €</span>
      </div>
      <div class="glass-card stat-widget" style="align-items:center; text-align:center;">
        <span class="stat-label">Ausgaben (gesamt)</span>
        <span class="stat-value" id="bal-expenses" style="color:var(--color-danger);">— €</span>
      </div>
      <div class="glass-card stat-widget" style="align-items:center; text-align:center;">
        <span class="stat-label">Netto-Saldo</span>
        <span class="stat-value" id="bal-net" style="color:var(--color-accent-primary);">— €</span>
      </div>
    </div>

    <div style="display:grid; grid-template-columns:420px 1fr; gap:var(--space-6);">

      <!-- Linke Spalte: Eingabeformular + letzte Transaktionen -->
      <div style="display:flex; flex-direction:column; gap:var(--space-6);">

        <!-- Schnellerfassungs-Formular -->
        <div class="glass-card">
          <div class="card-header"><h3>📝 Schnellerfassung</h3></div>
          <form id="tx-form" style="display:flex; flex-direction:column; gap:var(--space-4);" autocomplete="off">
            <div class="form-group">
              <label class="form-label">Typ</label>
              <div class="btn-group" style="width:100%;">
                <button type="button" id="type-expense" class="btn btn-danger btn-pill active-type" style="flex:1;">💸 Ausgabe</button>
                <button type="button" id="type-income" class="btn btn-success btn-pill" style="flex:1;">💰 Einnahme</button>
              </div>
            </div>
            <div class="form-group">
              <label class="form-label" for="tx-amount">Betrag (€)</label>
              <input type="number" id="tx-amount" class="form-input" placeholder="0,00"
                     step="0.01" min="0.01" required>
            </div>
            <div class="form-group">
              <label class="form-label" for="tx-category">Kategorie</label>
              <select id="tx-category" class="form-select">
                <option value="">Bitte wählen…</option>
                <optgroup label="Häufige Ausgaben">
                  <option value="Miete">Miete</option>
                  <option value="Lebensmittel">Lebensmittel</option>
                  <option value="Mobilität">Mobilität</option>
                  <option value="Strom">Strom</option>
                  <option value="Internet">Internet</option>
                  <option value="Versicherung">Versicherung</option>
                  <option value="Gesundheit">Gesundheit</option>
                  <option value="Freizeit">Freizeit</option>
                  <option value="Abos">Abos</option>
                  <option value="Sonstiges">Sonstiges</option>
                </optgroup>
                <optgroup label="Einnahmen">
                  <option value="Gehalt">Gehalt</option>
                  <option value="Nebenjob">Nebenjob</option>
                  <option value="Investition">Investition</option>
                  <option value="Geschenk">Geschenk</option>
                  <option value="Rückerstattung">Rückerstattung</option>
                </optgroup>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label" for="tx-description">Beschreibung</label>
              <input type="text" id="tx-description" class="form-input" placeholder="Optional…">
            </div>
            <button type="submit" class="btn btn-primary" style="width:100%;">
              ✓ Transaktion erfassen
            </button>
          </form>
        </div>

        <!-- Letzte Transaktionen -->
        <div class="glass-card" style="flex:1; min-height:0; display:flex; flex-direction:column;">
          <div class="card-header"><h3>📋 Letzte Transaktionen</h3></div>
          <div id="recent-tx-list" style="overflow-y:auto; flex:1;">
            <div class="spinner" style="margin:var(--space-6) auto;"></div>
          </div>
        </div>
      </div>

      <!-- Rechte Spalte: Charts -->
      <div style="display:flex; flex-direction:column; gap:var(--space-6);">

        <!-- Monatliches Budget (Balken) -->
        <div class="glass-card">
          <div class="card-header"><h3>📊 Monatlicher Überblick</h3></div>
          <div style="height:250px; position:relative;">
            <canvas id="chart-monthly"></canvas>
          </div>
        </div>

        <!-- Kategorien-Verteilung (Donut) -->
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:var(--space-6);">
          <div class="glass-card">
            <div class="card-header"><h3>🍩 Kategorien (Ausgaben)</h3></div>
            <div style="height:250px; position:relative;">
              <canvas id="chart-categories"></canvas>
            </div>
          </div>
          <!-- Budget-Vergleich (Balken horizontal) -->
          <div class="glass-card">
            <div class="card-header"><h3>🎯 Budget-Vergleich</h3></div>
            <div style="height:250px; position:relative;">
              <canvas id="chart-budget"></canvas>
            </div>
          </div>
        </div>

        <!-- Umsatzverlauf (Linien) -->
        <div class="glass-card">
          <div class="card-header"><h3>📈 Umsatzverlauf (6 Monate)</h3></div>
          <div style="height:250px; position:relative;">
            <canvas id="chart-trends"></canvas>
          </div>
        </div>
      </div>
    </div>
  `;

  // ── 2.2 Zustand ────────────────────────────────────────────────────────
  let isExpense = true;

  // ── 2.3 Typ-Umschalter ─────────────────────────────────────────────────
  const btnExpense = document.getElementById('type-expense');
  const btnIncome = document.getElementById('type-income');
  const txCategory = document.getElementById('tx-category');

  btnExpense.addEventListener('click', () => {
    isExpense = true;
    btnExpense.classList.add('active-type');
    btnIncome.classList.remove('active-type');
    txCategory.value = '';
  });

  btnIncome.addEventListener('click', () => {
    isExpense = false;
    btnIncome.classList.add('active-type');
    btnExpense.classList.remove('active-type');
    txCategory.value = '';
  });

  // ── 2.4 Formular absenden ─────────────────────────────────────────────
  document.getElementById('tx-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const amount = parseFloat(document.getElementById('tx-amount').value);
    const category = txCategory.value.trim();
    const description = document.getElementById('tx-description').value.trim();

    if (!amount || amount <= 0) return;
    if (!category) return;

    try {
      await post('/finance/transactions', {
        amount,
        category,
        is_expense: isExpense,
        description: description || null
      });

      // Formular zurücksetzen
      document.getElementById('tx-amount').value = '';
      document.getElementById('tx-description').value = '';
      txCategory.value = '';

      // Daten neu laden
      await Promise.all([_loadBalance(), _loadRecentTransactions(), _loadAllCharts()]);
    } catch (err) {
      console.error('Fehler beim Erfassen:', err);
    }
  });

  // ── 2.5 Saldo laden ────────────────────────────────────────────────────
  async function _loadBalance() {
    try {
      const data = await get('/finance/balance', {}, { skipToast: true });
      document.getElementById('bal-income').textContent =
        `${(Number(data.total_income) || 0).toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
      document.getElementById('bal-expenses').textContent =
        `${(Number(data.total_expenses) || 0).toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
      const net = (Number(data.net_balance) || 0).toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      document.getElementById('bal-net').textContent = `${net} €`;
    } catch {
      // Stumm
    }
  }

  // ── 2.6 Letzte Transaktionen ───────────────────────────────────────────
  async function _loadRecentTransactions() {
    const listEl = document.getElementById('recent-tx-list');
    try {
      const data = await get('/finance/transactions', { limit: 20 }, { skipToast: true });
      const txs = data.items || data || [];
      if (txs.length === 0) {
        listEl.innerHTML = `<div class="text-xs text-tertiary" style="text-align:center; padding:var(--space-6);">Keine Transaktionen</div>`;
        return;
      }
      listEl.innerHTML = txs.map(tx => {
        const sign = tx.is_expense ? '−' : '+';
        const color = tx.is_expense ? 'var(--color-danger)' : 'var(--color-success)';
        const date = new Date(tx.timestamp).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' });
        return `
          <div style="display:flex; align-items:center; gap:var(--space-3);
                      padding:var(--space-2) var(--space-1);
                      border-bottom:1px solid var(--color-border-subtle);">
            <span style="color:${color}; font-weight:var(--font-bold); min-width:80px; text-align:right;">
              ${sign} ${Number(tx.amount).toFixed(2)} €
            </span>
            <span class="text-xs" style="flex:1; color:var(--color-text-secondary);">
              ${_escapeHtml(tx.category)}
            </span>
            <span class="text-xs" style="color:var(--color-text-tertiary);">${date}</span>
            <button class="btn-icon btn-ghost tx-delete-btn" data-id="${tx.id}"
                    title="Löschen" style="font-size:12px; width:24px; height:24px; opacity:0.5;">✕</button>
          </div>`;
      }).join('');

      // Delete-Buttons
      listEl.querySelectorAll('.tx-delete-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const id = btn.dataset.id;
          try {
            await del(`/finance/transactions/${id}`, { skipToast: false });
            await Promise.all([_loadBalance(), _loadRecentTransactions(), _loadAllCharts()]);
          } catch { /* Toast handled by api.js */ }
        });
      });
    } catch {
      listEl.innerHTML = `<div class="text-xs text-tertiary" style="text-align:center; padding:var(--space-6);">Fehler beim Laden</div>`;
    }
  }

  // ── 2.7 Chart.js laden & Charts initialisieren ─────────────────────────

  /**
   * Lädt Chart.js dynamisch vom CDN (nur einmal).
   * @returns {Promise<typeof Chart>}
   */
  async function _loadChartJS() {
    if (window.Chart) return window.Chart;
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js';
      script.onload = () => {
        // Globale Defaults setzen
        if (window.Chart && window.Chart.defaults) {
          window.Chart.defaults.color = CHART_COLORS.tickLabels;
          window.Chart.defaults.borderColor = CHART_COLORS.gridLines;
          window.Chart.defaults.font.family = "'Inter', sans-serif";
          window.Chart.defaults.font.size = 11;
        }
        resolve(window.Chart);
      };
      script.onerror = () => reject(new Error('Chart.js konnte nicht geladen werden.'));
      document.head.appendChild(script);
    });
  }

  /** Chart-Instanzen für Cleanup */
  let chartMonthly = null, chartCategories = null, chartBudget = null, chartTrends = null;

  async function _loadAllCharts() {
    try {
      const Chart = await _loadChartJS();
      const [categories, trends] = await Promise.all([
        get('/finance/categories', {}, { skipToast: true, skipLoader: true }),
        get('/finance/trends', {}, { skipToast: true, skipLoader: true })
      ]);
      _renderMonthlyChart(Chart);
      _renderCategoryChart(Chart, categories);
      _renderBudgetChart(Chart, categories);
      _renderTrendsChart(Chart, trends);
    } catch (err) {
      console.error('Chart-Ladefehler:', err);
    }
  }

  // ── Monats-Balkendiagramm ──────────────────────────────────────────────
  async function _renderMonthlyChart(Chart) {
    const canvas = document.getElementById('chart-monthly');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    // Aktuellen Monat und die letzten 5 Monate laden
    const now = new Date();
    const months = [];
    const incomeData = [];
    const expenseData = [];

    for (let i = 5; i >= 0; i--) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      const year = d.getFullYear();
      const month = d.getMonth() + 1;
      const label = d.toLocaleDateString('de-DE', { month: 'short' });
      months.push(label);

      try {
        const summary = await get('/finance/summary/monthly', { year, month }, { skipToast: true, skipLoader: true });
        incomeData.push(Number(summary.total_income) || 0);
        expenseData.push(Number(summary.total_expenses) || 0);
      } catch {
        incomeData.push(0);
        expenseData.push(0);
      }
    }

    if (chartMonthly) chartMonthly.destroy();

    chartMonthly = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: months,
        datasets: [
          {
            label: 'Einnahmen',
            data: incomeData,
            backgroundColor: 'rgba(0, 255, 136, 0.25)',
            borderColor: CHART_COLORS.success,
            borderWidth: 1,
            borderRadius: 6,
            borderSkipped: false,
          },
          {
            label: 'Ausgaben',
            data: expenseData,
            backgroundColor: 'rgba(255, 64, 129, 0.25)',
            borderColor: CHART_COLORS.danger,
            borderWidth: 1,
            borderRadius: 6,
            borderSkipped: false,
          }
        ]
      },
      options: {
        ...CHART_DEFAULTS,
        plugins: {
          legend: {
            labels: { color: CHART_COLORS.tickLabels, usePointStyle: true, pointStyleWidth: 8 }
          }
        },
        scales: {
          x: {
            grid: { color: CHART_COLORS.gridLines },
            ticks: { color: CHART_COLORS.tickLabels }
          },
          y: {
            grid: { color: CHART_COLORS.gridLines },
            ticks: { color: CHART_COLORS.tickLabels, callback: v => v + ' €' }
          }
        }
      }
    });
  }

  // ── Kategorien-Donut ───────────────────────────────────────────────────
  function _renderCategoryChart(Chart, categories) {
    const canvas = document.getElementById('chart-categories');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    const catList = Array.isArray(categories) ? categories : (categories.items || []);
    const expenseCats = catList.filter(c => c.is_expense !== false).slice(0, 6);

    const colors = [
      CHART_COLORS.danger, CHART_COLORS.warning, CHART_COLORS.amber,
      CHART_COLORS.primary, CHART_COLORS.blue, CHART_COLORS.cyan
    ];

    if (chartCategories) chartCategories.destroy();

    chartCategories = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: expenseCats.map(c => c.category),
        datasets: [{
          data: expenseCats.map(c => Number(c.total_amount) || 0),
          backgroundColor: colors.slice(0, expenseCats.length).map(c => c + '99'),
          borderColor: colors.slice(0, expenseCats.length),
          borderWidth: 2,
          hoverBorderColor: colors.slice(0, expenseCats.length).map(c => c + 'FF'),
          hoverBorderWidth: 3,
        }]
      },
      options: {
        ...CHART_DEFAULTS,
        cutout: '60%',
        plugins: {
          legend: {
            position: 'bottom',
            labels: { color: CHART_COLORS.tickLabels, padding: 12, usePointStyle: true, pointStyleWidth: 8 }
          }
        }
      }
    });
  }

  // ── Budget-Vergleich (horizontaler Balken) ─────────────────────────────
  function _renderBudgetChart(Chart, categories) {
    const canvas = document.getElementById('chart-budget');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    const catList = Array.isArray(categories) ? categories : (categories.items || []);
    const expenseCats = catList.filter(c => c.is_expense !== false).slice(0, 5);

    if (chartBudget) chartBudget.destroy();

    chartBudget = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: expenseCats.map(c => c.category),
        datasets: [{
          label: 'Ausgaben',
          data: expenseCats.map(c => Number(c.total_amount) || 0),
          backgroundColor: 'rgba(160, 107, 255, 0.30)',
          borderColor: CHART_COLORS.primary,
          borderWidth: 1,
          borderRadius: 4,
          borderSkipped: false,
        }]
      },
      options: {
        ...CHART_DEFAULTS,
        indexAxis: 'y',
        plugins: {
          legend: { display: false }
        },
        scales: {
          x: {
            grid: { color: CHART_COLORS.gridLines },
            ticks: { color: CHART_COLORS.tickLabels, callback: v => v + ' €' }
          },
          y: {
            grid: { display: false },
            ticks: { color: CHART_COLORS.tickLabels }
          }
        }
      }
    });
  }

  // ── Umsatzverlauf (Linien) ─────────────────────────────────────────────
  function _renderTrendsChart(Chart, trends) {
    const canvas = document.getElementById('chart-trends');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    const trendList = Array.isArray(trends) ? trends : (trends.items || []);

    if (chartTrends) chartTrends.destroy();

    chartTrends = new Chart(ctx, {
      type: 'line',
      data: {
        labels: trendList.map(t => t.period),
        datasets: [
          {
            label: 'Einnahmen',
            data: trendList.map(t => Number(t.income) || 0),
            borderColor: CHART_COLORS.success,
            backgroundColor: 'rgba(0,255,136,0.06)',
            fill: true,
            tension: 0.4,
            pointRadius: 3,
            pointBackgroundColor: CHART_COLORS.success,
            pointBorderColor: CHART_COLORS.success,
            pointHoverRadius: 6,
          },
          {
            label: 'Ausgaben',
            data: trendList.map(t => Number(t.expenses) || 0),
            borderColor: CHART_COLORS.danger,
            backgroundColor: 'rgba(255,64,129,0.06)',
            fill: true,
            tension: 0.4,
            pointRadius: 3,
            pointBackgroundColor: CHART_COLORS.danger,
            pointBorderColor: CHART_COLORS.danger,
            pointHoverRadius: 6,
          },
          {
            label: 'Netto',
            data: trendList.map(t => Number(t.net) || 0),
            borderColor: CHART_COLORS.primary,
            borderWidth: 2,
            borderDash: [6, 3],
            fill: false,
            tension: 0.4,
            pointRadius: 4,
            pointBackgroundColor: CHART_COLORS.primary,
            pointBorderColor: '#ffffff',
            pointBorderWidth: 2,
            pointHoverRadius: 7,
          }
        ]
      },
      options: {
        ...CHART_DEFAULTS,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            labels: { color: CHART_COLORS.tickLabels, usePointStyle: true, pointStyleWidth: 8 }
          },
          tooltip: {
            backgroundColor: 'rgba(30,30,58,0.95)',
            titleColor: '#e8e8f0',
            bodyColor: '#e8e8f0',
            borderColor: 'rgba(255,255,255,0.12)',
            borderWidth: 1,
            padding: 10,
            cornerRadius: 8,
          }
        },
        scales: {
          x: {
            grid: { color: CHART_COLORS.gridLines },
            ticks: { color: CHART_COLORS.tickLabels }
          },
          y: {
            grid: { color: CHART_COLORS.gridLines },
            ticks: { color: CHART_COLORS.tickLabels, callback: v => v + ' €' }
          }
        }
      }
    });
  }

  // ── 2.8 Initialisierung ────────────────────────────────────────────────
  await Promise.all([
    _loadBalance(),
    _loadRecentTransactions(),
    _loadAllCharts()
  ]);

  // ── 2.9 Cleanup ────────────────────────────────────────────────────────
  return () => {
    // Chart-Instanzen zerstören, um Memory-Leaks zu vermeiden
    [chartMonthly, chartCategories, chartBudget, chartTrends].forEach(c => {
      if (c && typeof c.destroy === 'function') c.destroy();
    });
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