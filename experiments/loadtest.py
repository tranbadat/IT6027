"""Kiểm thử tải pipeline: ghi dồn N dòng access log rồi đo thông lượng từng khâu C1 -> C2 -> C3 -> detector.

  python3 experiments/loadtest.py --lines 20000

Dòng log hợp lệ (định dạng waf_combined, domain lab.local, 10% mang payload) được nối vào file log của
Nginx mục tiêu bằng `docker exec`, mô phỏng một đợt tăng đột biến lưu lượng. Container loadmon.py đo
offset Kafka; kết quả ghi ra experiments/results/loadtest.{json,md}. Chạy trên hệ thống đang chạy và
SAU các thực nghiệm khác (dùng chung domain lab.local).
"""
import argparse
import datetime
import json
import os
import random
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = "/var/log/waf/nginx/lab.local.access.log"
BENIGN_PATHS = ["/", "/p/{n}", "/c/phones", "/search?q=laptop", "/api/products?id={n}", "/assets/app.css"]
ATTACK_PATHS = ["/api/products?id=1%20UNION%20SELECT%20pw--", "/search?q=%3Cscript%3Ealert(1)%3C/script%3E",
                "/api/load?file=../../../../etc/passwd", "/api/x?cmd=1;cat%20/etc/passwd"]


def container(service):
    out = subprocess.run(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.service={service}"],
                         capture_output=True, text=True, check=True).stdout.split()
    if not out:
        sys.exit(f"không thấy container {service}; cần hệ thống đang chạy")
    return out[0]


def network_of(container_id):
    out = subprocess.run(["docker", "inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}",
                          container_id], capture_output=True, text=True, check=True).stdout.split()
    return out[0]


def make_lines(count, seed):
    rng = random.Random(seed)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%d/%b/%Y:%H:%M:%S +0000")
    lines = []
    for i in range(count):
        ip = f"10.200.{(i // 250) % 250}.{i % 250 + 1}"
        if rng.random() < 0.1:
            path = rng.choice(ATTACK_PATHS)
        else:
            path = rng.choice(BENIGN_PATHS).format(n=rng.randint(1, 500))
        lines.append(f'{ip} - - [{stamp}] "GET {path} HTTP/1.1" 200 15 "-" "Mozilla/5.0 loadtest" '
                     f'"lab.local" rt=0.001 sid="load-{seed}-{i}"')
    return "\n".join(lines) + "\n"


def first_time(samples, key, topic, target):
    for sample in samples:
        if sample[key][topic] >= target:
            return sample["t"]
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lines", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default=os.path.join(HERE, "results"))
    args = parser.parse_args()

    kafka, nginx = container("kafka"), container("web-nginx")
    network = network_of(kafka)
    monitor = subprocess.Popen(
        ["docker", "run", "--rm", "--network", network, "-v", f"{HERE}:/x:ro", "waf/base:dev",
         "python", "-u", "/x/loadmon.py", "--expect", str(args.lines)],
        stdout=subprocess.PIPE, text=True)
    ready = json.loads(monitor.stdout.readline())
    assert ready["event"] == "ready", ready

    payload = make_lines(args.lines, args.seed)
    appended_at = time.time()
    subprocess.run(["docker", "exec", "-i", nginx, "sh", "-c", f"cat >> {LOG_FILE}"],
                   input=payload, text=True, check=True)
    append_seconds = time.time() - appended_at

    samples = []
    for line in monitor.stdout:
        row = json.loads(line)
        if row["event"] == "sample":
            samples.append(row)
        elif row["event"] == "done":
            break
    monitor.wait()

    n = args.lines
    stages = {}
    for topic, label in (("log.raw.ingested", "C1 thu thập"), ("log.normalized", "C2 chuẩn hoá"),
                         ("log.enriched", "C3 làm giàu")):
        done_at = first_time(samples, "produced", topic, n)
        stages[label] = {"seconds_to_all": done_at,
                         "throughput_per_second": round(n / done_at, 1) if done_at else None,
                         "produced": samples[-1]["produced"][topic] if samples else 0}
    detector_done = next((s["t"] for s in samples if s["produced"]["log.enriched"] >= n and s["lag"]["detector"] <= 0),
                         None)
    stages["detector"] = {"seconds_to_all": detector_done,
                          "throughput_per_second": round(n / detector_done, 1) if detector_done else None}
    result = {
        "lines": n, "append_seconds": round(append_seconds, 2), "stages": stages,
        "max_lag": {g: max(s["lag"][g] for s in samples) for g in ("c2-parser", "c3-enrichment", "detector")}
        if samples else {},
        "attack_detected": samples[-1]["produced"]["attack.detected"] if samples else 0,
        "alert_triggered": samples[-1]["produced"]["alert.triggered"] if samples else 0,
    }
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "loadtest.json"), "w", encoding="utf-8") as handle:
        json.dump({**result, "samples": samples}, handle, ensure_ascii=False)
    md = [f"Ghi dồn {n} dòng log trong {result['append_seconds']} s.", "",
          "| Khâu | Thời gian xử lý hết (s) | Thông lượng (message/s) |", "|---|---|---|"]
    for label, stage in stages.items():
        md.append(f"| {label} | {stage['seconds_to_all']} | {stage['throughput_per_second']} |")
    md += ["", "Lag lớn nhất: " + ", ".join(f"{g} {v}" for g, v in result["max_lag"].items()),
           f"attack.detected sinh ra: {result['attack_detected']}, alert.triggered: {result['alert_triggered']}."]
    with open(os.path.join(args.out, "loadtest.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
