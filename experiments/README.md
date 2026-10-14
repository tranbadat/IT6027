# Thực nghiệm

Thư mục này chứa công cụ và kết quả đánh giá định lượng hệ thống, phục vụ câu hỏi nghiên cứu đã đặt ra ở tuần 3 và 4: một lớp tín hiệu hành vi đơn giản, không cần huấn luyện có mang lại cải thiện đo được so với chỉ dùng rule hay không, và hệ thống đứng ở đâu so với một WAF dựa trên chữ ký phổ biến.

## Dữ liệu

Lưu lượng có nhãn do [`traffic.py`](traffic.py) sinh ra (chỉ thư viện chuẩn, tái lập được theo seed) và gửi thật tới Nginx mục tiêu, nên log đi qua đúng pipeline C1 → C2 → C3 → detector như khi vận hành. Domain thí nghiệm `lab.local` có vhost riêng tin header `X-Forwarded-For` từ dải mạng nội bộ, để giả lập nhiều IP nguồn trên một máy. Mỗi request mang cookie `sessionid` duy nhất; Nginx ghi giá trị này, C3 đưa thành `session_id` và detector giữ nguyên trong mọi kết quả, nhờ đó ghép kết quả phát hiện với nhãn.

| Loại nguồn | Số nguồn | Hành vi | Nhãn |
|---|---|---|---|
| Người dùng bình thường | 80 | 2–7 lần tải trang (HTML + CSS/JS/ảnh/favicon), nghỉ 3–15 giây; có truy vấn dễ gây nhầm (`O'Brien`, `union station`, `select a plan`, `redirect_url=`, User-Agent Android) và link hỏng 404 | benign |
| Ứng dụng gọi API liên tục | 3 | 40–70 request, mỗi 1,3–2 giây (~30–45 request/phút) | benign |
| Bot tìm kiếm | 2 | 45 trang khác nhau, ~1 trang/giây, thỉnh thoảng 404 | benign |
| Kẻ tấn công payload | 30 | 6–18 request, 75% mang payload thuộc 2–4 trong 9 nhóm (SQLi, XSS, path traversal, command injection, LFI, RFI, Log4Shell, SSTI, file nhạy cảm), có biến thể làm rối; 25% duyệt bình thường | attack (payload) |
| Công cụ dò thư mục | 4 | 35–55 đường dẫn quản trị/backup trong ~20 giây, một nửa dùng User-Agent của công cụ, một nửa giả trình duyệt | attack (scan) |
| HTTP flood | 2 | 150–200 request/phút vào trang chủ hoặc một API, không payload, không lỗi | attack (flood) |

Mỗi lượt khoảng 1.970 request trong ~185 giây.

## Cấu hình so sánh

[`Evaluate.java`](../services/detector/src/main/java/com/example/waf/detector/eval/Evaluate.java) phát lại các envelope `log.enriched` của lượt chạy qua chính `RecordProcessor` của detector. Cửa sổ trượt tính theo thời gian của request, nên kết quả phát lại tái lập được và khớp với detector đang chạy (cấu hình C, cột `live`).

| Cấu hình | Rule | Tín hiệu hành vi (request burst, flood, lỗi, số path khác nhau) |
|---|---|---|
| A | có | tắt |
| B | tắt | có |
| C | có | có (mặc định khi vận hành) |
| CRS | ModSecurity v3 + OWASP CRS 4.25 LTS, paranoia 1, ngưỡng bất thường 5, chế độ DetectionOnly | (không áp dụng) |

CRS nhận đúng tập request đó (cùng seed và `request_id`, [`modsec/docker-compose.yml`](modsec/docker-compose.yml)); một request được coi là bị phát hiện khi rule 949110 khớp (tổng điểm bất thường ≥ 5). Khác biệt cần lưu ý: CRS thấy toàn bộ request (mọi header, body), còn hệ thống chỉ thấy các trường có trong access log.

