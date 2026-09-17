FROM waf/base:dev
COPY --chown=waf:waf common /app/common
# COPY cả thư mục p5-dashboard (gồm index.html) để service tự phục vụ UI ở "/".
COPY --chown=waf:waf p5-dashboard /app/p5-dashboard
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
CMD ["python", "/app/p5-dashboard/main.py"]
