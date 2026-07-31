/* CSV'den toplu kayıt gönderimi. */

import { api } from './api.js';
import { pick, t } from './i18n.js';
import { download, escapeHtml, openModal, parseCsv, toast } from './ui.js';

function templateFor(spec) {
  const header = spec.fields.map((field) => field.name).join(';');
  const sample = spec.fields.map((field) => field.placeholder || '').join(';');
  return `﻿${header}\r\n${sample}`;
}

/** Başlık satırını alan adlarına eşler (Türkçe etiketleri de kabul eder). */
function mapHeader(spec, header) {
  const normalize = (value) => String(value || '')
    .replace(/^﻿/, '').trim().toLocaleLowerCase('tr');

  const byName = new Map();
  spec.fields.forEach((field) => {
    byName.set(normalize(field.name), field.name);
    byName.set(normalize(pick(field, 'label')), field.name);
    byName.set(normalize(field.label_tr), field.name);
    byName.set(normalize(field.label_en), field.name);
  });

  return header.map((cell) => byName.get(normalize(cell)) || null);
}

export function openImporter({ spec, onDone }) {
  const modal = openModal({
    title: `${t('import.title')} — ${pick(spec, 'label')}`,
    subtitle: t('import.subtitle'),
    size: 'wide',
    body: `
      <div class="row" style="margin-bottom:16px">
        <button class="btn btn--ghost btn--sm" data-act="template">⬇ ${escapeHtml(t('import.template'))}</button>
        <span class="muted small">${escapeHtml(spec.fields.map((f) => f.name).join(' ; '))}</span>
      </div>

      <div class="dropzone" data-role="drop">
        <div style="font-size:26px;margin-bottom:8px">📄</div>
        <div>${escapeHtml(t('import.drop'))}</div>
        <input type="file" accept=".csv,text/csv,text/plain" hidden data-role="file">
      </div>

      <p class="section-title">${escapeHtml(t('import.paste'))}</p>
      <textarea class="input" data-role="paste" rows="5"
                placeholder="${escapeHtml(spec.fields.map((f) => f.name).join(';'))}"></textarea>

      <div data-role="preview"></div>`,
    footer: `
      <span class="spacer small muted" data-role="status"></span>
      <button class="btn btn--ghost" data-act="cancel">${escapeHtml(t('common.cancel'))}</button>
      <button class="btn btn--primary" data-act="send" disabled>${escapeHtml(t('import.sendAll'))}</button>`,
  });

  const root = modal.root;
  const preview = root.querySelector('[data-role="preview"]');
  const statusNode = root.querySelector('[data-role="status"]');
  const sendButton = root.querySelector('[data-act="send"]');
  const fileInput = root.querySelector('[data-role="file"]');
  const dropzone = root.querySelector('[data-role="drop"]');

  let records = [];

  const renderPreview = (rows, problems) => {
    if (!rows.length) {
      preview.innerHTML = '';
      sendButton.disabled = true;
      statusNode.textContent = '';
      return;
    }
    const columns = spec.fields.map((field) => field.name);
    const shown = rows.slice(0, 50);

    preview.innerHTML = `
      <p class="section-title">${escapeHtml(t('import.preview'))}</p>
      ${problems.length ? `<div class="hint hint--warn"><div>${
        escapeHtml(problems.join(' '))}</div></div>` : ''}
      <div class="card"><div class="table-wrap" style="max-height:320px;overflow-y:auto">
        <table class="data">
          <thead><tr><th>#</th>${
            columns.map((name) => `<th>${escapeHtml(name)}</th>`).join('')
          }</tr></thead>
          <tbody>
            ${shown.map((row, index) => `
              <tr>
                <td class="mono">${index + 1}</td>
                ${columns.map((name) => `<td>${escapeHtml(row[name] ?? '')}</td>`).join('')}
              </tr>`).join('')}
          </tbody>
        </table>
      </div></div>
      ${rows.length > shown.length
        ? `<p class="small muted" style="margin-top:8px">… ${rows.length - shown.length} satır daha</p>`
        : ''}`;

    statusNode.textContent = `${rows.length} ${t('import.rows')}`;
    sendButton.disabled = false;
  };

  const ingest = (text) => {
    const rows = parseCsv(text);
    if (rows.length < 2) {
      records = [];
      preview.innerHTML = `<div class="hint hint--err">${escapeHtml(t('import.emptyFile'))}</div>`;
      sendButton.disabled = true;
      return;
    }

    const mapping = mapHeader(spec, rows[0]);
    const recognised = mapping.filter(Boolean).length;
    const problems = [];
    if (!recognised) {
      records = [];
      preview.innerHTML = `<div class="hint hint--err">${escapeHtml(t('import.badHeader'))}</div>`;
      sendButton.disabled = true;
      return;
    }
    const missing = spec.fields
      .filter((field) => field.required && !mapping.includes(field.name))
      .map((field) => field.name);
    if (missing.length) problems.push(`Eksik sütun(lar): ${missing.join(', ')}.`);

    records = rows.slice(1).map((line) => {
      const record = {};
      mapping.forEach((name, index) => {
        if (name) record[name] = line[index] ?? '';
      });
      return record;
    }).filter((record) => Object.values(record).some((value) => String(value).trim() !== ''));

    renderPreview(records, problems);
  };

  root.querySelector('[data-act="template"]').addEventListener('click', () => {
    download(`${spec.key}-sablon.csv`, templateFor(spec));
  });

  root.querySelector('[data-act="cancel"]').addEventListener('click', modal.close);

  dropzone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files?.[0];
    if (file) ingest(await file.text());
  });
  ['dragenter', 'dragover'].forEach((type) =>
    dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.add('is-over');
    }));
  ['dragleave', 'drop'].forEach((type) =>
    dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.remove('is-over');
    }));
  dropzone.addEventListener('drop', async (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) ingest(await file.text());
  });

  let pasteTimer;
  root.querySelector('[data-role="paste"]').addEventListener('input', (event) => {
    clearTimeout(pasteTimer);
    const { value } = event.target;
    pasteTimer = setTimeout(() => { if (value.trim()) ingest(value); }, 350);
  });

  sendButton.addEventListener('click', async () => {
    if (!records.length) return;
    sendButton.disabled = true;
    preview.innerHTML = `
      <p class="section-title">${escapeHtml(t('import.sending'))}…</p>
      <div class="progress"><div class="progress__bar" style="width:12%"></div></div>
      <p class="small muted" style="margin-top:10px">${records.length} ${escapeHtml(t('common.records'))}</p>`;

    try {
      const response = await api.bulk(spec.key, records);
      const { results, summary } = response;
      const failures = results.filter((item) => !item.success);

      preview.innerHTML = `
        <p class="section-title">${escapeHtml(t('import.done'))}</p>
        <div class="hint ${failures.length ? 'hint--warn' : 'hint--ok'}"><div>
          <b>${summary.succeeded}</b> ${escapeHtml(t('import.succeeded'))} ·
          <b>${summary.failed}</b> ${escapeHtml(t('import.failed'))}
        </div></div>
        ${failures.length ? `
          <div class="card"><div class="table-wrap" style="max-height:280px;overflow-y:auto">
            <table class="data">
              <thead><tr><th>#</th><th>Hata</th></tr></thead>
              <tbody>${failures.map((item) => `
                <tr><td class="mono">${item.index + 1}</td>
                    <td>${escapeHtml(item.message || '')}</td></tr>`).join('')}
              </tbody>
            </table>
          </div></div>` : ''}`;

      statusNode.textContent = '';
      sendButton.textContent = t('common.close');
      sendButton.disabled = false;
      sendButton.onclick = () => { modal.close(); onDone?.(); };

      toast(failures.length ? 'warn' : 'ok', t('import.done'),
        `${summary.succeeded} ${t('import.succeeded')}, ${summary.failed} ${t('import.failed')}`);
    } catch (error) {
      preview.innerHTML = `<div class="hint hint--err">${escapeHtml(error.message)}</div>`;
      sendButton.disabled = false;
      toast('err', t('common.error'), error.message);
    }
  });
}
