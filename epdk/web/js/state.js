/* Uygulamanın paylaşılan durumu. */

import { api } from './api.js';

export const state = {
  app: { name: 'EPDK Petrol Stok İzleme', version: '' },
  environments: {},
  settings: {},
  session: { active: false },
  tables: [],
  readonlyColumns: [],
  gumrukOptions: [],
  lookups: { tanks: null, gtip: null },
};

export function tableSpec(key) {
  return state.tables.find((table) => table.key === key) || null;
}

export function gumrukLabel(value) {
  const raw = String(value === true ? 1 : value === false ? 0 : value ?? '');
  const custom = state.settings.gumruk_labels || {};
  if (custom[raw]) return `${raw} – ${custom[raw]}`;
  const option = state.gumrukOptions.find((item) => String(item.value) === raw);
  return option ? option.label_tr : raw || '—';
}

export async function loadMeta() {
  const payload = await api.meta();
  state.app = payload.app;
  state.environments = payload.environments;
  state.settings = payload.settings;
  state.session = payload.session;
  state.tables = payload.schema.tables;
  state.readonlyColumns = payload.schema.readonlyColumns;
  state.gumrukOptions = payload.schema.gumrukOptions;
  return payload;
}

/** Tank ve GTİP listelerini (bir kez) yükler; hata olursa sessizce boş bırakır. */
export async function ensureLookups(force = false) {
  const jobs = [];
  if (force || state.lookups.gtip === null) {
    jobs.push(
      api.gtip(force)
        .then((r) => { state.lookups.gtip = r.data || []; })
        .catch(() => { state.lookups.gtip = []; }),
    );
  }
  if (force || state.lookups.tanks === null) {
    jobs.push(
      api.tanks(force)
        .then((r) => { state.lookups.tanks = r.data || []; })
        .catch(() => { state.lookups.tanks = []; }),
    );
  }
  await Promise.all(jobs);
  return state.lookups;
}

export function gtipName(code) {
  const list = state.lookups.gtip || [];
  const found = list.find((item) => String(item.gtipNo) === String(code));
  return found ? found.petrolTuru : '';
}
