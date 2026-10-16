// Quy tắc: phân bố theo danh mục, danh sách có bật/tắt, mở rộng xem regex, số lần khớp và xu hướng.
import { state, emit, esc, icon, apiJson, num, rangeLabel, typeLabel, typeClass, typeTag, RULE_GROUPS, openMenu, toast, emptyState } from "../core.js";
import { sparkline } from "../charts.js";
import { openRuleDrawer } from "../dialogs.js";

export const meta = { title: "Quy tắc" };

const PAGE = 8;
const view = { search: "", group: "", status: "", page: 1, open: new Set(), stats: [] };
let root = null;

function filtered() {
  const needle = view.search.toLowerCase();
  return state.rules
    .filter((r) => (!needle || `${r.id} ${r.description}`.toLowerCase().includes(needle))
      && (!view.group || r.group === view.group)
      && (!view.status || (view.status === "on" ? r.enabled : !r.enabled)))
    .sort((a, b) => b.score - a.score || a.id.localeCompare(b.id));
}

function categories() {
  const counts = RULE_GROUPS.map((g) => ({ group: g, count: state.rules.filter((r) => r.group === g).length })).filter((c) => c.count);
  const max = Math.max(...counts.map((c) => c.count), 1);
  const enabled = state.rules.filter((r) => r.enabled).length;
  return `<div class="panel-head"><h2 class="panel-title">Phân bố quy tắc theo danh mục</h2>`
    + `<span class="panel-meta">${num(state.rules.length)} quy tắc · ${num(enabled)} đang bật · ${num(state.rules.length - enabled)} đang tắt</span></div>`
    + `<div class="cat-bars">${counts.map((c) => `<div class="${typeClass(c.group)}"><div class="v"><span>${esc(typeLabel(c.group))}</span><b>${num(c.count)}</b></div>`
      + `<div class="track"><i style="width:${((c.count * 100) / max).toFixed(1)}%"></i></div></div>`).join("")}</div>`;
}

function rowsHtml(items) {
  const stats = Object.fromEntries(view.stats.map((s) => [s.rule_id, s]));
  return items.map((r) => {
    const s = stats[r.id] || { count: 0, trend: [] };
    const open = view.open.has(r.id);
    return `<div class="trow" style="min-height:70px;${open ? "background:var(--surface-alt)" : ""}" data-id="${esc(r.id)}">`
      + `<span class="cell" style="width:72px"><button class="switch ${r.enabled ? "on" : ""}" data-toggle title="Bật/tắt quy tắc"></button></span>`
      + `<span class="cell" style="width:278px;display:flex;align-items:center;gap:8px;cursor:pointer" data-expand>${icon(open ? "chevron-down" : "chevron-right", 12)}`
      + `<span style="display:flex;flex-direction:column;gap:3px;min-width:0"><span style="overflow:hidden;text-overflow:ellipsis">${esc(r.description)}</span>`
      + `<span class="mono muted" style="font-size:10px">ID ${esc(r.id)}</span></span></span>`
      + `<span class="cell" style="width:150px">${typeTag(r.group)}</span>`
      + `<span class="cell" style="width:70px;font-weight:600">${num(r.score)}</span>`
      + `<span class="cell mono" style="width:176px;font-size:11px">${esc((r.targets || []).join(", ").toUpperCase())}</span>`
      + `<span class="cell" style="width:230px;display:flex;align-items:center;gap:20px"><b style="font-weight:600;min-width:48px">${num(s.count)}</b>${sparkline(s.trend, { width: 104, height: 32 })}</span>`
      + `<span class="cell" style="width:30px"><button class="icon-btn" data-more title="Thêm">${icon("ellipsis")}</button></span></div>`
      + (open ? `<div data-id="${esc(r.id)}" style="display:flex;flex-direction:column;gap:10px;padding:18px 24px 18px 116px;background:var(--surface-alt);border-bottom:1px solid var(--border)">`
        + `<div class="toggle-row" style="font-size:10px"><span class="muted" style="text-transform:uppercase;font-weight:600">Biểu thức chính quy · RE2</span>`
        + `<button class="link" data-edit style="font-size:10px">Chỉnh sửa quy tắc →</button></div>`
        + `<div class="code" style="background:var(--surface);color:var(--accent-text)">${esc(r.pattern)}</div>`
        + `<span class="muted" style="font-size:10px">Chuẩn hóa: ${esc((r.normalizers || []).join(", ") || "không")} · cảnh báo khi tổng điểm của nguồn ≥ 70</span></div>` : "");
  }).join("");
}

