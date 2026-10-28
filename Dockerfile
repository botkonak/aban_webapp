FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WEBAPP_HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
RUN mkdir -p /app/data/webapp_uploads

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import os,ssl,urllib.request; p=os.getenv('PORT','8000'); tls=bool(os.getenv('SSL_CERT') and os.getenv('SSL_KEY')); c=ssl._create_unverified_context() if tls else None; urllib.request.urlopen(('https' if tls else 'http')+'://127.0.0.1:'+p+'/health', timeout=5, context=c)"

CMD ["python", "main.py"]
