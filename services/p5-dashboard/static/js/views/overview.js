// Tổng quan bảo mật: chỉ số, tấn công theo thời gian, phân loại, xếp hạng, mật độ theo giờ, IP nổi bật, sự kiện gần đây.
import { state, emit, esc, icon, apiJson, num, pct, rangeLabel, fmtShort, fmtHM, fmtDM, fmtDate, fmtEventTime,
  typeTag, typeText, typeClass, sevTag, emptyState } from "../core.js";
import { sparkline, stackedArea, donut, categoryLegend, ranking, heatmap } from "../charts.js";

export const meta = { title: "Tổng quan" };

function kpiCard({ label, iconName, value, trend, cmp }) {
  return `<div class="card kpi"><div class="kpi-label"><span>${esc(label)}</span>${icon(iconName, 15)}</div>`
    + `<div class="kpi-value"><b>${value}</b>${sparkline(trend)}</div><div class="kpi-cmp">${cmp}</div></div>`;
}

function changeCmp(change, worseWhenUp = true) {
  const text = pct(change);
  if (text === null) return `<span>chưa có kỳ trước để so sánh</span>`;
  const up = change > 0;
  const cls = up === worseWhenUp ? "up-bad" : "down-good";
  return `<strong class="${change === 0 ? "" : cls}">${text}</strong><span>so với ${esc(rangeLabel().replace(" qua", ""))} trước</span>`;
}

function skeleton() {
  const card = `<div class="card kpi"><div class="skel" style="height:12px;width:60%"></div><div class="skel" style="height:26px;width:40%"></div><div class="skel" style="height:8px"></div></div>`;
  const bars = Array.from({ length: 18 }, (_, i) => `<div class="skel" style="flex:1;height:${40 + ((i * 37) % 60)}%"></div>`).join("");
  return `<div class="grid-kpi">${card.repeat(5)}</div><div class="card panel"><div class="skel" style="height:12px;width:20%"></div>`
    + `<div style="display:flex;gap:10px;align-items:flex-end;height:150px">${bars}</div>`
    + `<div class="muted" style="display:flex;gap:8px;align-items:center;justify-content:center;font-size:10px"><span class="spinner"></span>Đang tải dữ liệu bảo mật… Vui lòng đợi trong giây lát.</div></div>`;
}

function timelinePanel(data) {
  const buckets = data.timeline || [];
  const total = data.kpi.attacks.value;
  const chart = stackedArea(buckets);
  const start = new Date(data.window.start).getTime();
  const step = data.window.timeline_minutes * 60000;
  let peak = "";
  if (chart.peak > 0) {
    const x = chart.xPct(chart.peakIndex);
    const side = x > 75 ? `right:${(100 - x + 1).toFixed(1)}%` : `left:${(x + 1).toFixed(1)}%`;
    peak = `<div class="peak-line" style="left:${x.toFixed(2)}%"></div><div class="peak-note" style="${side}">Đỉnh · ${num(chart.peak)} lượt`
      + `<small>${esc(fmtShort(new Date(start + chart.peakIndex * step)))}</small></div>`;
  }
  const ticks = Array.from({ length: 9 }, (_, i) => {
    const t = new Date(start + ((buckets.length * step) * i) / 8);
    return `<span>${i === 0 || fmtHM(t) === "00:00" ? `${fmtDM(t)} ${fmtHM(t)}` : fmtHM(t)}</span>`;
  }).join("");
  const yLabels = [4, 3, 2, 1, 0].map((i) => `<span>${num((chart.max * i) / 4)}</span>`).join("");
  return `<div class="card panel"><div class="panel-head"><div><h2 class="panel-title">Tấn công theo thời gian</h2>`
    + `<p class="panel-sub">${num(total)} lượt tấn công · tổng hợp mỗi ${data.window.timeline_minutes} phút</p></div>`
    + `<span class="panel-meta">${esc(rangeLabel())}</span></div>`
    + `<div class="legend"><span class="sev-critical">Nghiêm trọng</span><span class="sev-high">Cao</span><span class="sev-medium">Trung bình</span><span class="sev-low">Thấp</span></div>`
    + `<div class="chart-row"><div class="y-axis" style="height:185px">${yLabels}</div><div class="chart-area"><div style="position:relative">${chart.svg}${peak}</div>`
    + `<div class="x-axis">${ticks}</div></div></div></div>`;
}