function paint() {
  root.querySelector("#ru-cats").innerHTML = categories();
  const items = filtered();
  const pages = Math.max(1, Math.ceil(items.length / PAGE));
  view.page = Math.min(view.page, pages);
  const shown = items.slice((view.page - 1) * PAGE, view.page * PAGE);
  root.querySelector("#ru-rows").innerHTML = rowsHtml(shown)
    || emptyState({ title: "Không có quy tắc phù hợp", text: "Thử đổi từ khóa hoặc bộ lọc." });
  const nums = Array.from({ length: pages }, (_, i) => `<button class="num ${i + 1 === view.page ? "on" : ""}" data-page="${i + 1}">${i + 1}</button>`).join("");
  root.querySelector("#ru-foot").innerHTML = `<span>Hiển thị ${items.length ? (view.page - 1) * PAGE + 1 : 0}–${(view.page - 1) * PAGE + shown.length} trong ${num(items.length)} quy tắc</span>`
    + `<div class="pager"><button class="btn sm" data-page="${view.page - 1}" ${view.page <= 1 ? "disabled" : ""}>Trước</button>${nums}`
    + `<button class="btn sm" data-page="${view.page + 1}" ${view.page >= pages ? "disabled" : ""}>Sau</button></div>`;
}

async function update(rule, changes) {
  try {
    await apiJson(`/api/rules/${encodeURIComponent(rule.id)}`, { method: "PUT", body: JSON.stringify({ ...rule, ...changes }) });
    emit("rules-changed");
  } catch (err) {
    toast(`Không cập nhật được ${rule.id}: ${err.message}`);
  }
}

export function render(el) {
  root = el;
  el.innerHTML = `<div class="page-head"><div><h1 class="page-title">Quy tắc phát hiện</h1><p class="page-sub">Cấu hình quy tắc phát hiện, điểm rủi ro và các trường cần kiểm tra.</p></div>`
    + `<button class="btn primary" id="ru-add">${icon("plus")}Thêm quy tắc</button></div>`
    + `<div class="card panel" id="ru-cats" style="gap:20px"></div>`
    + `<div class="card"><div class="table-head compact"><label class="field" style="width:320px">${icon("search")}<input id="ru-search" placeholder="Tìm tên hoặc ID quy tắc…"></label>`
    + `<div class="filters"><label class="select">${icon("chevron-down")}<select id="ru-group"><option value="">Tất cả danh mục</option>`
    + RULE_GROUPS.map((g) => `<option value="${g}">${esc(typeLabel(g))}</option>`).join("") + `</select></label>`
    + `<label class="select">${icon("chevron-down")}<select id="ru-status"><option value="">Tất cả trạng thái</option><option value="on">Đang bật</option><option value="off">Đang tắt</option></select></label></div></div>`
    + `<div class="thead"><span class="cell" style="width:72px">Bật / Tắt</span><span class="cell" style="width:278px">Tên quy tắc / ID</span>`
    + `<span class="cell" style="width:150px">Danh mục</span><span class="cell" style="width:70px">Điểm ↓</span><span class="cell" style="width:176px">Trường kiểm tra</span>`
    + `<span class="cell" style="width:230px" id="ru-match-head">Lượt khớp</span><span class="cell" style="width:30px"></span></div>`
    + `<div id="ru-rows"></div><div class="table-foot" id="ru-foot"></div></div>`;

  el.querySelector("#ru-add").addEventListener("click", () => openRuleDrawer(null, () => emit("rules-changed")));
  el.querySelector("#ru-search").addEventListener("input", (e) => { view.search = e.target.value; view.page = 1; paint(); });
  el.querySelector("#ru-group").addEventListener("change", (e) => { view.group = e.target.value; view.page = 1; paint(); });
  el.querySelector("#ru-status").addEventListener("change", (e) => { view.status = e.target.value; view.page = 1; paint(); });
  el.addEventListener("click", (e) => {
    const page = e.target.closest("[data-page]");
    if (page && !page.disabled) { view.page = Number(page.dataset.page); paint(); return; }
    const row = e.target.closest("[data-id]");
    if (!row) return;
    const rule = state.rules.find((r) => r.id === row.dataset.id);
    if (!rule) return;
    if (e.target.closest("[data-toggle]")) update(rule, { enabled: !rule.enabled });
    else if (e.target.closest("[data-expand]")) { if (view.open.has(rule.id)) view.open.delete(rule.id); else view.open.add(rule.id); paint(); }
    else if (e.target.closest("[data-edit]")) openRuleDrawer(rule, () => emit("rules-changed"));
    const more = e.target.closest("[data-more]");
    if (more) openMenu(more, [
      { label: "Sửa quy tắc", onClick: () => openRuleDrawer(rule, () => emit("rules-changed")) },
      { label: "Xem sự kiện khớp", onClick: () => emit("navigate", { view: "history", params: { q: rule.id } }) },
      { label: "Xóa quy tắc", danger: true, onClick: async () => {
        if (!confirm(`Xóa quy tắc ${rule.id}?`)) return;
        try {
          await apiJson(`/api/rules/${encodeURIComponent(rule.id)}`, { method: "DELETE" });
          toast(`Đã xóa ${rule.id}`);
          emit("rules-changed");
        } catch (err) { toast(`Không xóa được: ${err.message}`); }
      } },
    ]);
  });
}

export async function load(el = root) {
  el.querySelector("#ru-match-head").textContent = `Lượt khớp ${rangeLabel().replace(" qua", "")}`;
  try {
    view.stats = (await apiJson(`/api/dashboard/stats/rules?hours=${state.hours}`)).rules || [];
  } catch (_) {
    view.stats = [];
  }
  paint();
}
