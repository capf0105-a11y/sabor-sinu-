/* ==========================================================================
   Sabor Sinú · Frontend
   Conversación, tarjetas de menú, carrito sincronizado y seguimiento.
   ========================================================================== */

'use strict';

const STORAGE_KEY = 'sinu_session_id';
const THEME_KEY = 'sinu_theme';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const els = {
  chatScroll: $('#chatScroll'),
  messageList: $('#messageList'),
  typingRow: $('#typingRow'),
  quickReplies: $('#quickReplies'),
  composerForm: $('#composerForm'),
  input: $('#composerInput'),
  sendBtn: $('#sendBtn'),
  menuShortcut: $('#menuShortcut'),
  cartPanel: $('#cartPanel'),
  cartBody: $('#cartBody'),
  cartFoot: $('#cartFoot'),
  cartCountLabel: $('#cartCountLabel'),
  cartBadge: $('#cartBadge'),
  cartTrigger: $('#cartTrigger'),
  cartClose: $('#cartClose'),
  mobileCartBar: $('#mobileCartBar'),
  mobileCartCount: $('#mobileCartCount'),
  mobileCartTotal: $('#mobileCartTotal'),
  overlay: $('#overlay'),
  toasts: $('#toasts'),
  themeToggle: $('#themeToggle'),
  iconMoon: $('#iconMoon'),
  iconSun: $('#iconSun'),
  headerPhone: $('#headerPhone'),
  statusModal: $('#statusModal'),
  statusContent: $('#statusContent'),
  metaTheme: $('#metaTheme')
};

const state = {
  config: null,
  menu: [],
  menuById: {},
  sessionId: localStorage.getItem(STORAGE_KEY) || null,
  cart: emptyCart(),
  stage: 'browsing',
  lastOrder: null,
  sending: false,
  cartBusy: false,
  cardQty: {},
  lastUserMessage: '',
  statusOrderId: null,
  statusTimer: null,
  cartOpen: false,
  clearTimer: null
};

/* --------------------------------------------------------------------------
   Utilidades
   -------------------------------------------------------------------------- */

function emptyCart() {
  return {
    items: [],
    count: 0,
    subtotal: 0,
    delivery_fee: 0,
    total: 0,
    currency: 'COP',
    free_delivery: false,
    free_delivery_from: 80000,
    remaining_for_free: 80000,
    delivery_estimated: true
  };
}

function money(value) {
  const number = Number(value) || 0;
  return '$' + number.toLocaleString('es-CO', { maximumFractionDigits: 0 });
}

function escapeHtml(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[char]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
}

function mdLite(text) {
  let out = escapeHtml(text);
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(https?:\/\/[^\s<]+)/g, (url) => (
    `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`
  ));
  return out.replace(/\n/g, '<br>');
}

