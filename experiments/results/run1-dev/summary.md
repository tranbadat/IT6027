Tổng 1973 request; độ phủ pipeline (request có trong log.enriched): 0.995. Loại khỏi đánh giá theo request: 9 request không quan sát được (HTTP [400], nhóm ['path_traversal']).

Nguồn: benign_api_poller 3, benign_crawler 2, benign_visitor 80, flooder 2, payload_attacker 30, scanner 4

### Mức request (payload)

| Cấu hình | TP | FP | FN | Precision | Recall | FPR | F1 |
|---|---|---|---|---|---|---|---|
| A | 274 | 6 | 19 | 0.979 | 0.935 | 0.005 | 0.956 |
| C | 274 | 6 | 19 | 0.979 | 0.935 | 0.005 | 0.956 |
| live | 274 | 6 | 19 | 0.979 | 0.935 | 0.005 | 0.956 |
| CRS | 221 | 6 | 72 | 0.974 | 0.754 | 0.005 | 0.850 |

### Recall theo nhóm tấn công

| Nhóm | A | C | live | CRS |
|---|---|---|---|---|
| command_injection | 1.000 | 1.000 | 1.000 | 0.850 |
| lfi | 1.000 | 1.000 | 1.000 | 1.000 |
| log4shell | 1.000 | 1.000 | 1.000 | 1.000 |
| path_traversal | 1.000 | 1.000 | 1.000 | 0.886 |
| rfi | 1.000 | 1.000 | 1.000 | 0.000 |
| sensitive_file | 1.000 | 1.000 | 1.000 | 1.000 |
| sql_injection | 0.795 | 0.795 | 0.795 | 1.000 |
| ssti | 0.625 | 0.625 | 0.625 | 0.375 |
| xss | 0.750 | 0.750 | 0.750 | 1.000 |

### Mức nguồn (IP)

| Cấu hình | Nguồn độc hại bị cảnh báo | Nguồn hợp lệ bị cảnh báo nhầm | Precision | Recall | Trễ trung vị (s) | Theo loại nguồn |
|---|---|---|---|---|---|---|
| A | 32/36 | 6/85 | 0.842 | 0.889 | 0.000 | benign_api_poller 0/3; benign_crawler 1/2; benign_visitor 5/80; flooder 0/2; payload_attacker 30/30; scanner 2/4 |
| B | 4/36 | 0/85 | 1.000 | 0.111 | 7.800 | benign_api_poller 0/3; benign_crawler 0/2; benign_visitor 0/80; flooder 0/2; payload_attacker 0/30; scanner 4/4 |
| C | 34/36 | 6/85 | 0.850 | 0.944 | 0.000 | benign_api_poller 0/3; benign_crawler 1/2; benign_visitor 5/80; flooder 0/2; payload_attacker 30/30; scanner 4/4 |
| live | 34/36 | 6/85 | 0.850 | 0.944 | 0.000 | benign_api_poller 0/3; benign_crawler 1/2; benign_visitor 5/80; flooder 0/2; payload_attacker 30/30; scanner 4/4 |
| CRS (≥1 request bị đánh dấu) | 34/36 | 6/85 | 0.850 | 0.944 | - | benign_api_poller 0/3; benign_crawler 1/2; benign_visitor 5/80; flooder 0/2; payload_attacker 30/30; scanner 4/4 |

CRS đánh dấu thêm 111 request dò quét/flood (không tính vào bảng payload).
