# Kế hoạch triển khai đề tài: Hệ thống giám sát và phát hiện tấn công web (WAF Log Analyzer)

## 1. Tổng quan phân công

Người 1 giữ vai trò Log Collection Engineer. Người 1 phụ trách các module C1, C2, C3, chịu trách nhiệm thu thập log truy cập web từ Nginx và Apache rồi chuẩn hoá thành dữ liệu có cấu trúc, làm nguyên liệu đầu vào cho Người 2.

Người 2 giữ vai trò Detection Engine Developer. Người 2 phụ trách các module D1, D2, D3, chịu trách nhiệm phát hiện các mẫu tấn công như SQL Injection, XSS, path traversal thông qua một rule engine tự viết, đồng thời chấm điểm bất thường cho từng request và từng phiên truy cập.

Người 3 giữ vai trò Platform Engineer. Người 3 phụ trách các module P1 đến P5, xây dựng hạ tầng dùng chung gồm quản lý phạm vi theo domain và ứng dụng, điều phối luồng xử lý, cảnh báo, và dashboard thời gian thực mà cả Người 1 lẫn Người 2 đều phụ thuộc vào.

## 2. Nguyên tắc chung

Thiết kế theo hướng interface first. Người 3 định nghĩa API contract (OpenAPI hoặc JSON schema) và dựng mock server cho Scope Service, Event Bus, và Auth ngay từ tuần đầu tiên, để Người 1 và Người 2 có thể phát triển song song mà không cần chờ nhau.

Toàn hệ thống dùng chung một event schema thống nhất, gồm các trường event, request_id hoặc session_id, timestamp, và data.

Mỗi service có Dockerfile riêng, cộng với một docker compose chung đặt ở gốc repository, để ba người có thể chạy thử toàn bộ hệ thống trên máy cá nhân.

Có buổi daily sync ngắn khoảng 15 phút mỗi ngày để báo cáo API nào đã sẵn sàng, API nào còn ở dạng mock.

Mọi log được xử lý chỉ trong phạm vi domain hoặc ứng dụng đã được xác nhận qua Scope Service của Người 3. Người 1 và Người 2 đều phải gọi kiểm tra scope trước khi xử lý log thuộc một domain mới.

Hệ thống chỉ dừng ở mức phát hiện và cảnh báo, không tự động chặn IP hay thay đổi cấu hình web server. Nếu nhóm muốn làm thêm tính năng tự động phản ứng, ví dụ chặn IP, tính năng đó bắt buộc phải đi qua bước phê duyệt thủ công và mặc định ở chế độ dry run.

## 3. Chi tiết nhiệm vụ Người 1: Log Collection Engineer

Mục tiêu chung của Người 1 là xây dựng toàn bộ pipeline thu thập và chuẩn hoá log truy cập web, sau đó publish kết quả qua event bus để Người 2 tiêu thụ.

### 3.1. Module C1: Log Ingestion

Input: log truy cập của Nginx hoặc Apache, gồm access log và có thể mở rộng thêm error log. Có hai cách kết nối, có thể chọn một hoặc hỗ trợ cả hai: đọc trực tiếp file log trên cùng máy hoặc qua volume mount nếu chạy Docker, dùng cơ chế theo dõi liên tục giống như tail, mỗi khi web server ghi thêm dòng log mới thì đọc ngay dòng đó; hoặc nhận log qua syslog khi web server và collector chạy trên các máy khác nhau. Cả hai cách đều chạy như một tiến trình nền liên tục, chỉ cần cấu hình đường dẫn hoặc kênh syslog một lần, không cần thao tác copy file thủ công về sau.

Output: event log.raw.ingested, gồm dòng log gốc, nguồn log, thời điểm thu thập, được publish gần như ngay khi dòng log xuất hiện.

Công nghệ gợi ý: ngôn ngữ tự chọn có khả năng đọc file theo kiểu tail, theo dõi log rotation, và xử lý streaming tốt, Go, Python, Node đều phù hợp.

