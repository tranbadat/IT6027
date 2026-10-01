# Một Dockerfile, ba target độc lập: c1, c2, c3 (mỗi service build/chạy riêng).
#   docker build --target c1 -t waf/c1-ingestion .
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY wafcollect ./wafcollect
RUN useradd --system --uid 10001 waf && mkdir -p /var/lib/wafcollect && chown waf /var/lib/wafcollect
USER waf

FROM base AS c1
VOLUME ["/var/lib/wafcollect"]
CMD ["python", "-m", "wafcollect.c1_ingestion"]

FROM base AS c2
CMD ["python", "-m", "wafcollect.c2_parsing"]

FROM base AS c3
CMD ["python", "-m", "wafcollect.c3_enrichment"]