## Chỉ số

- **Theo request (payload):** request mang payload có bị phát hiện (`attack.detected`, hoặc CRS đánh dấu) không. Precision, recall, FPR tính trên request bình thường. Không áp dụng cho cấu hình B (không có rule).
- **Theo nguồn (IP):** nguồn độc hại có bị cảnh báo (`alert.triggered`) không, nguồn hợp lệ có bị cảnh báo nhầm không, và độ trễ trung vị từ request độc hại đầu tiên tới cảnh báo đầu tiên (theo thời gian kịch bản). Với CRS, một nguồn được tính là bị phát hiện khi có ít nhất một request bị đánh dấu.
- Request bị Nginx từ chối (400) trước khi chọn vhost được ghi vào log của vhost mặc định, không mang IP và cookie thí nghiệm, nên không ghép được với nhãn. Các request này được loại khỏi đánh giá theo request cho mọi hệ thống (kể cả CRS) và được báo số lượng riêng.

## Quy trình

1. **Lượt 1, tập phát triển (seed 42), [`results/run1-dev`](results/run1-dev/summary.md):** dùng để tìm lỗi của hệ thống và của chính quy trình đo.
2. **Sửa lỗi thiết kế tìm được ở lượt 1:** flood từ một nguồn không bao giờ đạt ngưỡng cảnh báo (chỉ có tín hiệu `request_burst` +30). Bổ sung tín hiệu `request_flood` (≥ 100 request/60 giây, +40). Không chỉnh rule theo các payload bị bỏ sót ở lượt 1, để tránh làm đẹp kết quả trên chính bộ payload dùng để đánh giá.
3. **Lượt 2, tập đánh giá (seed 1337), [`results/summary.md`](results/summary.md):** dữ liệu mới, kết quả chính thức.

## Kết quả (lượt 2)

1.796 request, 99,8% có mặt trong `log.enriched`; 3 request path traversal bị Nginx trả 400 trước khi chọn vhost được loại khỏi đánh giá theo request. Tập đánh giá theo request gồm 270 request mang payload và 1.051 request bình thường. Kết quả phát lại offline của cấu hình C trùng hoàn toàn với detector đang chạy (cột `live` trong [`results/summary.md`](results/summary.md)), nên các số dưới đây cũng là số của hệ thống khi vận hành.

### Mức request

| Hệ thống | TP | FP | FN | Precision | Recall | FPR | F1 |
|---|---|---|---|---|---|---|---|
| Hệ thống (A, C) | 255 | 4 | 15 | 0,985 | 0,944 | 0,004 | 0,964 |
| ModSecurity + CRS | 209 | 4 | 61 | 0,981 | 0,774 | 0,004 | 0,865 |

A và C trùng nhau ở mức request vì tín hiệu hành vi chỉ cộng điểm cho nguồn, không sinh `attack.detected`.

| Nhóm (số request) | Hệ thống | CRS |
|---|---|---|
| command_injection (27) | 1,000 | 0,704 |
| lfi (43) | 1,000 | 1,000 |
| log4shell (60) | 1,000 | 1,000 |
| path_traversal (23) | 1,000 | 0,957 |
| rfi (26) | 1,000 | 0,000 |
| sensitive_file (29) | 1,000 | 1,000 |
| sql_injection (6) | 0,667 | 1,000 |
| ssti (36) | 0,722 | 0,278 |
| xss (20) | 0,850 | 1,000 |

### Mức nguồn

| Cấu hình | Nguồn độc hại bị cảnh báo | Nguồn hợp lệ bị cảnh báo nhầm | Kẻ tấn công payload | Dò quét | Flood | Trễ trung vị |
|---|---|---|---|---|---|---|
| A (chỉ rule) | 31/36 | 2/85 | 29/30 | 2/4 | 0/2 | 0 s |
| B (chỉ hành vi) | 6/36 | 0/85 | 0/30 | 4/4 | 2/2 | 8,1 s |
| C (kết hợp) | 35/36 | 2/85 | 29/30 | 4/4 | 2/2 | 0 s |
| CRS (≥ 1 request bị đánh dấu) | 34/36 | 2/85 | 30/30 | 4/4 | 0/2 | - |

