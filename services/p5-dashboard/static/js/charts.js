// Biểu đồ vẽ từ dữ liệu thật (SVG/HTML), theo kiểu dáng của thiết kế.
import { esc, num, typeClass, typeLabel, fmtShort } from "./core.js";

/** Trục y "đẹp": làm tròn đỉnh lên bậc 1/2/2.5/5 × 10^n. */
export function niceMax(value) {
  if (value <= 0) return 4;
  const exp = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((m) => m * exp >= value) * exp;
  return step;
}

export function sparkline(values, { width = 68, height = 32, color = "var(--text)" } = {}) {
  const data = values && values.length > 1 ? values : new Array(24).fill(0);
  const max = Math.max(...data, 1);
  if (!data.some((v) => v > 0)) color = "var(--border)"; // không có dữ liệu: đường phẳng mờ như thiết kế
  const stepX = data.length > 1 ? width / (data.length - 1) : width;
  const points = data.map((v, i) => `${(i * stepX).toFixed(1)},${(height - 2 - (v / max) * (height - 4)).toFixed(1)}`);
  return `<svg class="spark" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">`
    + `<polyline fill="none" stroke="${color}" stroke-width="1.2" stroke-linejoin="round" points="${points.join(" ")}"/></svg>`;
}

const SEV_ORDER = ["low", "medium", "high", "critical"]; // xếp chồng từ dưới lên
const SEV_COLOR = { critical: "var(--sev-critical)", high: "var(--sev-high)", medium: "var(--sev-medium)", low: "var(--sev-low)" };

/** Vùng xếp chồng theo mức độ. buckets = [{critical, high, medium, low}]. */
export function stackedArea(buckets, { height = 185 } = {}) {
  const width = 1000;
  const n = buckets.length;
  const totals = buckets.map((b) => SEV_ORDER.reduce((s, k) => s + (b[k] || 0), 0));
  const max = niceMax(Math.max(...totals, 0));
  const x = (i) => (n > 1 ? (i * width) / (n - 1) : width / 2);
  const y = (v) => height - (v / max) * height;
  let lower = new Array(n).fill(0);
  const paths = SEV_ORDER.map((sev) => {
    const upper = lower.map((base, i) => base + (buckets[i][sev] || 0));
    const top = upper.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    const bottom = lower.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).reverse();
    lower = upper;
    return `<polygon points="${top.join(" ")} ${bottom.join(" ")}" fill="${SEV_COLOR[sev]}" opacity="0.9"/>`;
  });
  const grid = [0, 1, 2, 3, 4].map((i) => `<line x1="0" x2="${width}" y1="${(i * height) / 4}" y2="${(i * height) / 4}" stroke="var(--border)" vector-effect="non-scaling-stroke"/>`);
  const svg = `<svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">${grid.join("")}${paths.join("")}</svg>`;
  const peakIndex = totals.indexOf(Math.max(...totals));
  return { svg, max, peakIndex, peak: totals[peakIndex] || 0, xPct: (i) => (n > 1 ? (i * 100) / (n - 1) : 50) };
}

/** Vòng tròn phân loại. items = [{attack_type, count}] (đã sắp xếp giảm dần). */
export function donut(items, { size = 162, thickness = 18 } = {}) {
  const total = items.reduce((s, it) => s + it.count, 0);
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  const gap = items.length > 1 ? 2 : 0;
  let offset = 0;
  const arcs = items.map((it) => {
    const length = total ? (it.count / total) * c : 0;
    const dash = Math.max(length - gap, 0);
    const arc = `<circle class="${typeClass(it.attack_type)}" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--c)" stroke-width="${thickness}" stroke-dasharray="${dash} ${c - dash}" stroke-dashoffset="${-offset}" transform="rotate(-90 ${size / 2} ${size / 2})"/>`;
    offset += length;
    return arc;
  });
  const ring = total ? "" : `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--surface-soft)" stroke-width="${thickness}"/>`;
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">${ring}${arcs.join("")}</svg>`;
}

export function categoryLegend(items) {
  const total = items.reduce((s, it) => s + it.count, 0) || 1;
  return items.map((it) => `<div class="${typeClass(it.attack_type)}"><span class="dot"></span><span>${esc(typeLabel(it.attack_type))}</span>`
    + `<span>${num(it.count)}　${Math.round((it.count * 100) / total)}%</span></div>`).join("");
}

/** Danh sách xếp hạng: dòng chữ đơn cách + thanh tỉ lệ. items = [{label, count, cls}]. */
export function ranking(items) {
  const max = Math.max(...items.map((it) => it.count), 1);
  return items.map((it) => `<div class="rank-line"><span>${esc(it.label)}</span><span>${num(it.count)}</span></div>`
    + `<div class="rank-bar ${it.cls || ""}" style="width:${Math.max((it.count * 100) / max, 1).toFixed(1)}%"></div>`).join("");
}

/** Biểu đồ cột (HTML), cột cao nhất tô đỏ. Trả {html, max}. */
export function columns(values, { height = 104, highlightPeak = true, titles } = {}) {
  const max = niceMax(Math.max(...values, 0));
  const peak = Math.max(...values, 0);
  const peakIndex = peak > 0 && highlightPeak ? values.lastIndexOf(peak) : -1;
  const bars = values.map((v, i) => `<i class="${i === peakIndex ? "peak" : ""}" style="height:${((v / max) * 100).toFixed(1)}%" title="${esc(titles ? titles[i] : num(v))}"></i>`);
  return { html: `<div class="bars" style="height:${height}px">${bars.join("")}</div>`, max };
}

export function yAxis(max, height, steps = 2) {
  const labels = [];
  for (let i = steps; i >= 0; i -= 1) labels.push(`<span>${num(Math.round((max * i) / steps))}</span>`);
  return `<div class="y-axis" style="height:${height}px">${labels.join("")}</div>`;
}

/** Mật độ 7 × 24. days = [{date, weekday, hours[24]}]. */
const WEEKDAY = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "CN"];
export function heatmap(days) {
  const max = Math.max(...days.flatMap((d) => d.hours), 0);
  const level = (v) => (v <= 0 || !max ? 0 : Math.min(4, Math.ceil((v / max) * 4)));
  const cells = days.flatMap((d) => d.hours.map((v, h) => `<i class="l${level(v)}" title="${esc(d.date)} ${String(h).padStart(2, "0")}:00 · ${num(v)} lượt"></i>`));
  const labels = days.map((d) => `<span>${WEEKDAY[d.weekday]}</span>`);
  const hours = ["00", "03", "06", "09", "12", "15", "18", "21"].map((h) => `<span>${h}:00</span>`);
  return {
    max,
    html: `<div class="heat-hours"><span></span>${hours.join("")}</div>`
      + `<div class="heat"><div class="heat-days">${labels.join("")}</div><div class="heat-grid">${cells.join("")}</div></div>`,
  };
}

/** Nhãn trục thời gian: `count` mốc cách đều giữa start và end. */
export function timeTicks(start, end, count = 5, format = fmtShort) {
  const s = new Date(start).getTime();
  const e = new Date(end).getTime();
  return Array.from({ length: count }, (_, i) => `<span>${esc(format(new Date(s + ((e - s) * i) / (count - 1))))}</span>`).join("");
}