function nowTime() {
  return new Date().toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit' });
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function typingDelay(text) {
  return Math.min(1100, Math.max(420, 320 + String(text || '').length * 7));
}

function isCheckout() {
  return String(state.stage || '').startsWith('checkout_');
}

function paymentShort(order) {
  const key = order?.customer?.payment;
  if (key === 'nequi') return 'Nequi';
  if (key === 'llave') return 'Llave';
  return order?.customer?.payment_label || '—';
}

function whatsappOrderLink(order) {
  const base = state.config?.contact?.whatsapp || 'https://wa.me/573044667353';
  const c = order?.customer || {};
  const items = (order?.items || []).map((item) => {
    const mods = (item.modifiers || []).map((m) => m.name).filter(Boolean);
    const extra = mods.length ? ` (${mods.join(', ')})` : '';
    return `- ${item.qty}x ${item.name}${extra}: ${money(item.line_total)}`;
  });
  const text = [
    `Hola, acabo de hacer el pedido ${order?.id || ''}.`,
    `Nombre: ${c.name || ''}`,
    `Celular: ${c.phone || ''}`,
    `Dirección: ${c.address || ''}${c.barrio ? ` (${c.barrio})` : ''}`,
    `Pago: ${paymentShort(order)}`,
    '',
    'Productos:',
    ...items,
    '',
    `Total: ${money(order?.total || 0)}`,
  ].join('\n');
  return `${base}?text=${encodeURIComponent(text)}`;
}

function whatsappProofLink(order) {
  const base = state.config?.contact?.whatsapp || 'https://wa.me/573044667353';
  const c = order?.customer || {};
  const items = (order?.items || []).map((item) => {
    const mods = (item.modifiers || []).map((m) => m.name).filter(Boolean);
    const extra = mods.length ? ` (${mods.join(', ')})` : '';
    return `- ${item.qty}x ${item.name}${extra}: ${money(item.line_total)}`;
  });
  const payment = paymentShort(order);
  const number = c.payment === 'llave'
    ? (state.config?.contact?.llave || '304 466 7353')
    : (state.config?.contact?.nequi || '304 466 7353');
  const text = [
    'COMPROBANTE DE PAGO',
    `Pedido: ${order?.id || ''}`,
    `Cliente: ${c.name || ''}`,
    `Celular: ${c.phone || ''}`,
    `Dirección: ${c.address || ''}${c.barrio ? ` (${c.barrio})` : ''}`,
    `Pago: ${payment} al ${number} — ${money(order?.total || 0)}`,
    '',
    'Productos:',
    ...items,
    '',
    `Total: ${money(order?.total || 0)}`,
    '',
    'Adjunto la captura del pago.'
  ].join('\n');
  return `${base}?text=${encodeURIComponent(text)}`;
}

/* --------------------------------------------------------------------------
   API
   -------------------------------------------------------------------------- */

async function api(path, options = {}) {
  const { method = 'GET', body, timeout = 20000 } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(`/api${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal
    });
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    if (!response.ok) {
      const detail = data && data.detail ? data.detail : `Error ${response.status}`;
      throw new Error(typeof detail === 'string' ? detail : 'No pudimos procesar la solicitud.');
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('El servidor tardó demasiado en responder. Intenta de nuevo.');
    }
    if (error instanceof TypeError) {
      throw new Error('No hay conexión con el servidor. Revisa tu internet e intenta de nuevo.');
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/* --------------------------------------------------------------------------
   Inicio
   -------------------------------------------------------------------------- */

document.addEventListener('DOMContentLoaded', init);

async function init() {
  bindEvents();
  updateThemeUI();
  autoResize();

  try {
    const [config, menu] = await Promise.all([api('/config'), api('/menu')]);
    state.config = config;
    state.menu = menu.items || [];
    state.menuById = Object.fromEntries(state.menu.map((item) => [item.id, item]));
    applyConfig();
    renderWelcome();
    renderCart();
    await restoreSession();
    if (!els.quickReplies.children.length) setQuickReplies(defaultQuickReplies());
  } catch (error) {
    renderFatal(error);
  }
}

function applyConfig() {
  const contact = state.config.contact || {};
  if (contact.phone_link) {
    els.headerPhone.href = contact.phone_link;
    const noscriptLink = $('noscript + *') || null;
    if (noscriptLink) noscriptLink.href = contact.phone_link;
  }
  els.metaTheme.setAttribute('content', document.documentElement.classList.contains('dark') ? '#0f0d0b' : '#f7f1e7');
}

async function restoreSession() {
  if (!state.sessionId) return;
  try {
    const session = await api(`/session/${encodeURIComponent(state.sessionId)}`);
    applySession(session);
    if (session.cart && session.cart.count > 0) {
      addSystemNote(`Recuperé tu carrito: ${session.cart.count} artículo(s).`);
    }
    if (session.resume && session.resume.blocks && session.resume.blocks.length) {
      addSystemNote('Retomamos tu pedido donde lo dejaste.');
      await renderBlocks(session.resume.blocks, { fast: true });
      setQuickReplies(session.resume.quick_replies || []);
    } else if (session.last_order_id) {
      addSystemNote(`Tu último pedido fue ${session.last_order_id}.`);
    }
  } catch (error) {
    state.sessionId = null;
    localStorage.removeItem(STORAGE_KEY);
  }
}

function defaultQuickReplies() {
  const items = [
    { label: 'Ver el menú', value: 'Ver el menú' },
    { label: 'Recomiéndame algo', value: 'Recomiéndame algo' }
  ];
  if (state.cart && state.cart.count > 0) {
    items.unshift({ label: 'Finalizar pedido', value: 'Finalizar pedido' });
  }
  items.push({ label: 'Estado de mi pedido', value: 'Estado de mi pedido' });
  return items;
}

/* --------------------------------------------------------------------------
   Bienvenida
   -------------------------------------------------------------------------- */

function renderWelcome() {
  const brand = state.config?.brand || {};
  const contact = state.config?.contact || {};
  const delivery = state.config?.delivery || {};
  const name = brand.name || 'Sabor Sinú';

  const hero = document.createElement('div');
  hero.className = 'hero-wrap';
  hero.innerHTML = `
    <div class="hero-card">
      <div class="hero-eyebrow"><span class="dot"></span> ${escapeHtml(contact.hours || 'Abierto ahora')}</div>
      <h1>Tu antojo costeño,<br><em>a un mensaje.</em></h1>
      <p>Hola, soy <strong>Sinu</strong>, el asistente de ${escapeHtml(name)}. Arma tu pedido, te calculo el domicilio y lo confirmamos por aquí en menos de un minuto.</p>
      <div class="hero-actions">
        <button class="btn-primary" type="button" data-quick="Ver el menú">
          Ver el menú
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
        </button>
        <button class="btn-ghost" type="button" data-quick="Recomiéndame algo">Recomiéndame algo</button>
      </div>
      <div class="hero-meta">
        <span>🛵 Domicilio desde ${money(delivery.base_fee)}</span>
        <span>🎁 Gratis desde ${money(delivery.free_from)}</span>
        <span>⏱ ${delivery.eta_minutes || 45} min aprox.</span>
      </div>
    </div>`;
  els.messageList.appendChild(hero);
  scrollToBottom(true);
}

function renderFatal(error) {
  els.messageList.innerHTML = `
    <div class="error-card">
      <strong>No pudimos cargar el menú.</strong><br>
      ${escapeHtml(error.message || 'Revisa tu conexión e intenta de nuevo.')}
      <div style="margin-top:12px">
        <button class="btn-secondary btn-sm" type="button" data-retry>Reintentar</button>
      </div>
    </div>`;
}

/* --------------------------------------------------------------------------
   Envío de mensajes y render de bloques
   -------------------------------------------------------------------------- */

async function sendMessage(rawText) {
  const text = String(rawText || '').trim();
  if (!text || state.sending) return;

  state.sending = true;
  state.lastUserMessage = text;
  setSendingUI(true);
  appendUser(text);
  clearQuickReplies();
  showTyping();
  scrollToBottom(true);

  try {
    const response = await api('/chat', {
      method: 'POST',
      body: { session_id: state.sessionId, message: text }
    });
    state.sessionId = response.session_id;
    localStorage.setItem(STORAGE_KEY, response.session_id);
    await renderBlocks(response.blocks || []);
    hideTyping();
    applyCart(response.cart);
    state.stage = response.stage || 'browsing';
    if (response.order) state.lastOrder = response.order;
    renderCart();
    setQuickReplies(response.quick_replies || []);
  } catch (error) {
    hideTyping();
    appendBotText(`No pude completar tu solicitud: **${error.message}**`);
    setQuickReplies([{ label: 'Reintentar', value: state.lastUserMessage }]);
  } finally {
    state.sending = false;
    setSendingUI(false);
    scrollToBottom();
  }
}

async function renderBlocks(blocks, options = {}) {
  for (const block of blocks || []) {
    if (!block || !block.type) continue;

    if (block.type === 'text') {
      if (!options.fast) {
        showTyping();
        await wait(typingDelay(block.text));
        hideTyping();
      }
      appendBotText(block.text);
      scrollToBottom();
    } else if (block.type === 'products') {
      if (!options.fast) await wait(120);
      revealNewBlock(appendProducts(block));
    } else if (block.type === 'summary') {
      if (!options.fast) await wait(150);
      revealNewBlock(appendBlock(summaryHTML(block.summary)));
    } else if (block.type === 'success') {
      if (!options.fast) await wait(200);
      revealNewBlock(appendBlock(successHTML(block.order)));
    } else if (block.type === 'status') {
      if (!options.fast) await wait(120);
      revealNewBlock(appendBlock(statusInlineHTML(block.order)));
    }
  }
  hideTyping();
}

function appendUser(text) {
  const row = document.createElement('div');
  row.className = 'msg user';
  row.innerHTML = `
    <div class="msg-body">
      <div class="bubble">${mdLite(text)}</div>
      <span class="msg-time">${nowTime()}</span>
    </div>`;
  els.messageList.appendChild(row);
}

function appendBotText(text) {
  const row = document.createElement('div');
  row.className = 'msg bot';
  row.innerHTML = `
    <div class="avatar" aria-hidden="true">S</div>
    <div class="msg-body">
      <div class="bubble">${mdLite(text)}</div>
      <span class="msg-time">Sinu · ${nowTime()}</span>
    </div>`;
  els.messageList.appendChild(row);
}

function appendBlock(html) {
  const row = document.createElement('div');
  row.className = 'block-row';
  row.innerHTML = `
    <div class="avatar" aria-hidden="true">S</div>
    <div class="block-content">${html}</div>`;
  els.messageList.appendChild(row);
  return row;
}

function revealNewBlock(element) {
  if (!element) return;
  requestAnimationFrame(() => {
    const container = els.chatScroll;
    const rect = element.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    const isTall = rect.height > containerRect.height * 0.72;
    if (isTall) {
      const top = container.scrollTop + (rect.top - containerRect.top) - 12;
      container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    } else {
      scrollToBottom(true);
    }
  });
}

function addSystemNote(text) {
  const note = document.createElement('div');
  note.className = 'system-note';
  note.textContent = text;
  els.messageList.appendChild(note);
}

function appendProducts(block) {
  const ids = (block.product_ids || []).filter((id) => state.menuById[id]);
  if (!ids.length) return;
  const cards = ids.map((id) => productCardHTML(state.menuById[id])).join('');
  const title = block.title
    ? `<div class="products-title"><h3>${escapeHtml(block.title)}</h3><span>${ids.length} opciones</span></div>`
    : '';
  return appendBlock(`<div class="products-block">${title}${cards}</div>`);
}

function productCardHTML(product) {
  const qty = state.cardQty[product.id] || 1;
  const soldOut = product.available === false;
  const fee = Number(state.config?.delivery?.base_fee || 0);
  const tags = (product.ingredients || [])
    .slice(0, 3)
    .map((ingredient) => `<span class="product-tag">${escapeHtml(ingredient)}</span>`)
    .join('');

  return `
    <article class="product-card${soldOut ? ' sold-out' : ''}" data-product="${escapeAttr(product.id)}">
      <div class="product-media">
        <img src="${escapeAttr(product.image)}" alt="${escapeAttr(product.name)}" width="800" height="600" loading="lazy">
        <span class="product-badge">${soldOut ? 'Agotado' : escapeHtml(product.badge)}</span>
      </div>
      <div class="product-body">
        <div class="product-head">
          <h3>${escapeHtml(product.name)}</h3>
          <span class="product-price">${money(product.price)}</span>
        </div>
        ${soldOut ? '' : `<small class="product-ship">+ ${money(fee)} domicilio</small>`}
        <p class="product-desc" data-desc>${escapeHtml(product.description)}</p>
        <button class="product-more" type="button" data-more>Ver ingredientes</button>
        <div class="product-tags">${tags}</div>
        <div class="product-actions">
          <div class="stepper" role="group" aria-label="Cantidad de ${escapeAttr(product.name)}">
            <button type="button" data-step="-1" aria-label="Quitar una unidad" ${soldOut ? 'disabled' : ''}>−</button>
            <span data-qty aria-live="polite">${qty}</span>
            <button type="button" data-step="1" aria-label="Agregar una unidad" ${soldOut ? 'disabled' : ''}>+</button>
          </div>
          <button class="btn-add" type="button" data-add ${soldOut ? 'disabled' : ''}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
            ${soldOut ? 'Agotado' : 'Agregar'}
          </button>
        </div>
      </div>
    </article>`;
}

/* --------------------------------------------------------------------------
   Tarjetas de resumen, éxito y estado
   -------------------------------------------------------------------------- */

function itemsHTML(items) {
  return (items || []).map((item) => {
    const detail = [
      ...(item.modifiers || []).map((modifier) => modifier.name),
      ...(item.notes || [])
    ].join(' · ');
    return `
      <div class="summary-item">
        <div>
          <b>${item.qty} ×</b> ${escapeHtml(item.name)}
          ${detail ? `<small>${escapeHtml(detail)}</small>` : ''}
        </div>
        <b>${money(item.line_total)}</b>
      </div>`;
  }).join('');
}

function totalsHTML(summary) {
  const delivery = summary.delivery_fee === 0
    ? (summary.subtotal > 0 ? 'Gratis' : money(0))
    : `+ ${money(summary.delivery_fee)}`;
  return `
    <div class="summary-totals">
      <div class="summary-total-row"><span>Subtotal</span><span>${money(summary.subtotal)}</span></div>
      <div class="summary-total-row"><span>Domicilio</span><span>${delivery}</span></div>
      <div class="summary-total-row grand"><span>Total</span><b>${money(summary.total)}</b></div>
    </div>`;
}

function summaryHTML(summary) {
  const customer = summary.customer || {};
  return `
    <div class="summary-card">
      <div class="summary-head">
        <h3>Revisa tu pedido</h3>
        <span>Todo listo</span>
      </div>
      <div class="summary-body">
        <div class="summary-customer">
          <div class="summary-field"><span>Cliente</span><strong>${escapeHtml(customer.name)}</strong></div>
          <div class="summary-field"><span>Celular</span><strong>${escapeHtml(customer.phone)}</strong></div>
          <div class="summary-field"><span>Dirección</span><strong>${escapeHtml(customer.address)}${customer.barrio ? ` · ${escapeHtml(customer.barrio)}` : ''}</strong></div>
          <div class="summary-field"><span>Pago</span><strong>${escapeHtml(customer.payment_label)}</strong></div>
        </div>
        <div class="summary-items">${itemsHTML(summary.items)}</div>
        ${totalsHTML(summary)}
        <div class="summary-total-row" style="font-size:11px;color:var(--muted)">
          <span>Tiempo estimado</span><span>${escapeHtml(summary.eta_label || '45 min aprox.')}</span>
        </div>
      </div>
    </div>`;
}

function successHTML(order) {
  const contact = state.config?.contact || {};
  const phoneLink = contact.phone_link || `tel:${order.customer?.phone || ''}`;
  const payment = paymentShort(order);
  const paymentNumber = order.customer?.payment === 'llave'
    ? (contact.llave || '304 466 7353')
    : (contact.nequi || '304 466 7353');
  return `
    <div class="success-card">
      <div class="success-top">
        <p class="eyebrow">Pedido recibido</p>
        <h3>¡Pedido Recibido!</h3>
        <div class="success-order"><span>N.º</span><code>${escapeHtml(order.id)}</code></div>
      </div>
      <div class="success-body">
        <div class="success-metrics">
          <div class="success-metric"><span>Total</span><strong>${money(order.total)}</strong></div>
          <div class="success-metric"><span>Pago</span><strong>${escapeHtml(payment)}</strong></div>
          <div class="success-metric"><span>Llega en</span><strong>${escapeHtml(order.eta_label || '45 min')}</strong></div>
        </div>
        <div class="success-note">
          <strong>Falta verificar tu pago</strong>
          <p>Transfiere <b>${money(order.total)}</b> por <b>${escapeHtml(payment)}</b> al <b>${escapeHtml(paymentNumber)}</b> y envía la captura por WhatsApp. Preparamos tu pedido apenas confirmemos el pago.</p>
        </div>
        <div class="summary-items">${itemsHTML(order.items)}</div>
        ${totalsHTML(order)}
        <div class="success-actions">
          <a class="btn-primary" href="${escapeAttr(whatsappProofLink(order))}" target="_blank" rel="noopener">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5Z"/></svg>
            Enviar comprobante por WhatsApp
          </a>
          <button class="btn-secondary" type="button" data-open-status="${escapeAttr(order.id)}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
            Ver estado
          </button>
          <a class="btn-secondary" href="${escapeAttr(phoneLink)}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.9.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92Z"/></svg>
            Llamar al despacho
          </a>
        </div>
        <button class="btn-ghost btn-block btn-sm" type="button" data-new-order>Hacer otro pedido</button>
      </div>
    </div>`;
}

function timelineHTML(status) {
  return `
    <div class="status-progress"><span style="width:${Math.max(4, status.progress || 0)}%"></span></div>
    <div class="timeline">
      ${(status.steps || []).map((step) => `
        <div class="timeline-step ${step.done ? 'done' : ''} ${step.current ? 'current' : ''} ${!step.done ? 'pending' : ''}">
          <span class="timeline-dot" aria-hidden="true">${step.done && !step.current ? '✓' : ''}</span>
          <div class="timeline-copy">
            <strong>${escapeHtml(step.label)}</strong>
            <span>${escapeHtml(step.description)}</span>
            <time>${step.done ? 'Actualizado' : 'Estimado'} ${escapeHtml(step.at_label)}</time>
          </div>
        </div>`).join('')}
    </div>`;
}

function statusInlineHTML(order) {
  return `
    <div class="status-card">
      <div class="status-head">
        <h3>Pedido ${escapeHtml(order.id)}</h3>
        <span class="status-pill">${escapeHtml(order.status.label)}</span>
      </div>
      ${timelineHTML(order.status)}
      <div style="margin-top:14px">
        <button class="btn-secondary btn-sm btn-block" type="button" data-open-status="${escapeAttr(order.id)}">Abrir seguimiento</button>
      </div>
    </div>`;
}

function statusDetailHTML(order) {
  const customer = order.customer || {};
  return `
    <div class="status-head" style="margin-bottom:12px">
      <div>
        <p class="eyebrow">Pedido</p>
        <h3 class="status-code">${escapeHtml(order.id)}</h3>
        <p style="font-size:11px;color:var(--muted)">${escapeHtml(order.created_at_display)}</p>
      </div>
      <span class="status-pill">${escapeHtml(order.status.label)}</span>
    </div>
    ${timelineHTML(order.status)}
    <div class="summary-items" style="margin-top:18px;border-top:1px solid var(--border);padding-top:14px">${itemsHTML(order.items)}</div>
    ${totalsHTML(order)}
    <div class="summary-customer" style="margin-top:14px;border-top:1px dashed var(--border-strong);padding-top:14px">
      <div class="summary-field"><span>Entrega</span><strong>${escapeHtml(customer.address)}${customer.barrio ? ` · ${escapeHtml(customer.barrio)}` : ''}</strong></div>
      <div class="summary-field"><span>Pago</span><strong>${escapeHtml(customer.payment_label)}</strong></div>
      <div class="summary-field"><span>Cliente</span><strong>${escapeHtml(customer.name)}</strong></div>
      <div class="summary-field"><span>Celular</span><strong>${escapeHtml(customer.phone)}</strong></div>
    </div>
    <div style="display:flex;gap:8px;margin-top:16px;flex-wrap:wrap">
      <a class="btn-primary btn-sm" href="${escapeAttr(state.config?.contact?.phone_link || 'tel:+573044667353')}">Llamar al despacho</a>
      <button class="btn-secondary btn-sm" type="button" data-refresh-status>Actualizar estado</button>
    </div>
    <p style="font-size:10px;color:var(--muted);text-align:center;margin-top:12px">El estado se actualiza automáticamente cada 30 segundos.</p>`;
}

/* --------------------------------------------------------------------------
   Carrito
   -------------------------------------------------------------------------- */

function applyCart(cart) {
  state.cart = cart || emptyCart();
  renderCart();
}

function applySession(session) {
  state.sessionId = session.session_id;
  localStorage.setItem(STORAGE_KEY, session.session_id);
  state.cart = session.cart || emptyCart();
  state.stage = session.stage || 'browsing';
  state.lastOrder = session.last_order_id || state.lastOrder;
  renderCart();
}

function renderCart() {
  const cart = state.cart || emptyCart();
  const count = cart.count || 0;

  els.cartCountLabel.textContent = count;
  els.cartBadge.textContent = count;
  els.cartBadge.classList.toggle('hidden', count === 0);

  els.mobileCartBar.classList.toggle('hidden', count === 0);
  els.mobileCartCount.textContent = `${count} artículo${count === 1 ? '' : 's'}`;
  els.mobileCartTotal.textContent = money(cart.total);

  if (!cart.items || cart.items.length === 0) {
    els.cartBody.innerHTML = `
      <div class="cart-empty">
        <div class="cart-empty-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>
        </div>
        <h3>Tu carrito está vacío</h3>
        <p>Pídele a Sinu lo que se te antoje o explora el menú.</p>
        <button class="btn-secondary btn-sm" type="button" data-quick="Ver el menú">Explorar el menú</button>
      </div>`;
    els.cartFoot.innerHTML = '';
    return;
  }

  els.cartBody.innerHTML = cart.items.map((item) => {
    const detail = [
      ...(item.modifiers || []).map((modifier) => modifier.name),
      ...(item.notes || [])
    ].join(' · ');
    return `
      <div class="cart-item" data-uid="${escapeAttr(item.uid)}">
        <div class="cart-thumb"><img src="${escapeAttr(item.image)}" alt="" loading="lazy"></div>
        <div class="cart-info">
          <h4>${escapeHtml(item.name)}</h4>
          ${detail ? `<small>${escapeHtml(detail)}</small>` : ''}
          <div class="stepper" role="group" aria-label="Cantidad de ${escapeAttr(item.name)}">
            <button type="button" data-cart-action="dec" aria-label="Quitar una unidad">−</button>
            <span>${item.qty}</span>
            <button type="button" data-cart-action="inc" aria-label="Agregar una unidad">+</button>
          </div>
        </div>
        <div class="cart-side">
          <b>${money(item.line_total)}</b>
          <button class="cart-remove" type="button" data-cart-action="remove">Quitar</button>
        </div>
      </div>`;
  }).join('');

  const remaining = cart.remaining_for_free || 0;
  const progress = cart.free_delivery
    ? 100
    : Math.min(100, Math.round((cart.subtotal / (cart.free_delivery_from || 80000)) * 100));
  const freeLabel = cart.free_delivery
    ? '<strong>¡Domicilio gratis desbloqueado!</strong>'
    : `Te faltan ${money(remaining)} para el domicilio gratis`;
  const deliveryLabel = cart.delivery_fee === 0
    ? 'Gratis'
    : (cart.delivery_estimated
      ? `${money(cart.delivery_fee)} <span style="font-size:9px;color:var(--muted)">est.</span>`
      : money(cart.delivery_fee));

  els.cartFoot.innerHTML = `
    <div class="free-delivery">
      <div class="free-delivery-label"><span>${freeLabel}</span><span>${Math.round(progress)}%</span></div>
      <div class="free-bar"><span style="width:${progress}%"></span></div>
    </div>
    <div class="cart-totals">
      <div class="cart-total-row"><span>Subtotal</span><span>${money(cart.subtotal)}</span></div>
      <div class="cart-total-row"><span>Domicilio</span><span>${deliveryLabel}</span></div>
      <div class="cart-total-row grand"><span>Total</span><b>${money(cart.total)}</b></div>
    </div>
    <button class="cart-checkout" type="button" data-checkout ${isCheckout() ? 'disabled' : ''}>
      ${isCheckout() ? 'Completa los datos en el chat' : 'Finalizar pedido'}
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
    </button>
    <p class="cart-note">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>
      ${isCheckout() ? 'Te pedimos los datos de entrega en el chat.' : 'Pago con Nequi o Llave al 304 466 7353'}
    </p>
    <button class="cart-clear" type="button" data-clear-cart>Vaciar carrito</button>`;
}

async function addToCart(productId, qty, button) {
  if (state.cartBusy) return;
  state.cartBusy = true;
  if (button) button.disabled = true;
  try {
    const response = await api('/cart', {
      method: 'POST',
      body: {
        session_id: state.sessionId,
        action: 'add',
        product_id: productId,
        qty
      }
    });
    state.sessionId = response.session_id;
    localStorage.setItem(STORAGE_KEY, response.session_id);
    applyCart(response.cart);
    state.stage = response.stage || 'browsing';
    renderCart();
    const product = state.menuById[productId];
    toast(`${qty} × ${product ? product.name : 'Producto'} agregado`, 'success');
    pulseBadge();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    state.cartBusy = false;
    if (button) button.disabled = false;
  }
}

async function cartAction(action, uid) {
  if (state.cartBusy) return;
  state.cartBusy = true;
  try {
    const response = await api('/cart', {
      method: 'POST',
      body: { session_id: state.sessionId, action, uid }
    });
    state.sessionId = response.session_id;
    localStorage.setItem(STORAGE_KEY, response.session_id);
    applyCart(response.cart);
    state.stage = response.stage || 'browsing';
    renderCart();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    state.cartBusy = false;
  }
}

async function clearCart() {
  if (state.cartBusy) return;
  state.cartBusy = true;
  try {
    const response = await api('/cart', {
      method: 'POST',
      body: { session_id: state.sessionId, action: 'clear' }
    });
    state.sessionId = response.session_id;
    localStorage.setItem(STORAGE_KEY, response.session_id);
    applyCart(response.cart);
    state.stage = response.stage || 'browsing';
    renderCart();
    toast('Carrito vacío', 'info');
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    state.cartBusy = false;
  }
}

function pulseBadge() {
  if (!els.cartBadge || els.cartBadge.classList.contains('hidden')) return;
  if (els.cartBadge.animate) {
    els.cartBadge.animate(
      [{ transform: 'scale(1)' }, { transform: 'scale(1.35)' }, { transform: 'scale(1)' }],
      { duration: 320, easing: 'ease-out' }
    );
  }
}

/* --------------------------------------------------------------------------
   Chips de respuesta rápida
   -------------------------------------------------------------------------- */

function setQuickReplies(replies) {
  const list = (replies || []).filter((reply) => reply && reply.label);
  if (!list.length) {
    clearQuickReplies();
    return;
  }
  els.quickReplies.innerHTML = list.map((reply, index) => `
    <button class="chip" type="button" data-quick="${escapeAttr(reply.value || reply.label)}" style="animation-delay:${index * 25}ms">
      ${escapeHtml(reply.label)}
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18l6-6-6-6"/></svg>
    </button>`).join('');
}

function clearQuickReplies() {
  els.quickReplies.innerHTML = '';
}

function handleQuickValue(value) {
  if (!value) return;
  if (/^tel:/i.test(value)) {
    window.location.href = value;
    return;
  }
  if (/^https?:/i.test(value)) {
    window.open(value, '_blank', 'noopener');
    return;
  }
  sendMessage(value);
}

/* --------------------------------------------------------------------------
   Seguimiento
   -------------------------------------------------------------------------- */

async function openStatus(orderId) {
  if (!orderId) return;
  state.statusOrderId = orderId;
  els.statusModal.hidden = false;
  document.body.classList.add('no-scroll');
  els.statusContent.innerHTML = `
    <div class="skeleton" style="height:20px;width:45%;margin-bottom:14px"></div>
    <div class="skeleton" style="height:8px;width:100%;margin-bottom:18px"></div>
    <div class="skeleton" style="height:180px;width:100%"></div>`;
  const closeBtn = $('[data-close-modal]', els.statusModal);
  if (closeBtn) closeBtn.focus();
  await refreshStatus();
  clearInterval(state.statusTimer);
  state.statusTimer = setInterval(refreshStatus, 30000);
}

async function refreshStatus() {
  if (!state.statusOrderId) return;
  try {
    const data = await api(`/orders/${encodeURIComponent(state.statusOrderId)}`);
    els.statusContent.innerHTML = statusDetailHTML(data.order);
  } catch (error) {
    els.statusContent.innerHTML = `
      <div class="error-card">
        ${escapeHtml(error.message)}
        <div style="margin-top:10px"><button class="btn-secondary btn-sm" type="button" data-refresh-status>Reintentar</button></div>
      </div>`;
  }
}

function closeStatus() {
  els.statusModal.hidden = true;
  clearInterval(state.statusTimer);
  state.statusTimer = null;
  state.statusOrderId = null;
  if (!state.cartOpen) document.body.classList.remove('no-scroll');
}

/* --------------------------------------------------------------------------
   Carrito móvil (bottom sheet)
   -------------------------------------------------------------------------- */

function openCart() {
  if (window.innerWidth >= 1024) return;
  state.cartOpen = true;
  els.cartPanel.classList.add('open');
  els.overlay.hidden = false;
  requestAnimationFrame(() => els.overlay.classList.add('show'));
  document.body.classList.add('no-scroll');
  els.cartTrigger.setAttribute('aria-expanded', 'true');
  els.cartClose.focus();
}

function closeCart() {
  if (!state.cartOpen) return;
  state.cartOpen = false;
  els.cartPanel.classList.remove('open');
  els.overlay.classList.remove('show');
  setTimeout(() => {
    if (!state.cartOpen) els.overlay.hidden = true;
  }, 220);
  if (!state.statusOrderId) document.body.classList.remove('no-scroll');
  els.cartTrigger.setAttribute('aria-expanded', 'false');
  els.cartTrigger.focus();
}

/* --------------------------------------------------------------------------
   Toasts
   -------------------------------------------------------------------------- */

function toast(message, type = 'info') {
  const icons = {
    success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>'
  };
  const element = document.createElement('div');
  element.className = `toast ${type}`;
  element.innerHTML = `${icons[type] || icons.info}<span>${escapeHtml(message)}</span>`;
  els.toasts.appendChild(element);
  setTimeout(() => {
    element.classList.add('out');
    setTimeout(() => element.remove(), 240);
  }, 3400);
}

/* --------------------------------------------------------------------------
   Tema
   -------------------------------------------------------------------------- */

function updateThemeUI() {
  const dark = document.documentElement.classList.contains('dark');
  els.iconMoon.classList.toggle('hidden', dark);
  els.iconSun.classList.toggle('hidden', !dark);
  els.metaTheme.setAttribute('content', dark ? '#0f0d0b' : '#f7f1e7');
  els.themeToggle.setAttribute('aria-label', dark ? 'Activar modo claro' : 'Activar modo oscuro');
}

function toggleTheme() {
  const dark = !document.documentElement.classList.contains('dark');
  document.documentElement.classList.toggle('dark', dark);
  try { localStorage.setItem(THEME_KEY, dark ? 'dark' : 'light'); } catch (error) { /* sin almacenamiento */ }
  updateThemeUI();
}

/* --------------------------------------------------------------------------
   UI del composer
   -------------------------------------------------------------------------- */

function setSendingUI(sending) {
  els.sendBtn.disabled = sending;
  els.input.setAttribute('aria-busy', sending ? 'true' : 'false');
}

function showTyping() {
  els.typingRow.classList.remove('hidden');
  scrollToBottom();
}

function hideTyping() {
  els.typingRow.classList.add('hidden');
}

function autoResize() {
  els.input.style.height = 'auto';
  els.input.style.height = `${Math.min(120, els.input.scrollHeight)}px`;
}

function scrollToBottom(force = false) {
  const el = els.chatScroll;
  if (!el) return;
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 240;
  if (force || nearBottom) {
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }
}

/* --------------------------------------------------------------------------
   Eventos
   -------------------------------------------------------------------------- */

function bindEvents() {
  els.composerForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const value = els.input.value;
    if (!value.trim()) return;
    els.input.value = '';
    autoResize();
    sendMessage(value);
  });

  els.input.addEventListener('input', autoResize);
  els.input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      els.composerForm.requestSubmit();
    }
  });

  els.menuShortcut.addEventListener('click', () => sendMessage('Ver el menú'));
  els.themeToggle.addEventListener('click', toggleTheme);

  els.cartTrigger.addEventListener('click', openCart);
  els.cartClose.addEventListener('click', closeCart);
  els.overlay.addEventListener('click', closeCart);

  els.cartBody.addEventListener('click', (event) => {
    const button = event.target.closest('[data-cart-action]');
    if (!button) return;
    const item = button.closest('[data-uid]');
    if (!item) return;
    cartAction(button.dataset.cartAction, item.dataset.uid);
  });

  els.cartFoot.addEventListener('click', (event) => {
    const checkout = event.target.closest('[data-checkout]');
    if (checkout) {
      if (!checkout.disabled) {
        closeCart();
        sendMessage('Finalizar pedido');
      }
      return;
    }

    const clear = event.target.closest('[data-clear-cart]');
    if (clear) {
      if (clear.dataset.armed === '1') {
        clearTimeout(state.clearTimer);
        clearCart();
        return;
      }
      clear.dataset.armed = '1';
      clear.textContent = '¿Seguro? Toca de nuevo';
      clearTimeout(state.clearTimer);
      state.clearTimer = setTimeout(() => {
        if (document.body.contains(clear)) {
          clear.dataset.armed = '0';
          clear.textContent = 'Vaciar carrito';
        }
      }, 3000);
    }
  });

  els.messageList.addEventListener('click', async (event) => {
    const more = event.target.closest('[data-more]');
    if (more) {
      const card = more.closest('.product-card');
      const desc = card ? card.querySelector('[data-desc]') : null;
      if (desc) {
        desc.classList.toggle('expanded');
        more.textContent = desc.classList.contains('expanded') ? 'Ver menos' : 'Ver ingredientes';
      }
      return;
    }

    const step = event.target.closest('[data-step]');
    if (step) {
      const card = step.closest('.product-card');
      if (!card) return;
      const productId = card.dataset.product;
      const current = state.cardQty[productId] || 1;
      const next = Math.min(20, Math.max(1, current + Number(step.dataset.step)));
      state.cardQty[productId] = next;
      const qtyLabel = card.querySelector('[data-qty]');
      if (qtyLabel) qtyLabel.textContent = next;
      return;
    }

    const add = event.target.closest('[data-add]');
    if (add) {
      const card = add.closest('.product-card');
      if (!card) return;
      const productId = card.dataset.product;
      const qty = state.cardQty[productId] || 1;
      await addToCart(productId, qty, add);
      state.cardQty[productId] = 1;
      const qtyLabel = card.querySelector('[data-qty]');
      if (qtyLabel) qtyLabel.textContent = '1';
    }
  });

  document.addEventListener('click', (event) => {
    const quick = event.target.closest('[data-quick]');
    if (quick) {
      event.preventDefault();
      handleQuickValue(quick.dataset.quick);
      return;
    }

    const open = event.target.closest('[data-open-status]');
    if (open) {
      event.preventDefault();
      openStatus(open.dataset.openStatus);
      return;
    }

    const newOrder = event.target.closest('[data-new-order]');
    if (newOrder) {
      event.preventDefault();
      startNewOrder();
      return;
    }

    const refresh = event.target.closest('[data-refresh-status]');
    if (refresh) {
      event.preventDefault();
      refreshStatus();
      return;
    }

    if (event.target.closest('[data-close-modal]')) {
      event.preventDefault();
      closeStatus();
      return;
    }

    if (event.target.closest('[data-open-cart]')) {
      event.preventDefault();
      openCart();
      return;
    }

    if (event.target.closest('[data-retry]')) {
      event.preventDefault();
      window.location.reload();
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!els.statusModal.hidden) {
      closeStatus();
    } else if (state.cartOpen) {
      closeCart();
    }
  });

  window.addEventListener('resize', () => {
    if (window.innerWidth >= 1024 && state.cartOpen) {
      state.cartOpen = false;
      els.cartPanel.classList.remove('open');
      els.overlay.classList.remove('show');
      els.overlay.hidden = true;
      document.body.classList.remove('no-scroll');
      els.cartTrigger.setAttribute('aria-expanded', 'false');
    }
  });
}

async function startNewOrder() {
  try {
    if (state.sessionId) {
      await api(`/session/${encodeURIComponent(state.sessionId)}/reset`, { method: 'POST' });
    }
  } catch (error) {
    /* si la sesión expiró, seguimos con una nueva */
  }
  state.cart = emptyCart();
  state.stage = 'browsing';
  state.lastOrder = null;
  state.cardQty = {};
  els.messageList.innerHTML = '';
  clearQuickReplies();
  renderCart();
  renderWelcome();
  setQuickReplies(defaultQuickReplies());
  els.input.focus();
  scrollToBottom(true);
}
