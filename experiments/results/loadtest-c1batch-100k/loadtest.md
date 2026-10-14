Ghi dồn 100000 dòng log trong 0.13 s.

| Khâu | Thời gian xử lý hết (s) | Thông lượng (message/s) |
|---|---|---|
| C1 thu thập | 3.08 | 32467.5 |
| C2 chuẩn hoá | 6.26 | 15974.4 |
| C3 làm giàu | 6.26 | 15974.4 |
| detector | 17.32 | 5773.7 |

Lag lớn nhất: c2-parser 70500, c3-enrichment 500, detector 68799
attack.detected sinh ra: 17465, alert.triggered: 9638.
