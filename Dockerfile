FROM python:3.11-slim

WORKDIR /app

# 시스템 패키지 (sqlite3: 백업용)
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs data

# 환경변수 기본값
ENV CHATBOT_PORT=8080 \
    CHATBOT_HOST=0.0.0.0 \
    CHATBOT_DEBUG=false \
    CHATBOT_LOG_LEVEL=INFO \
    CHATBOT_DB_PATH=logs/chat_logs.db \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# 헬스체크 (30초 간격, 10초 타임아웃, 3회 재시도)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python deploy/healthcheck.py --host 127.0.0.1 --port 8080 || exit 1

CMD ["gunicorn", "-c", "deploy/gunicorn_config.py", "web_server:app"]
