"""Tính chỉ số thực nghiệm từ nhãn và kết quả phát hiện (chỉ thư viện chuẩn).

Đầu vào (thư mục results/):
  labels.jsonl        nhãn từng request (traffic.py, lượt gửi qua Nginx của pipeline)
  enriched.jsonl      envelope log.enriched của lượt chạy (độ phủ pipeline)
  A.jsonl, B.jsonl, C.jsonl   kết quả phát lại offline 3 cấu hình (Evaluate.java)
  live.jsonl          kết quả detector đang chạy (cấu hình C, qua Kafka)  [tuỳ chọn]
  modsec.jsonl        request bị ModSecurity + OWASP CRS đánh dấu          [tuỳ chọn]
Đầu ra: summary.json và summary.md.

Hai mức đánh giá:
  - Theo request (payload): request mang payload có bị phát hiện (attack.detected / CRS) không.
  - Theo nguồn (IP): nguồn độc hại có bị cảnh báo (alert.triggered) không, nguồn hợp lệ có bị
    cảnh báo nhầm không, và độ trễ từ request độc hại đầu tiên tới cảnh báo đầu tiên.
"""
import json
import os
import sys
from collections import defaultdict

PAYLOAD_EXCLUDED = {"benign", "scan", "flood"}


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def ratio(num, den):
    return round(num / den, 4) if den else None


def confusion(positives, negatives, detected):
    tp = len(positives & detected)
    fp = len(negatives & detected)
    fn = len(positives - detected)
    tn = len(negatives - detected)
    precision, recall = ratio(tp, tp + fp), ratio(tp, tp + fn)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision and recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall,
            "fpr": ratio(fp, fp + tn), "f1": f1}


def actor_of(ip, categories):
    if categories - PAYLOAD_EXCLUDED:
        return "payload_attacker"
    if "scan" in categories:
        return "scanner"
    if "flood" in categories:
        return "flooder"
    if ip.startswith("66.249."):
        return "benign_crawler"
    if ip.startswith("198.51.100.") and int(ip.rsplit(".", 1)[1]) >= 200:  # traffic.py: 198.51.100.200+
        return "benign_api_poller"
    return "benign_visitor"


