/* Kayıt ekleme / düzenleme formu. Alanlar şemadan otomatik üretilir. */

import { api } from './api.js';
import { pick, t } from './i18n.js';
import { state, gtipName } from './state.js';
import { escapeHtml, nearestHalfHour, isoDate, openModal, toast } from './ui.js';

/* ------------------------------------------------------------- alanlar */

function datalistFor(field) {
  if (field.kind === 'gtip') {
    const list = state.lookups.gtip || [];
    return `<datalist id="dl-${field.name}">${
      list.map((item) =>
        `<option value="${escapeHtml(item.gtipNo)}">${escapeHtml(item.petrolTuru)}</option>`).join('')
    }</datalist>`;
  }
  if (field.kind === 'tank') {
    const list = state.lookups.tanks || [];
    return `<datalist id="dl-${field.name}">${
      list.map((item) =>
        `<option value="${escapeHtml(item.tankNo)}">${escapeHtml(
          [item.yakitTuru, item.kapasiteM3 ? `${item.kapasiteM3} m³` : '', item.tankTuru]
            .filter(Boolean).join(' · '),
        )}</option>`).join('')
    }</datalist>`;
  }
  return '';
}

function inputFor(field, value) {
  const common = `id="f-${field.name}" name="${field.name}" class="input"`;
  const safe = escapeHtml(value ?? '');

  switch (field.kind) {
    case 'select':
      return `<select ${common}>
        ${(field.options || []).map((option) => `
          <option value="${escapeHtml(option.value)}" ${
            String(value) === String(option.value) ? 'selected' : ''
          }>${escapeHtml(pick(option, 'label'))}</option>`).join('')}
      </select>`;

    case 'date':
      return `<input ${common} type="date" value="${safe.slice(0, 10)}"
                     max="${isoDate(0)}">`;

    case 'datetime': {
      // datetime-local dakika hassasiyetiyle çalışır; 1800 sn adım :00/:30 sağlar
      const local = safe ? safe.slice(0, 16) : '';
      return `<input ${common} type="datetime-local" step="1800" value="${local}">`;
    }

    case 'gtip':
    case 'tank':
      return `<input ${common} type="text" list="dl-${field.name}" value="${safe}"
                     placeholder="${escapeHtml(field.placeholder)}" spellcheck="false"
                     autocomplete="off">${datalistFor(field)}`;

    case 'decimal':
      return `<input ${common} type="text" inputmode="decimal" value="${safe}"
                     placeholder="${escapeHtml(field.placeholder)}" autocomplete="off">`;

    case 'integer':
      return `<input ${common} type="text" inputmode="numeric" value="${safe}"
                     placeholder="${escapeHtml(field.placeholder)}" autocomplete="off">`;

    case 'upper':
      return `<input ${common} type="text" value="${safe}" data-upper="1"
                     placeholder="${escapeHtml(field.placeholder)}"
                     style="text-transform:uppercase" autocomplete="off">`;

    default:
      return `<input ${common} type="text" value="${safe}"
                     placeholder="${escapeHtml(field.placeholder)}" autocomplete="off">`;
  }
}

function fieldHtml(field, value) {
  const help = pick(field, 'help');
  return `
    <div class="field ${field.width === 'full' ? 'field--full' : ''}" data-field="${field.name}">
      <label class="field__label" for="f-${field.name}">
        ${escapeHtml(pick(field, 'label'))}${field.required ? '<span class="req">*</span>' : ''}
      </label>
      ${inputFor(field, value)}
      ${help ? `<p class="field__help">${escapeHtml(help)}</p>` : ''}
      <p class="field__error" hidden></p>
    </div>`;
}

/* ------------------------------------------------------- yardımcı hesap */

function dep1Helpers() {
  return `
    <div class="row small" style="margin:-6px 0 16px">
      <button type="button" class="link" data-calc="ton">↻ ${escapeHtml(t('table.calcTon'))}</button>
      <span class="muted">·</span>
      <button type="button" class="link" data-calc="density">↻ ${escapeHtml(t('table.calcDensity'))}</button>
    </div>`;
}

