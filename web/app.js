"use strict";

// ---------- Configuração ----------
const TAP_MAX_MS = 250;        // duração máxima de um toque para contar como clique
const TAP_MAX_MOVE = 8;        // px de tolerância de movimento num toque
const DRAG_WINDOW_MS = 300;    // janela após um toque em que novo toque+arraste = segurar
const SCROLL_PX_PER_NOTCH = 30;
const PING_EVERY_MS = 2000;

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const statusText = $("status-text");
const latencyEl = $("latency");
const pad = $("pad");
const padHint = $("pad-hint");
const padError = $("pad-error");
const padErrorText = $("pad-error-text");
const tokenForm = $("token-form");
const tokenInput = $("token-input");
const sensInput = $("sens");

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* armazenamento indisponível */ }
  return null;
}

// ---------- Token (vem do QR Code: ?t=...) ----------
const params = new URLSearchParams(location.search);
let token = params.get("t");
if (token) {
  store("token", token);
  history.replaceState(null, "", location.pathname);
} else {
  token = store("token");
}

// ---------- Sensibilidade ----------
const savedSens = parseFloat(store("sens"));
if (!Number.isNaN(savedSens)) sensInput.value = savedSens;
sensInput.addEventListener("input", () => store("sens", sensInput.value));

// ---------- WebSocket com reconexão ----------
let ws = null;
let retryDelay = 500;
let retryTimer = null;
let pingTimer = null;
let stopped = false;

function setStatus(kind, text) {
  statusEl.className = "status " + kind;
  statusText.textContent = text;
  if (kind !== "ok") latencyEl.textContent = "";
}

function showError(text, askToken = false) {
  padErrorText.textContent = text;
  tokenForm.hidden = !askToken;
  padError.hidden = false;
}

// Digitar/colar o token manualmente. Necessário no iPhone: o app instalado na
// tela inicial não compartilha o armazenamento com o Safari onde o QR foi aberto.
tokenForm.addEventListener("submit", (e) => {
  e.preventDefault();
  let value = tokenInput.value.trim();
  const m = value.match(/[?&]t=([^&#\s]+)/);   // aceita também a URL inteira
  if (m) value = decodeURIComponent(m[1]);
  if (!value) return;
  token = value;
  store("token", token);
  tokenInput.value = "";
  tokenInput.blur();
  padError.hidden = true;
  stopped = false;
  retryDelay = 500;
  connect();
});

function connect() {
  if (stopped || !token) return;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  clearTimeout(retryTimer);

  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
  setStatus("", "Conectando…");

  ws.onopen = () => {
    retryDelay = 500;
    relockPending = true;
    setStatus("ok", "Conectado");
    clearInterval(pingTimer);
    pingTimer = setInterval(() => send({ type: "ping", t: performance.now() }), PING_EVERY_MS);
    send({ type: "ping", t: performance.now() });
  };

  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "pong" && typeof msg.t === "number") {
      latencyEl.textContent = Math.round(performance.now() - msg.t) + " ms";
    } else if (msg.type === "monitors" && Array.isArray(msg.list)) {
      monitors = msg.list;
      renderScreens();
    } else if (msg.type === "lock_state") {
      onLockState(msg);
    }
  };

  ws.onclose = (ev) => {
    clearInterval(pingTimer);
    resetGesture();
    if (ev.code === 4401) {
      stopped = true;
      store("token", null);
      setStatus("err", "Acesso negado");
      showError("Token inválido. Escaneie de novo o QR Code exibido no PC.", true);
      return;
    }
    setStatus("", "Reconectando…");
    retryTimer = setTimeout(connect, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 5000);
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    retryDelay = 500;
    connect();
    requestWakeLock();
  }
});

// ---------- Envio agrupado por quadro (requestAnimationFrame) ----------
let pendMoveX = 0, pendMoveY = 0, pendScrollY = 0, pendScrollX = 0;
let frameQueued = false;

function queueFlush() {
  if (!frameQueued) {
    frameQueued = true;
    requestAnimationFrame(flush);
  }
}

function flush() {
  frameQueued = false;
  if (pendMoveX || pendMoveY) {
    send({ type: "move", dx: +pendMoveX.toFixed(2), dy: +pendMoveY.toFixed(2) });
    pendMoveX = pendMoveY = 0;
  }
  if (pendScrollX || pendScrollY) {
    send({ type: "scroll", dx: +pendScrollX.toFixed(3), dy: +pendScrollY.toFixed(3) });
    pendScrollX = pendScrollY = 0;
  }
}

