/* Sayfa görünümleri: panel, tablolar, referans listeler, geçmiş, ayarlar. */

import { api } from './api.js';
import { groupedBarChart, legendHtml, tankFillChart, trendChart } from './charts.js';
import { openRecordForm } from './form.js';
import { pick, t, getLanguage } from './i18n.js';
import { openImporter } from './importer.js';
import {
  currentSlotStatus, slotCardHtml, startSlotTicker, wireSlotCard,
} from './slots.js';
import { ensureLookups, gtipName, gumrukLabel, state, tableSpec } from './state.js';
import {
  confirmDialog, copyText, download, emptyHtml, errorHtml, escapeHtml,
  filterRows, fmtDate, fmtDateTime, fmtDateTimeShort, fmtNumber, icon, isoDate,
  loadingHtml, openModal, relativeTime, sortRows, toast, toCsv,
} from './ui.js';

const view = () => document.getElementById('view');

/* ==========================================================================
   PANEL
   ========================================================================== */

export async function renderDashboard(ctx) {
  view().innerHTML = `<div class="view__inner">${loadingHtml()}</div>`;
  let payload;
  try {
    payload = await api.dashboard();
  } catch (error) {
    view().innerHTML = `<div class="view__inner">${errorHtml(error.message, 'data-retry="1"')}</div>`;
    view().querySelector('[data-retry]')?.addEventListener('click', () => renderDashboard(ctx));
    return;
  }

  const { summary, activity, session } = payload;

  const cards = state.tables.map((spec) => {
    const info = summary[spec.key] || {};
    const failed = info.ok === false;
    return `
      <div class="card stat">
        <div class="stat__top">
          <div>
            <div class="stat__label">${escapeHtml(spec.short)}</div>
            <div class="small muted" style="margin-top:3px">${escapeHtml(pick(spec, 'desc'))}</div>
          </div>
          <div class="stat__icon">${icon(spec.icon)}</div>
        </div>
        <div class="stat__value">${failed ? '—' : (info.total ?? 0)}</div>
        <div class="stat__meta">
          ${failed
            ? `<span class="pill pill--err">${escapeHtml(info.message || t('common.error'))}</span>`
            : `${escapeHtml(t('common.today'))}: <b>${info.today ?? 0}</b> ·
               ${escapeHtml(t('common.yesterday'))}: <b>${info.yesterday ?? 0}</b>`}
        </div>
        <div class="stat__foot">
          <button class="btn btn--sm btn--ghost" data-go="${spec.key}">${escapeHtml(t('dash.viewAll'))}</button>
          <button class="btn btn--sm btn--primary" data-new="${spec.key}">${icon('plus')} ${escapeHtml(t('common.new'))}</button>
        </div>
      </div>`;
  }).join('');

  const periods = state.tables.map((spec) => `
    <li>
      <span class="pill pill--soft">${escapeHtml(spec.short)}</span>
      <span class="small">${escapeHtml(pick(spec, 'period'))}</span>
    </li>`).join('');

  view().innerHTML = `
    <div class="view__inner stack">
      <div class="grid grid--stats">${cards}</div>

      <div class="card" id="slot-card">
        <div class="card__head">
          <div>
            <h2 class="card__title">${escapeHtml(t('slot.title'))}</h2>
            <p class="card__sub">${escapeHtml(t('slot.subtitle'))}</p>
          </div>
        </div>
        <div id="slot-body"></div>
      </div>

      <div class="grid grid--2">
        <div class="card">
          <div class="card__head">
            <div>
              <h2 class="card__title">${escapeHtml(t('dash.tankFill'))}</h2>
              <p class="card__sub">${escapeHtml(t('dash.tankFillSub'))}</p>
            </div>
          </div>
          <div class="card__body"><div class="viz" id="chart-tanks"></div></div>
        </div>

        <div class="card">
          <div class="card__head">
            <div>
              <h2 class="card__title">${escapeHtml(t('dash.byProduct'))}</h2>
              <p class="card__sub">${escapeHtml(t('dash.byProductSub'))}</p>
            </div>
          </div>
          <div class="card__body">
            <div class="viz" id="chart-products"></div>
            <div id="legend-products"></div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">${escapeHtml(t('dash.trend'))}</h2>
            <p class="card__sub">${escapeHtml(t('dash.trendSub'))}</p>
          </div>
        </div>
        <div class="card__body">
          <div class="viz" id="chart-trend"></div>
          <div id="legend-trend"></div>
        </div>
      </div>

      <div class="grid grid--2">
        <div class="card">
          <div class="card__head">
            <div>
              <h2 class="card__title">${escapeHtml(t('dash.recentActivity'))}</h2>
              <p class="card__sub">${escapeHtml(t('log.subtitle'))}</p>
            </div>
            <div class="card__actions">
              <button class="btn btn--sm btn--ghost" data-go-page="log">${escapeHtml(t('dash.viewAll'))}</button>
            </div>
          </div>
          <div class="card__body">
            ${activity.length ? `<ul class="list-plain">${activity.map((entry) => `
              <li>
                <span class="pill ${entry.success ? 'pill--ok' : 'pill--err'}">
                  ${entry.success ? '✓' : '✕'}
                </span>
                <span>
                  <b>${escapeHtml(entry.action)}</b>
                  ${entry.table_key ? ` · ${escapeHtml(entry.table_key.toUpperCase())}` : ''}
                  ${entry.message ? `<div class="small muted">${escapeHtml(String(entry.message).slice(0, 90))}</div>` : ''}
                </span>
                <span class="when">${escapeHtml(relativeTime(entry.ts))}</span>
              </li>`).join('')}</ul>`
              : `<p class="muted small">${escapeHtml(t('dash.noActivity'))}</p>`}
          </div>
        </div>

        <div class="card">
          <div class="card__head">
            <h2 class="card__title">${escapeHtml(t('dash.session'))}</h2>
          </div>
          <div class="card__body stack">
            <dl class="kv">
              <dt>${escapeHtml(t('login.username'))}</dt><dd>${escapeHtml(session.username || '—')}</dd>
              <dt>${escapeHtml(t('dash.licence'))}</dt>
              <dd>${(session.licences || []).map((l) => `<span class="pill pill--soft">${escapeHtml(l)}</span>`).join(' ') || '—'}</dd>
              <dt>${escapeHtml(t('dash.env'))}</dt><dd>${escapeHtml(envLabel(session.environment))}</dd>
              <dt>${escapeHtml(t('dash.address'))}</dt><dd class="small mono">${escapeHtml(session.baseUrl || '—')}</dd>
              <dt>${escapeHtml(t('dash.authority'))}</dt>
              <dd class="small">${escapeHtml(session.authority || '—')}</dd>
            </dl>
            <div>
              <p class="section-title">${escapeHtml(t('dash.deadline'))}</p>
              <ul class="list-plain">${periods}</ul>
            </div>
          </div>
        </div>
      </div>
    </div>`;

  drawCharts(payload);
  paintSlotCard(() => renderDashboard(ctx), payload.summary?.dep1?.rows || []);

  view().querySelectorAll('[data-go]').forEach((button) =>
    button.addEventListener('click', () => ctx.navigate(button.dataset.go)));
  view().querySelectorAll('[data-go-page]').forEach((button) =>
    button.addEventListener('click', () => ctx.navigate(button.dataset.goPage)));
  view().querySelectorAll('[data-new]').forEach((button) =>
    button.addEventListener('click', async () => {
      await ensureLookups();
      openRecordForm({
        spec: tableSpec(button.dataset.new),
        onDone: () => renderDashboard(ctx),
      });
    }));
}

