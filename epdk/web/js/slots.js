/* DEP-1 yarım saatlik bildirim döngüsü.
 *
 * Kılavuz: DEP-1 verisi yalnızca tam saat (:00) ve buçuklarda (:30) kabul
 * edilir. Depolar da bu ritimle çalışır: 16:00, 16:30, 17:00 …
 *
 * Bu modül "şu an açık olan saat dilimi", "sıradaki dilime kalan süre" ve
 * "bu dilim için hangi tanklar bildirilmedi" bilgisini üretir.
 */

import { openRecordForm } from './form.js';
import { t } from './i18n.js';
import { state, tableSpec } from './state.js';
import { escapeHtml, icon } from './ui.js';

export const SLOT_MINUTES = 30;

const pad = (value) => String(value).padStart(2, '0');

/** Bir tarihi EPDK'nın beklediği YYYY-AA-GGTSS:DD:00 biçimine çevirir. */
export function toSlotIso(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
       + `T${pad(date.getHours())}:${pad(date.getMinutes())}:00`;
}

/** Verilen ana ait (ya da ondan önceki) yarım saatlik dilim. */
export function slotFor(date = new Date()) {
  const slot = new Date(date);
  slot.setSeconds(0, 0);
  slot.setMinutes(slot.getMinutes() < SLOT_MINUTES ? 0 : SLOT_MINUTES);
  return slot;
}

/**
 * Bildirim döngüsünün o anki durumu.
 * @param {Array} rows   DEP-1 kayıtları
 * @param {Array} tanks  lisansa kayıtlı tanklar
 */
export function slotStatus(rows = [], tanks = [], now = new Date()) {
  const current = slotFor(now);
  const next = new Date(current.getTime() + SLOT_MINUTES * 60000);
  const currentIso = toSlotIso(current);

  const reported = new Set(
    rows.filter((row) => String(row.saat ?? '').slice(0, 16) === currentIso.slice(0, 16))
      .map((row) => String(row.tankNumarasi ?? '')),
  );

  const list = tanks.map((tank) => ({
    tankNo: String(tank.tankNo ?? ''),
    fuel: tank.yakitTuru || '',
    done: reported.has(String(tank.tankNo ?? '')),
  }));

  return {
    current,
    currentIso,
    next,
    secondsToNext: Math.max(0, Math.round((next.getTime() - now.getTime()) / 1000)),
    label: `${pad(current.getHours())}:${pad(current.getMinutes())}`,
    nextLabel: `${pad(next.getHours())}:${pad(next.getMinutes())}`,
    tanks: list,
    done: list.filter((item) => item.done).length,
    total: list.length,
    // Tank listesi hiç yüklenemediyse eksik tank çıkarımı yapılamaz
    known: list.length > 0,
  };
}

export function formatCountdown(seconds) {
  const total = Math.max(0, seconds);
  const minutes = Math.floor(total / 60);
  return `${pad(minutes)}:${pad(total % 60)}`;
}

/* ------------------------------------------------------------- görünüm */

/**
 * Bildirim döngüsü kartı.
 * @param {object} status slotStatus() çıktısı
 * @param {boolean} compact tablo sayfasındaki dar şerit için
 */
export function slotCardHtml(status, { compact = false } = {}) {
  const complete = status.known && status.done === status.total;
  const chips = status.tanks.map((tank) => `
    <button class="slot-chip ${tank.done ? 'is-done' : ''}"
            data-slot-tank="${escapeHtml(tank.tankNo)}"
            title="${escapeHtml(tank.done
              ? `${tank.tankNo} — ${t('slot.reported')}`
              : `${tank.tankNo} — ${t('slot.addNow')}`)}">
      <span class="slot-chip__mark">${tank.done ? '✓' : '+'}</span>
      ${escapeHtml(tank.tankNo)}
    </button>`).join('');

  const progress = status.known
    ? `<div class="slot-progress">
         <div class="slot-progress__bar" style="width:${
           status.total ? (status.done / status.total) * 100 : 0}%"></div>
       </div>`
    : '';

  return `
    <div class="slot ${compact ? 'slot--compact' : ''} ${complete ? 'is-complete' : ''}">
      <div class="slot__clock">
        <div class="slot__label">${escapeHtml(t('slot.openSlot'))}</div>
        <div class="slot__time">${escapeHtml(status.label)}</div>
      </div>

      <div class="slot__next">
        <div class="slot__label">${escapeHtml(t('slot.nextSlot'))} · ${escapeHtml(status.nextLabel)}</div>
        <div class="slot__countdown" data-slot-countdown>${
          escapeHtml(formatCountdown(status.secondsToNext))}</div>
      </div>

      <div class="slot__tanks">
        <div class="slot__label">
          ${status.known
            ? `${status.done}/${status.total} ${escapeHtml(t('slot.tanksReported'))}`
            : escapeHtml(t('slot.noTankList'))}
        </div>
        ${progress}
        <div class="slot__chips">${chips}</div>
      </div>

      <div class="slot__action">
        ${complete
          ? `<span class="badge badge--ok">${icon('check')} ${escapeHtml(t('slot.allDone'))}</span>`
          : `<button class="btn btn--sm btn--primary" data-slot-new>
               ${icon('plus')} ${escapeHtml(t('common.new'))}
             </button>`}
      </div>
    </div>`;
}

/** Karttaki düğmeleri bağlar: eksik tank → önceden doldurulmuş form. */
export function wireSlotCard(root, status, onDone) {
  const spec = tableSpec('dep1');
  if (!spec) return;

  const openFor = (tankNo) => openRecordForm({
    spec,
    // Kayıt açık saat dilimine sabitlenir; kullanıcı yine değiştirebilir
    record: { saat: status.currentIso, ...(tankNo ? { tankNumarasi: tankNo } : {}) },
    onDone,
  });

  root.querySelectorAll('[data-slot-tank]').forEach((chip) =>
    chip.addEventListener('click', () => openFor(chip.dataset.slotTank)));
  root.querySelector('[data-slot-new]')?.addEventListener('click', () => openFor(''));
}

/**
 * Sayfada görünen geri sayımları saniyede bir günceller.
 * Dönen değer, sayaç kaldırılırken çağrılacak temizleme işlevidir.
 */
export function startSlotTicker(getStatus, onSlotChange) {
  let lastSlot = getStatus().currentIso;
  const timer = setInterval(() => {
    const nodes = document.querySelectorAll('[data-slot-countdown]');
    if (!nodes.length) return;
    const status = getStatus();
    if (status.currentIso !== lastSlot) {
      // Yeni saat dilimi başladı: sayımı ve tank durumunu tazele
      lastSlot = status.currentIso;
      onSlotChange?.();
      return;
    }
    nodes.forEach((node) => {
      node.textContent = formatCountdown(status.secondsToNext);
    });
  }, 1000);
  return () => clearInterval(timer);
}

/** Panel/tablo görünümleri için hazır durum (yerel duruma göre hesaplanır). */
export function currentSlotStatus(rows) {
  return slotStatus(rows, state.lookups.tanks || []);
}