function accelGain(dist, dt) {
  // Lento = preciso; rápido = atravessa a TV.
  const speed = dt > 0 ? dist / dt : 0;           // px/ms
  const sens = parseFloat(sensInput.value) || 1.5;
  return sens * (0.6 + Math.min(speed, 3) * 1.2);
}

function haptic(ms = 10) {
  if (navigator.vibrate) navigator.vibrate(ms);
}

function click(button, action = "tap") {
  send({ type: "click", button, action });
  if (action !== "up") haptic();
}

// ---------- Gestos no touchpad ----------
const touches = new Map();     // identifier -> {x, y, t}
let g = null;                  // gesto atual
let lastTap = { t: 0, x: 0, y: 0 };

function resetGesture() {
  if (g && g.dragging) click("left", "up");
  g = null;
  touches.clear();
  pad.classList.remove("active", "dragging");
}

pad.addEventListener("touchstart", (e) => {
  if (!padError.hidden) return;   // deixa usar o campo de token
  e.preventDefault();
  padHint.hidden = true;
  const now = e.timeStamp;

  for (const t of e.changedTouches) {
    touches.set(t.identifier, { x: t.clientX, y: t.clientY, t: now });
  }

  if (!g) {
    const t0 = e.changedTouches[0];
    const nearLastTap = Math.hypot(t0.clientX - lastTap.x, t0.clientY - lastTap.y) < 60;
    g = {
      start: now,
      maxTouches: touches.size,
      travel: 0,
      dragCandidate: touches.size === 1 && now - lastTap.t < DRAG_WINDOW_MS && nearLastTap,
      dragging: false,
    };
    pad.classList.add("active");
  }
  g.maxTouches = Math.max(g.maxTouches, touches.size);
  if (touches.size > 1) g.dragCandidate = false;
}, { passive: false });

pad.addEventListener("touchmove", (e) => {
  if (!padError.hidden) return;
  e.preventDefault();
  if (!g) return;
  const now = e.timeStamp;

  let sumDx = 0, sumDy = 0, n = 0, dt = 0;
  for (const t of e.changedTouches) {
    const prev = touches.get(t.identifier);
    if (!prev) continue;
    sumDx += t.clientX - prev.x;
    sumDy += t.clientY - prev.y;
    dt = Math.max(dt, now - prev.t);
    n++;
    touches.set(t.identifier, { x: t.clientX, y: t.clientY, t: now });
  }
  if (!n) return;

  const dx = sumDx / n, dy = sumDy / n;
  const dist = Math.hypot(dx, dy);
  g.travel += dist;

  if (touches.size >= 2) {
    // Rolagem com 2 dedos (rolagem "natural", igual ao celular).
    pendScrollY += dy / SCROLL_PX_PER_NOTCH;
    pendScrollX -= dx / SCROLL_PX_PER_NOTCH;
    queueFlush();
    return;
  }

  // Depois de usar 2 dedos, o dedo que sobra não move o cursor.
  if (g.maxTouches > 1) return;

  if (g.dragCandidate && !g.dragging && g.travel > TAP_MAX_MOVE) {
    g.dragging = true;
    click("left", "down");
    pad.classList.add("dragging");
  }

  const gain = accelGain(dist, dt);
  pendMoveX += dx * gain;
  pendMoveY += dy * gain;
  queueFlush();
}, { passive: false });

function onTouchEnd(e) {
  if (!padError.hidden) return;
  e.preventDefault();
  for (const t of e.changedTouches) touches.delete(t.identifier);
  if (!g || touches.size > 0) return;

  const now = e.timeStamp;
  const isTap = now - g.start < TAP_MAX_MS && g.travel < TAP_MAX_MOVE * g.maxTouches;

  if (g.dragging) {
    click("left", "up");
    lastTap.t = 0;
  } else if (isTap && g.maxTouches === 2) {
    click("right");
    lastTap.t = 0;
  } else if (isTap && g.maxTouches === 1) {
    // Clique imediato (sem esperar para detectar toque duplo).
    // Um segundo toque rápido gera duplo clique; toque + arrastar segura o botão.
    click("left");
    const t = e.changedTouches[0];
    lastTap = { t: now, x: t.clientX, y: t.clientY };
  }

  g = null;
  pad.classList.remove("active", "dragging");
}
pad.addEventListener("touchend", onTouchEnd, { passive: false });
pad.addEventListener("touchcancel", onTouchEnd, { passive: false });

