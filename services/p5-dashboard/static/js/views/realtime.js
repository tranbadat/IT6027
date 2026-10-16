// Thời gian thực: sự kiện từ SSE (/api/dashboard/stream), lưu lượng 60 giây, chi tiết yêu cầu.
import { state, emit, esc, icon, num, fmtTime, fmtDate, typeText, typeLabel, sevTag, sevLabel, SEVERITIES,
  RULE_GROUPS, toast } from "../core.js";
import { columns } from "../charts.js";

export const meta = { title: "Thời gian thực" };

const MAX_EVENTS = 300;
const SHOWN = 10;
const live = {
  events: [],            // attack.detected mới nhất trước: {env, key, receivedAt}
  traffic: new Map(),    // giây (epoch) -> số request (log.enriched)
  paused: false,
  autoUpdate: true,
  selectedId: null,
  filters: { text: "", domain: "", severity: "", type: "" },
  dirty: true,
};
let root = null;
let active = false;
let received = 0;

const nowSec = () => Math.floor(Date.now() / 1000);

export function onEvent(env) {
  if (env.event === "log.enriched") {
    const sec = nowSec();
    live.traffic.set(sec, (live.traffic.get(sec) || 0) + 1);
    for (const key of live.traffic.keys()) if (key < sec - 120) live.traffic.delete(key);
    live.dirty = true;
    return;
  }
  if (env.event !== "attack.detected" || live.paused) return;
  // key chọn dòng: event_id từ SSE, thiếu thì số thứ tự cục bộ (tránh mọi dòng cùng khoá rỗng).
  received += 1;
  live.events.unshift({ env, key: env.event_id || `rx-${received}`, receivedAt: Date.now() });
  if (live.events.length > MAX_EVENTS) live.events.length = MAX_EVENTS;
  live.dirty = true;
}

function matches({ env }) {
  const d = env.data || {};
  const f = live.filters;
  if (f.domain && env.domain !== f.domain) return false;
  if (f.severity && d.severity !== f.severity) return false;
  if (f.type && d.attack_type !== f.type) return false;
  if (f.text) {
    const needle = f.text.toLowerCase();
    if (!`${d.src_ip || ""} ${d.url || ""}`.toLowerCase().includes(needle)) return false;
  }
  return true;
}

function rate() {
  const now = Date.now();
  const last = live.events.filter((e) => e.receivedAt > now - 60000).length;
  const prev = live.events.filter((e) => e.receivedAt <= now - 60000 && e.receivedAt > now - 120000).length;
  return { last, change: prev ? Math.round(((last - prev) * 1000) / prev) / 10 : null };
}

function statusBar() {
  const r = rate();
  const tag = live.paused ? `<span class="tag warn">Đã tạm dừng</span>`
    : state.streamOnline ? `<span class="tag ok">Đang truyền trực tiếp</span>` : `<span class="tag danger">Mất kết nối luồng</span>`;
  const change = r.change === null ? "" : `<span class="${r.change > 0 ? "up-bad" : "down-good"}">${r.change > 0 ? "↑" : "↓"} ${num(Math.abs(r.change))}% so với phút trước</span>`;
  return `${tag}<div class="divider-v"></div><span><b>${num(r.last)}</b> <span class="muted">sự kiện / phút</span></span>${change}`;
}

function trafficPanel() {
  const sec = nowSec();
  const values = Array.from({ length: 60 }, (_, i) => live.traffic.get(sec - 59 + i) || 0);
  const total = values.reduce((s, v) => s + v, 0);
  const chart = columns(values, { titles: values.map((v, i) => `${fmtTime((sec - 59 + i) * 1000)} · ${v} yêu cầu`) });
  const labels = [50, 35, 20, 5, 0].map((ago) => `<span>${fmtTime((sec - ago) * 1000)}</span>`).join("");
  return `<div class="panel-head"><div><h2 class="panel-title">Lưu lượng trong 60 giây</h2>`
    + `<p class="panel-sub">Yêu cầu / giây · trung bình ${num(Math.round((total / 60) * 10) / 10)} · đỉnh ${num(Math.max(...values))}</p></div>`
    + `<span class="panel-meta">60 giây qua</span></div>`
    + `<div class="chart-row"><div class="y-axis" style="height:104px"><span>${num(chart.max)}</span><span>${num(chart.max / 2)}</span><span>0</span></div>`
    + `<div class="chart-area">${chart.html}<div class="x-axis">${labels}</div></div></div>`;
}