Deliverable: service độc lập chạy liên tục, có test bằng cách gửi request thật tới một Nginx hoặc Apache đang chạy và xác nhận collector đọc được dòng log mới gần như tức thời, không phải test bằng cách nạp tay một file log tĩnh.

### 3.2. Module C2: Parsing và chuẩn hoá request, response

Input: consume log.raw.ingested.

Output: event log.normalized, gồm các trường đã tách, phương thức HTTP, URL, query string, header, user agent, địa chỉ IP nguồn, mã trạng thái phản hồi, kích thước phản hồi, thời gian xử lý, thời điểm request.

Deliverable: parser xử lý được định dạng combined log format phổ biến của Nginx và Apache, có test cho các trường hợp dòng log lỗi định dạng, URL chứa ký tự encode, hoặc log nhiều dòng.

### 3.3. Module C3: Enrichment và gom phiên truy cập

Input: consume log.normalized.

Output: event log.enriched, bổ sung thông tin geoIP theo địa chỉ IP, gom nhóm request theo phiên dựa trên IP và cookie nếu có, tính tần suất request trong một khoảng thời gian cho từng IP.

Lưu ý: cần xử lý theo luồng hoặc theo batch để tránh tốn bộ nhớ khi lượng log lớn, giới hạn số lượng request giữ trong bộ nhớ tại một thời điểm.

## 4. Chi tiết nhiệm vụ Người 2: Detection Engine Developer

Mục tiêu chung của Người 2 là từ dữ liệu log đã chuẩn hoá của Người 1, phát hiện các mẫu tấn công và chấm điểm bất thường, không xây dựng khả năng tấn công thật.

### 4.1. Module D1: Rule Engine, đây là lõi kỹ thuật

Input: consume log.enriched.

Output: event attack.detected, gồm URL, rule_id, loại tấn công, mức độ nghiêm trọng, và bằng chứng khớp mẫu.

Yêu cầu bắt buộc: tự viết engine đọc rule dạng YAML hoặc JSON, áp dụng matcher dựa trên regex hoặc pattern lên các trường của request, gồm URL, query string, body nếu có, header, hỗ trợ tối thiểu ba nhóm tấn công là SQL Injection, XSS, và path traversal. Đây là phần thể hiện năng lực kỹ thuật chính của đề tài, không nên chỉ gọi lại một bộ rule có sẵn như OWASP CRS mà không tự xử lý logic khớp mẫu.

Deliverable: unit test cho từng loại matcher, tối thiểu năm rule mẫu chạy đúng, bao gồm ít nhất một rule cho mỗi loại tấn công nêu trên.

### 4.2. Module D2: Quản lý rule

Input: file rule do người dùng cung cấp hoặc cập nhật qua API.

Output: API quản lý rule cho phép tạo, sửa, xoá, và kiểm tra rule, dạng CRUD, có validate schema.

Lưu ý: cần validate cú pháp rule trước khi nạp vào engine, để tránh regex gây treo hệ thống do backtracking không kiểm soát.

### 4.3. Module D3: Chấm điểm bất thường

Input: consume attack.detected và log.enriched, ví dụ tần suất request tăng đột biến, tỷ lệ mã lỗi bất thường, kèm xác nhận scope hợp lệ từ Người 3.

Output: event anomaly.scored, là điểm tổng hợp kết hợp giữa số lượng rule khớp và các tín hiệu thống kê bất thường, khi điểm vượt ngưỡng cấu hình thì publish alert.triggered.

Ràng buộc: module chỉ tính điểm và đề xuất mức độ nghiêm trọng, không tự động chặn IP hay thay đổi cấu hình hệ thống. Nếu có tính năng phản ứng tự động, bắt buộc phải qua bước phê duyệt thủ công.

## 5. Chi tiết nhiệm vụ Người 3: Platform Engineer

