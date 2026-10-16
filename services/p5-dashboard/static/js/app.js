// Khởi tạo dashboard: đăng nhập, dữ liệu dùng chung, điều hướng, luồng SSE, thanh công cụ.
import { state, on, emit, $, $$, esc, apiJson, requestToken, sessionValid, RANGES, fmtTime, fmtDate, fmtEventTime,
  relTime, num, typeLabel, openMenu } from "./core.js";
import * as overview from "./views/overview.js";
import * as realtime from "./views/realtime.js";
import * as history from "./views/history.js";
import * as domains from "./views/domains.js";
import * as rules from "./views/rules.js";
import * as connections from "./views/connections.js";

const VIEWS = { overview, realtime, history, domains, rules, connections };
const ALIASES = { settings: "connections", "": "overview" };
const REFRESH_MS = 15000;
const STREAM_EVENTS = ["attack.detected", "anomaly.scored", "alert.triggered", "log.enriched"];
let current = null;
const liveAlerts = [];

// ---------- Đăng nhập ----------
function askLogin() {
  return new Promise((resolve) => {
    const box = $("#login");
    const msg = $("#login-msg");
    box.hidden = false;
    $("#login-secret").focus();
    $("#login-form").onsubmit = async (e) => {
      e.preventDefault();
      msg.hidden = true;
      let ok = false;
      try { ok = await requestToken($("#login-id").value.trim(), $("#login-secret").value); } catch (_) { /* gateway lỗi */ }
      if (ok) { box.hidden = true; $("#login-secret").value = ""; resolve(); }
      else { msg.textContent = "Sai tài khoản hoặc mật khẩu (hoặc gateway không phản hồi)."; msg.hidden = false; }
    };
  });
}

// Cookie còn hạn -> dùng tiếp; local (AUTH_ALLOW_ANY) -> tự đăng nhập admin/admin; production -> form đăng nhập.
async function authenticate() {
  try { state.user = sessionStorage.getItem("waf-user") || state.user; } catch (_) { /* bỏ qua */ }
  if (await sessionValid()) return;
  if (await requestToken("admin", "admin")) return;
  await askLogin();
}

// ---------- Dữ liệu dùng chung ----------
async function loadShared() {
  const [allowlist, ruleDoc, summary] = await Promise.all([
    apiJson("/api/scope/allowlist").then((r) => r.items || []).catch(() => state.allowlist),
    apiJson("/api/rules/").then((r) => r.rules || []).catch(() => state.rules),
    apiJson("/api/dashboard/summary").catch(() => state.summary),
  ]);
  state.allowlist = allowlist;
  state.rules = ruleDoc;
  state.summary = summary;
  paintSidebar();
}

function paintSidebar() {
  const enabled = state.allowlist.filter((d) => d.enabled).length;
  $("#ws-domains").textContent = `${num(enabled)} tên miền được giám sát`;
  $("#account-name").textContent = state.user;
  $("#avatar").textContent = state.user.slice(0, 2);
  const stage = state.summary && state.summary.stages && state.summary.stages[0];
  const collector = $("#collector");
  collector.classList.toggle("down", !state.streamOnline);
  collector.querySelector("b").textContent = state.streamOnline ? "● Luồng sự kiện hoạt động" : "● Mất kết nối luồng sự kiện";
  $("#collector-sync").textContent = stage ? `Pipeline đồng bộ ${relTime(stage.at)}` : "Chưa có thống kê pipeline";
  const pill = $("#live-pill");
  pill.className = `tag ${state.streamOnline ? "ok" : "danger"}`;
  pill.textContent = state.streamOnline ? "Trực tiếp" : "Mất kết nối";
}

// ---------- Điều hướng ----------
function show(name) {
  const viewName = VIEWS[name] ? name : ALIASES[name] || "overview";
  // Đổi màn hình thì đóng hộp thoại, ngăn kéo và menu đang mở của màn hình cũ.
  document.getElementById("dialog-host").innerHTML = "";
  $$(".menu.floating").forEach((menu) => menu.remove());
  if (current && VIEWS[current].setActive) VIEWS[current].setActive(false);
  current = viewName;
  $$(".view").forEach((s) => s.classList.toggle("active", s.dataset.view === viewName));
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === viewName));
  $("#crumb").textContent = VIEWS[viewName].meta.title;
  if (VIEWS[viewName].setActive) VIEWS[viewName].setActive(true);
  reloadCurrent();
}

