Ghi dồn 20000 dòng log trong 0.09 s.

| Khâu | Thời gian xử lý hết (s) | Thông lượng (message/s) |
|---|---|---|
| C1 thu thập | 1.16 | 17241.4 |
| C2 chuẩn hoá | 2.11 | 9478.7 |
| C3 làm giàu | 2.11 | 9478.7 |
| detector | 4.46 | 4484.3 |

Lag lớn nhất: c2-parser 13500, c3-enrichment 0, detector 14300
attack.detected sinh ra: 3378, alert.triggered: 1935.