function distributionRow(data) {
  const types = data.types || [];
  const totalTypes = types.reduce((s, t) => s + t.count, 0);
  const shown = types.slice(0, 6);
  const typesPanel = `<div class="card panel"><div class="panel-head"><h2 class="panel-title">Loại tấn công</h2><span class="panel-meta">${types.length} loại</span></div>`
    + (totalTypes ? `<div class="donut-wrap">${donut(shown)}<div class="donut-total"><b>${num(totalTypes)}</b><small>lượt tấn công</small></div></div>`
      + `<div class="cat-legend">${categoryLegend(shown)}</div>` : `<p class="muted">Chưa có tấn công trong ${esc(rangeLabel())}.</p>`)
    + `</div>`;
  const domains = data.top_domains || [];
  const domainsPanel = `<div class="card panel"><div class="panel-head"><h2 class="panel-title">Tên miền bị tấn công nhiều nhất</h2><span class="panel-meta">Lượt tấn công</span></div>`
    + (domains.length ? `<div class="rank">${ranking(domains.map((d) => ({ label: d.domain, count: d.count })))}</div>` : `<p class="muted">Chưa có dữ liệu.</p>`)
    + `<p class="panel-note">Tổng cộng ${state.allowlist.length} tên miền · ${esc(rangeLabel())}</p></div>`;
  const rules = data.top_rules || [];
  const rulesPanel = `<div class="card panel"><div class="panel-head"><h2 class="panel-title">Quy tắc được kích hoạt nhiều nhất</h2><span class="panel-meta">Lượt khớp</span></div>`
    + (rules.length ? `<div class="rank">${ranking(rules.map((r) => ({ label: r.rule_id, count: r.count, cls: typeClass(r.attack_type) })))}</div>` : `<p class="muted">Chưa có dữ liệu.</p>`)
    + `<p class="panel-note">Top ${rules.length} trong ${state.rules.length} quy tắc</p></div>`;
  return `<div class="row">${typesPanel}${domainsPanel}${rulesPanel}</div>`;
}

function heatPanel(days) {
  const h = heatmap(days);
  const first = days[0] ? fmtDM(days[0].date + "T12:00:00") : "";
  const last = days.length ? fmtDate(days[days.length - 1].date + "T12:00:00") : "";
  const legend = [0, 1, 2, 3, 4].map((l) => `<i style="background:var(--heat-${l})"></i>`).join("");
  return `<div class="card panel"><div class="panel-head"><div><h2 class="panel-title">Mật độ tấn công theo giờ</h2>`
    + `<p class="panel-sub">7 ngày gần nhất · ${esc(first)}–${esc(last)} · UTC+7</p></div><span class="panel-meta">Lượt tấn công / giờ</span></div>`
    + h.html + `<div class="heat-legend"><span>Ít</span>${legend}<span>Nhiều · ${num(h.max)}</span></div></div>`;
}

function topIpsPanel(data) {
  const total = data.kpi.attacks.value || 1;
  const rows = (data.top_ips || []).map((ip) => `<div class="trow"><span class="cell mono" style="width:230px">${esc(ip.ip)}</span>`
    + `<span class="cell" style="width:150px">${Math.round((ip.count * 100) / total)}%</span>`
    + `<span class="cell" style="width:180px;font-weight:600">${num(ip.count)}</span>`
    + `<span class="cell" style="width:190px">${typeTag(ip.main_type)}</span>`
    + `<span class="cell" style="width:176px">${sparkline(ip.trend, { width: 176, height: 32 })}</span>`
    + `<span class="cell"><button class="link" data-investigate="${esc(ip.ip)}">Điều tra →</button></span></div>`).join("");
  return `<div class="card"><div class="table-head"><h2 class="panel-title">IP nguồn có nhiều tấn công nhất</h2><button class="link" data-go="history">Xem tất cả IP →</button></div>`
    + `<div class="thead"><span class="cell" style="width:230px">IP nguồn</span><span class="cell" style="width:150px">Tỉ trọng</span>`
    + `<span class="cell" style="width:180px">Lượt tấn công ↓</span><span class="cell" style="width:190px">Loại phổ biến</span>`
    + `<span class="cell" style="width:176px">Xu hướng</span><span class="cell">Hành động</span></div>`
    + (rows || `<div class="trow"><span class="cell muted">Chưa có IP nào bị phát hiện trong ${esc(rangeLabel())}.</span></div>`) + `</div>`;
}

export function eventRows(attacks, { clickable = false } = {}) {
  return attacks.map((a, i) => `<div class="trow tall ${clickable ? "clickable" : ""}" data-index="${i}">`
    + `<span class="cell mono muted" style="width:78px">${esc(fmtEventTime(a.detected_at))}</span>`
    + `<span class="cell" style="width:148px"><span class="mono" style="font-size:11px">${esc(a.src_ip)}</span><span class="sub">${esc(a.method || "")}</span></span>`
    + `<span class="cell grow">${esc(a.domain)}<span class="sub mono">${esc(a.url || "")}</span></span>`
    + `<span class="cell" style="width:142px">${typeText(a.attack_type)}</span>`
    + `<span class="cell" style="width:130px">${sevTag(a.severity)}</span>`
    + `<span class="cell mono" style="width:150px;font-size:11px" title="${esc(a.rule_name || "")}">${esc(a.rule_id || "–")}</span>`
    + `<span class="cell mono" style="width:48px">${esc(a.status ?? "–")}</span></div>`).join("");
}

