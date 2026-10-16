// Phần dùng chung: trạng thái, gọi API qua gateway (cookie waf_token), định dạng, nhãn, icon.

export const TZ = "Asia/Ho_Chi_Minh";
export const RANGES = [
  { hours: 1, label: "1 giờ qua" },
  { hours: 6, label: "6 giờ qua" },
  { hours: 24, label: "24 giờ qua" },
  { hours: 168, label: "7 ngày qua" },
];

export const state = {
  hours: 24,
  user: "admin",
  allowlist: [],
  rules: [],
  summary: null,
  streamOnline: false,
  lastSync: null,
};

const listeners = new Map();
export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, []);
  listeners.get(event).push(fn);
}
export function emit(event, payload) {
  (listeners.get(event) || []).forEach((fn) => fn(payload));
}

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function icon(name, size) {
  const s = size ? `--s:${size}px;` : "";
  return `<i class="i" style="${s}--icon:url(/static/icons/${name}.svg)" aria-hidden="true"></i>`;
}

// ---------- API ----------
export async function api(path, options = {}) {
  const init = { credentials: "include", headers: { "content-type": "application/json" }, ...options };
  return fetch(path, init);
}

export async function apiJson(path, options) {
  const response = await api(path, options);
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      message = body.error || body.message || body.detail || message;
    } catch (_) { /* body không phải JSON */ }
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return response.json();
}

export async function requestToken(clientId, secret) {
  const response = await api("/api/auth/token", {
    method: "POST",
    body: JSON.stringify({ client_id: clientId, client_secret: secret }),
  });
  if (response.ok) {
    state.user = clientId;
    try { sessionStorage.setItem("waf-user", clientId); } catch (_) { /* chế độ riêng tư */ }
  }
  return response.ok;
}

export async function sessionValid() {
  return (await api("/api/dashboard/summary")).ok;
}

// ---------- Định dạng ----------
const numberFormat = new Intl.NumberFormat("vi-VN");
export const num = (value) => (value === null || value === undefined ? "–" : numberFormat.format(value));
export function pct(value) {
  if (value === null || value === undefined) return null;
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1, minimumFractionDigits: 1 }).format(Math.abs(value))}%`;
}

const timeFormat = new Intl.DateTimeFormat("vi-VN", { timeZone: TZ, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const dateFormat = new Intl.DateTimeFormat("vi-VN", { timeZone: TZ, day: "2-digit", month: "2-digit", year: "numeric" });
const hmFormat = new Intl.DateTimeFormat("vi-VN", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
const dmParts = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, day: "2-digit", month: "2-digit" });

const toDate = (value) => (value instanceof Date ? value : new Date(value));
export const fmtTime = (value) => (value ? timeFormat.format(toDate(value)) : "–");
export const fmtDate = (value) => (value ? dateFormat.format(toDate(value)) : "–");
export const fmtHM = (value) => hmFormat.format(toDate(value));
/** "dd/mm" theo múi giờ hiển thị (Intl vi-VN dùng dấu "-" nên ghép tay cho giống thiết kế). */
export function fmtDM(value) {
  const parts = Object.fromEntries(dmParts.formatToParts(toDate(value)).map((p) => [p.type, p.value]));
  return `${parts.day}/${parts.month}`;
}
/** "dd/mm HH:MM". */
export const fmtShort = (value) => (value ? `${fmtDM(value)} ${fmtHM(value)}` : "–");

export function sameDay(a, b) {
  return fmtDate(a) === fmtDate(b);
}

/** Giờ của sự kiện: hôm nay -> HH:MM:SS, ngày khác -> dd/mm HH:MM. */
export function fmtEventTime(value) {
  if (!value) return "–";
  return sameDay(value, new Date()) ? fmtTime(value) : fmtShort(value);
}

export function relTime(value) {
  if (!value) return "–";
  const seconds = Math.max(0, Math.round((Date.now() - toDate(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds} giây trước`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} phút trước`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} giờ trước`;
  return `${Math.floor(seconds / 86400)} ngày trước`;
}

export const rangeLabel = (hours = state.hours) => (RANGES.find((r) => r.hours === hours) || { label: `${hours} giờ qua` }).label;

// ---------- Nhãn ----------
export const SEVERITIES = ["critical", "high", "medium", "low"];
const SEVERITY_LABEL = { critical: "Nghiêm trọng", high: "Cao", medium: "Trung bình", low: "Thấp" };
export const sevLabel = (severity) => SEVERITY_LABEL[severity] || severity || "–";
export function sevFromScore(score) {
  if (score >= 90) return "critical";
  if (score >= 70) return "high";
  if (score >= 40) return "medium";
  return "low";
}
export const sevTag = (severity) => (severity ? `<span class="tag sev-${esc(severity)}">${esc(sevLabel(severity))}</span>` : "");

const TYPE_LABEL = {
  sql_injection: "SQLi", xss: "XSS", lfi: "LFI", rfi: "RFI", path_traversal: "Path traversal",
  command_injection: "Cmd injection", log4shell: "Log4Shell", ssti: "SSTI", sensitive_file: "File nhạy cảm",
  scanner: "Scanner", traffic_flood: "Flood", error_scan: "Dò lỗi", path_scan: "Dò đường dẫn", ddos: "DDoS",
};
export const RULE_GROUPS = ["sql_injection", "xss", "path_traversal", "command_injection", "lfi", "rfi",
  "sensitive_file", "log4shell", "ssti", "scanner"];
export const typeLabel = (type) => TYPE_LABEL[type] || type || "–";
export const typeClass = (type) => `t-${TYPE_LABEL[type] ? type : "other"}`;
export const typeTag = (type) => (type ? `<span class="tag ${typeClass(type)}">${esc(typeLabel(type))}</span>` : "");
export const typeText = (type) => (type ? `<span class="type-text ${typeClass(type)}">${esc(typeLabel(type))}</span>` : "–");

export function toast(message) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

export function emptyState({ title, text, actions = "", foot = "" }) {
  return `<div class="empty"><div class="empty-icon">${icon("search")}</div><h3>${esc(title)}</h3>`
    + `<p>${esc(text)}</p>${actions ? `<div class="actions">${actions}</div>` : ""}${foot ? `<small>${esc(foot)}</small>` : ""}</div>`;
}

/** Mở menu nổi cạnh `anchor`; đóng khi bấm ra ngoài. items = [{label, danger, onClick}]. */
export function openMenu(anchor, items) {
  document.querySelectorAll(".menu.floating").forEach((m) => m.remove());
  const menu = document.createElement("div");
  menu.className = "menu floating";
  items.forEach((item) => {
    const button = document.createElement("button");
    button.textContent = item.label;
    if (item.danger) button.className = "danger";
    button.addEventListener("click", () => { menu.remove(); item.onClick(); });
    menu.appendChild(button);
  });
  document.body.appendChild(menu);
  const rect = anchor.getBoundingClientRect();
  menu.style.top = `${rect.bottom + window.scrollY + 4}px`;
  menu.style.left = `${Math.max(8, rect.right + window.scrollX - menu.offsetWidth)}px`;
  setTimeout(() => document.addEventListener("click", function close(e) {
    if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("click", close); }
  }), 0);
  return menu;
}

export function download(filename, text, type = "text/csv;charset=utf-8") {
  const url = URL.createObjectURL(new Blob(["﻿" + text], { type }));
  const link = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}