def main(results_dir):
    labels = [row for row in read_jsonl(os.path.join(results_dir, "labels.jsonl")) if row.get("status")]
    by_id = {row["request_id"]: row for row in labels}
    enriched_ids = {env.get("session_id") for env in read_jsonl(os.path.join(results_dir, "enriched.jsonl"))}

    # Request không có trong log.enriched (Nginx từ chối 400 trước khi chọn vhost nên ghi vào log của
    # vhost mặc định, không mang IP/cookie thí nghiệm) không ghép được với nhãn: loại khỏi đánh giá
    # theo request cho MỌI hệ thống (kể cả CRS) để so sánh trên cùng một tập.
    unobservable = set(by_id) - enriched_ids if enriched_ids else set()
    payload = {rid for rid, row in by_id.items()
               if row["label"] == "attack" and row["category"] not in PAYLOAD_EXCLUDED and rid not in unobservable}
    benign = {rid for rid, row in by_id.items() if row["label"] == "benign" and rid not in unobservable}
    ip_categories = defaultdict(set)
    first_malicious_t = {}
    for row in sorted(labels, key=lambda r: r["t"]):
        ip_categories[row["ip"]].add(row["category"])
        if row["label"] == "attack":
            first_malicious_t.setdefault(row["ip"], row["t"])
    actors = {ip: actor_of(ip, cats) for ip, cats in ip_categories.items()}
    malicious_ips = {ip for ip, actor in actors.items() if not actor.startswith("benign")}
    benign_ips = set(actors) - malicious_ips

    summary = {
        "requests": len(labels),
        "pipeline_coverage": ratio(len(enriched_ids & set(by_id)), len(by_id)),
        "unobservable": {"count": len(unobservable),
                         "statuses": sorted({by_id[rid]["status"] for rid in unobservable}),
                         "categories": sorted({by_id[rid]["category"] for rid in unobservable})},
        "sources": {actor: sum(1 for a in actors.values() if a == actor) for actor in sorted(set(actors.values()))},
        "request_level": {}, "request_level_by_category": {}, "source_level": {},
    }
    counts = defaultdict(int)
    for row in labels:
        counts[row["category"]] += 1
    summary["requests_by_category"] = dict(sorted(counts.items()))

    configs = {}
    for name in ("A", "B", "C", "live"):
        rows = read_jsonl(os.path.join(results_dir, f"{name}.jsonl"))
        if rows:
            configs[name] = rows

    for name, rows in configs.items():
        detected = {r["envelope"].get("session_id") for r in rows if r["topic"] == "attack.detected"}
        if name != "B":
            summary["request_level"][name] = confusion(payload, benign, detected)
            summary["request_level_by_category"][name] = {
                cat: ratio(len({rid for rid in payload if by_id[rid]["category"] == cat} & detected),
                           len({rid for rid in payload if by_id[rid]["category"] == cat}))
                for cat in sorted({by_id[rid]["category"] for rid in payload})}
        alerts = [r["envelope"] for r in rows if r["topic"] == "alert.triggered"]
        alerted_ips, delays = set(), {}
        for env in alerts:
            ip = env["data"].get("src_ip")
            trigger = by_id.get(env.get("session_id"))
            alerted_ips.add(ip)
            if ip in first_malicious_t and trigger and ip not in delays:
                delays[ip] = round(trigger["t"] - first_malicious_t[ip], 1)
        source = confusion(malicious_ips, benign_ips, alerted_ips)
        source["by_actor"] = {actor: f"{sum(1 for ip, a in actors.items() if a == actor and ip in alerted_ips)}"
                                     f"/{sum(1 for a in actors.values() if a == actor)}"
                              for actor in sorted(set(actors.values()))}
        source["median_seconds_to_first_alert"] = (sorted(delays.values())[len(delays) // 2] if delays else None)
        source["alerts"] = len(alerts)
        summary["source_level"][name] = source

    modsec = read_jsonl(os.path.join(results_dir, "modsec.jsonl"))
    if modsec:
        flagged = {row["request_id"] for row in modsec if row["flagged"]}
        summary["request_level"]["CRS"] = confusion(payload, benign, flagged)
        summary["request_level_by_category"]["CRS"] = {
            cat: ratio(len({rid for rid in payload if by_id[rid]["category"] == cat} & flagged),
                       len({rid for rid in payload if by_id[rid]["category"] == cat}))
            for cat in sorted({by_id[rid]["category"] for rid in payload})}
        flagged_ips = {by_id[rid]["ip"] for rid in flagged if rid in by_id}
        crs_source = confusion(malicious_ips, benign_ips, flagged_ips)
        crs_source["by_actor"] = {actor: f"{sum(1 for ip, a in actors.items() if a == actor and ip in flagged_ips)}"
                                         f"/{sum(1 for a in actors.values() if a == actor)}"
                                  for actor in sorted(set(actors.values()))}
        summary["source_level"]["CRS (≥1 request bị đánh dấu)"] = crs_source
        summary["crs_flagged_scan_flood_requests"] = len(
            {rid for rid in flagged if rid in by_id and by_id[rid]["category"] in ("scan", "flood")})

    with open(os.path.join(results_dir, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    with open(os.path.join(results_dir, "summary.md"), "w", encoding="utf-8") as handle:
        handle.write(render_markdown(summary))
    print(render_markdown(summary))


def fmt(value):
    return "-" if value is None else (f"{value:.3f}" if isinstance(value, float) else str(value))


def render_markdown(s):
    u = s["unobservable"]
    out = [f"Tổng {s['requests']} request; độ phủ pipeline (request có trong log.enriched): {fmt(s['pipeline_coverage'])}. "
           f"Loại khỏi đánh giá theo request: {u['count']} request không quan sát được (HTTP {u['statuses']}, nhóm {u['categories']}).",
           "", "Nguồn: " + ", ".join(f"{k} {v}" for k, v in s["sources"].items()), "",
           "### Mức request (payload)", "",
           "| Cấu hình | TP | FP | FN | Precision | Recall | FPR | F1 |", "|---|---|---|---|---|---|---|---|"]
    for name, m in s["request_level"].items():
        out.append(f"| {name} | {m['tp']} | {m['fp']} | {m['fn']} | {fmt(m['precision'])} | {fmt(m['recall'])} | "
                   f"{fmt(m['fpr'])} | {fmt(m['f1'])} |")
    cats = sorted({c for per in s["request_level_by_category"].values() for c in per})
    out += ["", "### Recall theo nhóm tấn công", "",
            "| Nhóm | " + " | ".join(s["request_level_by_category"]) + " |",
            "|---|" + "---|" * len(s["request_level_by_category"])]
    for cat in cats:
        out.append(f"| {cat} | " + " | ".join(fmt(per.get(cat)) for per in s["request_level_by_category"].values()) + " |")
    out += ["", "### Mức nguồn (IP)", "",
            "| Cấu hình | Nguồn độc hại bị cảnh báo | Nguồn hợp lệ bị cảnh báo nhầm | Precision | Recall | Trễ trung vị (s) | Theo loại nguồn |",
            "|---|---|---|---|---|---|---|"]
    for name, m in s["source_level"].items():
        by_actor = "; ".join(f"{k} {v}" for k, v in m["by_actor"].items())
        out.append(f"| {name} | {m['tp']}/{m['tp'] + m['fn']} | {m['fp']}/{m['fp'] + m['tn']} | {fmt(m['precision'])} | "
                   f"{fmt(m['recall'])} | {fmt(m.get('median_seconds_to_first_alert'))} | {by_actor} |")
    if "crs_flagged_scan_flood_requests" in s:
        out += ["", f"CRS đánh dấu thêm {s['crs_flagged_scan_flood_requests']} request dò quét/flood (không tính vào bảng payload)."]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "results"))