async function reloadCurrent() {
  if (!current) return;
  await VIEWS[current].load(document.getElementById(`view-${current}`));
  const now = new Date();
  $("#updated-at").textContent = `Cập nhật lúc ${fmtTime(now)} · ${fmtDate(now)}`;
}

function routeFromHash() {
  show(location.hash.slice(1));
}

// ---------- Luồng sự kiện ----------
function connectStream() {
  let source;
  try { source = new EventSource("/api/dashboard/stream", { withCredentials: true }); } catch (_) { return; }
  source.onopen = () => { state.streamOnline = true; paintSidebar(); };
  source.onerror = () => { state.streamOnline = false; paintSidebar(); };
  STREAM_EVENTS.forEach((name) => source.addEventListener(name, (e) => {
    let env;
    try { env = JSON.parse(e.data); } catch (_) { return; }
    realtime.onEvent(env);
    if (name === "alert.triggered") {
      liveAlerts.unshift({ domain: env.domain, ...(env.data || {}), triggered_at: env.timestamp });
      liveAlerts.length = Math.min(liveAlerts.length, 20);
      $("#bell-badge").hidden = false;
    }
  }));
}

function openAlerts(anchor) {
  $("#bell-badge").hidden = true;
  const alerts = liveAlerts.length ? liveAlerts : ((state.summary && state.summary.alerts) || []);
  const items = alerts.slice(0, 6).map((a) => ({
    label: `${fmtEventTime(a.triggered_at)} · ${a.domain} · ${(a.attack_types || []).map(typeLabel).join(", ") || "cảnh báo"} · ${a.src_ip || ""}`,
    onClick: () => emit("navigate", { view: "history", params: { q: a.src_ip || "" } }),
  }));
  const menu = openMenu(anchor, items.length ? items : [{ label: "Chưa có cảnh báo", onClick: () => {} }]);
  menu.style.minWidth = "360px";
}

// ---------- Thanh công cụ ----------
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("waf-theme", theme); } catch (_) { /* chế độ riêng tư */ }
  $("#theme-icon").style.setProperty("--icon", `url(/static/icons/${theme === "dark" ? "sun" : "moon"}.svg)`);
}

function bindToolbar() {
  const range = $("#range-select");
  range.innerHTML = RANGES.map((r) => `<option value="${r.hours}" ${r.hours === state.hours ? "selected" : ""}>${esc(r.label)}</option>`).join("");
  range.addEventListener("change", (e) => emit("range", Number(e.target.value)));
  $("#refresh-btn").addEventListener("click", async () => { await loadShared(); reloadCurrent(); });
  $("#theme-btn").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  setTheme(document.documentElement.dataset.theme || "light");
  $("#bell-btn").addEventListener("click", (e) => openAlerts(e.currentTarget));
  $("#global-search").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    emit("navigate", { view: "history", params: { q: e.target.value.trim() } });
  });
  $("#nav").addEventListener("click", (e) => {
    const item = e.target.closest("[data-view]");
    if (item) location.hash = item.dataset.view;
  });
}

on("navigate", ({ view, params }) => {
  if (view === "history" && params) history.setParams(params);
  if (location.hash.slice(1) === view) show(view); else location.hash = view;
});
on("range", (hours) => {
  state.hours = hours;
  $("#range-select").value = String(hours);
  reloadCurrent();
});
on("allowlist-changed", async () => { await loadShared(); reloadCurrent(); });
on("rules-changed", async () => { await loadShared(); reloadCurrent(); });

async function main() {
  bindToolbar();
  await authenticate();
  Object.entries(VIEWS).forEach(([name, view]) => view.render(document.getElementById(`view-${name}`)));
  await loadShared();
  connectStream();
  window.addEventListener("hashchange", routeFromHash);
  routeFromHash();
  setInterval(async () => {
    state.summary = await apiJson("/api/dashboard/summary").catch(() => state.summary);
    paintSidebar();
    if (current === "overview") reloadCurrent();
  }, REFRESH_MS);
}

main();