Mục tiêu chung của Người 3 là cung cấp hạ tầng dùng chung sớm nhất có thể, để Người 1 và Người 2 không bị chặn tiến độ phát triển.

### 5.1. Module P1: Scope Management, làm đầu tiên

Output: API GET /scope/check nhận domain hoặc tên ứng dụng, trả về việc domain đó có được phép xử lý hay không, đây là API mà mọi service khác đều phải gọi trước khi xử lý log của domain tương ứng.

Deliverable ưu tiên tuần đầu: có mock hoặc API thật cho endpoint scope check, để Người 1 và Người 2 tích hợp ngay từ sớm.

### 5.2. Module P2: Auth Gateway

Output: xác thực JWT cho các API nội bộ, định tuyến request giữa các service qua gateway chung.

### 5.3. Module P3: Pipeline Orchestration

Input: cấu hình pipeline dạng YAML, mô tả domain cần theo dõi và thứ tự các bước từ thu thập log đến phát hiện và chấm điểm.

Output: điều phối việc gọi giữa các service, theo dõi trạng thái xử lý theo batch hoặc theo khoảng thời gian, publish event stage.completed.

### 5.4. Module P4: Alerting

Input: consume anomaly.scored khi vượt ngưỡng cấu hình.

Output: gửi thông báo qua webhook hoặc email, kèm chi tiết loại tấn công, URL bị nhắm tới, mức độ nghiêm trọng, cho phép cấu hình ngưỡng cảnh báo riêng theo từng domain.

### 5.5. Module P5: Dashboard thời gian thực

Input: consume toàn bộ event log, attack, anomaly.

Output: API tổng hợp cho dashboard, hiển thị lưu lượng truy cập theo thời gian thực theo domain, danh sách các loại tấn công phát hiện nhiều nhất, danh sách địa chỉ IP nguồn tấn công nhiều nhất, lịch sử cảnh báo.

## 6. Sơ đồ phụ thuộc và mốc tích hợp

Giai đoạn 0: Người 3 hoàn thành API contract và mock cho Scope Service, Auth, và Event Bus. Giai đoạn này không phụ thuộc vào ai, cần làm trước tiên.

Giai đoạn 1: Người 1 hoàn thành module C1, Log Ingestion, chạy độc lập, chỉ cần có mock của Scope Service.

Giai đoạn 2: Người 1 hoàn thành module C2 và C3, Parsing, chuẩn hoá và Enrichment, dựa trên output của giai đoạn 1.

Giai đoạn 3: Người 2 hoàn thành module D1 và D2, Rule Engine và quản lý rule, cần có dữ liệu log.enriched thật từ Người 1, hoặc tạm thời dùng dữ liệu mẫu.

Giai đoạn 4: Người 2 hoàn thành module D3, Chấm điểm bất thường, cần có attack.detected thật từ giai đoạn 3.

Giai đoạn 5: Người 3 hoàn thành module P3, Pipeline Orchestration, kết nối toàn bộ pipeline, cần các service ở giai đoạn 1 đến 4 đã có API thật, không còn ở dạng mock.

Giai đoạn 6: Cả ba người cùng hoàn thiện Alerting, Dashboard, và kiểm thử toàn trình, sau khi giai đoạn 5 hoàn tất.

## 7. Định nghĩa hoàn thành của toàn hệ thống

Chạy được một kịch bản thật từ đầu đến cuối theo hướng thời gian thực, không phải nạp tay một lần: trỏ Log Collector vào log đang được một Nginx hoặc Apache thật ghi liên tục cho một domain đã khai báo, gửi một số request thử tới web server đó, bao gồm ít nhất một request dạng SQL Injection, hệ thống tự động đọc log mới, chuẩn hoá, phát hiện request khớp với rule tấn công tự viết, chấm điểm bất thường, và hiển thị gần như tức thời trên dashboard kèm cảnh báo tương ứng, không cần thao tác nạp lại file log thủ công.

Không service nào xử lý log của domain ngoài phạm vi đã được xác nhận qua Scope Service.

