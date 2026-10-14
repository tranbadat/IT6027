Ghi dồn 20000 dòng log trong 0.2 s.

| Khâu | Thời gian xử lý hết (s) | Thông lượng (message/s) |
|---|---|---|
| C1 thu thập | 7.0 | 2857.1 |
| C2 chuẩn hoá | 138.15 | 144.8 |
| C3 làm giàu | 138.15 | 144.8 |
| detector | 138.15 | 144.8 |

Lag lớn nhất: c2-parser 19087, c3-enrichment 7, detector 6
attack.detected sinh ra: 3389, alert.triggered: 1938.
