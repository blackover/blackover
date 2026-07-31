/* Uygulama girişi: oturum, menü, yönlendirme. */

import { api } from './api.js';
import { applyStatic, getLanguage, pick, setLanguage, t } from './i18n.js';
import {
  ensureLookups, loadMeta, resetCustomerState, state, tableSpec,
} from './state.js';
import { escapeHtml, fmtDuration, icon, initials, toast } from './ui.js';
import {
  renderDashboard, renderGtip, renderLog, renderSettings, renderTable,
  renderTanks, resetViewState,
} from './views.js';

const appNode = document.getElementById('app');
const loginNode = document.getElementById('login-screen');

let currentRoute = 'dashboard';
let tokenTimer = null;
let expiryWarned = false;

const ctx = { navigate, applyPreferences };

/* ------------------------------------------------------------- yönlendirme */

function routes() {
  const tables = state.tables.map((spec) => ({
    key: spec.key,
    label: pick(spec, 'label'),
    subtitle: pick(spec, 'desc'),
    icon: spec.icon,
    group: 'nav.submissions',
    render: () => renderTable(ctx, spec.key),
  }));

  return [
    {
      key: 'dashboard', label: t('nav.dashboard'), subtitle: t('dash.subtitle'),
      icon: 'dashboard', group: '', render: () => renderDashboard(ctx),
    },
    ...tables,
    {
      key: 'tanks', label: t('nav.tanks'), subtitle: t('lookup.tanksSub'),
      icon: 'tank', group: 'nav.reference', render: () => renderTanks(),
    },
    {
      key: 'gtip', label: t('nav.gtip'), subtitle: t('lookup.gtipSub'),
      icon: 'fuel', group: 'nav.reference', render: () => renderGtip(),
    },
    {
      key: 'log', label: t('nav.log'), subtitle: t('log.subtitle'),
      icon: 'history', group: 'nav.system', render: () => renderLog(ctx),
    },
    {
      key: 'settings', label: t('nav.settings'), subtitle: t('settings.subtitle'),
      icon: 'settings', group: 'nav.system', render: () => renderSettings(ctx),
    },
  ];
}

function buildNav() {
  const nav = document.getElementById('nav');
  let lastGroup = null;
  nav.innerHTML = routes().map((route) => {
    const heading = route.group && route.group !== lastGroup
      ? `<div class="nav__group">${escapeHtml(t(route.group))}</div>` : '';
    lastGroup = route.group || lastGroup;
    return `${heading}
      <a href="#${route.key}" data-route="${route.key}"
         class="${route.key === currentRoute ? 'is-active' : ''}">
        ${icon(route.icon)}
        <span class="nav__label">${escapeHtml(route.label)}</span>
      </a>`;
  }).join('');

  nav.querySelectorAll('[data-route]').forEach((link) =>
    link.addEventListener('click', (event) => {
      event.preventDefault();
      navigate(link.dataset.route);
    }));
}

function navigate(key) {
  const route = routes().find((item) => item.key === key) || routes()[0];
  currentRoute = route.key;
  window.location.hash = route.key;

  document.getElementById('page-title').textContent = route.label;
  document.getElementById('page-subtitle').textContent = route.subtitle || '';
  document.querySelectorAll('[data-route]').forEach((link) =>
    link.classList.toggle('is-active', link.dataset.route === route.key));
  appNode.classList.remove('is-menu-open');

  route.render();
}

/* ------------------------------------------------------------------ oturum */

function paintSession() {
  const session = state.session;
  const environment = state.environments[session.environment];
  const badge = document.getElementById('env-badge');
  badge.textContent = environment ? pick(environment, 'label') : t('login.custom');
  badge.className = `badge badge--${session.environment === 'prod' ? 'prod'
    : session.environment === 'test' ? 'test' : 'custom'}`;

  document.getElementById('session-user').textContent = session.username || '—';
  document.getElementById('session-licence').textContent =
    (session.licences || []).join(', ') || '—';
  document.getElementById('session-initial').textContent = initials(session.username);
}