Không có hành vi tự động chặn IP hay thay đổi hệ thống ngoài phạm vi phát hiện và cảnh báo, trừ khi có phê duyệt thủ công và mặc định ở chế độ dry run.

Có tài liệu API dạng OpenAPI và file README cho từng service, để người ngoài nhóm có thể chạy lại được hệ thống.

## 8. Lịch trình chi tiết theo tuần, tổng thời gian bốn tuần

### 8.1. Người 1: Log Collection Engineer

Tuần 1

- [ ] Nhiệm vụ: thiết kế schema cho log.raw.ingested và log.normalized, viết tài liệu ngắn mô tả các trường để Người 2 và Người 3 tham khảo sớm. Output: file mô tả schema. Deadline: ngày 3 của tuần 1.

- [ ] Nhiệm vụ: viết Log Ingestion, module C1, đọc log Nginx hoặc Apache theo cơ chế theo dõi liên tục, xử lý log rotation cơ bản, tích hợp gọi mock Scope Service của Người 3. Output: service C1 chạy được, publish event log.raw.ingested, có Dockerfile riêng. Deadline: cuối tuần 1.

Tuần 2

- [ ] Nhiệm vụ: viết parser chuẩn hoá log thành log.normalized, module C2, xử lý các trường hợp dòng log lỗi định dạng và URL chứa ký tự encode. Output: module C2 hoàn chỉnh kèm unit test cho parser. Deadline: giữa tuần 2.

- [ ] Nhiệm vụ: viết module Enrichment, C3, gom phiên theo IP và cookie, tính tần suất request theo thời gian. Output: module C3 hoàn chỉnh, publish event log.enriched. Deadline: cuối tuần 2.

Tuần 3

- [ ] Nhiệm vụ: tích hợp toàn bộ pipeline từ C1 đến C3 với dữ liệu log thật của ít nhất một domain, chuyển sang gọi Scope Service thật thay vì mock. Output: pipeline thu thập log chạy đúng với dữ liệu thật, có test tích hợp. Deadline: giữa tuần 3.

- [ ] Nhiệm vụ: viết README và tài liệu mô tả event schema cho các service của mình, dọn code, fix lỗi phát sinh khi Người 2 và Người 3 tích hợp thử. Output: README hoàn chỉnh. Deadline: cuối tuần 3.

Tuần 4

- [ ] Nhiệm vụ: cùng cả nhóm kiểm thử từ đầu đến cuối, xử lý lỗi phát sinh khi ghép nối toàn hệ thống, chuẩn bị dữ liệu cho buổi demo. Output: pipeline thu thập log hoạt động ổn định trong kịch bản demo cuối cùng. Deadline: trước buổi báo cáo cuối tuần 4.

### 8.2. Người 2: Detection Engine Developer

Tuần 1

- [ ] Nhiệm vụ: thiết kế schema rule dạng YAML cho ba nhóm tấn công SQL Injection, XSS, path traversal, chuẩn bị bộ dữ liệu log mẫu để phát triển song song khi chưa có log thật từ Người 1. Output: file schema rule, bộ dữ liệu log mẫu. Deadline: ngày 3 của tuần 1.

- [ ] Nhiệm vụ: viết phần lõi của Rule Engine, module D1, đọc rule và áp dụng matcher regex lên dữ liệu mẫu. Output: engine chạy được với dữ liệu mẫu, publish event attack.detected. Deadline: cuối tuần 1.

Tuần 2

- [ ] Nhiệm vụ: viết tối thiểu năm rule mẫu, ít nhất một rule cho mỗi loại tấn công, kèm unit test cho từng loại matcher. Output: bộ rule mẫu và test pass. Deadline: giữa tuần 2.

- [ ] Nhiệm vụ: viết API quản lý rule, module D2, hỗ trợ tạo sửa xoá rule, validate schema và chặn regex nguy hiểm. Output: module D2 hoàn chỉnh. Deadline: cuối tuần 2.

