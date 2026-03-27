"""보세전시장 챗봇 Gunicorn 설정.

사용법:
    gunicorn -c deploy/gunicorn_config.py web_server:app
"""

import multiprocessing
import os

# 서버 소켓
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8080")

# 워커 프로세스
workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "sync"
worker_connections = 1000

# 타임아웃
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 30))
graceful_timeout = 30
keepalive = 5

# 프리로드 (메모리 절약: FAQ 데이터를 한 번만 로드)
preload_app = True

# 로깅
accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "/app/logs/gunicorn_access.log")
errorlog = os.environ.get("GUNICORN_ERROR_LOG", "/app/logs/gunicorn_error.log")
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# 프로세스 이름
proc_name = "bonded-exhibition-chatbot"

# 보안: 요청 크기 제한 (5MB)
limit_request_body = 5 * 1024 * 1024
limit_request_line = 8190

# 임시 파일 디렉토리
tmp_upload_dir = None

# 서버 훅
def on_starting(server):
    """서버 시작 시 로그 디렉토리 생성."""
    os.makedirs("/app/logs", exist_ok=True)


def post_fork(server, worker):
    """워커 포크 후 로깅."""
    server.log.info(f"Worker spawned (pid: {worker.pid})")


def on_exit(server):
    """서버 종료 시 로깅."""
    server.log.info("Shutting down gracefully...")
