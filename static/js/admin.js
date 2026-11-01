'use strict';

const $ = (id) => document.getElementById(id);
const state = { items: [] };

function toast(message) {
  const el = $('toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove('show'), 2200);
}

function money(value) {
  return '$' + Number(value || 0).toLocaleString('es-CO', { maximumFractionDigits: 0 });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[char]));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    }
  });
  if (response.status === 401 || response.status === 403) {
    throw new Error('auth');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || 'No se pudo guardar el cambio.');
  }
  return data;
}

async function login(password) {
  const response = await fetch('/api/admin/login', {
    method: 'POST',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || 'No se pudo iniciar sesión.');
  }
  return data;
}

function showLogin(message = '') {
  $('panelView').hidden = true;
  $('loginView').hidden = false;
  $('loginError').textContent = message;
  $('token').focus();
}

function showPanel() {
  $('loginView').hidden = true;
  $('panelView').hidden = false;
}

function render() {
  const list = $('list');
  list.innerHTML = state.items.map((item) => `
    <div class="item ${item.available ? '' : 'is-out'}" data-id="${escapeHtml(item.id)}">
      <div class="item-info">
        <strong>${escapeHtml(item.name)}</strong>
        <small>${money(item.price)} · ${escapeHtml(item.category)}</small>
        <span class="pill ${item.available ? 'ok' : 'out'}">${item.available ? 'Disponible' : 'Agotado'}</span>
      </div>
      <button class="switch ${item.available ? 'out' : 'ok'}" type="button"
              data-toggle="${escapeHtml(item.id)}" data-next="${item.available ? 'false' : 'true'}">
        ${item.available ? 'Marcar agotado' : 'Marcar disponible'}
      </button>
    </div>`).join('');

  const out = state.items.filter((item) => !item.available).length;
  $('summary').textContent = out
    ? `${out} agotado${out === 1 ? '' : 's'} de ${state.items.length}`
    : `${state.items.length} productos disponibles`;
}

async function load() {
  try {
    const data = await api('/api/admin/stock');
    state.items = data.items || [];
    $('persistenceHint').hidden = data.persistent !== false;
    showPanel();
    render();
  } catch (error) {
    if (error.message === 'auth') {
      showLogin('');
    } else {
      showLogin(error.message);
    }
  }
}

$('loginForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const password = $('token').value;
  if (!password) return;
  const button = event.target.querySelector('button[type="submit"]');
  button.disabled = true;
  $('loginError').textContent = '';
  try {
    await login(password);
    $('token').value = '';
    await load();
  } catch (error) {
    showLogin(error.message);
  } finally {
    button.disabled = false;
  }
});

$('logout').addEventListener('click', async () => {
  try {
    await fetch('/api/admin/logout', {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store'
    });
  } catch (error) {
    /* la sesión local se cierra igual */
  }
  state.items = [];
  $('token').value = '';
  showLogin('');
});

$('list').addEventListener('click', async (event) => {
  const button = event.target.closest('[data-toggle]');
  if (!button) return;
  const id = button.dataset.toggle;
  const available = button.dataset.next === 'true';
  button.disabled = true;
  try {
    const data = await api(`/api/admin/stock/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify({ available })
    });
    const item = state.items.find((entry) => entry.id === data.id);
    if (item) item.available = data.available;
    render();
    toast(data.available ? `${data.name}: disponible` : `${data.name}: agotado`);
  } catch (error) {
    button.disabled = false;
    if (error.message === 'auth') {
      showLogin('La sesión expiró. Entra de nuevo.');
    } else {
      toast(error.message);
    }
  }
});

load();