/* --------------------------------------------------- bildirim döngüsü */

let slotTicker = null;

/**
 * DEP-1 döngü kartını çizer ve saniyelik geri sayımı başlatır.
 * @param {Function} reload  saat dilimi değişince ya da kayıt eklenince
 * @param {Array} rows       DEP-1 kayıtları
 * @param {string} hostId    kartın yerleştirileceği kap
 */
function paintSlotCard(reload, rows, hostId = 'slot-body') {
  const host = document.getElementById(hostId);
  if (!host) return;

  const status = currentSlotStatus(rows);
  host.innerHTML = slotCardHtml(status, { compact: hostId !== 'slot-body' });
  wireSlotCard(host, status, reload);

  slotTicker?.();
  slotTicker = startSlotTicker(() => currentSlotStatus(rows), reload);
}

/* ------------------------------------------------------- panel grafikleri */


const num = (value) => {
  const parsed = Number(String(value ?? '').replace(',', '.'));
  return Number.isFinite(parsed) ? parsed : 0;
};

/** Her tank için en son DEP-1 bildirimi (kapasiteye göre doluluk). */
function buildTankFill(payload) {
  const rows = payload.summary?.dep1?.rows || [];
  const latest = new Map();
  rows.forEach((row) => {
    const key = String(row.tankNumarasi ?? '');
    const stamp = String(row.saat ?? '');
    if (!latest.has(key) || stamp > latest.get(key).saat) latest.set(key, row);
  });

  return (payload.tanks || [])
    .map((tank) => {
      const row = latest.get(String(tank.tankNo ?? ''));
      return {
        tankNo: tank.tankNo,
        capacity: num(tank.kapasiteM3),
        stock: row ? num(row.tankStokM3) : 0,
        fuel: tank.yakitTuru,
        product: row ? (gtipName(row.petrolTuruGTIPNo) || tank.yakitTuru) : tank.yakitTuru,
        reportedAt: row ? fmtDateTime(row.saat) : t('dash.noTankData'),
      };
    })
    .sort((a, b) => b.capacity - a.capacity);
}

/** Tank başına son 24 saatlik stok serisi (en çok stoklu 5 tank). */
function buildTrend(payload) {
  const rows = payload.summary?.dep1?.rows || [];
  const byTank = new Map();

  rows.forEach((row) => {
    const time = new Date(String(row.saat ?? '').replace(' ', 'T')).getTime();
    if (!Number.isFinite(time)) return;
    const key = String(row.tankNumarasi ?? '—');
    if (!byTank.has(key)) byTank.set(key, new Map());
    // Aynı tank+saat için birden çok ürün varsa toplanır
    const points = byTank.get(key);
    points.set(time, (points.get(time) || 0) + num(row.tankStokTon));
  });

  const series = [...byTank.entries()]
    .map(([name, points]) => ({
      name,
      points: [...points.entries()].map(([time, value]) => ({ t: time, v: value })),
    }))
    .filter((s) => s.points.length >= 2)
    .sort((a, b) => Math.max(...b.points.map((p) => p.v)) - Math.max(...a.points.map((p) => p.v)));

  // Kategorik renk tavanı: en çok 5 seri, gerisi gösterilmez ve bu belirtilir
  return { series: series.slice(0, 5), hidden: Math.max(0, series.length - 5) };
}

