Tổng 1796 request; độ phủ pipeline (request có trong log.enriched): 0.998. Loại khỏi đánh giá theo request: 3 request không quan sát được (HTTP [400], nhóm ['path_traversal']).

Nguồn: benign_api_poller 3, benign_crawler 2, benign_visitor 80, flooder 2, payload_attacker 30, scanner 4

### Mức request (payload)

| Cấu hình | TP | FP | FN | Precision | Recall | FPR | F1 |
|---|---|---|---|---|---|---|---|
| A | 255 | 4 | 15 | 0.985 | 0.944 | 0.004 | 0.964 |
| C | 255 | 4 | 15 | 0.985 | 0.944 | 0.004 | 0.964 |
| live | 255 | 4 | 15 | 0.985 | 0.944 | 0.004 | 0.964 |
| CRS | 209 | 4 | 61 | 0.981 | 0.774 | 0.004 | 0.865 |

### Recall theo nhóm tấn công

| Nhóm | A | C | live | CRS |
|---|---|---|---|---|
| command_injection | 1.000 | 1.000 | 1.000 | 0.704 |
| lfi | 1.000 | 1.000 | 1.000 | 1.000 |
| log4shell | 1.000 | 1.000 | 1.000 | 1.000 |
| path_traversal | 1.000 | 1.000 | 1.000 | 0.957 |
| rfi | 1.000 | 1.000 | 1.000 | 0.000 |
| sensitive_file | 1.000 | 1.000 | 1.000 | 1.000 |
| sql_injection | 0.667 | 0.667 | 0.667 | 1.000 |
| ssti | 0.722 | 0.722 | 0.722 | 0.278 |
| xss | 0.850 | 0.850 | 0.850 | 1.000 |

### Mức nguồn (IP)

| Cấu hình | Nguồn độc hại bị cảnh báo | Nguồn hợp lệ bị cảnh báo nhầm | Precision | Recall | Trễ trung vị (s) | Theo loại nguồn |
|---|---|---|---|---|---|---|
| A | 31/36 | 2/85 | 0.939 | 0.861 | 0.000 | benign_api_poller 0/3; benign_crawler 1/2; benign_visitor 1/80; flooder 0/2; payload_attacker 29/30; scanner 2/4 |
| B | 6/36 | 0/85 | 1.000 | 0.167 | 8.100 | benign_api_poller 0/3; benign_crawler 0/2; benign_visitor 0/80; flooder 2/2; payload_attacker 0/30; scanner 4/4 |
| C | 35/36 | 2/85 | 0.946 | 0.972 | 0.000 | benign_api_poller 0/3; benign_crawler 1/2; benign_visitor 1/80; flooder 2/2; payload_attacker 29/30; scanner 4/4 |
| live | 35/36 | 2/85 | 0.946 | 0.972 | 0.000 | benign_api_poller 0/3; benign_crawler 1/2; benign_visitor 1/80; flooder 2/2; payload_attacker 29/30; scanner 4/4 |
| CRS (≥1 request bị đánh dấu) | 34/36 | 2/85 | 0.944 | 0.944 | - | benign_api_poller 0/3; benign_crawler 1/2; benign_visitor 1/80; flooder 0/2; payload_attacker 30/30; scanner 4/4 |

CRS đánh dấu thêm 92 request dò quét/flood (không tính vào bảng payload).