function paintToken() {
  const badge = document.getElementById('token-badge');
  const left = state.session.secondsLeft;
  badge.title = t('session.badgeTitle');
  if (left === null || left === undefined) { badge.textContent = '—'; return; }

  // Rozet açıkça "Oturum" der; yarım saatlik gönderim döngüsüyle
  // karıştırılmaması için (o sayaç panelde ayrı gösterilir).
  badge.textContent = `${t('session.badge')} ${fmtDuration(left)}`;
  badge.classList.toggle('badge--danger', left <= 120);
  badge.classList.toggle('badge--warn', left > 120 && left <= 420);

  if (left <= 0) {
    if (!expiryWarned) {
      expiryWarned = true;
      toast('err', t('session.expired'), '', 0);
      resetCustomerState();
      resetViewState();
      showLogin();
    }
  } else if (left <= 300 && !state.session.autoRenew && !expiryWarned) {
    expiryWarned = true;
    toast('warn', t('session.expiringSoon'), `${fmtDuration(left)} ${t('session.left')}`);
  }
}

function startTokenTimer() {
  clearInterval(tokenTimer);
  tokenTimer = setInterval(async () => {
    if (!state.session.active) return;
    if (state.session.secondsLeft !== null && state.session.secondsLeft !== undefined) {
      state.session.secondsLeft = Math.max(0, state.session.secondsLeft - 1);
    }
    paintToken();
    // Sunucu tarafındaki gerçek durumla dakikada bir eşitle
    if (Date.now() % 60000 < 1000) {
      try {
        const { session } = await api.session();
        state.session = session;
        if (!session.active) {
          resetCustomerState();
          resetViewState();
          showLogin();
        } else {
          expiryWarned = false;
        }
      } catch { /* geçici ağ hatası; bir sonraki turda yeniden denenir */ }
    }
  }, 1000);
}

/* ------------------------------------------------------------- tercihler */

function applyPreferences() {
  const settings = state.settings || {};
  const theme = settings.theme === 'dark' ? 'dark' : 'light';
  const themeChanged = document.documentElement.dataset.theme !== theme;
  document.documentElement.dataset.theme = theme;
  // Grafikler renklerini CSS değişkenlerinden okur; tema değişince yeniden çizilir
  if (themeChanged) window.dispatchEvent(new Event('epdk:theme'));
  if ((settings.language || 'tr') !== getLanguage()) {
    setLanguage(settings.language || 'tr');
    buildNav();
    const route = routes().find((item) => item.key === currentRoute);
    document.getElementById('page-title').textContent = route?.label || '';
    document.getElementById('page-subtitle').textContent = route?.subtitle || '';
  }
  document.getElementById('lang-toggle').textContent =
    getLanguage() === 'tr' ? 'TR' : 'EN';
}

async function savePreference(key, value) {
  state.settings[key] = value;
  applyPreferences();
  try {
    const { settings } = await api.saveSettings({ [key]: value });
    state.settings = settings;
  } catch { /* tercih yerelde uygulandı; kayıt sonraki denemede yapılır */ }
}

/* ----------------------------------------------------------- giriş ekranı */

function showLogin() {
  clearInterval(tokenTimer);
  appNode.hidden = true;
  loginNode.hidden = false;
  document.getElementById('username').value = state.settings.username || '';
  document.getElementById('login-version').textContent =
    `${state.app.name} · v${state.app.version}`;
  buildEnvironmentPicker();
  buildProfileList();
  const field = state.settings.username ? 'password' : 'username';
  setTimeout(() => document.getElementById(field)?.focus(), 60);
}

/**
 * Kayıtlı müşteriler — birden fazla lisansla çalışırken hızlı geçiş.
 * Yalnızca kullanıcı adı ve ortam saklanır; parola her girişte istenir.
 */
