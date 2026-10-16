// Tên miền: danh sách đích được giám sát (P1 allowlist) kèm số tấn công và xu hướng trong khoảng thời gian.
import { state, emit, esc, icon, apiJson, num, rangeLabel, relTime, fmtTime, openMenu, toast, emptyState } from "../core.js";
import { sparkline } from "../charts.js";
import { openDomainDialog } from "../dialogs.js";

export const meta = { title: "Tên miền" };

const view = { search: "", type: "", status: "", stats: [] };
let root = null;

function statBox(iconName, label, value) {
  return `<div class="card stat-box"><div class="stat-icon">${icon(iconName, 18)}</div><div><small>${esc(label)}</small><b>${value}</b></div></div>`;
}

function filtered() {
  const needle = view.search.toLowerCase();
  return state.allowlist.filter((d) => (!needle || `${d.target} ${d.note || ""}`.toLowerCase().includes(needle))
    && (!view.type || d.target_type === view.type)
    && (!view.status || (view.status === "on" ? d.enabled : !d.enabled)));
}

function rowsHtml(items) {
  const stats = Object.fromEntries(view.stats.map((s) => [s.domain, s]));
  return items.map((d) => {
    const s = stats[d.target] || { count: 0, trend: [], last_attack_at: null };
    return `<div class="trow" style="min-height:81px" data-target="${esc(d.target)}">`
      + `<span class="cell" style="width:320px;display:flex;flex-direction:column;gap:4px">`
      + `<span style="display:flex;align-items:center;gap:7px" class="mono">${icon("globe", 13)}<b style="font-weight:500">${esc(d.target)}</b></span>`
      + (d.note ? `<span class="mono muted" style="font-size:10px">↳ ${esc(d.note)}</span>` : "") + `</span>`
      + `<span class="cell" style="width:80px">${d.target_type === "app" ? "Ứng dụng" : "Tên miền"}</span>`
      + `<span class="cell" style="width:136px;display:flex;align-items:center;gap:8px"><button class="switch ${d.enabled ? "on" : ""}" data-toggle title="Bật/tắt giám sát"></button>`
      + `<span style="font-size:10px;color:${d.enabled ? "var(--ok)" : "var(--muted)"}">${d.enabled ? "Đang bật" : "Đang tắt"}</span></span>`
      + `<span class="cell" style="width:222px;display:flex;align-items:center;gap:20px"><b style="font-weight:600;min-width:48px">${num(s.count)}</b>${sparkline(s.trend, { width: 100, height: 32 })}</span>`
      + `<span class="cell muted" style="width:196px">${s.last_attack_at ? esc(relTime(s.last_attack_at)) : "Chưa có tấn công"}</span>`
      + `<span class="cell" style="display:flex;gap:18px"><button class="icon-btn" data-edit title="Sửa">${icon("square-pen", 15)}</button>`
      + `<button class="icon-btn" data-more title="Thêm">${icon("ellipsis", 15)}</button></span></div>`;
  }).join("");
}

function paint() {
  const items = filtered();
  const total = view.stats.reduce((s, d) => s + d.count, 0);
  root.querySelector("#dm-stats").innerHTML = statBox("globe", "Tên miền được giám sát", num(state.allowlist.length))
    + statBox("shield-check", "Đang hoạt động", `${num(state.allowlist.filter((d) => d.enabled).length)} / ${num(state.allowlist.length)}`)
    + statBox("activity", `Tấn công trong ${rangeLabel().replace(" qua", "")}`, num(total));
  root.querySelector("#dm-rows").innerHTML = rowsHtml(items)
    || `<div>${emptyState({ title: "Không có tên miền phù hợp", text: state.allowlist.length ? "Thử đổi từ khóa hoặc bộ lọc." : "Thêm tên miền đầu tiên để bắt đầu giám sát." })}</div>`;
  root.querySelector("#dm-foot").innerHTML = `<span>Hiển thị ${num(items.length)} trong ${num(state.allowlist.length)} tên miền</span><span>Cập nhật lúc ${esc(fmtTime(new Date()))}</span>`;
}