Tuần 3

- [ ] Nhiệm vụ: viết module chấm điểm bất thường, D3, kết hợp kết quả rule khớp với tín hiệu thống kê như tần suất request và tỷ lệ lỗi, tích hợp gọi Scope Service thật. Output: module D3 hoàn chỉnh, publish event anomaly.scored và alert.triggered khi vượt ngưỡng. Deadline: giữa tuần 3.

- [ ] Nhiệm vụ: tích hợp lại toàn bộ detection engine với dữ liệu log.enriched thật từ Người 1, tinh chỉnh ngưỡng cảnh báo, viết README. Output: pipeline từ D1 đến D3 chạy đúng với dữ liệu thật. Deadline: cuối tuần 3.

Tuần 4

- [ ] Nhiệm vụ: cùng cả nhóm kiểm thử từ đầu đến cuối, rà soát lại các rule để giảm báo động giả, fix lỗi phát sinh. Output: detection engine hoạt động ổn định trong kịch bản demo cuối cùng. Deadline: trước buổi báo cáo cuối tuần 4.

### 8.3. Người 3: Platform Engineer

Tuần 1

- [ ] Nhiệm vụ: định nghĩa API contract dạng OpenAPI cho Scope Service, Auth, và Event Bus, dựng mock server cho ba API này. Output: file OpenAPI, mock server chạy được. Deadline: ngày 2 của tuần 1, đây là mốc ưu tiên cao nhất vì cả nhóm phụ thuộc vào đây.

- [ ] Nhiệm vụ: viết Scope Service thật, module P1, và Auth Gateway, module P2, dùng JWT. Output: module P1 và P2 hoàn chỉnh, thay thế mock. Deadline: cuối tuần 1.

Tuần 2

- [ ] Nhiệm vụ: dựng Event Bus thật, có thể dùng công nghệ có sẵn như Redis pub sub hoặc RabbitMQ tuỳ mức độ quen thuộc của nhóm, viết docker compose chung đặt ở gốc repository. Output: event bus chạy được, file docker compose và hướng dẫn chạy thử toàn hệ thống. Deadline: giữa tuần 2.

- [ ] Nhiệm vụ: chuẩn bị khung sườn cho Pipeline Orchestration, module P3, định nghĩa cấu trúc pipeline YAML, trong lúc chờ Người 1 và Người 2 hoàn thiện service thật. Output: khung orchestration chạy được với dữ liệu giả lập. Deadline: cuối tuần 2.

Tuần 3

- [ ] Nhiệm vụ: hoàn thiện Pipeline Orchestration với các service thật từ Người 1 và Người 2, không còn dùng mock. Output: module P3 điều phối đúng toàn bộ pipeline. Deadline: giữa tuần 3.

- [ ] Nhiệm vụ: viết module Alerting, P4, gửi thông báo qua webhook hoặc email khi anomaly.scored vượt ngưỡng, và bắt đầu module Dashboard, P5. Output: module P4 hoàn chỉnh, khung dashboard hiển thị được dữ liệu cơ bản. Deadline: cuối tuần 3.

Tuần 4

- [ ] Nhiệm vụ: hoàn thiện Dashboard thời gian thực, module P5, hiển thị traffic theo domain, danh sách loại tấn công nhiều nhất, danh sách IP nguồn tấn công nhiều nhất, lịch sử cảnh báo. Output: module P5 hoàn chỉnh. Deadline: đầu tuần 4.

- [ ] Nhiệm vụ: chủ trì kiểm thử từ đầu đến cuối cùng cả nhóm, viết README tổng cho toàn hệ thống và tài liệu OpenAPI đầy đủ, chuẩn bị kịch bản demo và báo cáo. Output: hệ thống chạy ổn định theo đúng phần Định nghĩa hoàn thành, tài liệu đầy đủ, sẵn sàng báo cáo. Deadline: trước buổi báo cáo cuối tuần 4.
