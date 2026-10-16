// Kết nối: liên kết tới dịch vụ vận hành (DASHBOARD_LINKS hoặc mặc định theo host:cổng) và kiểm tra truy cập được không.
import { state, esc, icon, apiJson, num, fmtTime } from "../core.js";

export const meta = { title: "Kết nối" };

const CHECK_EVERY_MS = 30000;
const SLOW_MS = 1500;
const KNOWN = [
  { key: "grafana", icon: "chart-no-axes-combined", desc: "Trực quan hóa và phân tích bảo mật" },
  { key: "prometheus", icon: "flame", desc: "Thu thập chỉ số hệ thống và WAF" },
  { key: "mail", icon: "mail", desc: "Xem email cảnh báo do P4 gửi" },
  { key: "kafka", icon: "workflow", desc: "Giám sát luồng nhật ký và hàng đợi" },
  { key: "swagger", icon: "braces", desc: "Tài liệu và thử nghiệm API quản trị" },
];
let root = null;
let services = [];
let timer = null;

function defaults() {
  const h = location.hostname;
  return [["Grafana", `http://${h}:3000`], ["Prometheus", `http://${h}:9090`], ["Mailpit (email cảnh báo)", `http://${h}:8025`],
    ["Kafka UI", `http://${h}:8085`], ["Swagger (OpenAPI)", `http://${h}:8090`]].map(([name, url]) => ({ name, url }));
}

const known = (name) => KNOWN.find((k) => name.toLowerCase().includes(k.key)) || { icon: "external-link", desc: "Dịch vụ vận hành" };

/** Trình duyệt chỉ biết có truy cập được hay không (no-cors), không đọc được nội dung. */
async function probe(service) {
  const started = performance.now();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    await fetch(service.url, { mode: "no-cors", cache: "no-store", signal: controller.signal });
    const ms = Math.round(performance.now() - started);
    return { ok: true, ms, slow: ms > SLOW_MS };
  } catch (_) {
    return { ok: false, ms: null, slow: false };
  } finally {
    clearTimeout(timeout);
  }
}

function card(s) {
  const k = known(s.name);
  const status = !s.health ? `<span class="tag neutral">Đang kiểm tra</span>`
    : !s.health.ok ? `<span class="tag danger">Không truy cập được</span>`
      : s.health.slow ? `<span class="tag warn">Có độ trễ</span>` : `<span class="tag ok">Đã kết nối</span>`;
  const health = s.health && s.health.ok ? `${num(s.health.ms)} ms` : s.health ? "Không phản hồi từ trình duyệt" : "–";
  return `<div class="card service"><div class="service-top"><div class="service-logo">${icon(k.icon, 21)}</div>${status}</div>`
    + `<div><h3>${esc(s.name)}</h3><p>${esc(k.desc)}</p></div><div class="url">${esc(s.url.replace(/^https?:\/\//, ""))}</div>`
    + `<div class="separator"></div><div class="health"><small>Độ trễ phản hồi</small><b class="${s.health && s.health.slow ? "up-bad" : ""}">${esc(health)}</b></div>`
    + `<div class="service-foot"><span>${s.checkedAt ? `Kiểm tra lúc ${esc(fmtTime(s.checkedAt))}` : ""}</span>`
    + `<a class="btn sm" href="${esc(s.url)}" target="_blank" rel="noopener">${icon("external-link")}Mở dịch vụ</a></div></div>`;
}

function infoCard() {
  const stages = (state.summary && state.summary.stages) || [];
  const latest = stages.slice(0, 4).map((st) => `${esc(st.stage)}: ${num(st.processed)} sự kiện`).join("<br>");
  return `<div class="info-card">${icon("lock-keyhole", 22)}<h3>Truy cập an toàn</h3>`
    + `<p>Các dịch vụ vận hành nên chỉ mở trong mạng nội bộ, qua VPN hoặc qua Nginx có đăng nhập. Trình duyệt kiểm tra truy cập trực tiếp từ máy của bạn.</p>`
    + `<div class="separator"></div><div class="lines">Gateway: ${esc(location.host)}<br>Tài khoản: ${esc(state.user)}<br>`
    + `${latest || "Chưa có thống kê pipeline (stage.completed)"}</div></div>`;
}

function paint() {
  const ok = services.filter((s) => s.health && s.health.ok);
  const slow = ok.filter((s) => s.health.slow).length;
  const summary = root.querySelector("#cn-summary");
  const checked = services.filter((s) => s.health).length;
  summary.style.color = !checked || ok.length === services.length ? "var(--ok)" : ok.length ? "var(--sev-medium)" : "var(--danger)";
  summary.innerHTML = `${icon(ok.length === services.length ? "circle-check" : "info", 20)}<div style="line-height:1.5"><b style="font-size:12px">${num(ok.length)} / ${num(services.length)} dịch vụ truy cập được</b>`
    + `<div class="muted" style="font-size:11px">${num(ok.length - slow)} kết nối ổn định · ${num(slow)} kết nối cần theo dõi độ trễ</div></div>`
    + `<span class="muted" style="margin-left:auto;font-size:11px">Tự động kiểm tra mỗi 30 giây</span>`;
  root.querySelector("#cn-cards").innerHTML = services.map(card).join("") + infoCard();
}

async function check() {
  await Promise.all(services.map(async (s) => { s.health = await probe(s); s.checkedAt = new Date(); }));
  paint();
}

export function render(el) {
  root = el;
  el.innerHTML = `<div class="page-head"><div><h1 class="page-title">Kết nối</h1><p class="page-sub">Truy cập nhanh các dịch vụ vận hành và kiểm tra khả năng truy cập.</p></div>`
    + `<button class="btn" id="cn-check">${icon("refresh-cw")}Kiểm tra kết nối</button></div>`
    + `<div class="card" id="cn-summary" style="padding:16px;display:flex;align-items:center;gap:12px"></div>`
    + `<div class="services" id="cn-cards"></div>`;
  el.querySelector("#cn-check").addEventListener("click", check);
}

export async function load() {
  let links = [];
  try { links = (await apiJson("/api/dashboard/links")).links || []; } catch (_) { /* dùng mặc định */ }
  services = (links.length ? links : defaults()).map((l) => ({ name: l.name, url: l.url, health: null, checkedAt: null }));
  paint();
  check();
}

export function setActive(value) {
  clearInterval(timer);
  if (value) timer = setInterval(check, CHECK_EVERY_MS);
}
