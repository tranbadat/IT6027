FROM waf/base:dev
COPY --chown=waf:waf common /app/common
COPY --chown=waf:waf d3-anomaly-scorer /app/d3-anomaly-scorer
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import os,time,sys; sys.exit(0 if time.time()-os.path.getmtime('/tmp/alive')<30 else 1)"
CMD ["python", "/app/d3-anomaly-scorer/main.py"]
