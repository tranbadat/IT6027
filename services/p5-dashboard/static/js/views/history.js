// Lịch sử sự kiện: lọc, phân bố theo thời gian, nhật ký phân trang, xuất CSV, xem chi tiết.
import { state, emit, esc, icon, apiJson, num, rangeLabel, fmtShort, fmtEventTime, typeText, typeLabel, sevTag,
  sevLabel, SEVERITIES, RULE_GROUPS, emptyState, download, openMenu, toast } from "../core.js";
import { columns } from "../charts.js";
import { detailHtml } from "./realtime.js";

export const meta = { title: "Lịch sử" };

const view = {
  filters: { q: "", domain: "", attack_type: "", severity: "" },
  page: 1,
  size: 10,
  hidden: new Set(),
  rows: [],
};
let root = null;

const COLUMNS = { sev: "Mức độ", rule: "Quy tắc", status: "HTTP" };

function query(extra = {}) {
  const params = new URLSearchParams({ hours: state.hours, ...extra });
  Object.entries(view.filters).forEach(([k, v]) => { if (v) params.set(k, v); });
  return params.toString();
}

function chips() {
  const f = view.filters;
  const items = [`<span class="chip">${esc(rangeLabel())}</span>`];
  if (f.q) items.push(`<span class="chip">Tìm: ${esc(f.q)}<button data-clear="q">${icon("x", 11)}</button></span>`);
  if (f.domain) items.push(`<span class="chip">Tên miền: ${esc(f.domain)}<button data-clear="domain">${icon("x", 11)}</button></span>`);
  if (f.attack_type) items.push(`<span class="chip">Loại: ${esc(typeLabel(f.attack_type))}<button data-clear="attack_type">${icon("x", 11)}</button></span>`);
  if (f.severity) items.push(`<span class="chip">Mức độ: ${esc(sevLabel(f.severity))}<button data-clear="severity">${icon("x", 11)}</button></span>`);
  const any = f.q || f.domain || f.attack_type || f.severity;
  return `<span>Đang lọc:</span>${items.join("")}${any ? `<button class="link" data-clear="all" style="color:var(--muted)">Xóa tất cả</button>` : ""}`;
}

function rowsHtml(rows) {
  return rows.map((a, i) => `<div class="trow tall clickable" data-index="${i}">`
    + `<span class="cell mono muted" style="width:78px">${esc(fmtEventTime(a.detected_at))}</span>`
    + `<span class="cell" style="width:148px"><span class="mono" style="font-size:11px">${esc(a.src_ip)}</span><span class="sub">${esc(a.method || "")}</span></span>`
    + `<span class="cell grow">${esc(a.domain)}<span class="sub mono">${esc(a.url || "")}</span></span>`
    + `<span class="cell" style="width:142px">${typeText(a.attack_type)}</span>`
    + `<span class="cell c-sev" style="width:130px">${sevTag(a.severity)}</span>`
    + `<span class="cell mono c-rule" style="width:150px;font-size:11px" title="${esc(a.rule_name || "")}">${esc(a.rule_id || "–")}</span>`
    + `<span class="cell mono c-status" style="width:48px">${esc(a.status ?? "–")}</span></div>`).join("");
}

function pager(total) {
  const pages = Math.max(1, Math.ceil(total / view.size));
  const nums = new Set([1, pages, view.page - 1, view.page, view.page + 1].filter((p) => p >= 1 && p <= pages));
  let last = 0;
  const buttons = [...nums].sort((a, b) => a - b).map((p) => {
    const gap = p - last > 1 ? `<span class="muted">…</span>` : "";
    last = p;
    return `${gap}<button class="num ${p === view.page ? "on" : ""}" data-page="${p}">${num(p)}</button>`;
  }).join("");
  return `<label class="select" style="height:32px">${icon("chevron-down")}<select id="hi-size">`
    + [10, 25, 50].map((s) => `<option value="${s}" ${s === view.size ? "selected" : ""}>${s} / trang</option>`).join("")
    + `</select></label><button class="btn sm" data-page="${view.page - 1}" ${view.page <= 1 ? "disabled" : ""}>Trước</button>`
    + buttons + `<button class="btn sm" data-page="${view.page + 1}" ${view.page >= pages ? "disabled" : ""}>Sau</button>`;
}

