/* Yerel sunucuyla konuşan ince katman. */

export class ApiError extends Error {
  constructor(message, status, details) {
    super(message);
    this.status = status;
    this.details = details || null;
  }
}

async function call(path, { method = 'GET', body } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: {
        'Content-Type': 'application/json',
        // Sunucu bu başlığı zorunlu tutar (basit CSRF koruması).
        'X-Epdk-Client': 'web',
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    throw new ApiError(
      'Uygulama sunucusuna ulaşılamadı. Pencereyi kapatıp yeniden başlatmayı deneyin.',
      0,
    );
  }

  let payload = {};
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(`Sunucu beklenmeyen bir yanıt verdi (${response.status}).`, response.status);
  }

  if (!response.ok || payload.ok === false) {
    throw new ApiError(payload.error || `İstek başarısız (${response.status}).`,
      response.status, payload.details);
  }
  return payload;
}

export const api = {
  meta: () => call('/api/meta'),
  session: () => call('/api/session'),
  login: (body) => call('/api/login', { method: 'POST', body }),
  logout: () => call('/api/logout', { method: 'POST', body: {} }),
  saveSettings: (body) => call('/api/settings', { method: 'POST', body }),
  removeProfile: (body) => call('/api/profiles/remove', { method: 'POST', body }),
  dashboard: () => call('/api/dashboard'),

  tanks: (refresh = false) => call(`/api/lookup/tanks${refresh ? '?refresh=1' : ''}`),
  gtip: (refresh = false) => call(`/api/lookup/gtip${refresh ? '?refresh=1' : ''}`),

  list: (table) => call(`/api/table/${table}`),
  services: (table) => call(`/api/table/${table}/services`),
  create: (table, record, ignoreWarnings = true) =>
    call(`/api/table/${table}`, { method: 'POST', body: { record, ignoreWarnings } }),
  update: (table, record, ignoreWarnings = true) =>
    call(`/api/table/${table}`, { method: 'PUT', body: { record, ignoreWarnings } }),
  remove: (table, id) =>
    call(`/api/table/${table}/delete`, { method: 'POST', body: { id } }),
  bulk: (table, records) =>
    call(`/api/table/${table}/bulk`, { method: 'POST', body: { records } }),
  validate: (table, record) =>
    call(`/api/table/${table}/validate`, { method: 'POST', body: { record } }),

  log: (params = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== '' && value !== undefined && value !== null && value !== false) {
        query.set(key, value === true ? '1' : value);
      }
    });
    const suffix = query.toString();
    return call(`/api/log${suffix ? `?${suffix}` : ''}`);
  },
  clearLog: () => call('/api/log/clear', { method: 'POST', body: {} }),
};