function eventTable(list) {
  const rows = list.slice(0, SHOWN).map(({ env, key }) => {
    const d = env.data || {};
    const selected = key === live.selectedId ? "selected" : "";
    return `<div class="trow tall clickable ${selected}" data-id="${esc(key)}">`
      + `<span class="cell mono muted" style="width:78px">${esc(fmtTime(env.timestamp))}</span>`
      + `<span class="cell" style="width:172px"><span class="mono" style="font-size:11px">${esc(d.src_ip)}</span><span class="sub">${esc(env.domain)}</span></span>`
      + `<span class="cell" style="width:142px">${typeText(d.attack_type)}</span>`
      + `<span class="cell" style="width:140px">${sevTag(d.severity)}</span>`
      + `<span class="cell mono" style="width:60px">${esc(d.status ?? "–")}</span></div>`;
  }).join("");
  const latest = list[0];
  const delay = latest ? Math.max(0, latest.receivedAt - new Date(latest.env.timestamp).getTime()) : null;
  return `<div class="thead"><span class="cell" style="width:78px">Thời gian ↓</span><span class="cell" style="width:172px">IP nguồn</span>`
    + `<span class="cell" style="width:142px">Loại tấn công</span><span class="cell" style="width:140px">Mức độ</span><span class="cell" style="width:60px">HTTP</span></div>`
    + (rows || `<div class="trow"><span class="cell muted">Chưa có sự kiện phù hợp. Sự kiện mới sẽ xuất hiện ngay khi được phát hiện.</span></div>`)
    + `<div class="table-foot"><span>Hiển thị ${Math.min(list.length, SHOWN)} sự kiện gần nhất</span>`
    + (delay !== null ? `<span class="up-good">Độ trễ ${num(delay)} ms</span>` : "<span></span>") + `</div>`;
}

function highlight(text, needle) {
  const safe = esc(text);
  if (!needle) return safe;
  const at = text.toLowerCase().indexOf(needle.toLowerCase());
  if (at < 0) return safe;
  return esc(text.slice(0, at)) + `<mark>${esc(text.slice(at, at + needle.length))}</mark>` + esc(text.slice(at + needle.length));
}

export function detailHtml(env, { closable = true } = {}) {
  if (!env) {
    return `<div class="detail-head"><h3>Chi tiết yêu cầu</h3></div><p class="muted">Chọn một sự kiện trong bảng để xem chi tiết.</p>`;
  }
  const d = env.data || {};
  const decoded = (() => { try { return decodeURIComponent(d.url || ""); } catch (_) { return d.url || ""; } })();
  const payload = `${highlight(`${d.method || "GET"} ${decoded}`, d.matched_value)}\nHost: ${esc(env.domain || d.host || "")}`
    + (d.user_agent ? `\nUser-Agent: ${esc(d.user_agent)}` : "");
  const rule = d.rule_name ? `${d.rule_id} · ${d.rule_name}` : d.rule_id;
  return `<div class="detail-head"><h3>Chi tiết yêu cầu</h3>${closable ? `<button class="icon-btn" data-close-detail title="Đóng">${icon("x", 16)}</button>` : ""}</div>`
    + `<div style="display:flex;gap:8px;flex-wrap:wrap">${sevTag(d.severity)}<span class="tag neutral">Đã ghi nhận · HTTP ${esc(d.status ?? "–")}</span></div>`
    + `<div style="display:flex;flex-direction:column;gap:5px;font-size:10px"><span class="muted">Mã yêu cầu</span><span class="mono">${esc(env.request_id || env.event_id)}</span>`
    + `<span class="muted">${esc(fmtDate(env.timestamp))} · ${esc(fmtTime(env.timestamp))} UTC+7</span></div>`
    + `<div class="props"><div><span>IP nguồn</span><span class="mono">${esc(d.src_ip)}</span></div>`
    + `<div><span>Tên miền</span><span class="mono">${esc(env.domain)}</span></div>`
    + `<div><span>Phương thức</span><span>${esc(d.method || "–")}</span></div>`
    + `<div><span>Trường khớp</span><span class="mono">${esc(d.field || "–")}</span></div>`
    + `<div><span>Quy tắc</span><span>${esc(rule || "–")}</span></div>`
    + `<div><span>Điểm rủi ro</span><span>${d.score != null ? `${esc(d.score)} / 100` : esc(sevLabel(d.severity))}</span></div></div>`
    + `<div style="display:flex;flex-direction:column;gap:8px"><div class="detail-head"><b style="font-size:12px">Payload phát hiện</b>`
    + `<button class="icon-btn" data-copy title="Sao chép">${icon("copy")}</button></div><div class="code danger" data-payload>${payload}</div>`
    + `<div class="evidence">${esc(typeLabel(d.attack_type))}: khớp “${esc(d.matched_value || "")}” trong trường ${esc(d.field || "–")}.</div></div>`
    + `<button class="btn" data-history="${esc(d.src_ip || "")}" style="align-self:flex-start">${icon("arrow-up-right")}Xem trong lịch sử</button>`;
}

function selectedEnv(list) {
  if (live.selectedId === "__none__") return undefined;
  const found = live.events.find((e) => e.key === live.selectedId);
  return (found || list[0] || {}).env;
}

function paint() {
  if (!root || !active) return;
  root.querySelector("#rt-status").innerHTML = statusBar();
  root.querySelector("#rt-traffic").innerHTML = trafficPanel();
  if (!live.autoUpdate && !live.forcePaint) return;
  live.forcePaint = false;
  const list = live.events.filter(matches);
  root.querySelector("#rt-table").innerHTML = eventTable(list);
  root.querySelector("#rt-detail").innerHTML = detailHtml(selectedEnv(list));
}