// ---------- Botões físicos virtuais (segurar = manter pressionado) ----------
for (const btn of document.querySelectorAll(".mbtn")) {
  const button = btn.dataset.button;
  let held = false;
  const release = () => {
    if (!held) return;
    held = false;
    btn.classList.remove("held");
    click(button, "up");
  };
  btn.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    btn.setPointerCapture(e.pointerId);
    held = true;
    btn.classList.add("held");
    click(button, "down");
  });
  btn.addEventListener("pointerup", release);
  btn.addEventListener("pointercancel", release);
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
}

// ---------- Teclas e mídia ----------
for (const btn of document.querySelectorAll("[data-key], [data-media]")) {
  const msg = btn.dataset.key
    ? { type: "key", key: btn.dataset.key }
    : { type: "media", command: btn.dataset.media };
  let delay = null, repeat = null;
  const stop = () => {
    clearTimeout(delay);
    clearInterval(repeat);
    btn.classList.remove("held");
  };
  btn.addEventListener("pointerdown", (e) => {
    e.preventDefault();          // não tira o foco do teclado, se estiver aberto
    btn.classList.add("held");
    send(msg);
    haptic();
    if ("repeat" in btn.dataset) {  // volume: segurar = repetir
      delay = setTimeout(() => { repeat = setInterval(() => send(msg), 120); }, 400);
    }
  });
  btn.addEventListener("pointerup", stop);
  btn.addEventListener("pointercancel", stop);
  btn.addEventListener("pointerleave", stop);
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
}

// ---------- Teclado do celular ----------
// O texto vai para um <input> invisível. A cada mudança, comparamos com o valor
// anterior e enviamos apenas a diferença (backspaces + texto novo). Isso funciona
// com autocorretor e teclados que "compõem" palavras (Gboard), onde keydown
// chega sem a tecla real (keyCode 229).
const kbd = $("kbd");
const kbdBtn = $("kbd-btn");
const SENTINEL = " ";   // permite detectar backspace com o campo "vazio"
let kbdPrev = SENTINEL;
let composing = false;

function kbdReset() {
  kbd.value = SENTINEL;
  kbdPrev = SENTINEL;
  try { kbd.setSelectionRange(1, 1); } catch { /* ignore */ }
}

kbdBtn.addEventListener("pointerdown", (e) => e.preventDefault());
kbdBtn.addEventListener("click", () => {
  if (document.activeElement === kbd) {
    kbd.blur();
  } else {
    kbdReset();
    kbd.focus();
  }
});
kbd.addEventListener("focus", () => kbdBtn.setAttribute("aria-pressed", "true"));
kbd.addEventListener("blur", () => kbdBtn.setAttribute("aria-pressed", "false"));
kbd.addEventListener("compositionstart", () => { composing = true; });
kbd.addEventListener("compositionend", () => { composing = false; });

kbd.addEventListener("input", () => {
  const now = kbd.value;
  let i = 0;
  while (i < kbdPrev.length && i < now.length && kbdPrev[i] === now[i]) i++;
  const deleted = kbdPrev.length - i;
  const added = now.slice(i);
  for (let k = 0; k < deleted; k++) send({ type: "key", key: "backspace" });
  if (added) send({ type: "text", value: added });
  kbdPrev = now;
  if (!composing && (now.length === 0 || now.length > 40)) kbdReset();
});

kbd.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    send({ type: "key", key: "enter" });
    kbdReset();
  }
});

// ---------- Monitores: teleporte e trava ----------
const screensBar = $("screens");
const screenList = $("screen-list");
const lockBtn = $("lock");
const LONG_PRESS_MS = 600;

let monitors = [];
let lockState = { enabled: false, monitor: null };
let selected = store("selMonitor");
let relockPending = false;   // reaplicar a trava desejada após reconectar

function loadNames() {
  try { return JSON.parse(store("monitorNames") || "{}"); } catch { return {}; }
}
let names = loadNames();

function labelFor(m, i) {
  return names[m.id] || `Tela ${i + 1}`;
}

function renderScreens() {
  screensBar.hidden = monitors.length < 2;
  screenList.replaceChildren();
  monitors.forEach((m, i) => {
    const b = document.createElement("button");
    b.textContent = (lockState.enabled && lockState.monitor === m.id ? "🔒 " : "") + labelFor(m, i);
    b.title = `${m.id} — ${m.w}×${m.h}${m.primary ? " (principal)" : ""}`;
    if (m.id === selected) b.classList.add("selected");
    attachPress(b, () => focusMonitor(m.id), () => renameMonitor(m, i));
    screenList.append(b);
  });
  lockBtn.setAttribute("aria-pressed", String(lockState.enabled));
  lockBtn.textContent = lockState.enabled ? "🔒 Travado" : "🔓 Travar";
}