function distribution(data) {
  const h = data.histogram;
  if (!h) return "";
  const chart = columns(h.buckets, { height: 105 });
  const start = new Date(h.start).getTime();
  const end = start + h.buckets.length * h.bucket_minutes * 60000;
  const ticks = Array.from({ length: 5 }, (_, i) => `<span>${esc(fmtShort(new Date(start + ((end - start) * i) / 4)))}</span>`).join("");
  return `<div class="card panel"><div class="panel-head"><div><h2 class="panel-title">Phân bố sự kiện</h2>`
    + `<p class="panel-sub">${num(data.total)} sự kiện phù hợp · ${esc(fmtShort(new Date(start)))} – ${esc(fmtShort(new Date(end)))}</p></div>`
    + `<span class="panel-meta">Khoảng thời gian: ${h.bucket_minutes} phút</span></div>`
    + `<div class="chart-row"><div class="y-axis" style="height:105px"><span>${num(chart.max)}</span><span>${num(chart.max / 2)}</span><span>0</span></div>`
    + `<div class="chart-area">${chart.html}<div class="x-axis">${ticks}</div></div></div>`
    + `<div style="display:flex;gap:24px;font-size:11px" class="muted"><span>Tổng: <b style="color:var(--text)">${num(data.total)}</b></span>`
    + `<span>IP duy nhất: <b style="color:var(--text)">${num(data.unique_ips)}</b></span></div></div>`;
}

function resultsHtml(data) {
  const total = data.total || 0;
  if (!total) {
    const any = Object.values(view.filters).some(Boolean);
    return `<div class="card">${emptyState({
      title: "Không tìm thấy sự kiện phù hợp",
      text: `Không có sự kiện nào ${any ? "khớp bộ lọc " : ""}trong ${rangeLabel()}. Hãy mở rộng thời gian hoặc xóa bộ lọc để xem thêm dữ liệu.`,
      actions: `${any ? `<button class="btn primary" data-clear="all">${icon("filter")}Xóa bộ lọc</button>` : ""}`
        + (state.hours < 168 ? `<button class="btn" data-widen>${icon("calendar-days")}Mở rộng thời gian</button>` : ""),
      foot: state.streamOnline ? "Bộ thu thập vẫn đang hoạt động bình thường." : "",
    })}</div>`;
  }
  const from = (view.page - 1) * view.size + 1;
  const to = from + data.attacks.length - 1;
  const hide = [...view.hidden].map((c) => `.hi-table .c-${c}{display:none}`).join("");
  return `<style>${hide}</style><div class="card hi-table"><div class="table-head compact"><div style="display:flex;align-items:center;gap:8px">`
    + `<h2 class="panel-title">Nhật ký yêu cầu</h2><span class="tag neutral">${num(total)} sự kiện</span></div>`
    + `<div style="display:flex;align-items:center;gap:8px"><span class="muted" style="font-size:10px">Sắp xếp: Mới nhất trước</span>`
    + `<button class="btn sm" id="hi-cols">${icon("columns-3")}Cột hiển thị</button></div></div>`
    + `<div class="thead"><span class="cell" style="width:78px">Thời gian ↓</span><span class="cell" style="width:148px">IP nguồn</span>`
    + `<span class="cell grow">Tên miền / Đường dẫn</span><span class="cell" style="width:142px">Loại tấn công</span>`
    + `<span class="cell c-sev" style="width:130px">Mức độ</span><span class="cell c-rule" style="width:150px">Quy tắc</span><span class="cell c-status" style="width:48px">HTTP</span></div>`
    + `<div id="hi-rows">${rowsHtml(data.attacks)}</div>`
    + `<div class="table-foot"><span>Hiển thị ${num(from)}–${num(to)} trong ${num(total)} sự kiện</span><div class="pager">${pager(total)}</div></div></div>`;
}

function openDetail(row) {
  const env = { event_id: row.event_id, request_id: row.request_id, timestamp: row.detected_at, domain: row.domain, data: row };
  const host = document.getElementById("dialog-host");
  host.innerHTML = `<div class="overlay drawer-host"><aside class="drawer"><div class="dlg-body detail" style="width:auto;position:static">${detailHtml(env)}</div></aside></div>`;
  const close = () => { host.innerHTML = ""; };
  host.querySelector(".overlay").addEventListener("click", (e) => {
    if (e.target.classList.contains("overlay") || e.target.closest("[data-close-detail]")) close();
    const history = e.target.closest("[data-history]");
    if (history) { close(); setParams({ q: history.dataset.history }); load(root); }
    if (e.target.closest("[data-copy]")) {
      navigator.clipboard?.writeText(host.querySelector("[data-payload]")?.textContent || "").then(() => toast("Đã sao chép payload"));
    }
  });
}

async function exportCsv() {
  try {
    const data = await apiJson(`/api/dashboard/attacks?${query({ limit: 1000, offset: 0 })}`);
    const cols = ["detected_at", "domain", "src_ip", "method", "url", "attack_type", "severity", "rule_id", "status", "score", "matched_value"];
    const quote = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = [cols.join(","), ...data.attacks.map((a) => cols.map((c) => quote(a[c])).join(","))];
    download(`waf-lich-su-${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "")}.csv`, lines.join("\n"));
    if (data.total > data.attacks.length) toast(`Đã xuất ${num(data.attacks.length)} / ${num(data.total)} sự kiện mới nhất`);
  } catch (err) {
    toast(`Không xuất được CSV: ${err.message}`);
  }
}

export function setParams(params = {}) {
  view.filters = { q: "", domain: "", attack_type: "", severity: "", ...params };
  view.page = 1;
}

