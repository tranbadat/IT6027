"""Theo dõi offset Kafka trong lúc kiểm thử tải (chạy trong image waf/base:dev, nối vào network của Kafka).

In mỗi 0,25 giây một dòng JSON: số message mới của từng topic so với lúc bắt đầu và lag của từng
consumer group. Dừng khi topic cuối (log.enriched) đã nhận đủ --expect message và detector hết lag,
hoặc hết --timeout giây.
"""
import argparse
import json
import sys
import time

from confluent_kafka import Consumer, TopicPartition

STAGES = [  # (topic do khâu trước phát, consumer group của khâu sau)
    ("log.raw.ingested", "c2-parser"),
    ("log.normalized", "c3-enrichment"),
    ("log.enriched", "detector"),
]
EXTRA_TOPICS = ["attack.detected", "alert.triggered"]


def partitions(client, topic):
    meta = client.list_topics(topic, timeout=10).topics[topic]
    return [TopicPartition(topic, p) for p in meta.partitions]


def end_offset(client, topic):
    return sum(client.get_watermark_offsets(tp, timeout=10)[1] for tp in partitions(client, topic))


def committed(bootstrap, group, topic, probe):
    consumer = Consumer({"bootstrap.servers": bootstrap, "group.id": group, "enable.auto.commit": False})
    try:
        return sum(max(tp.offset, 0) for tp in consumer.committed(partitions(probe, topic), timeout=10))
    finally:
        consumer.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", default="kafka:19092")
    parser.add_argument("--expect", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()

    probe = Consumer({"bootstrap.servers": args.bootstrap, "group.id": "loadtest-monitor"})
    topics = [t for t, _ in STAGES] + EXTRA_TOPICS
    base = {t: end_offset(probe, t) for t in topics}
    print(json.dumps({"event": "ready", "base": base}), flush=True)
    start = time.time()
    while time.time() - start < args.timeout:
        now = time.time()
        produced = {t: end_offset(probe, t) - base[t] for t in topics}
        lags = {group: end_offset(probe, topic) - committed(args.bootstrap, group, topic, probe)
                for topic, group in STAGES}
        print(json.dumps({"event": "sample", "t": round(now - start, 2), "produced": produced, "lag": lags}),
              flush=True)
        if produced["log.enriched"] >= args.expect and lags["detector"] <= 0:
            break
        time.sleep(0.25)
    probe.close()
    print(json.dumps({"event": "done"}), flush=True)


if __name__ == "__main__":
    sys.exit(main())