Trễ tính từ request độc hại đầu tiên của nguồn tới cảnh báo đầu tiên; 0 s nghĩa là cảnh báo ngay ở request đầu tiên mang payload.

### Nhận xét

1. **Tín hiệu hành vi bổ sung đúng phần rule không thấy.** So với A, cấu hình C cảnh báo thêm 4 nguồn (2 công cụ dò quét giả User-Agent trình duyệt, 2 nguồn flood) và không thêm cảnh báo nhầm nào. Dùng riêng (B), tín hiệu hành vi không phát hiện được kẻ tấn công payload nào và cần trung vị 8,1 giây để tích đủ số request trong cửa sổ, nên chỉ có giá trị khi đi kèm rule.
2. **So với CRS ở cùng FPR, recall cao hơn (0,944 so với 0,774).** Chênh lệch đến từ RFI (CRS paranoia 1 không có rule nào khớp với `?page=http://evil.example/...`), command injection và SSTI. CRS tốt hơn ở SQLi và XSS. Ở mức nguồn, CRS phát hiện từng request nên không nhận ra flood không mang payload.
3. **Cảnh báo nhầm** ở cả hai hệ thống đều do cùng một truy vấn tìm kiếm `../ relative path docs` (rule `PT_DOTDOT_SLASH`, `PT_ENCODED_TRAVERSAL`; CRS 930110). Đây là trường hợp nhập nhằng chủ ý trong dữ liệu: chuỗi `../` trong tham số là dấu hiệu traversal hợp lệ, nhưng ở đây người dùng chỉ tìm tài liệu.
4. **Bỏ sót.** 15 request payload không bị phát hiện thuộc ba dạng: SSTI `${7*7}` (10), XSS trong ngữ cảnh JavaScript `'-alert(1)-'` (3), SQLi `-1' ORDER BY 3--` (2). Nguồn độc hại duy nhất C bỏ sót (203.0.113.30) chỉ gửi 4 request file nhạy cảm (`/.env`, `/.htpasswd`, `/backup.sql`): mỗi request khớp rule mức medium (65 điểm), dưới ngưỡng cảnh báo 70, và quá ít request để kích hoạt tín hiệu hành vi. Nguồn này vẫn xuất hiện qua `anomaly.scored` trên dashboard; nếu domain cần cảnh báo cả trường hợp này thì đặt ngưỡng P4 theo domain ≤ 65.

Các rule không được chỉnh theo những payload bỏ sót ở trên để kết quả không bị làm đẹp trên chính tập đánh giá; cần một tập payload khác để kiểm chứng khi bổ sung rule.

## Lượt 1 (tập phát triển)

Kết quả rule tương tự lượt 2 (recall 0,935, CRS 0,754, cùng FPR 0,005), cho thấy kết quả ổn định giữa hai seed. Lượt này tìm ra ba vấn đề, đã sửa trước lượt 2:

- **Hệ thống:** cả 2 nguồn flood không bị cảnh báo ở mọi cấu hình (chỉ đạt 30 điểm từ `request_burst`). Đã bổ sung tín hiệu `request_flood`; ở lượt 2 cả hai bị cảnh báo.
- **Quy trình đo:** 9 request path traversal bị Nginx trả 400 trước khi chọn vhost, nằm trong log của vhost mặc định nên ban đầu bị tính là bỏ sót. Nay được loại khỏi đánh giá cho mọi hệ thống và báo số lượng riêng.
- **Quy trình đo:** phân loại nguồn theo tiền tố IP xếp nhầm người dùng thường vào nhóm ứng dụng gọi API. Đã sửa thành so sánh octet cuối.

