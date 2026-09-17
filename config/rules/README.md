# Bộ rule phát hiện (D1 Rule Engine)

Rule do nhóm **tự viết** (không dùng lại OWASP CRS). Engine D1 đọc mọi file `*.yaml` ở đây,
compile regex và khớp trên các trường của request. D1 **hot-reload** khi file thay đổi (~5s),
nên có thể thêm/sửa/xoá rule để phát hiện **realtime mà không restart**.

## Định dạng rule

```yaml
- id: sqli-boolean-or            # duy nhất, ^[a-z0-9][a-z0-9-]{0,63}$
  name: SQL Injection tautology  # tên hiển thị
  attack_type: sql_injection     # xem danh mục bên dưới
  severity: high                 # low | medium | high | critical
  target: any                    # trường để khớp (xem bên dưới)
  pattern: "(?i)['\"\\s](or|and)\\s+..."   # regex Python
  description: ...
```

File có thể là **danh sách** nhiều rule hoặc **một mapping** (một rule). Rule sai cú pháp bị bỏ qua (log cảnh báo), không làm chết engine.

**`target`** — trường lấy từ `log.enriched`:
`path`, `query_string` (thô), `path_decoded`, `query_decoded` (đã giải mã %), `user_agent`, `referer`,
`url_decoded` (path+query đã giải mã), hoặc `any` (khớp trên path_decoded + query_decoded + user_agent + referer).

## Danh mục `attack_type` & ánh xạ OWASP Top 10

| attack_type | OWASP | Rule mẫu |
|---|---|---|
| `sql_injection` | A03 Injection | sqli-boolean-or, sqli-union-select, sqli-time-based, sqli-quote-comment |
| `xss` | A03 Injection | xss-script-tag, xss-event-handler, xss-js-uri, xss-html-injection |
| `path_traversal` | A01 Broken Access Control | pt-dotdot-slash, pt-encoded-traversal |
| `command_injection` | A03 Injection | ci-shell-metachar-cmd, ci-sensitive-cmd |
| `lfi` | A03 / A01 | lfi-system-files, lfi-php-wrappers |
| `rfi` | A03 / A10 SSRF-liên quan | rfi-remote-include |
| `sensitive_file` | A05 Security Misconfiguration | sf-dotfiles, sf-backup-config |
| `log4shell` | A06 Vulnerable Components | log4shell-jndi (CVE-2021-44228) |
| `ssti` | A03 Injection | ssti-expression |
| `scanner` | Recon / dò quét | scanner-tool-ua |

> 3 nhóm đầu (SQLi, XSS, path traversal) là **yêu cầu bắt buộc** của đề tài; phần còn lại mở rộng
> theo OWASP trong phạm vi **phát hiện được từ access log** (URL/query/User-Agent/Referer).
> Các mục OWASP cần trạng thái phiên/thân request (CSRF, auth, deserialization…) không suy được từ
> access log nên không đưa vào rule tĩnh — D3 bù bằng chấm điểm bất thường theo thống kê.

## Thêm/sửa rule lúc đang chạy (3 cách)

**Cách 1 — Giao diện quản trị (dễ nhất):** mở **http://localhost:8080/rules**
(có link "⚙ Quản trị Rule" ở dashboard). Xem danh sách, thêm/sửa/xoá rule, nút "Chỉ kiểm tra"
để validate trước. Trang gọi API D2 bên dưới; D1 nạp lại trong ~5s.

**Cách 2 — API D2 (cho tự động hoá, có validate + chặn ReDoS):**
```bash
TOKEN=$(curl -s -X POST localhost:8080/api/auth/token \
  -H 'content-type: application/json' -d '{"client_id":"soc","client_secret":"soc"}' | jq -r .access_token)
curl -s -H "Authorization: Bearer $TOKEN" -X POST -H 'content-type: application/json' \
  -d '{"id":"custom-admin-probe","name":"Do actuator","attack_type":"sensitive_file",
       "severity":"high","target":"path_decoded","pattern":"(?i)/(actuator|server-status)\\b"}' \
  localhost:8080/api/rules/
# D1 tự nạp trong ~5s -> request /actuator/env sẽ bị phát hiện, KHÔNG restart.
curl -s localhost:8080/api/rules/ -H "Authorization: Bearer $TOKEN"   # liệt kê
curl -s -X DELETE localhost:8080/api/rules/custom-admin-probe -H "Authorization: Bearer $TOKEN"
```
D2 từ chối rule sai schema hoặc regex nguy hiểm (nested quantifier, quá dài, hoặc match quá lâu).

**Cách 3 — thêm file YAML** vào thư mục này rồi lưu; D1 phát hiện thay đổi và nạp lại.

## An toàn regex

Tránh nested quantifier kiểu `(a+)+`, `(.*)*` (ReDoS). Ưu tiên lớp ký tự có giới hạn độ dài
(`[^}]{1,100}`) thay vì `.*`. D2 kiểm tra tự động khi tạo qua API.
