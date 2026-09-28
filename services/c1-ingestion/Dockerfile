FROM waf/base:dev
COPY --chown=waf:waf common /app/common
COPY --chown=waf:waf c1-ingestion /app/c1-ingestion
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import os,time,sys; sys.exit(0 if time.time()-os.path.getmtime('/tmp/alive')<30 else 1)"
CMD ["python", "/app/c1-ingestion/main.py"]