function syncInputs() {
  root.querySelector("#hi-q").value = view.filters.q;
  root.querySelector("#hi-type").value = view.filters.attack_type;
  root.querySelector("#hi-sev").value = view.filters.severity;
  root.querySelector("#hi-domain").value = view.filters.domain;
}

export function render(el) {
  root = el;
  const types = [...RULE_GROUPS, "traffic_flood", "error_scan", "path_scan"];
  el.innerHTML = `<div class="page-head"><div><h1 class="page-title">Lịch sử sự kiện</h1><p class="page-sub">Tìm kiếm, phân tích và xuất nhật ký tấn công đã được lưu trữ.</p></div>`
    + `<button class="btn" id="hi-csv">${icon("download")}Xuất CSV</button></div>`
    + `<form class="card panel" id="hi-form" style="padding:16px;gap:12px"><div class="filters">`
    + `<label class="field" style="width:390px">${icon("search")}<input id="hi-q" placeholder="Tìm theo IP, URL hoặc mã quy tắc…" maxlength="200"></label>`
    + `<label class="select">${icon("chevron-down")}<select id="hi-domain"><option value="">Tên miền</option></select></label>`
    + `<label class="select">${icon("chevron-down")}<select id="hi-type"><option value="">Loại tấn công</option>${types.map((t) => `<option value="${t}">${esc(typeLabel(t))}</option>`).join("")}</select></label>`
    + `<label class="select">${icon("chevron-down")}<select id="hi-sev"><option value="">Mức độ</option>${SEVERITIES.map((s) => `<option value="${s}">${esc(sevLabel(s))}</option>`).join("")}</select></label>`
    + `<button class="btn primary" type="submit">${icon("filter")}Áp dụng bộ lọc</button></div><div class="chips" id="hi-chips"></div></form>`
    + `<div id="hi-dist"></div><div id="hi-results"></div>`;

  el.querySelector("#hi-form").addEventListener("submit", (e) => {
    e.preventDefault();
    view.filters = { q: el.querySelector("#hi-q").value.trim(), domain: el.querySelector("#hi-domain").value,
      attack_type: el.querySelector("#hi-type").value, severity: el.querySelector("#hi-sev").value };
    view.page = 1;
    load(el);
  });
  el.querySelector("#hi-csv").addEventListener("click", exportCsv);
  el.addEventListener("click", (e) => {
    const clear = e.target.closest("[data-clear]");
    if (clear) {
      e.preventDefault();
      if (clear.dataset.clear === "all") setParams(); else { view.filters[clear.dataset.clear] = ""; view.page = 1; }
      load(el);
    }
    if (e.target.closest("[data-widen]")) emit("range", 168);
    const page = e.target.closest("[data-page]");
    if (page && !page.disabled) { view.page = Number(page.dataset.page); load(el); }
    const row = e.target.closest("#hi-rows [data-index]");
    if (row) openDetail(view.rows[Number(row.dataset.index)]);
    if (e.target.closest("#hi-cols")) {
      const menu = openMenu(e.target.closest("#hi-cols"), Object.entries(COLUMNS).map(([key, label]) => ({
        label: `${view.hidden.has(key) ? "☐" : "☑"}  ${label}`,
        onClick: () => { if (view.hidden.has(key)) view.hidden.delete(key); else view.hidden.add(key); load(el); },
      })));
      menu.style.minWidth = "160px";
    }
  });
  el.addEventListener("change", (e) => {
    if (e.target.id === "hi-size") { view.size = Number(e.target.value); view.page = 1; load(el); }
  });
}

export async function load(el = root) {
  const domains = [...new Set(state.allowlist.map((d) => d.target))].sort();
  const domainSelect = el.querySelector("#hi-domain");
  domainSelect.innerHTML = `<option value="">Tên miền</option>` + domains.map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join("");
  syncInputs();
  el.querySelector("#hi-chips").innerHTML = chips();
  const results = el.querySelector("#hi-results");
  results.innerHTML = `<div class="card panel"><div class="skel" style="height:14px;width:30%"></div>${'<div class="skel" style="height:30px"></div>'.repeat(5)}</div>`;
  try {
    const data = await apiJson(`/api/dashboard/attacks?${query({ limit: view.size, offset: (view.page - 1) * view.size, histogram: true })}`);
    if (!data.persisted) {
      el.querySelector("#hi-dist").innerHTML = "";
      results.innerHTML = `<div class="card">${emptyState({ title: "Chưa có dữ liệu lịch sử", text: "Dashboard chưa kết nối được Postgres để lưu lịch sử tấn công." })}</div>`;
      return;
    }
    view.rows = data.attacks;
    el.querySelector("#hi-dist").innerHTML = distribution(data);
    results.innerHTML = resultsHtml(data);
  } catch (err) {
    results.innerHTML = `<div class="card">${emptyState({ title: "Không tải được lịch sử", text: err.message })}</div>`;
  }
}