function attachCalculators(root) {
  const read = (name) => {
    const node = root.querySelector(`[name="${name}"]`);
    if (!node) return NaN;
    return Number(String(node.value).replace(',', '.'));
  };
  const write = (name, value) => {
    const node = root.querySelector(`[name="${name}"]`);
    if (node) node.value = Number(value.toFixed(3));
  };

  root.querySelectorAll('[data-calc]').forEach((button) => {
    button.addEventListener('click', () => {
      const mode = button.dataset.calc;
      if (mode === 'ton') {
        const m3 = read('tankStokM3');
        const density = read('petrolTuruYogunluk');
        if (Number.isFinite(m3) && Number.isFinite(density) && density > 0) {
          write('tankStokTon', (m3 * density) / 1000);
        } else {
          toast('warn', t('common.warning'), 'Önce m³ ve yoğunluk değerlerini girin.');
        }
      } else {
        const m3 = read('tankStokM3');
        const ton = read('tankStokTon');
        if (Number.isFinite(m3) && Number.isFinite(ton) && m3 > 0) {
          write('petrolTuruYogunluk', (ton / m3) * 1000);
        } else {
          toast('warn', t('common.warning'), 'Önce m³ ve ton değerlerini girin.');
        }
      }
    });
  });
}

/* ---------------------------------------------------------- okuma/yazma */

export function readForm(spec, root) {
  const record = {};
  spec.fields.forEach((field) => {
    const node = root.querySelector(`[name="${field.name}"]`);
    if (!node) return;
    let value = node.value;
    if (field.kind === 'datetime' && value) {
      value = value.length === 16 ? `${value}:00` : value;
    }
    if (field.kind === 'upper' && value) {
      value = value.toLocaleUpperCase('tr');
    }
    record[field.name] = typeof value === 'string' ? value.trim() : value;
  });
  return record;
}

function clearIssues(root) {
  root.querySelectorAll('.field').forEach((node) => {
    node.classList.remove('has-error');
    const error = node.querySelector('.field__error');
    if (error) { error.hidden = true; error.textContent = ''; }
  });
  const banner = root.querySelector('[data-role="issues"]');
  if (banner) banner.innerHTML = '';
}

function showIssues(root, errors = [], warnings = []) {
  clearIssues(root);
  const general = [];

  const place = (issue, isError) => {
    const holder = root.querySelector(`.field[data-field="${issue.field}"]`);
    const message = pick(issue, 'message');
    if (!holder) { general.push({ ...issue, message }); return; }
    if (isError) holder.classList.add('has-error');
    const target = holder.querySelector('.field__error');
    target.hidden = false;
    target.className = isError ? 'field__error' : 'field__warn';
    target.textContent = message;
  };

  errors.forEach((issue) => place(issue, true));
  warnings.forEach((issue) => place(issue, false));

  const banner = root.querySelector('[data-role="issues"]');
  if (!banner) return;
  const blocks = [];
  if (errors.length) {
    blocks.push(`<div class="hint hint--err"><div>
      <b>${escapeHtml(t('table.validationFailed'))}</b>
      <ul>${errors.map((i) => `<li>${escapeHtml(pick(i, 'message'))}</li>`).join('')}</ul>
    </div></div>`);
  } else if (warnings.length) {
    blocks.push(`<div class="hint hint--warn"><div>
      <b>${escapeHtml(t('table.warningsTitle'))}</b>
      <ul>${warnings.map((i) => `<li>${escapeHtml(pick(i, 'message'))}</li>`).join('')}</ul>
    </div></div>`);
  }
  if (general.length && !errors.length && !warnings.length) {
    blocks.push(`<div class="hint hint--err">${
      general.map((i) => escapeHtml(i.message)).join('<br>')}</div>`);
  }
  banner.innerHTML = blocks.join('');
}

/* ------------------------------------------------------------- pencere */

/**
 * Kayıt formunu açar.
 * @param {object} options.spec   tablo tanımı
 * @param {object} options.record düzenlenecek kayıt (boşsa yeni kayıt)
 * @param {Function} options.onDone başarıdan sonra çağrılır
 */