/** Bugünkü DEP-2 (verilen) ve DR (alınan) stoklarının ürün bazında toplamı. */
function buildByProduct(payload) {
  const today = isoDate(0);
  const totals = new Map();

  const collect = (rows, slot) => {
    (rows || []).forEach((row) => {
      if (String(row.tarih ?? '').slice(0, 10) !== today) return;
      const code = String(row.petrolTuruGTIPNo ?? '—');
      if (!totals.has(code)) totals.set(code, [0, 0]);
      totals.get(code)[slot] += num(row.gunBasiStokTon);
    });
  };
  collect(payload.summary?.dep2?.rows, 0);
  collect(payload.summary?.dr?.rows, 1);

  return [...totals.entries()]
    .map(([code, values]) => ({
      label: gtipName(code) || code,
      sub: code,
      values,
      total: values[0] + values[1],
    }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 8);
}

function drawCharts(payload) {
  lastDashboard = payload;
  const empty = (host, message) => {
    host.innerHTML = `<div class="viz-empty">${escapeHtml(message)}</div>`;
  };

  const tanksHost = document.getElementById('chart-tanks');
  if (tanksHost) {
    const tanks = buildTankFill(payload).filter((tank) => tank.capacity > 0);
    if (tanks.length) tankFillChart(tanksHost, tanks.slice(0, 8));
    else empty(tanksHost, t('dash.noChartData'));
  }

  const trendHost = document.getElementById('chart-trend');
  if (trendHost) {
    const { series, hidden } = buildTrend(payload);
    const legend = document.getElementById('legend-trend');
    if (series.length) {
      trendChart(trendHost, series, { unit: 'ton' });
      legend.innerHTML = legendHtml(series.map((s) => s.name))
        + (hidden ? `<p class="small muted" style="margin:6px 0 0">
             +${hidden} ${escapeHtml(t('dash.moreTanks'))}</p>` : '');
    } else {
      empty(trendHost, t('dash.noChartData'));
      legend.innerHTML = '';
    }
  }

  const productHost = document.getElementById('chart-products');
  if (productHost) {
    const rows = buildByProduct(payload);
    const legend = document.getElementById('legend-products');
    const names = [t('dash.given'), t('dash.received')];
    if (rows.length) {
      groupedBarChart(productHost, rows, names, { unit: 'ton' });
      legend.innerHTML = legendHtml(names);
    } else {
      empty(productHost, t('dash.noChartData'));
      legend.innerHTML = '';
    }
  }
}

/** Pencere boyutu ya da tema değişince grafikleri yeniden çizer. */
let redrawTimer;
const scheduleRedraw = () => {
  clearTimeout(redrawTimer);
  redrawTimer = setTimeout(() => {
    if (lastDashboard && document.getElementById('chart-tanks')) drawCharts(lastDashboard);
  }, 160);
};
window.addEventListener('resize', scheduleRedraw);
window.addEventListener('epdk:theme', scheduleRedraw);

function envLabel(key) {
  const env = state.environments[key];
  if (!env) return key === 'custom' ? t('login.custom') : (key || '—');
  return pick(env, 'label');
}

/* ==========================================================================
   TABLO SAYFASI (DEP-1 / DEP-2 / DR)
   ========================================================================== */

const tableUi = new Map();   // tablo başına arama/sıralama durumu
let lastDashboard = null;    // grafiklerin yeniden çizimi için son panel verisi

/**
 * Müşteri değişince görünüm durumunu sıfırlar: bir önceki lisansın seçili
 * satırları, araması ve panel verisi taşınmamalıdır. (Kolon tercihi
 * tarayıcıda saklanır ve kullanıcıya ait bir ayardır; korunur.)
 */
export function resetViewState() {
  tableUi.clear();
  lastDashboard = null;
  slotTicker?.();
  slotTicker = null;
}

function uiState(key) {
  if (!tableUi.has(key)) {
    // DEP-1 saate, DEP-2/DR tarihe göre; en yeni kayıt üstte
    const sort = key === 'dep1' ? 'saat' : 'tarih';
    tableUi.set(key, {
      search: '', sort, dir: 'desc', tab: 'records',
      selected: new Set(), hidden: hiddenColumns(key),
    });
  }
  return tableUi.get(key);
}

function columnsFor(spec) {
  return [
    { name: 'islemZamani', label: t('table.processedAt'), kind: 'datetime' },
    ...spec.fields.map((field) => ({
      name: field.name,
      label: pick(field, 'short') || pick(field, 'label'),
      full: pick(field, 'label'),
      kind: field.kind,
      decimals: field.decimals,
    })),
  ];
}

/* Gizlenen kolonlar tarayıcıda saklanır — kullanıcı bir kez ayarlar.
   Hiç ayarlanmamışsa, tablonun yatay kaydırma olmadan sığması için servisin
   ürettiği "İşlem Zamanı" kolonu gizli başlar. */
const DEFAULT_HIDDEN = ['islemZamani'];

function hiddenColumns(key) {
  try {
    const stored = localStorage.getItem(`epdk.cols.${key}`);
    if (stored === null) return new Set(DEFAULT_HIDDEN);
    return new Set(JSON.parse(stored));
  } catch {
    return new Set(DEFAULT_HIDDEN);
  }
}

function saveHiddenColumns(key, hidden) {
  try {
    localStorage.setItem(`epdk.cols.${key}`, JSON.stringify([...hidden]));
  } catch { /* gizli mod: tercih saklanamaz, sorun değil */ }
}

function openColumnPicker(spec, columns, ui, refresh) {
  const modal = openModal({
    title: t('table.columns'),
    subtitle: t('table.columnsHelp'),
    size: 'sm',
    body: `<div>${columns.map((column) => `
      <label class="checkbox">
        <input type="checkbox" data-col="${escapeHtml(column.name)}"
               ${ui.hidden.has(column.name) ? '' : 'checked'}>
        <span>${escapeHtml(column.full || column.label)}</span>
      </label>`).join('')}</div>`,
    footer: `
      <button class="btn btn--ghost spacer" data-act="all">${escapeHtml(t('table.showAll'))}</button>
      <button class="btn btn--primary" data-act="close">${escapeHtml(t('common.close'))}</button>`,
  });

  const apply = () => { saveHiddenColumns(spec.key, ui.hidden); refresh(); };

  modal.root.querySelectorAll('[data-col]').forEach((box) =>
    box.addEventListener('change', () => {
      if (box.checked) ui.hidden.delete(box.dataset.col);
      else ui.hidden.add(box.dataset.col);
      apply();
    }));
  modal.root.querySelector('[data-act="all"]').addEventListener('click', () => {
    ui.hidden.clear();
    modal.root.querySelectorAll('[data-col]').forEach((box) => { box.checked = true; });
    apply();
  });
  modal.root.querySelector('[data-act="close"]').addEventListener('click', modal.close);
}

function cellHtml(column, row) {
  const value = row[column.name];
  switch (column.kind) {
    case 'decimal':
      return `<td class="num">${fmtNumber(value, column.decimals ?? 3)}</td>`;
    case 'integer':
      return `<td class="num">${value === null || value === undefined || value === ''
        ? '—' : escapeHtml(String(Math.round(Number(value)) || value))}</td>`;
    case 'date':
      return `<td class="nowrap">${fmtDate(value)}</td>`;
    case 'datetime':
      return `<td class="nowrap small" title="${escapeHtml(value ?? '')}">`
        + `${fmtDateTimeShort(value)}</td>`;
    case 'select':
      return `<td><span class="pill pill--soft">${escapeHtml(gumrukLabel(value))}</span></td>`;
    case 'gtip': {
      const name = gtipName(value);
      return `<td class="strong gtip-cell" title="${escapeHtml(
        [value, name].filter(Boolean).join(' — '))}">${escapeHtml(value ?? '—')}${
        name ? `<div class="small muted clip-line">${escapeHtml(name)}</div>` : ''}</td>`;
    }
    case 'tank':
      return `<td class="strong">${escapeHtml(value ?? '—')}</td>`;
    case 'upper':
      return `<td class="clip" title="${escapeHtml(value ?? '')}">${escapeHtml(value ?? '—')}</td>`;
    default:
      return `<td>${escapeHtml(value ?? '—')}</td>`;
  }
}

export async function renderTable(ctx, key) {
  const spec = tableSpec(key);
  if (!spec) { view().innerHTML = errorHtml('Bilinmeyen tablo.'); return; }
  const ui = uiState(key);

  view().innerHTML = `
    <div class="view__inner">
      <div class="card" id="table-card">
        ${spec.extra_query ? `
          <div class="tabs" role="tablist">
            <button role="tab" data-tab="records" aria-selected="${ui.tab === 'records'}">
              ${escapeHtml(t('table.records'))}
            </button>
            <button role="tab" data-tab="services" aria-selected="${ui.tab === 'services'}">
              ${escapeHtml(pick(spec.extra_query, 'label'))}
            </button>
          </div>` : ''}
        ${spec.key === 'dep1' ? '<div id="slot-strip"></div>' : ''}
        <div id="table-content">${loadingHtml()}</div>
      </div>
    </div>`;

  view().querySelectorAll('[data-tab]').forEach((button) =>
    button.addEventListener('click', () => {
      ui.tab = button.dataset.tab;
      ui.selected.clear();
      renderTable(ctx, key);
    }));

  await ensureLookups();
  if (ui.tab === 'services') await loadServices(spec);
  else await loadRecords(ctx, spec, ui);
}

async function loadServices(spec) {
  const host = document.getElementById('table-content');
  try {
    const { data } = await api.services(spec.key);
    if (!data.length) {
      host.innerHTML = emptyHtml({
        icon: '🗂️', title: t('table.emptyTitle'),
        text: 'Bu sorguda görüntülenecek kayıt yok.',
      });
      return;
    }
    const columns = Object.keys(data[0]).map((name) => ({ name, label: name }));
    host.innerHTML = `
      <div class="toolbar">
        <span class="badge">${data.length} ${escapeHtml(t('common.records'))}</span>
        <div class="toolbar__spacer"></div>
      </div>
      <div class="table-wrap">
        <table class="data">
          <thead><tr>${columns.map((c) => `<th>${escapeHtml(c.label)}</th>`).join('')}</tr></thead>
          <tbody>${data.map((row) => `<tr>${columns.map((c) => {
            const value = row[c.name];
            if (c.name === 'gumrukDurumu') return `<td>${escapeHtml(gumrukLabel(value))}</td>`;
            if (c.name === 'tarih') return `<td class="nowrap">${fmtDate(value)}</td>`;
            if (typeof value === 'number') return `<td class="num">${fmtNumber(value)}</td>`;
            return `<td>${escapeHtml(value ?? '—')}</td>`;
          }).join('')}</tr>`).join('')}</tbody>
        </table>
      </div>`;
  } catch (error) {
    host.innerHTML = errorHtml(error.message);
  }
}

async function loadRecords(ctx, spec, ui) {
  const host = document.getElementById('table-content');
  host.innerHTML = loadingHtml();

  let rows = [];
  try {
    const response = await api.list(spec.key);
    rows = response.data || [];
  } catch (error) {
    host.innerHTML = errorHtml(error.message, 'data-retry="1"');
    host.querySelector('[data-retry]')?.addEventListener('click', () => renderTable(ctx, spec.key));
    return;
  }

  const allColumns = columnsFor(spec);
  const refresh = () => loadRecords(ctx, spec, ui);

  if (spec.key === 'dep1') paintSlotCard(refresh, rows, 'slot-strip');
  const paint = () => {
    const columns = allColumns.filter((column) => !ui.hidden.has(column.name));
    const filtered = sortRows(filterRows(rows, ui.search), ui.sort, ui.dir);
    host.innerHTML = tableHtml(spec, columns, filtered, rows.length, ui);
    wire(ctx, spec, columns, allColumns, filtered, ui, refresh);
  };
  paint();
}

function tableHtml(spec, columns, rows, totalCount, ui) {
  const selectedCount = ui.selected.size;

  const toolbar = `
    <div class="toolbar">
      <input class="input search" type="search" data-role="search"
             placeholder="${escapeHtml(t('common.search'))}" value="${escapeHtml(ui.search)}">
      <span class="badge">${rows.length}${
        rows.length !== totalCount ? ` / ${totalCount}` : ''} ${escapeHtml(t('common.records'))}</span>
      ${selectedCount ? `<span class="badge badge--warn">${selectedCount} ${escapeHtml(t('common.selected'))}</span>
        <button class="btn btn--sm btn--danger" data-act="bulk-delete">
          ${icon('trash')} ${escapeHtml(t('common.deleteSelected'))}
        </button>` : ''}
      <div class="toolbar__spacer"></div>
      <button class="btn btn--sm btn--ghost" data-act="columns" title="${escapeHtml(t('table.columns'))}">
        ${icon('columns')} ${escapeHtml(t('table.columns'))}${
          ui.hidden.size ? ` (${ui.hidden.size})` : ''}
      </button>
      <button class="btn btn--sm btn--ghost" data-act="refresh">${icon('refresh')} ${escapeHtml(t('common.refresh'))}</button>
      <button class="btn btn--sm btn--ghost" data-act="export">${icon('download')} ${escapeHtml(t('common.export'))}</button>
      <button class="btn btn--sm btn--ghost" data-act="import">${icon('upload')} ${escapeHtml(t('common.import'))}</button>
      <button class="btn btn--sm btn--primary" data-act="new">${icon('plus')} ${escapeHtml(t('common.new'))}</button>
    </div>`;

  if (!rows.length) {
    return toolbar + emptyHtml({
      icon: ui.search ? '🔍' : '📭',
      title: ui.search ? t('table.noMatch') : t('table.emptyTitle'),
      text: ui.search ? '' : t('table.emptyText'),
      action: ui.search ? '' :
        `<button class="btn btn--primary" data-act="new">${icon('plus')} ${escapeHtml(t('common.new'))}</button>`,
    });
  }

  const head = columns.map((column) => {
    const active = ui.sort === column.name;
    return `<th class="sortable" data-sort="${column.name}">
      ${escapeHtml(column.label)}
      <span class="sort-mark">${active ? (ui.dir === 'asc' ? '▲' : '▼') : '↕'}</span>
    </th>`;
  }).join('');

  const body = rows.map((row) => `
    <tr data-id="${escapeHtml(row.id)}" class="${ui.selected.has(row.id) ? 'is-selected' : ''}">
      <td><input type="checkbox" data-select="${escapeHtml(row.id)}"
                 ${ui.selected.has(row.id) ? 'checked' : ''}></td>
      ${columns.map((column) => cellHtml(column, row)).join('')}
      <td class="col-actions">
        <div class="row-actions">
          <button class="icon-btn" data-act="detail" data-id="${escapeHtml(row.id)}"
                  title="${escapeHtml(t('common.details'))}">${icon('eye')}</button>
          <button class="icon-btn" data-act="edit" data-id="${escapeHtml(row.id)}"
                  title="${escapeHtml(t('common.edit'))}">${icon('edit')}</button>
          <button class="icon-btn" data-act="delete" data-id="${escapeHtml(row.id)}"
                  title="${escapeHtml(t('common.delete'))}"
                  style="color:var(--danger)">${icon('trash')}</button>
        </div>
      </td>
    </tr>`).join('');

  return `${toolbar}
    <div class="table-wrap">
      <table class="data">
        <thead><tr>
          <th style="width:34px"><input type="checkbox" data-role="select-all"></th>
          ${head}
          <th class="col-actions" style="width:98px"></th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

function wire(ctx, spec, columns, allColumns, rows, ui, refresh) {
  const host = document.getElementById('table-content');
  const find = (id) => rows.find((row) => String(row.id) === String(id));

  host.querySelector('[data-act="columns"]')?.addEventListener('click', () =>
    openColumnPicker(spec, allColumns, ui, refresh));

  const search = host.querySelector('[data-role="search"]');
  if (search) {
    let timer;
    search.addEventListener('input', () => {
      clearTimeout(timer);
      const { value } = search;
      timer = setTimeout(() => {
        ui.search = value;
        const position = search.selectionStart;
        refresh().then(() => {
          const next = document.querySelector('[data-role="search"]');
          if (next) { next.focus(); next.setSelectionRange(position, position); }
        });
      }, 260);
    });
  }

  host.querySelectorAll('[data-sort]').forEach((header) =>
    header.addEventListener('click', () => {
      const key = header.dataset.sort;
      if (ui.sort === key) ui.dir = ui.dir === 'asc' ? 'desc' : 'asc';
      else { ui.sort = key; ui.dir = 'asc'; }
      refresh();
    }));

  host.querySelectorAll('[data-select]').forEach((box) =>
    box.addEventListener('change', () => {
      const id = box.dataset.select;
      if (box.checked) ui.selected.add(id); else ui.selected.delete(id);
      refresh();
    }));

  const selectAll = host.querySelector('[data-role="select-all"]');
  if (selectAll) {
    selectAll.checked = rows.length > 0 && rows.every((row) => ui.selected.has(row.id));
    selectAll.addEventListener('change', () => {
      if (selectAll.checked) rows.forEach((row) => ui.selected.add(row.id));
      else ui.selected.clear();
      refresh();
    });
  }

  host.querySelectorAll('[data-act="new"]').forEach((button) =>
    button.addEventListener('click', () =>
      openRecordForm({ spec, onDone: refresh })));

  host.querySelector('[data-act="refresh"]')?.addEventListener('click', () => {
    ui.selected.clear();
    refresh();
  });

  host.querySelector('[data-act="import"]')?.addEventListener('click', () =>
    openImporter({ spec, onDone: refresh }));

  host.querySelector('[data-act="export"]')?.addEventListener('click', () => {
    const exportColumns = [{ name: 'id', label: 'id' }, ...columns.map((c) => ({ name: c.name, label: c.name }))];
    download(`${spec.key}-${new Date().toISOString().slice(0, 10)}.csv`,
      toCsv(rows, exportColumns));
    toast('ok', t('common.export'), `${rows.length} ${t('common.records')}`);
  });

  host.querySelectorAll('[data-act="edit"]').forEach((button) =>
    button.addEventListener('click', () =>
      openRecordForm({ spec, record: find(button.dataset.id), onDone: refresh })));

  host.querySelectorAll('[data-act="detail"]').forEach((button) =>
    button.addEventListener('click', () => showDetail(spec, find(button.dataset.id))));

  host.querySelectorAll('[data-act="delete"]').forEach((button) =>
    button.addEventListener('click', async () => {
      const row = find(button.dataset.id);
      if (state.settings.confirm_delete !== false) {
        const ok = await confirmDialog({
          title: t('table.deleteTitle'),
          text: `${t('table.deleteText')}\n\nID: ${row.id}`,
          confirmLabel: t('common.delete'),
          danger: true,
        });
        if (!ok) return;
      }
      try {
        await api.remove(spec.key, row.id);
        toast('ok', t('table.deleted'));
        ui.selected.delete(row.id);
        refresh();
      } catch (error) {
        toast('err', t('common.error'), error.message);
      }
    }));

  host.querySelector('[data-act="bulk-delete"]')?.addEventListener('click', async () => {
    const ids = [...ui.selected];
    const ok = await confirmDialog({
      title: t('table.deleteTitle'),
      text: `${ids.length} ${t('table.deleteMany')}`,
      confirmLabel: t('common.delete'),
      danger: true,
    });
    if (!ok) return;

    let done = 0;
    let failed = 0;
    for (const id of ids) {
      try {
        await api.remove(spec.key, id);
        done += 1;
      } catch {
        failed += 1;
      }
    }
    ui.selected.clear();
    toast(failed ? 'warn' : 'ok', t('table.deleted'),
      `${done} ${t('import.succeeded')}${failed ? `, ${failed} ${t('import.failed')}` : ''}`);
    refresh();
  });
}

function showDetail(spec, row) {
  if (!row) return;
  const entries = [
    ...state.readonlyColumns.map((column) => [pick(column, 'label'), row[column.name]]),
    ...spec.fields.map((field) => [pick(field, 'label'),
      field.kind === 'select' ? gumrukLabel(row[field.name]) : row[field.name]]),
  ];
  const modal = openModal({
    title: `${pick(spec, 'label')} — ${t('common.details')}`,
    subtitle: row.id,
    body: `
      <dl class="kv">
        ${entries.map(([label, value]) => `
          <dt>${escapeHtml(label)}</dt>
          <dd>${escapeHtml(value === null || value === undefined || value === '' ? '—' : value)}</dd>
        `).join('')}
      </dl>
      <p class="section-title">JSON</p>
      <pre class="code">${escapeHtml(JSON.stringify(row, null, 2))}</pre>`,
    footer: `
      <button class="btn btn--ghost spacer" data-act="copy">${escapeHtml(t('common.copy'))}</button>
      <button class="btn btn--primary" data-act="close">${escapeHtml(t('common.close'))}</button>`,
  });
  modal.root.querySelector('[data-act="close"]').addEventListener('click', modal.close);
  modal.root.querySelector('[data-act="copy"]').addEventListener('click', () =>
    copyText(JSON.stringify(row, null, 2)));
}

/* ==========================================================================
   REFERANS LİSTELER
   ========================================================================== */

export async function renderTanks() {
  view().innerHTML = `<div class="view__inner">${loadingHtml()}</div>`;
  try {
    const { data } = await api.tanks(true);
    state.lookups.tanks = data;
    const total = data.reduce((sum, tank) => sum + (Number(tank.kapasiteM3) || 0), 0);
    view().innerHTML = `
      <div class="view__inner">
        <div class="card">
          <div class="card__head">
            <div>
              <h2 class="card__title">${escapeHtml(t('lookup.tanksTitle'))}</h2>
              <p class="card__sub">${escapeHtml(t('lookup.tanksSub'))}</p>
            </div>
            <div class="card__actions">
              <span class="badge">${data.length} tank</span>
              <span class="badge badge--ok">${fmtNumber(total, 2)} m³</span>
            </div>
          </div>
          ${data.length ? `
          <div class="table-wrap">
            <table class="data">
              <thead><tr>
                <th>Tank No</th><th>Tesis (İl - İlçe)</th><th>Yakıt Türü</th>
                <th>Tank Türü</th><th class="text-right">${escapeHtml(t('lookup.capacity'))} (m³)</th>
              </tr></thead>
              <tbody>${data.map((tank) => `
                <tr>
                  <td class="strong">${escapeHtml(tank.tankNo)}</td>
                  <td>${escapeHtml(tank.tesisIlIlce ?? '—')}</td>
                  <td>${escapeHtml(tank.yakitTuru ?? '—')}</td>
                  <td><span class="pill pill--soft">${escapeHtml(tank.tankTuru ?? '—')}</span></td>
                  <td class="num">${fmtNumber(tank.kapasiteM3, 2)}</td>
                </tr>`).join('')}
              </tbody>
            </table>
          </div>` : emptyHtml({
            icon: '🛢️', title: t('table.emptyTitle'),
            text: 'Lisansınıza kayıtlı tank bulunamadı.',
          })}
        </div>
      </div>`;
  } catch (error) {
    view().innerHTML = `<div class="view__inner">${errorHtml(error.message)}</div>`;
  }
}

export async function renderGtip() {
  view().innerHTML = `<div class="view__inner">${loadingHtml()}</div>`;
  try {
    const { data } = await api.gtip(true);
    state.lookups.gtip = data;
    const draw = (needle = '') => {
      const rows = filterRows(data, needle);
      const host = document.getElementById('gtip-body');
      host.innerHTML = rows.length ? `
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>GTİP No</th><th>Petrol Türü</th><th>Başlangıç</th><th>Bitiş</th></tr></thead>
            <tbody>${rows.map((item) => `
              <tr>
                <td class="strong mono" style="font-size:13px;color:var(--text)">${escapeHtml(item.gtipNo)}</td>
                <td>${escapeHtml(item.petrolTuru)}</td>
                <td class="nowrap small">${fmtDate(item.basTarih)}</td>
                <td class="nowrap small">${item.bitTarih ? fmtDate(item.bitTarih) : '—'}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>` : emptyHtml({ icon: '🔍', title: t('table.noMatch'), text: '' });
      document.getElementById('gtip-count').textContent =
        `${rows.length} ${t('common.records')}`;
    };

    view().innerHTML = `
      <div class="view__inner">
        <div class="card">
          <div class="card__head">
            <div>
              <h2 class="card__title">${escapeHtml(t('lookup.gtipTitle'))}</h2>
              <p class="card__sub">${escapeHtml(t('lookup.gtipSub'))}</p>
            </div>
            <div class="card__actions">
              <span class="badge" id="gtip-count"></span>
            </div>
          </div>
          <div class="toolbar">
            <input class="input search" type="search" id="gtip-search"
                   placeholder="${escapeHtml(t('common.search'))}">
          </div>
          <div id="gtip-body"></div>
        </div>
      </div>`;
    draw();
    document.getElementById('gtip-search').addEventListener('input', (event) =>
      draw(event.target.value));
  } catch (error) {
    view().innerHTML = `<div class="view__inner">${errorHtml(error.message)}</div>`;
  }
}

/* ==========================================================================
   İŞLEM GEÇMİŞİ
   ========================================================================== */

const logUi = { failures: false, table: '', search: '' };

export async function renderLog(ctx) {
  view().innerHTML = `
    <div class="view__inner">
      <div class="card">
        <div class="card__head">
          <div>
            <h2 class="card__title">${escapeHtml(t('log.title'))}</h2>
            <p class="card__sub">${escapeHtml(t('log.subtitle'))}</p>
          </div>
          <div class="card__actions">
            <button class="btn btn--sm btn--ghost" data-act="clear">${icon('trash')} ${escapeHtml(t('log.clear'))}</button>
          </div>
        </div>
        <div class="toolbar">
          <input class="input search" type="search" data-role="search"
                 placeholder="${escapeHtml(t('common.search'))}" value="${escapeHtml(logUi.search)}">
          <select class="input" data-role="table" style="width:auto">
            <option value="">${escapeHtml(t('common.all'))}</option>
            ${state.tables.map((spec) => `
              <option value="${spec.key}" ${logUi.table === spec.key ? 'selected' : ''}>
                ${escapeHtml(spec.short)}
              </option>`).join('')}
          </select>
          <label class="checkbox" style="margin:0">
            <input type="checkbox" data-role="failures" ${logUi.failures ? 'checked' : ''}>
            <span>${escapeHtml(t('log.onlyErrors'))}</span>
          </label>
          <div class="toolbar__spacer"></div>
          <span class="badge" data-role="count"></span>
        </div>
        <div id="log-body">${loadingHtml()}</div>
      </div>
    </div>`;

  const load = async () => {
    const host = document.getElementById('log-body');
    try {
      const { entries } = await api.log({
        limit: 400, failures: logUi.failures, table: logUi.table, q: logUi.search,
      });
      document.querySelector('[data-role="count"]').textContent =
        `${entries.length} ${t('common.records')}`;

      host.innerHTML = entries.length ? `
        <div class="table-wrap">
          <table class="data">
            <thead><tr>
              <th>Zaman</th><th>İşlem</th><th>Tablo</th><th>Uç</th>
              <th>Sonuç</th><th>Mesaj</th><th style="width:44px"></th>
            </tr></thead>
            <tbody>${entries.map((entry) => `
              <tr>
                <td class="nowrap small">${fmtDateTime(entry.ts)}</td>
                <td class="strong">${escapeHtml(entry.action)}</td>
                <td>${entry.table_key ? `<span class="pill pill--soft">${escapeHtml(entry.table_key.toUpperCase())}</span>` : '—'}</td>
                <td class="mono">${escapeHtml(entry.endpoint || '—')}</td>
                <td><span class="pill ${entry.success ? 'pill--ok' : 'pill--err'}">
                  ${entry.success ? 'BAŞARILI' : 'HATA'}</span></td>
                <td class="small">${escapeHtml(String(entry.message || '—').slice(0, 120))}</td>
                <td><button class="icon-btn" data-detail="${entry.id}">${icon('eye')}</button></td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>` : emptyHtml({ icon: '📋', title: t('log.empty'), text: '' });

      host.querySelectorAll('[data-detail]').forEach((button) =>
        button.addEventListener('click', () => {
          const entry = entries.find((item) => String(item.id) === button.dataset.detail);
          showLogDetail(entry);
        }));
    } catch (error) {
      host.innerHTML = errorHtml(error.message);
    }
  };

  let timer;
  view().querySelector('[data-role="search"]').addEventListener('input', (event) => {
    clearTimeout(timer);
    logUi.search = event.target.value;
    timer = setTimeout(load, 280);
  });
  view().querySelector('[data-role="table"]').addEventListener('change', (event) => {
    logUi.table = event.target.value;
    load();
  });
  view().querySelector('[data-role="failures"]').addEventListener('change', (event) => {
    logUi.failures = event.target.checked;
    load();
  });
  view().querySelector('[data-act="clear"]').addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: t('log.clearConfirm'), text: t('log.clearText'),
      confirmLabel: t('log.clear'), danger: true,
    });
    if (!ok) return;
    await api.clearLog();
    toast('ok', t('log.clear'));
    load();
  });

  load();
}

function showLogDetail(entry) {
  if (!entry) return;
  const modal = openModal({
    title: t('log.detailTitle'),
    subtitle: `${entry.action} · ${fmtDateTime(entry.ts)}`,
    body: `
      <dl class="kv">
        <dt>Sonuç</dt><dd><span class="pill ${entry.success ? 'pill--ok' : 'pill--err'}">
          ${entry.success ? 'BAŞARILI' : 'HATA'}</span></dd>
        <dt>Uç nokta</dt><dd class="mono small">${escapeHtml(entry.endpoint || '—')}</dd>
        <dt>HTTP</dt><dd>${escapeHtml(entry.status ?? '—')}</dd>
        <dt>Süre</dt><dd>${entry.duration_ms ? `${entry.duration_ms} ms` : '—'}</dd>
        <dt>Kullanıcı</dt><dd>${escapeHtml(entry.username || '—')}</dd>
        <dt>Mesaj</dt><dd>${escapeHtml(entry.message || '—')}</dd>
      </dl>
      <p class="section-title">${escapeHtml(t('log.request'))}</p>
      <pre class="code">${escapeHtml(JSON.stringify(entry.request ?? {}, null, 2))}</pre>
      <p class="section-title">${escapeHtml(t('log.response'))}</p>
      <pre class="code">${escapeHtml(JSON.stringify(entry.response ?? {}, null, 2))}</pre>`,
    footer: `<button class="btn btn--primary" data-act="close">${escapeHtml(t('common.close'))}</button>`,
  });
  modal.root.querySelector('[data-act="close"]').addEventListener('click', modal.close);
}

/* ==========================================================================
   AYARLAR
   ========================================================================== */

export async function renderSettings(ctx) {
  const settings = state.settings;
  view().innerHTML = `
    <div class="view__inner grid grid--2">
      <div class="card">
        <div class="card__head"><h2 class="card__title">${escapeHtml(t('settings.connection'))}</h2></div>
        <div class="card__body">
          <div class="field">
            <label class="field__label">${escapeHtml(t('settings.timeout'))}</label>
            <input class="input" type="number" min="5" max="180" data-set="timeout"
                   value="${escapeHtml(settings.timeout)}">
          </div>
          <label class="checkbox">
            <input type="checkbox" data-set="auto_renew" ${settings.auto_renew ? 'checked' : ''}>
            <span>${escapeHtml(t('settings.autoRenew'))}</span>
          </label>
          <label class="checkbox">
            <input type="checkbox" data-set="confirm_delete" ${settings.confirm_delete !== false ? 'checked' : ''}>
            <span>${escapeHtml(t('settings.confirmDelete'))}</span>
          </label>
          <dl class="kv" style="margin-top:18px">
            <dt>${escapeHtml(t('dash.env'))}</dt><dd>${escapeHtml(envLabel(state.session.environment))}</dd>
            <dt>${escapeHtml(t('dash.address'))}</dt>
            <dd class="mono small">${escapeHtml(state.session.baseUrl || '—')}</dd>
          </dl>
        </div>
      </div>

      <div class="card">
        <div class="card__head"><h2 class="card__title">${escapeHtml(t('settings.appearance'))}</h2></div>
        <div class="card__body">
          <div class="field">
            <label class="field__label">${escapeHtml(t('settings.language'))}</label>
            <select class="input" data-set="language">
              <option value="tr" ${settings.language === 'tr' ? 'selected' : ''}>Türkçe</option>
              <option value="en" ${settings.language === 'en' ? 'selected' : ''}>English</option>
            </select>
          </div>
          <div class="field">
            <label class="field__label">${escapeHtml(t('settings.theme'))}</label>
            <select class="input" data-set="theme">
              <option value="light" ${settings.theme === 'light' ? 'selected' : ''}>${escapeHtml(t('settings.themeLight'))}</option>
              <option value="dark" ${settings.theme === 'dark' ? 'selected' : ''}>${escapeHtml(t('settings.themeDark'))}</option>
            </select>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card__head"><h2 class="card__title">${escapeHtml(t('settings.data'))}</h2></div>
        <div class="card__body">
          <div class="field">
            <label class="field__label">${escapeHtml(t('settings.logLimit'))}</label>
            <input class="input" type="number" min="100" max="20000" step="100" data-set="log_limit"
                   value="${escapeHtml(settings.log_limit)}">
          </div>
          <p class="field__help">${escapeHtml(t('settings.dataDir'))}</p>
        </div>
      </div>

      <div class="card">
        <div class="card__head"><h2 class="card__title">${escapeHtml(t('settings.about'))}</h2></div>
        <div class="card__body">
          <dl class="kv">
            <dt>Uygulama</dt><dd>${escapeHtml(state.app.name)}</dd>
            <dt>Sürüm</dt><dd>${escapeHtml(state.app.version)}</dd>
            <dt>Gümrük kodları</dt>
            <dd class="small">${state.gumrukOptions.map((option) =>
              escapeHtml(pick(option, 'label'))).join('<br>')}</dd>
          </dl>
          <p class="field__help" style="margin-top:14px">
            Bildirim kuralları EPDK “Petrol Piyasası Stok İzleme Sistemi Web Servis
            Kullanım Kılavuzu” esas alınarak uygulanmaktadır.
          </p>
        </div>
      </div>
    </div>`;

  view().querySelectorAll('[data-set]').forEach((input) =>
    input.addEventListener('change', async () => {
      const key = input.dataset.set;
      const value = input.type === 'checkbox' ? input.checked
        : (input.type === 'number' ? Number(input.value) : input.value);
      try {
        const { settings: saved } = await api.saveSettings({ [key]: value });
        state.settings = saved;
        ctx.applyPreferences();
        toast('ok', t('settings.saved'));
      } catch (error) {
        toast('err', t('common.error'), error.message);
      }
    }));
}