function optionList(values, labeler, allLabel) {
  return `<option value="">${esc(allLabel)}</option>` + values.map((v) => `<option value="${esc(v)}">${esc(labeler(v))}</option>`).join("");
}

export function render(el) {
  root = el;
  const types = [...RULE_GROUPS, "traffic_flood", "error_scan", "path_scan"];
  el.innerHTML = `<div class="page-head"><div><h1 class="page-title">Thời gian thực</h1><p class="page-sub">Theo dõi và điều tra các yêu cầu tấn công ngay khi chúng được phát hiện.</p></div></div>`
    + `<div class="card stream-bar"><div class="stream-state" id="rt-status"></div><div class="filters">`
    + `<button class="btn" id="rt-pause">${icon("pause")}<span>Tạm dừng</span></button><button class="btn" id="rt-clear">${icon("eraser")}Xóa màn hình</button></div></div>`
    + `<div class="filters"><label class="field" style="width:302px">${icon("search")}<input id="rt-text" placeholder="Lọc theo IP hoặc đường dẫn…"></label>`
    + `<label class="select">${icon("chevron-down")}<select id="rt-domain"></select></label>`
    + `<label class="select">${icon("chevron-down")}<select id="rt-sev">${optionList(SEVERITIES, (s) => `Mức độ: ${sevLabel(s)}`, "Mức độ: Tất cả")}</select></label>`
    + `<label class="select">${icon("chevron-down")}<select id="rt-type">${optionList(types, typeLabel, "Loại tấn công")}</select></label>`
    + `<button class="btn" id="rt-reset">${icon("rotate-ccw")}Đặt lại</button></div>`
    + `<div class="split"><div class="main"><div class="card panel" id="rt-traffic"></div>`
    + `<div class="card"><div class="table-head compact"><h2 class="panel-title">Yêu cầu bị phát hiện mới nhất</h2>`
    + `<button class="link" id="rt-auto" style="color:var(--muted);display:inline-flex;gap:6px;align-items:center">${icon("arrow-down-to-line", 12)}<span>Tự động cập nhật đang bật</span></button></div>`
    + `<div id="rt-table"></div></div></div><aside class="card detail" id="rt-detail"></aside></div>`;

  const f = live.filters;
  const bind = (id, key) => el.querySelector(id).addEventListener("input", (e) => { f[key] = e.target.value; live.forcePaint = true; paint(); });
  bind("#rt-text", "text"); bind("#rt-domain", "domain"); bind("#rt-sev", "severity"); bind("#rt-type", "type");
  el.querySelector("#rt-reset").addEventListener("click", () => {
    Object.assign(f, { text: "", domain: "", severity: "", type: "" });
    ["#rt-text", "#rt-domain", "#rt-sev", "#rt-type"].forEach((id) => { el.querySelector(id).value = ""; });
    live.forcePaint = true; paint();
  });
  el.querySelector("#rt-pause").addEventListener("click", (e) => {
    live.paused = !live.paused;
    e.currentTarget.innerHTML = live.paused ? `${icon("play")}<span>Tiếp tục</span>` : `${icon("pause")}<span>Tạm dừng</span>`;
    paint();
  });
  el.querySelector("#rt-clear").addEventListener("click", () => { live.events = []; live.selectedId = null; live.forcePaint = true; paint(); });
  el.querySelector("#rt-auto").addEventListener("click", (e) => {
    live.autoUpdate = !live.autoUpdate;
    e.currentTarget.querySelector("span").textContent = live.autoUpdate ? "Tự động cập nhật đang bật" : "Tự động cập nhật đang tắt";
    live.forcePaint = true; paint();
  });
  el.querySelector("#rt-table").addEventListener("click", (e) => {
    const row = e.target.closest("[data-id]");
    if (row) { live.selectedId = row.dataset.id; live.forcePaint = true; paint(); }
  });
  el.querySelector("#rt-detail").addEventListener("click", (e) => {
    if (e.target.closest("[data-close-detail]")) { live.selectedId = "__none__"; live.forcePaint = true; paint(); }
    const history = e.target.closest("[data-history]");
    if (history) emit("navigate", { view: "history", params: { q: history.dataset.history } });
    if (e.target.closest("[data-copy]")) {
      const text = el.querySelector("[data-payload]")?.textContent || "";
      navigator.clipboard?.writeText(text).then(() => toast("Đã sao chép payload"), () => toast("Không sao chép được"));
    }
  });
  setInterval(() => { if (live.dirty || active) { live.dirty = false; paint(); } }, 1000);
}

export function load() {
  const select = root.querySelector("#rt-domain");
  const current = select.value;
  const domains = [...new Set([...state.allowlist.map((d) => d.target), ...live.events.map((e) => e.env.domain)])].filter(Boolean).sort();
  select.innerHTML = optionList(domains, (d) => d, "Tất cả tên miền");
  select.value = current;
  live.forcePaint = true;
  paint();
}

export function setActive(value) { active = value; if (value) { live.forcePaint = true; paint(); } }