function attachPress(el, onTap, onLong) {
  let timer = null, longFired = false;
  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    longFired = false;
    timer = setTimeout(() => { longFired = true; haptic(30); onLong(); }, LONG_PRESS_MS);
  });
  const cancel = () => clearTimeout(timer);
  el.addEventListener("pointerup", () => { cancel(); if (!longFired) onTap(); });
  el.addEventListener("pointercancel", cancel);
  el.addEventListener("pointerleave", cancel);
  el.addEventListener("contextmenu", (e) => e.preventDefault());
}

function focusMonitor(id) {
  selected = id;
  store("selMonitor", id);
  send({ type: "focus_monitor", monitor: id });
  haptic();
  renderScreens();
}

function renameMonitor(m, i) {
  const name = prompt(`Nome para ${m.id} (${m.w}×${m.h}):`, labelFor(m, i));
  if (name === null) return;
  if (name.trim()) names[m.id] = name.trim().slice(0, 20);
  else delete names[m.id];
  store("monitorNames", JSON.stringify(names));
  renderScreens();
}

function setLock(enabled) {
  const target = monitors.some((m) => m.id === selected) ? selected : undefined;
  send({ type: "lock_monitor", enabled, monitor: target });
}

lockBtn.addEventListener("click", () => {
  const want = !lockState.enabled;
  store("lockWanted", want ? "1" : null);
  setLock(want);
  haptic();
});

function onLockState(msg) {
  const wasEnabled = lockState.enabled;
  lockState = { enabled: !!msg.enabled, monitor: msg.monitor || null };
  if (lockState.monitor) {
    selected = lockState.monitor;
    store("selMonitor", selected);
  }
  if (relockPending) {
    relockPending = false;
    if (!lockState.enabled && store("lockWanted")) setLock(true);
  } else if (wasEnabled && !lockState.enabled && store("lockWanted")) {
    // Trava caiu sem o usuário pedir: a tela travada foi desconectada.
    store("lockWanted", null);
    haptic([40, 60, 40]);
  }
  renderScreens();
}

// ---------- Tela cheia e Wake Lock ----------
const fsBtn = $("fullscreen");
if (!document.documentElement.requestFullscreen) {
  fsBtn.hidden = true;
} else {
  fsBtn.addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen({ navigationUI: "hide" }).catch(() => {});
  });
}

let wakeLock = null;
async function requestWakeLock() {
  // Só existe em contexto seguro (HTTPS/localhost) — ver Fase 3.
  if (!("wakeLock" in navigator) || wakeLock) return;
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener("release", () => { wakeLock = null; });
  } catch { /* negado ou indisponível */ }
}
document.addEventListener("pointerdown", requestWakeLock, { once: true });

// ---------- Service Worker (instalar como app) e aviso de certificado ----------
const certBanner = $("cert-banner");

function showCertBanner() {
  if (store("certBannerHidden")) return;
  certBanner.hidden = false;
}
$("cert-close").addEventListener("click", () => {
  certBanner.hidden = true;
  store("certBannerHidden", "1");
});
$("cert-help").addEventListener("click", () => {
  alert(
    "1. Toque em \"Baixar certificado\".\n\n" +
    "ANDROID: Configurações > Segurança > Mais configurações > Criptografia e credenciais > " +
    "Instalar um certificado > Certificado de CA > escolha mouse-remoto-ca.crt.\n\n" +
    "IPHONE: Ajustes > Perfil Baixado > Instalar. Depois: Ajustes > Geral > Sobre > " +
    "Ajustes de Confiança de Certificados > ative \"Mouse Remoto CA\".\n\n" +
    "2. Feche e abra o navegador de novo, acesse o endereço e use \"Adicionar à tela inicial\"."
  );
});

if (location.protocol === "https:") {
  if (!window.isSecureContext || !("serviceWorker" in navigator)) {
    showCertBanner();
  } else {
    // Com o certificado não confiável, o registro falha com erro de SSL.
    navigator.serviceWorker.register("sw.js").catch(showCertBanner);
  }
}

// ---------- Início ----------
if (!token) {
  setStatus("err", "Sem token");
  showError("Escaneie o QR Code exibido no terminal do PC para conectar.", true);
} else {
  connect();
}