async function save(d, changes) {
  try {
    await apiJson("/api/scope/allowlist", { method: "POST", body: JSON.stringify({ ...d, ...changes }) });
    emit("allowlist-changed");
  } catch (err) {
    toast(`Không cập nhật được ${d.target}: ${err.message}`);
  }
}

export function render(el) {
  root = el;
  el.innerHTML = `<div class="page-head"><div><h1 class="page-title">Tên miền</h1><p class="page-sub">Quản lý các đích được NGINX WAF Log Analyzer giám sát.</p></div>`
    + `<button class="btn primary" id="dm-add">${icon("plus")}Thêm tên miền</button></div>`
    + `<div class="row" id="dm-stats" style="gap:16px"></div>`
    + `<div class="card"><div class="table-head compact"><label class="field" style="width:350px">${icon("search")}<input id="dm-search" placeholder="Tìm tên miền hoặc ghi chú…"></label>`
    + `<div class="filters"><label class="select">${icon("chevron-down")}<select id="dm-type"><option value="">Tất cả loại</option><option value="domain">Tên miền</option><option value="app">Ứng dụng</option></select></label>`
    + `<label class="select">${icon("chevron-down")}<select id="dm-status"><option value="">Tất cả trạng thái</option><option value="on">Đang hoạt động</option><option value="off">Đang tắt</option></select></label></div></div>`
    + `<div class="thead"><span class="cell" style="width:320px">Tên miền / Ghi chú</span><span class="cell" style="width:80px">Loại</span>`
    + `<span class="cell" style="width:136px">Giám sát</span><span class="cell" style="width:222px" id="dm-count-head">Tấn công ↓</span>`
    + `<span class="cell" style="width:196px">Tấn công gần nhất</span><span class="cell">Thao tác</span></div>`
    + `<div id="dm-rows"></div><div class="table-foot" id="dm-foot"></div></div>`
    + `<div class="note">${icon("info", 16)}<span>Tên miền được phân tích khi log của nó tới bộ thu thập (C1). Thay đổi trạng thái có hiệu lực trong vòng 60 giây (bộ nhớ đệm kiểm tra phạm vi).</span></div>`;

  el.querySelector("#dm-add").addEventListener("click", () => openDomainDialog(null, () => emit("allowlist-changed")));
  el.querySelector("#dm-search").addEventListener("input", (e) => { view.search = e.target.value; paint(); });
  el.querySelector("#dm-type").addEventListener("change", (e) => { view.type = e.target.value; paint(); });
  el.querySelector("#dm-status").addEventListener("change", (e) => { view.status = e.target.value; paint(); });
  el.querySelector("#dm-rows").addEventListener("click", (e) => {
    const row = e.target.closest("[data-target]");
    if (!row) return;
    const d = state.allowlist.find((x) => x.target === row.dataset.target);
    if (e.target.closest("[data-toggle]")) save(d, { enabled: !d.enabled });
    if (e.target.closest("[data-edit]")) openDomainDialog(d, () => emit("allowlist-changed"));
    const more = e.target.closest("[data-more]");
    if (more) openMenu(more, [
      { label: "Xem lịch sử tấn công", onClick: () => emit("navigate", { view: "history", params: { domain: d.target } }) },
      { label: "Xóa khỏi danh sách giám sát", danger: true, onClick: async () => {
        if (!confirm(`Xóa ${d.target} khỏi danh sách giám sát?`)) return;
        try {
          await apiJson(`/api/scope/allowlist/${encodeURIComponent(d.target)}`, { method: "DELETE" });
          toast(`Đã xóa ${d.target}`);
          emit("allowlist-changed");
        } catch (err) { toast(`Không xóa được: ${err.message}`); }
      } },
    ]);
  });
}

export async function load(el = root) {
  el.querySelector("#dm-count-head").textContent = `Tấn công ${rangeLabel().replace(" qua", "")} ↓`;
  try {
    view.stats = (await apiJson(`/api/dashboard/stats/domains?hours=${state.hours}`)).domains || [];
  } catch (_) {
    view.stats = [];
  }
  paint();
}
