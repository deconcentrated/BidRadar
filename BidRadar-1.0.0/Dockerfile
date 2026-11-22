FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-pol antiword \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY *.py *.html *.sh ./
COPY konfiguracja/ konfiguracja/
COPY static/ static/
COPY dokumenty/ dokumenty/
COPY przyklady_testu/ przyklady_testu/
ENV RM_HOST=0.0.0.0 RM_PORT=8000 RM_DB=/dane/bidradar.db RM_OUT=/dane/out RM_XLSX=/paczka/Risk_Miner_dane_projekty.xlsx \
    PYTHONUNBUFFERED=1
VOLUME ["/dane"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=4)"
CMD ["python3", "server.py"]