## Kiểm thử tải

[`loadtest.py`](loadtest.py) ghi dồn 20.000 dòng log hợp lệ (domain `lab.local`, 20.000 IP khác nhau, 10% mang payload) vào log của Nginx trong chưa tới 0,2 giây, mô phỏng một đợt tăng đột biến. [`loadmon.py`](loadmon.py) đo offset Kafka mỗi 0,25 giây. Môi trường: triển khai 3 tầng (`deploy/`) trên một máy Apple M4, Docker Desktop cấp 10 CPU và 8 GB RAM; mỗi service một instance, mỗi topic 3 partition.

### Lần đo đầu: điểm nghẽn tại C2

Kết quả: [`results/loadtest.md`](results/loadtest.md).

| Khâu | Xử lý hết 20.000 message sau (s) | Thông lượng (message/s) | Lag lớn nhất |
|---|---|---|---|
| C1 thu thập | 7,0 | 2.857 | - |
| C2 chuẩn hoá | 138,2 | 145 | 19.087 |
| C3 làm giàu | 138,2 | 145 | 7 |
| detector | 138,2 | 145 | 6 |

- C2 flush producer và commit offset đồng bộ sau từng message trước khi đọc message tiếp theo, nên thông lượng bị giới hạn bởi độ trễ khứ hồi tới broker.
- `log.raw.ingested` dùng key `domain` (dòng thô chưa có IP), nên mọi dòng của một domain vào cùng một partition: thêm instance C2 cũng không giúp khi đột biến chỉ ở một domain.
- Các consumer phụ (P3 thống kê pipeline, metrics-exporter) đọc cả ba topic log (60.000 message) với cùng cơ chế, tụt lại khoảng 43.000 message khi detector đã xong và cần thêm khoảng 5 phút để đuổi kịp.

### Bước 1: consume theo batch và key theo client ở C2

Hai thay đổi trong [`common/bus.py`](../services/common/bus.py) và [C1](../services/c1-ingestion/main.py):

1. **Consume theo batch:** xử lý lần lượt các message đã có sẵn (tối đa 500 message hoặc 1 giây xử lý), flush producer và commit một lần cho cả batch. Poll vẫn được gọi giữa mọi message, nên handler chậm (P4 gửi email) không làm consumer bị loại khỏi group. Vẫn at-least-once: một message đầu ra không được xác nhận thì cả batch không được commit và được xử lý lại. Khi lưu lượng thấp, batch chỉ có một message nên độ trễ không đổi.
2. **Key theo client cho log thô:** C1 lấy IP ở đầu dòng làm key `domain|IP`, nên log của một domain chia đều 3 partition và có thể chạy tới 3 instance C2.

Kết quả: [`results/loadtest-batch/loadtest.md`](results/loadtest-batch/loadtest.md).

| Khâu | Xử lý hết 20.000 message sau (s) | Thông lượng (message/s) | Lag lớn nhất |
|---|---|---|---|
| C1 thu thập | 8,8 | 2.275 | - |
| C2 chuẩn hoá | 8,8 | 2.275 | 12 |
| C3 làm giàu | 8,8 | 2.275 | 34 |
| detector | 8,8 | 2.275 | 146 |

Thời gian xử lý hết đợt tải giảm từ 138 giây xuống 8,8 giây. Mọi khâu sau C1 theo kịp, nên điểm nghẽn chuyển sang C1: mỗi dòng vẫn được gửi và chờ broker xác nhận riêng.

### Bước 2: C1 gửi theo vòng poll

C1 gửi mọi dòng mới của một vòng poll liền nhau rồi chờ broker xác nhận một lần (`EventBus.deferred_delivery`, cùng cơ chế với consume theo batch). Broker không xác nhận thì C1 ghi log lỗi kèm số dòng của vòng và chạy tiếp, như hành vi cũ với từng dòng.

