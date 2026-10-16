// Hộp thoại thêm/sửa tên miền (P1) và ngăn thêm/sửa quy tắc (rule-api), theo thiết kế.
import { state, esc, icon, api, apiJson, num, typeLabel, sevFromScore, sevLabel, RULE_GROUPS, debounce, toast } from "./core.js";

const DOMAIN_RE = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$/;
const TARGETS = ["url", "query", "body", "headers", "user_agent", "referer"];
const NORMALIZERS = ["url_decode", "html_decode", "sql_canonical"];

function mount(html, { drawer = false } = {}) {
  const host = document.getElementById("dialog-host");
  host.innerHTML = `<div class="overlay ${drawer ? "drawer-host" : ""}">${html}</div>`;
  const overlay = host.firstElementChild;
  const close = () => { host.innerHTML = ""; document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
  overlay.querySelectorAll("[data-dismiss]").forEach((b) => b.addEventListener("click", close));
  return { root: overlay, close };
}

function switchButton(id, on) {
  return `<button type="button" class="switch ${on ? "on" : ""}" id="${id}" role="switch" aria-checked="${on}"></button>`;
}

function bindSwitch(el) {
  el.addEventListener("click", () => {
    el.classList.toggle("on");
    el.setAttribute("aria-checked", el.classList.contains("on"));
  });
}

// ---------- Tên miền ----------
export function openDomainDialog(existing, onSaved) {
  const editing = Boolean(existing);
  const d = existing || { target: "", target_type: "domain", enabled: true, note: "" };
  const enabledRules = state.rules.filter((r) => r.enabled).length;
  const { root, close } = mount(`<form class="modal" id="dm-form" novalidate>
    <div class="dlg-head"><div><h2>${editing ? "Sửa tên miền" : "Thêm tên miền"}</h2><p>${editing ? "Cập nhật loại, ghi chú hoặc trạng thái giám sát." : "Thêm một đích mới để bắt đầu giám sát log."}</p></div>
      <button type="button" class="icon-btn" data-dismiss title="Đóng">${icon("x", 18)}</button></div>
    <div class="dlg-body">
      <div class="form-row"><label for="dm-target">Tên miền <span class="req">*</span></label>
        <div class="field mono">${icon("globe")}<input id="dm-target" value="${esc(d.target)}" placeholder="secure.shop.vn" ${editing ? "readonly" : ""} autocomplete="off"></div>
        <span class="hint">Nhập tên miền hoặc tên ứng dụng, không bao gồm http:// hoặc https://.</span></div>
      <div class="form-row"><label for="dm-note">Ghi chú</label>
        <div class="field mono">${icon("server")}<input id="dm-note" value="${esc(d.note || "")}" placeholder="upstream 10.0.1.24:8080" maxlength="200"></div>
        <span class="hint">Thông tin tham khảo, ví dụ máy chủ gốc hoặc người phụ trách.</span></div>
      <div class="form-row"><label for="dm-type">Loại</label>
        <label class="select block">${icon("chevron-down")}<select id="dm-type">
          <option value="domain" ${d.target_type === "domain" ? "selected" : ""}>Tên miền (Web/API)</option>
          <option value="app" ${d.target_type === "app" ? "selected" : ""}>Ứng dụng</option></select></label></div>
      <div class="toggle-box"><div><b>Bật giám sát</b><small>Log của đích này được phân tích bởi ${num(enabledRules)} quy tắc đang bật.</small></div>${switchButton("dm-enabled", d.enabled)}</div>
      <div class="form-msg" id="dm-msg" hidden></div>
    </div>
    <div class="dlg-foot"><button type="button" class="btn" data-dismiss>Hủy</button>
      <button type="submit" class="btn primary">${editing ? "Lưu thay đổi" : `${icon("plus")}Thêm tên miền`}</button></div></form>`);
  bindSwitch(root.querySelector("#dm-enabled"));
  root.querySelector("#dm-target").focus();
  root.querySelector("#dm-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = root.querySelector("#dm-msg");
    const target = root.querySelector("#dm-target").value.trim().replace(/^https?:\/\//i, "").replace(/\/.*$/, "").toLowerCase();
    if (!DOMAIN_RE.test(target)) {
      msg.textContent = "Tên miền không hợp lệ."; msg.hidden = false; return;
    }
    const body = { target, target_type: root.querySelector("#dm-type").value,
      enabled: root.querySelector("#dm-enabled").classList.contains("on"), note: root.querySelector("#dm-note").value.trim() || null };
    try {
      await apiJson("/api/scope/allowlist", { method: "POST", body: JSON.stringify(body) });
      close();
      toast(editing ? `Đã cập nhật ${target}` : `Đã thêm ${target}`);
      onSaved();
    } catch (err) {
      msg.textContent = `Không lưu được: ${err.message}`; msg.hidden = false;
    }
  });
}

// ---------- Quy tắc ----------
function checks(name, options, selected) {
  return options.map((o) => `<label class="check"><input type="checkbox" name="${name}" value="${o}" ${selected.includes(o) ? "checked" : ""}>${o}</label>`).join("");
}

function scoreTag(score) {
  const sev = sevFromScore(score);
  return `<span class="tag sev-${sev}" title="${esc(sevLabel(sev))}">${score} / 100</span>`;
}

export function openRuleDrawer(existing, onSaved) {
  const editing = Boolean(existing);
  const r = existing || { id: "", group: "sql_injection", description: "", enabled: true, targets: ["url", "query"],
    pattern: "", score: 85, normalizers: ["url_decode", "html_decode"] };
  const { root, close } = mount(`<form class="drawer" id="ru-form" novalidate>
    <div class="dlg-head"><div><h2>${editing ? "Sửa quy tắc" : "Thêm quy tắc phát hiện"}</h2><p>Mẫu tùy chỉnh · Bộ máy RE2</p></div>
      <button type="button" class="icon-btn" data-dismiss title="Đóng">${icon("x", 18)}</button></div>
    <div class="dlg-body">
      <div class="form-row"><label for="ru-name">Tên quy tắc <span class="req">*</span></label>
        <div class="field">${icon("shield-check")}<input id="ru-name" value="${esc(r.description)}" placeholder="Phát hiện SQLi tùy chỉnh" maxlength="200"></div></div>
      <div class="form-cols">
        <div class="form-row"><label for="ru-id">ID quy tắc <span class="req">*</span></label>
          <div class="field mono">${icon("hash")}<input id="ru-id" value="${esc(r.id)}" placeholder="SQLI_CUSTOM_1" ${editing ? "readonly" : ""} maxlength="80"></div></div>
        <div class="form-row"><label for="ru-group">Danh mục</label>
          <label class="select block">${icon("chevron-down")}<select id="ru-group">${RULE_GROUPS.map((g) => `<option value="${g}" ${g === r.group ? "selected" : ""}>${esc(typeLabel(g))}</option>`).join("")}</select></label></div>
      </div>
      <div class="form-row" style="gap:12px"><div class="toggle-row"><span class="label">Điểm rủi ro</span><span id="ru-score-tag">${scoreTag(r.score)}</span></div>
        <input type="range" class="slider" id="ru-score" min="1" max="100" value="${r.score}">
        <div class="ticks"><span>1 · Thấp</span><span>50</span><span>100 · Nghiêm trọng</span></div></div>
      <div class="form-row" style="gap:10px"><span class="label">Trường kiểm tra <span class="req">*</span></span><div class="checks">${checks("target", TARGETS, r.targets)}</div></div>
      <div class="form-row" style="gap:10px"><span class="label">Chuẩn hóa trước khi so khớp</span><div class="checks">${checks("norm", NORMALIZERS, r.normalizers)}</div></div>
      <div class="form-row" style="gap:8px"><div class="toggle-row"><span class="label">Biểu thức chính quy <span class="req">*</span></span><span class="hint">RE2 · Không hỗ trợ lookaround</span></div>
        <div class="editor"><span>1</span><textarea id="ru-pattern" spellcheck="false" placeholder="(?i)(union\\s+select|select\\s+.+\\s+from)">${esc(r.pattern)}</textarea></div>
        <div class="status-line" id="ru-valid"></div></div>
      <div class="form-row" style="gap:9px"><span class="label">Kiểm thử mẫu</span>
        <input class="sample" id="ru-sample" placeholder="/api/users?id=1 OR 1=1 --" value="/api/users?id=1 OR 1=1 --">
        <button type="button" class="btn" id="ru-test" style="align-self:flex-start">${icon("play")}Chạy kiểm thử</button>
        <div id="ru-test-result"></div></div>
      <div class="toggle-row"><div><b>Bật quy tắc sau khi lưu</b><small>Áp dụng cho mọi tên miền đang được giám sát.</small></div>${switchButton("ru-enabled", r.enabled)}</div>
      <div class="form-msg" id="ru-msg" hidden></div>
    </div>
    <div class="dlg-foot"><span>Thay đổi có hiệu lực ngay (detector nạp lại rule không cần khởi động lại).</span>
      <div style="display:flex;gap:8px"><button type="button" class="btn" data-dismiss>Hủy</button><button type="submit" class="btn primary">Lưu quy tắc</button></div></div></form>`, { drawer: true });

  bindSwitch(root.querySelector("#ru-enabled"));
  const form = () => ({
    id: root.querySelector("#ru-id").value.trim().toUpperCase(),
    group: root.querySelector("#ru-group").value,
    description: root.querySelector("#ru-name").value.trim(),
    enabled: root.querySelector("#ru-enabled").classList.contains("on"),
    targets: [...root.querySelectorAll('input[name="target"]:checked')].map((i) => i.value),
    pattern: root.querySelector("#ru-pattern").value.trim(),
    score: Number(root.querySelector("#ru-score").value),
    normalizers: [...root.querySelectorAll('input[name="norm"]:checked')].map((i) => i.value),
  });
  // Kiểm tra cú pháp và chạy thử chỉ quan tâm biểu thức: dùng ID tạm để ID đang gõ dở không báo lỗi ở đây (ID kiểm tra khi lưu).
  const preview = (rule) => ({ ...rule, id: "PREVIEW_RULE", description: rule.description || "preview" });
  const valid = root.querySelector("#ru-valid");
  const validate = debounce(async () => {
    const rule = form();
    if (!rule.pattern) { valid.className = "status-line"; valid.innerHTML = ""; return; }
    const response = await api("/api/rules/validate", { method: "POST", body: JSON.stringify(preview(rule)) });
    const body = await response.json().catch(() => ({}));
    valid.className = `status-line ${response.ok ? "ok" : "err"}`;
    valid.innerHTML = response.ok ? `${icon("circle-check")}Cú pháp RE2 hợp lệ` : `${icon("info")}${esc(body.error || `HTTP ${response.status}`)}`;
  }, 400);
  root.querySelector("#ru-pattern").addEventListener("input", validate);
  root.querySelectorAll('input[name="target"], input[name="norm"]').forEach((i) => i.addEventListener("change", validate));
  root.querySelector("#ru-score").addEventListener("input", (e) => { root.querySelector("#ru-score-tag").innerHTML = scoreTag(Number(e.target.value)); });
  root.querySelector("#ru-id").addEventListener("input", (e) => { e.target.value = e.target.value.toUpperCase(); });
  validate();

  root.querySelector("#ru-test").addEventListener("click", async () => {
    const out = root.querySelector("#ru-test-result");
    const rule = form();
    const response = await api("/api/rules/test", { method: "POST", body: JSON.stringify({
      rule: { ...preview(rule), enabled: true },
      sample: root.querySelector("#ru-sample").value }) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      out.innerHTML = `<div class="test-result err">${icon("info", 15)}<div><b>Không chạy được kiểm thử</b><span>${esc(body.error || `HTTP ${response.status}`)}</span></div></div>`;
    } else if (body.matched) {
      out.innerHTML = `<div class="test-result">${icon("circle-check", 15)}<div><b>Khớp mẫu · trường ${esc(body.field)}</b>`
        + `<span>${esc(body.value)} · vị trí ${body.start}–${body.end} · ${esc(String(body.elapsed_ms).replace(".", ","))} ms</span></div></div>`;
    } else {
      out.innerHTML = `<div class="test-result none">${icon("info", 15)}<div><b>Không khớp</b><span>${esc(String(body.elapsed_ms).replace(".", ","))} ms</span></div></div>`;
    }
  });

  root.querySelector("#ru-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = root.querySelector("#ru-msg");
    const rule = form();
    if (!rule.description || !rule.id || !rule.pattern || !rule.targets.length) {
      msg.textContent = "Cần nhập tên, ID, biểu thức và chọn ít nhất một trường kiểm tra."; msg.hidden = false; return;
    }
    try {
      await apiJson(editing ? `/api/rules/${encodeURIComponent(rule.id)}` : "/api/rules/", {
        method: editing ? "PUT" : "POST", body: JSON.stringify(rule) });
      close();
      toast(editing ? `Đã cập nhật ${rule.id}` : `Đã thêm ${rule.id}`);
      onSaved();
    } catch (err) {
      msg.textContent = `Không lưu được: ${err.message}`; msg.hidden = false;
    }
  });
}
