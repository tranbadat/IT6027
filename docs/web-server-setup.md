# Cấu hình web server để C1 thu thập log

C1 đọc được combined log format mặc định của Nginx và Apache mà không cần sửa gì. Để có thêm **thời gian xử lý**
và **gom phiên theo cookie**, thêm hai trường mở rộng ở cuối dòng: `rt=` và `ck="…"`. Không có hai trường này pipeline
vẫn chạy, chỉ là `response_time_ms = null` và phiên gom theo IP.

## Nginx

```nginx
log_format waf '$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent '
               '"$http_referer" "$http_user_agent" rt=$request_time ck="$cookie_PHPSESSID"';

server {
    server_name shop.example.com;
    access_log /var/log/nginx/shop-access.log waf;
}
```

- `$cookie_PHPSESSID` chỉ log **đúng cookie phiên** (đổi tên theo ứng dụng: `sessionid`, `JSESSIONID`, `connect.sid`…). Đừng log cả `$http_cookie`: file log sẽ chứa mọi token và dữ liệu nhạy cảm của người dùng.
- Cookie rỗng được Nginx ghi là `-`, C2 hiểu là không có cookie.
- Mỗi domain/`server` một file log riêng để C1 gắn đúng `domain` (một nguồn ↔ một domain).

### Qua syslog (web server và collector ở máy khác)

```nginx
access_log syslog:server=collector.internal:5514,tag=shop_example,severity=info waf;
```

Trong `c1.yaml` ánh xạ `tag` → domain qua `tag_domains` (xem `config/c1.example.yaml`).

Nginx chỉ gửi access log qua syslog bằng **UDP** (hoặc unix socket), nên có thể mất log khi mạng nghẽn hoặc C1 quá tải (C1 đếm số datagram bị bỏ). Nếu cần đáng tin cậy hơn, cho rsyslog/syslog-ng đọc file log trên máy web rồi chuyển tiếp tới C1 bằng **TCP** (`protocol: tcp`, framing theo dòng; chưa hỗ trợ octet-counting của RFC 6587).

## Apache

```apache
LogFormat "%h %l %u %t \"%r\" %>s %b \"%{Referer}i\" \"%{User-Agent}i\" rt_us=%D ck=\"%{PHPSESSID}C\"" waf
CustomLog /var/log/apache2/shop-access.log waf
```

- `%D` là micro giây nên dùng khoá `rt_us=` (Nginx `$request_time` là giây nên dùng `rt=`).
- Định dạng `vhost_combined` (`%v:%p %h …`) cũng được C2 nhận ra; giá trị vhost vào trường `vhost`.

## Log rotation

C1 xử lý được cả hai kiểu, không mất dòng:

| Kiểu | Cách C1 xử lý | Khuyến nghị |
| --- | --- | --- |
| Đổi tên file rồi tạo file mới (mặc định của `logrotate`, kèm `nginx -s reopen` / `postrotate` gửi USR1) | Đọc nốt file cũ tới EOF rồi mở file mới từ đầu | **Nên dùng** |
| `copytruncate` | Phát hiện file bị cắt ngắn, đọc lại từ đầu | Tránh nếu có thể: nếu file bị cắt rồi ghi lại vượt quá offset cũ trong chưa tới một chu kỳ poll (0,25s) thì không phát hiện được |

Nếu C1 đang **tắt** đúng lúc file rotate, các dòng chỉ nằm trong file đã rotate (`access.log.1`) sẽ không được đọc. Để chắc chắn, đừng để C1 tắt lúc rotate (ví dụ chạy rotate ngoài giờ bảo trì).

## Quyền truy cập file

Tiến trình C1 cần **quyền đọc** file log. Trong Docker, mount volume ở chế độ read-only (`:ro`). Image Nginx chính thức để `access.log` là symlink tới stdout, vì vậy hãy dùng tên file khác (như `waf-access.log` trong `dev/nginx/default.conf`).

## Kiểm tra nhanh log format

```bash
tail -n 1 /var/log/nginx/shop-access.log
python3 - <<'PY'
from wafcollect.c2_parsing.parser import parse_line
import sys, json
line = open("/var/log/nginx/shop-access.log").read().splitlines()[-1]
r = parse_line(line)
print(r.status, r.warnings); print(json.dumps(r.data, indent=2, ensure_ascii=False))
PY
```