| Khâu | 20.000 dòng: xử lý hết sau (s) | 100.000 dòng: xử lý hết sau (s) | 100.000 dòng: thông lượng (message/s) | 100.000 dòng: lag lớn nhất |
|---|---|---|---|---|
| C1 thu thập | 1,2 | 3,1 | 32.468 | - |
| C2 chuẩn hoá | 2,1 | 6,3 | 15.974 | 70.500 |
| C3 làm giàu | 2,1 | 6,3 | 15.974 | 500 |
| detector | 4,5 | 17,3 | 5.774 | 68.799 |

Kết quả: [`results/loadtest-c1batch/loadtest.md`](results/loadtest-c1batch/loadtest.md) (20.000 dòng) và [`results/loadtest-c1batch-100k/loadtest.md`](results/loadtest-c1batch-100k/loadtest.md) (100.000 dòng). Mẫu được lấy khoảng mỗi 0,4 giây, nên với lượt 20.000 dòng chỉ nên so sánh tổng thời gian; thông lượng từng khâu lấy theo lượt 100.000 dòng.

- Đợt tải 20.000 dòng được phân tích hết sau 4,5 giây (lần đo đầu 138 giây, bước 1 là 8,8 giây). Lượt 100.000 dòng sinh 17.465 `attack.detected` và 9.638 `alert.triggered`; ở mọi lượt không message nào vào dead-letter.
- Điểm nghẽn hiện là detector (khoảng 5.800 message/s một instance). Key của `log.enriched` đã theo client và trạng thái cửa sổ tính theo client, nên có thể chạy tới 3 instance detector cùng group khi cần.
- P4 gửi email và webhook cho từng cảnh báo nên xử lý khoảng 200 message/s; với khoảng 14.700 cảnh báo và điểm bất thường của lượt 100.000 dòng, P4 cần thêm khoảng 70 giây sau đợt tải để hết lag (ước tính theo tốc độ đo được) và không khởi động lại lần nào. Đây là trường hợp poll giữa mọi message (bước 1) được thiết kế để bảo vệ.

## Giới hạn

- Dữ liệu tổng hợp do nhóm tự sinh từ các mẫu payload phổ biến, chưa phải lưu lượng thật. Quy mô nhỏ (36 nguồn độc hại, 270 request payload; một số nhóm chỉ có 6 đến 30 request), nên chênh lệch vài điểm phần trăm giữa các nhóm không có ý nghĩa thống kê.
- Hệ thống chỉ thấy các trường trong access log (dòng request, mã trạng thái, User-Agent, Referer), không thấy body và phần lớn header; CRS thấy toàn bộ request. Trong bộ dữ liệu này mọi payload nằm ở URL nên khác biệt chưa thể hiện, nhưng với tấn công qua body thì hệ thống không phát hiện được.
- Hai hệ thống có vai trò khác nhau: CRS chạy trên đường đi của request và có thể chặn, hệ thống phân tích log sau khi request đã được phục vụ (trễ trung vị khoảng 200 ms). So sánh ở đây chỉ về khả năng phát hiện. CRS chạy cấu hình mặc định, không tinh chỉnh.
- Ngưỡng tín hiệu hành vi (20, 100, 8, 12 trong 60 giây) và ngưỡng cảnh báo 70 được chọn theo kinh nghiệm, chưa khảo sát độ nhạy theo ngưỡng.
- Số đo tải lấy trên một máy, các service dùng chung CPU; chỉ có ý nghĩa tương đối.

## Chạy lại

Trên hệ thống đang chạy (`make deploy-up` hoặc `make up`):

```bash
make experiment                 # = experiments/run.sh; SEED=1337 make experiment cho tập đánh giá
make loadtest LINES=20000       # kiểm thử tải, chạy sau thực nghiệm (dùng chung domain lab.local)
```

Dữ liệu thô (`*.jsonl`) không được commit; tái tạo được bằng cùng seed.