export const EVENT_HEAD = `<div class="thead"><span class="cell" style="width:78px">Thời gian ↓</span><span class="cell" style="width:148px">IP nguồn</span>`
  + `<span class="cell grow">Tên miền / Đường dẫn</span><span class="cell" style="width:142px">Loại tấn công</span>`
  + `<span class="cell" style="width:130px">Mức độ</span><span class="cell" style="width:150px">Quy tắc</span><span class="cell" style="width:48px">HTTP</span></div>`;

function recentPanel(attacks) {
  return `<div class="card"><div class="table-head"><div><h2 class="panel-title">Sự kiện gần đây</h2><p class="panel-sub">Các yêu cầu bị phát hiện mới nhất trên toàn hệ thống</p></div>`
    + `<button class="link" data-go="history">Xem lịch sử →</button></div>${EVENT_HEAD}`
    + (eventRows(attacks) || `<div class="trow"><span class="cell muted">Chưa có sự kiện.</span></div>`) + `</div>`;
}

export function render(el) {
  el.innerHTML = `<div class="page-head"><div><h1 class="page-title">Tổng quan bảo mật</h1><p class="page-sub">Theo dõi lưu lượng tấn công và hiệu quả phát hiện trên toàn hệ thống.</p></div>`
    + `<button class="btn" id="ov-export">${icon("download")}Xuất báo cáo</button></div><div id="ov-body">${skeleton()}</div>`;
  el.addEventListener("click", (e) => {
    const go = e.target.closest("[data-go]");
    if (go) emit("navigate", { view: go.dataset.go });
    const ip = e.target.closest("[data-investigate]");
    if (ip) emit("navigate", { view: "history", params: { q: ip.dataset.investigate } });
  });
  el.querySelector("#ov-export").addEventListener("click", () => window.print());
}

export async function load(el) {
  const body = el.querySelector("#ov-body");
  let data;
  let recent = [];
  try {
    [data, recent] = await Promise.all([
      apiJson(`/api/dashboard/overview?hours=${state.hours}`),
      apiJson("/api/dashboard/attacks?limit=3").then((r) => r.attacks || []).catch(() => []),
    ]);
  } catch (err) {
    body.innerHTML = `<div class="card">${emptyState({ title: "Không tải được dữ liệu", text: err.message })}</div>`;
    return;
  }
  if (!data.persisted) {
    body.innerHTML = `<div class="card">${emptyState({ title: "Chưa có dữ liệu lịch sử", text: "Dashboard chưa kết nối được Postgres nên chưa thống kê được. Sự kiện realtime vẫn xem được ở mục Thời gian thực." })}</div>`;
    return;
  }
  const k = data.kpi;
  const enabledDomains = state.allowlist.filter((d) => d.enabled).length;
  const enabledRules = state.rules.filter((r) => r.enabled).length;
  body.innerHTML = `<div style="display:flex;flex-direction:column;gap:24px">`
    + `<div class="grid-kpi">`
    + kpiCard({ label: "Lượt tấn công", iconName: "shield-alert", value: num(k.attacks.value), trend: k.attacks.trend, cmp: changeCmp(k.attacks.change_pct) })
    + kpiCard({ label: "Cảnh báo", iconName: "bell-ring", value: num(k.alerts.value), trend: k.alerts.trend, cmp: changeCmp(k.alerts.change_pct) })
    + kpiCard({ label: "Tên miền", iconName: "globe", value: num(state.allowlist.length), trend: k.domains_attacked.trend,
      cmp: `<strong class="up-good">${enabledDomains}/${state.allowlist.length}</strong><span>đang được giám sát</span>` })
    + kpiCard({ label: "Quy tắc", iconName: "list-filter", value: num(state.rules.length), trend: k.rules_matched.trend,
      cmp: `<strong class="up-good">${enabledRules}</strong><span>quy tắc đang bật</span>` })
    + kpiCard({ label: "IP duy nhất", iconName: "scan-line", value: num(k.unique_ips.value), trend: k.unique_ips.trend, cmp: changeCmp(k.unique_ips.change_pct) })
    + `</div>${timelinePanel(data)}${distributionRow(data)}${heatPanel(data.heatmap || [])}${topIpsPanel(data)}${recentPanel(recent)}</div>`;
}
