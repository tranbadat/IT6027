FROM waf/base:dev
COPY --chown=waf:waf common /app/common
COPY --chown=waf:waf p2-auth /app/p2-auth
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
CMD ["python", "/app/p2-auth/main.py"]
