# Image nền dùng chung cho mọi service Python (C1–C3, D1–D3, P1–P5).
# Build: docker build -t waf/base:dev services/base  (make build-base tự làm việc này).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --create-home --uid 10001 waf

USER waf