function buildProfileList() {
  const profiles = state.settings.profiles || [];
  const field = document.getElementById('profiles-field');
  const list = document.getElementById('profile-list');
  field.hidden = profiles.length === 0;
  if (!profiles.length) return;

  const currentUser = document.getElementById('username').value.trim();
  list.innerHTML = profiles.map((profile) => {
    const environment = state.environments[profile.environment];
    const envLabelText = environment ? pick(environment, 'label') : t('login.custom');
    return `
      <div class="profile ${profile.username === currentUser ? 'is-active' : ''}"
           data-profile="${escapeHtml(profile.username)}"
           data-env="${escapeHtml(profile.environment)}"
           data-url="${escapeHtml(profile.custom_base_url || '')}"
           role="button" tabindex="0">
        <span class="profile__avatar">${escapeHtml(initials(profile.username))}</span>
        <span class="profile__text">
          <span class="profile__name">${escapeHtml(profile.licence || profile.username)}</span>
          <span class="profile__meta">${escapeHtml(envLabelText)}</span>
        </span>
        <button type="button" class="profile__remove"
                title="${escapeHtml(t('login.removeProfile'))}">×</button>
      </div>`;
  }).join('');

  const choose = (node) => {
    document.getElementById('username').value = node.dataset.profile;
    const environment = node.dataset.env;
    document.querySelectorAll('#env-picker [data-env]').forEach((button) =>
      button.setAttribute('aria-selected', String(button.dataset.env === environment)));
    const customField = document.getElementById('custom-url-field');
    customField.hidden = environment !== 'custom';
    if (environment === 'custom') document.getElementById('custom-url').value = node.dataset.url;
    list.querySelectorAll('.profile').forEach((item) =>
      item.classList.toggle('is-active', item === node));
    document.getElementById('password').focus();
  };

  list.querySelectorAll('.profile').forEach((node) => {
    node.addEventListener('click', (event) => {
      if (event.target.closest('.profile__remove')) return;
      choose(node);
    });
    node.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(node); }
    });
    node.querySelector('.profile__remove').addEventListener('click', async (event) => {
      event.stopPropagation();
      try {
        const { settings } = await api.removeProfile({
          username: node.dataset.profile, environment: node.dataset.env,
        });
        state.settings = settings;
        buildProfileList();
      } catch (error) {
        toast('err', t('common.error'), error.message);
      }
    });
  });
}

function buildEnvironmentPicker() {
  const picker = document.getElementById('env-picker');
  const selected = state.settings.environment || 'test';
  const options = [
    ...Object.entries(state.environments).map(([key, value]) => ({
      key, label: pick(value, 'label'),
    })),
    { key: 'custom', label: t('login.custom') },
  ];

  picker.innerHTML = options.map((option) => `
    <button type="button" role="tab" data-env="${option.key}"
            aria-selected="${option.key === selected}">${escapeHtml(option.label)}</button>`).join('');

  const sync = (key) => {
    picker.querySelectorAll('[data-env]').forEach((button) =>
      button.setAttribute('aria-selected', String(button.dataset.env === key)));
    document.getElementById('custom-url-field').hidden = key !== 'custom';
  };
  sync(selected);

  picker.querySelectorAll('[data-env]').forEach((button) =>
    button.addEventListener('click', () => sync(button.dataset.env)));
}

