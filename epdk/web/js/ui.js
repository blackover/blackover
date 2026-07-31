/* Ortak arayüz yardımcıları: bildirim, pencere, biçimlendirme, CSV. */

import { t } from './i18n.js';

/* ------------------------------------------------------------------ metin */

export function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function initials(text) {
  const clean = String(text || '').replace(/^WSU-/, '');
  const match = clean.match(/[A-Za-zÇĞİÖŞÜçğıöşü]{1,2}/);
  return (match ? match[0] : '?').toUpperCase().slice(0, 2);
}

/* --------------------------------------------------------------- biçimler */

export function fmtNumber(value, decimals = 3) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (Number.isNaN(number)) return escapeHtml(value);
  return number.toLocaleString('tr-TR', {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
}

export function fmtDateTime(value) {
  if (!value) return '—';
  const date = new Date(String(value).replace(' ', 'T'));
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return date.toLocaleString('tr-TR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

/** Listelerde yer kazanmak için kısa biçim: aynı yıl içindeyse yıl atlanır. */
export function fmtDateTimeShort(value) {
  if (!value) return '—';
  const date = new Date(String(value).replace(' ', 'T'));
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleString('tr-TR', {
    day: '2-digit', month: '2-digit',
    ...(sameYear ? {} : { year: '2-digit' }),
    hour: '2-digit', minute: '2-digit',
  });
}

export function fmtDate(value) {
  if (!value) return '—';
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return date.toLocaleDateString('tr-TR', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
}

export function relativeTime(value) {
  if (!value) return '—';
  const date = new Date(String(value).replace(' ', 'T'));
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60) return 'az önce';
  if (diff < 3600) return `${Math.floor(diff / 60)} dk önce`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} sa önce`;
  return fmtDateTime(value);
}

/** Bugünün / dünün tarihini YYYY-MM-DD olarak verir. */
export function isoDate(offsetDays = 0) {
  const date = new Date();
  date.setDate(date.getDate() + offsetDays);
  return date.toISOString().slice(0, 10);
}

/** En yakın geçmiş yarım saati YYYY-MM-DDTHH:MM:SS olarak verir (DEP-1 için). */
export function nearestHalfHour() {
  const date = new Date();
  date.setSeconds(0, 0);
  date.setMinutes(date.getMinutes() < 30 ? 0 : 30);
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
       + `T${pad(date.getHours())}:${pad(date.getMinutes())}:00`;
}

/* --------------------------------------------------------------- ikonlar */

const ICONS = {
  dashboard: '<path d="M3 13h8V3H3v10zM13 21h8V11h-8v10zM13 3v6h8V3h-8zM3 21h8v-6H3v6z"/>',
  tank: '<rect x="3" y="7" width="18" height="13" rx="3"/><path d="M7 7V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v2M7 14h10"/>',
  warehouse: '<path d="M3 21V9l9-5 9 5v12"/><path d="M9 21v-7h6v7"/>',
  truck: '<path d="M3 16V6h11v10M14 9h4l3 3v4h-7"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  fuel: '<path d="M4 20V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v15M3 20h12M14 9h3a2 2 0 0 1 2 2v6a1.5 1.5 0 0 0 3 0v-8l-3-3"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4a1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H1a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 2.6 7a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H7a1.7 1.7 0 0 0 1-1.5V1a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V7a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  refresh: '<path d="M21 2v6h-6M3 22v-6h6"/><path d="M3.5 9a9 9 0 0 1 14.9-3.4L21 8M21 15a9 9 0 0 1-14.9 3.4L3 16"/>',
  edit: '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4z"/>',
  trash: '<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  columns: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
};

export function icon(name, className = '') {
  const path = ICONS[name] || ICONS.list;
  return `<svg viewBox="0 0 24 24" class="${className}">${path}</svg>`;
}

/* ------------------------------------------------------------- bildirim */

const TOAST_ICONS = { ok: '✓', err: '✕', warn: '!', info: 'i' };

export function toast(type, title, text = '', timeout = 5200) {
  const host = document.getElementById('toasts');
  const node = document.createElement('div');
  node.className = `toast toast--${type}`;
  node.innerHTML = `
    <div class="toast__icon">${TOAST_ICONS[type] || 'i'}</div>
    <div>
      <div class="toast__title">${escapeHtml(title)}</div>
      ${text ? `<div class="toast__text">${escapeHtml(text)}</div>` : ''}
    </div>
    <button class="toast__close" aria-label="Kapat">×</button>`;
  host.appendChild(node);

  const dismiss = () => {
    node.style.opacity = '0';
    node.style.transform = 'translateX(20px)';
    node.style.transition = '.18s';
    setTimeout(() => node.remove(), 180);
  };
  node.querySelector('.toast__close').addEventListener('click', dismiss);
  if (timeout) setTimeout(dismiss, timeout);
  return dismiss;
}

/* --------------------------------------------------------------- pencere */

/**
 * Modal pencere açar.
 * @returns {{close: Function, root: HTMLElement, body: HTMLElement}}
 */
export function openModal({ title, subtitle = '', body = '', footer = '', size = '' }) {
  const root = document.getElementById('modal-root');
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  backdrop.innerHTML = `
    <div class="modal ${size ? `modal--${size}` : ''}" role="dialog" aria-modal="true">
      <div class="modal__head">
        <div>
          <h2 class="modal__title">${escapeHtml(title)}</h2>
          ${subtitle ? `<p class="modal__sub">${escapeHtml(subtitle)}</p>` : ''}
        </div>
        <button class="icon-btn modal__close" aria-label="${t('common.close')}">✕</button>
      </div>
      <div class="modal__body"></div>
      ${footer ? `<div class="modal__foot">${footer}</div>` : ''}
    </div>`;

  const bodyNode = backdrop.querySelector('.modal__body');
  if (body instanceof Node) bodyNode.appendChild(body);
  else bodyNode.innerHTML = body;

  const close = () => {
    document.removeEventListener('keydown', onKey);
    backdrop.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };

  backdrop.querySelector('.modal__close').addEventListener('click', close);
  backdrop.addEventListener('mousedown', (event) => {
    if (event.target === backdrop) close();
  });
  document.addEventListener('keydown', onKey);

  root.appendChild(backdrop);
  setTimeout(() => {
    const focusable = backdrop.querySelector('input, select, textarea, button.btn--primary');
    focusable?.focus();
  }, 40);

  return { close, root: backdrop, body: bodyNode };
}

export function confirmDialog({ title, text, confirmLabel, danger = false }) {
  return new Promise((resolve) => {
    const modal = openModal({
      title,
      size: 'sm',
      body: `<p style="margin:0;line-height:1.65;color:var(--text-soft);font-size:14px">${escapeHtml(text)}</p>`,
      footer: `
        <button class="btn btn--ghost" data-act="no">${escapeHtml(t('common.cancel'))}</button>
        <button class="btn ${danger ? 'btn--danger' : 'btn--primary'}" data-act="yes">
          ${escapeHtml(confirmLabel || t('common.yes'))}
        </button>`,
    });
    modal.root.querySelector('[data-act="no"]').addEventListener('click', () => {
      modal.close(); resolve(false);
    });
    modal.root.querySelector('[data-act="yes"]').addEventListener('click', () => {
      modal.close(); resolve(true);
    });
  });
}

/* -------------------------------------------------------------- durumlar */

export function loadingHtml(text = t('common.loading')) {
  return `<div class="loading"><span class="spinner"></span><span>${escapeHtml(text)}</span></div>`;
}

export function emptyHtml({ icon: emoji = '📭', title, text, action = '' }) {
  return `
    <div class="empty">
      <div class="empty__icon">${emoji}</div>
      <div class="empty__title">${escapeHtml(title)}</div>
      <p class="empty__text">${escapeHtml(text)}</p>
      ${action}
    </div>`;
}

export function errorHtml(message, retryAttr = '') {
  return `
    <div class="empty">
      <div class="empty__icon">⚠️</div>
      <div class="empty__title">${escapeHtml(t('common.error'))}</div>
      <p class="empty__text">${escapeHtml(message)}</p>
      ${retryAttr ? `<button class="btn btn--ghost" ${retryAttr}>${escapeHtml(t('common.retry'))}</button>` : ''}
    </div>`;
}

/* ------------------------------------------------------------------ CSV */

export function toCsv(rows, columns) {
  const escapeCell = (value) => {
    const text = value === null || value === undefined ? '' : String(value);
    return /[";\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const lines = [columns.map((c) => escapeCell(c.label)).join(';')];
  rows.forEach((row) => {
    lines.push(columns.map((c) => escapeCell(row[c.name])).join(';'));
  });
  // Excel'in UTF-8 tanıması için BOM
  return `﻿${lines.join('\r\n')}`;
}

/** Noktalı virgül ya da virgül ayraçlı CSV metnini satır dizisine çevirir. */
export function parseCsv(text) {
  const clean = text.replace(/^﻿/, '').replace(/\r\n/g, '\n').trim();
  if (!clean) return [];

  const firstLine = clean.split('\n')[0];
  const delimiter = (firstLine.match(/;/g) || []).length >= (firstLine.match(/,/g) || []).length
    ? ';' : ',';

  const rows = [];
  let row = [];
  let cell = '';
  let quoted = false;

  for (let i = 0; i < clean.length; i += 1) {
    const char = clean[i];
    if (quoted) {
      if (char === '"') {
        if (clean[i + 1] === '"') { cell += '"'; i += 1; }
        else quoted = false;
      } else cell += char;
    } else if (char === '"') {
      quoted = true;
    } else if (char === delimiter) {
      row.push(cell.trim()); cell = '';
    } else if (char === '\n') {
      row.push(cell.trim()); rows.push(row); row = []; cell = '';
    } else {
      cell += char;
    }
  }
  row.push(cell.trim());
  rows.push(row);
  return rows.filter((line) => line.some((value) => value !== ''));
}

export function download(filename, content, mime = 'text/csv;charset=utf-8') {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    toast('ok', t('common.copied'));
  } catch {
    toast('warn', t('common.error'), 'Panoya kopyalanamadı.');
  }
}

/* ------------------------------------------------------------- sıralama */

export function sortRows(rows, key, direction) {
  const factor = direction === 'desc' ? -1 : 1;
  return [...rows].sort((a, b) => {
    const left = a[key];
    const right = b[key];
    const leftNumber = Number(left);
    const rightNumber = Number(right);
    if (!Number.isNaN(leftNumber) && !Number.isNaN(rightNumber)
        && left !== '' && right !== '' && left !== null && right !== null) {
      return (leftNumber - rightNumber) * factor;
    }
    return String(left ?? '').localeCompare(String(right ?? ''), 'tr') * factor;
  });
}

export function filterRows(rows, needle) {
  const query = needle.trim().toLocaleLowerCase('tr');
  if (!query) return rows;
  return rows.filter((row) =>
    Object.values(row).some((value) =>
      String(value ?? '').toLocaleLowerCase('tr').includes(query)));
}