export function openRecordForm({ spec, record = null, onDone }) {
  const editing = Boolean(record && record.id);
  const initial = { ...(record || {}) };

  if (!editing) {
    // Akıllı varsayılanlar
    if (spec.fields.some((f) => f.name === 'saat')) initial.saat = nearestHalfHour();
    if (spec.fields.some((f) => f.name === 'tarih')) initial.tarih = isoDate(0);
    if (spec.fields.some((f) => f.name === 'gumrukDurumu') && !initial.gumrukDurumu) {
      initial.gumrukDurumu = '1';
    }
  }

  const isDep1 = spec.key === 'dep1';
  const bodyHtml = `
    <div data-role="issues"></div>
    <form class="form-grid" id="record-form" novalidate>
      ${spec.fields.map((field) => fieldHtml(field, initial[field.name])).join('')}
    </form>
    ${isDep1 ? dep1Helpers() : ''}
    <div class="hint hint--info">
      <div>
        <b>${escapeHtml(t('table.uniqueRule'))}:</b> ${escapeHtml(spec.unique_tr)}
      </div>
    </div>`;

  const modal = openModal({
    title: editing ? t('table.editTitle') : t('table.newTitle'),
    subtitle: `${pick(spec, 'label')} · ${pick(spec, 'desc')}`,
    body: bodyHtml,
    footer: `
      <button class="btn btn--ghost spacer" data-act="check">${escapeHtml(t('table.checkFirst'))}</button>
      <button class="btn btn--ghost" data-act="cancel">${escapeHtml(t('common.cancel'))}</button>
      <button class="btn btn--primary" data-act="submit">
        ${escapeHtml(editing ? t('common.save') : t('common.send'))}
      </button>`,
  });

  const root = modal.root;
  attachCalculators(root);

  // Unvan alanları her zaman büyük harf
  root.querySelectorAll('[data-upper]').forEach((node) => {
    node.addEventListener('input', () => {
      const position = node.selectionStart;
      node.value = node.value.toLocaleUpperCase('tr');
      node.setSelectionRange(position, position);
    });
  });

  // GTİP seçilince adını göster
  const gtipInput = root.querySelector('[name="petrolTuruGTIPNo"]');
  if (gtipInput) {
    const holder = gtipInput.closest('.field').querySelector('.field__help');
    const original = holder ? holder.textContent : '';
    const sync = () => {
      if (!holder) return;
      const name = gtipName(gtipInput.value.trim());
      holder.textContent = name ? `→ ${name}` : original;
      holder.style.color = name ? 'var(--primary)' : '';
    };
    gtipInput.addEventListener('input', sync);
    sync();
  }

  const submitButton = root.querySelector('[data-act="submit"]');
  const checkButton = root.querySelector('[data-act="check"]');

  root.querySelector('[data-act="cancel"]').addEventListener('click', modal.close);

  checkButton.addEventListener('click', async () => {
    checkButton.disabled = true;
    try {
      const result = await api.validate(spec.key, readForm(spec, root));
      showIssues(root, result.errors, result.warnings);
      if (result.valid && !result.warnings.length) {
        toast('ok', t('common.success'), 'Kayıt kurallara uygun görünüyor.');
      }
    } catch (error) {
      toast('err', t('common.error'), error.message);
    } finally {
      checkButton.disabled = false;
    }
  });

  const submit = async () => {
    const payload = readForm(spec, root);
    if (editing) payload.id = record.id;

    submitButton.disabled = true;
    const label = submitButton.textContent;
    submitButton.innerHTML = '<span class="spinner"></span>';
    try {
      const response = editing
        ? await api.update(spec.key, payload)
        : await api.create(spec.key, payload);

      const newId = response.result?.message;
      toast('ok', editing ? t('table.updated') : t('table.saved'),
        newId && String(newId).length > 8 ? `ID: ${newId}` : '');
      modal.close();
      onDone?.();
    } catch (error) {
      if (error.status === 422 && error.details) {
        showIssues(root, error.details.errors || [], error.details.warnings || []);
        toast('err', t('common.error'), t('table.validationFailed'));
      } else {
        showIssues(root, [], []);
        const banner = root.querySelector('[data-role="issues"]');
        banner.innerHTML = `<div class="hint hint--err"><div>
          <b>${escapeHtml(t('common.error'))}:</b> ${escapeHtml(error.message)}</div></div>`;
        toast('err', t('common.error'), error.message);
      }
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = label;
    }
  };

  submitButton.addEventListener('click', submit);
  root.querySelector('#record-form').addEventListener('submit', (event) => {
    event.preventDefault();
    submit();
  });
}