async function submitLogin(event) {
  event.preventDefault();
  const button = document.getElementById('login-submit');
  const errorNode = document.getElementById('login-error');
  const environment = document.querySelector('#env-picker [aria-selected="true"]')?.dataset.env || 'test';

  errorNode.hidden = true;
  button.disabled = true;
  button.innerHTML = `<span class="spinner"></span><span>${escapeHtml(t('login.connecting'))}</span>`;

  try {
    const { session } = await api.login({
      username: document.getElementById('username').value.trim(),
      password: document.getElementById('password').value,
      environment,
      customBaseUrl: document.getElementById('custom-url').value.trim(),
      autoRenew: document.getElementById('auto-renew').checked,
    });
    // Farklı bir müşteriye geçiliyor olabilir; önceki lisansın tank ve GTİP
    // listeleri ile tablo durumu taşınmamalı.
    resetCustomerState();
    resetViewState();
    state.session = session;
    document.getElementById('password').value = '';
    await enterApp();
  } catch (error) {
    errorNode.textContent = error.message;
    errorNode.hidden = false;
  } finally {
    button.disabled = false;
    button.innerHTML = `<span>${escapeHtml(t('login.submit'))}</span>`;
  }
}

/* ------------------------------------------------------------- uygulama */

async function enterApp() {
  await loadMeta();   // profiller dâhil güncel ayarları getirir
  applyPreferences();
  loginNode.hidden = true;
  appNode.hidden = false;
  paintSession();
  paintToken();
  startTokenTimer();
  buildNav();
  expiryWarned = false;

  const hash = window.location.hash.replace('#', '');
  navigate(routes().some((route) => route.key === hash) ? hash : 'dashboard');

  ensureLookups().catch(() => { /* referans listeleri sonra da yüklenebilir */ });
}

/* ------------------------------------------------------------- olaylar */

function bindChrome() {
  document.getElementById('login-form').addEventListener('submit', submitLogin);

  document.getElementById('toggle-password').addEventListener('click', () => {
    const input = document.getElementById('password');
    input.type = input.type === 'password' ? 'text' : 'password';
  });

  document.getElementById('login-lang').addEventListener('click', () => {
    const next = getLanguage() === 'tr' ? 'en' : 'tr';
    setLanguage(next);
    state.settings.language = next;
    document.getElementById('login-lang').textContent = next === 'tr' ? 'EN' : 'TR';
    buildEnvironmentPicker();
  });

  document.getElementById('logout-btn').addEventListener('click', async () => {
    try { await api.logout(); } catch { /* oturum zaten kapanmış olabilir */ }
    state.session = { active: false };
    resetCustomerState();
    resetViewState();
    showLogin();
  });

  document.getElementById('theme-toggle').addEventListener('click', () =>
    savePreference('theme', state.settings.theme === 'dark' ? 'light' : 'dark'));

  document.getElementById('lang-toggle').addEventListener('click', () =>
    savePreference('language', getLanguage() === 'tr' ? 'en' : 'tr'));

  document.getElementById('sidebar-toggle').addEventListener('click', () =>
    appNode.classList.toggle('is-collapsed'));

  document.getElementById('mobile-menu').addEventListener('click', () =>
    appNode.classList.toggle('is-menu-open'));

  window.addEventListener('hashchange', () => {
    const hash = window.location.hash.replace('#', '');
    if (hash && hash !== currentRoute) navigate(hash);
  });

  // Klavye kısayolu: yeni kayıt (N)
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'n' || event.ctrlKey || event.metaKey || event.altKey) return;
    const tag = document.activeElement?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (document.querySelector('.modal-backdrop')) return;
    const spec = tableSpec(currentRoute);
    if (spec) {
      event.preventDefault();
      import('./form.js').then(({ openRecordForm }) =>
        openRecordForm({ spec, onDone: () => navigate(currentRoute) }));
    }
  });
}

async function boot() {
  bindChrome();
  try {
    await loadMeta();
  } catch (error) {
    document.body.innerHTML =
      `<div style="padding:40px;font-family:sans-serif">Uygulama başlatılamadı: ${escapeHtml(error.message)}</div>`;
    return;
  }
  setLanguage(state.settings.language || 'tr');
  document.documentElement.dataset.theme = state.settings.theme === 'dark' ? 'dark' : 'light';
  applyStatic();

  if (state.session.active) await enterApp();
  else showLogin();
}

boot();
