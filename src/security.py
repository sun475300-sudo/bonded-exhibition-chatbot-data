"""보안 미들웨어 모듈.

API Key 인증, Rate Limiting, 입력 살균 기능을 제공한다.
"""

import json
import logging
import os
import re
import time
from functools import wraps

from flask import request, jsonify

logger = logging.getLogger("chatbot.security")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECURITY_CONFIG_PATH = os.path.join(BASE_DIR, "config", "security_config.json")


def _load_security_config():
    """보안 설정 파일을 로드한다."""
    if os.path.exists(SECURITY_CONFIG_PATH):
        try:
            with open(SECURITY_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"보안 설정 파일 로드 실패: {e}")
    return {}


class APIKeyAuth:
    """API Key 기반 인증 미들웨어.

    환경변수 CHATBOT_API_KEYS(쉼표 구분) 또는 config에서 API Key를 로드한다.
    API Key가 설정되어 있지 않으면 인증을 비활성화한다(개발 모드 호환).
    """

    DEFAULT_EXEMPT_PATHS = [
        "/",
        "/api/health",
        "/admin",
        "/static",
        "/manifest.json",
        "/sw.js",
    ]

    def __init__(self, app=None, api_keys=None, exempt_paths=None):
        self.api_keys = set()
        self.exempt_paths = list(exempt_paths or self.DEFAULT_EXEMPT_PATHS)
        self._enabled = False

        if api_keys:
            self.api_keys = set(api_keys)
            self._enabled = True
        else:
            self._load_keys()

        if app is not None:
            self.init_app(app)

    def _load_keys(self):
        """환경변수 또는 설정 파일에서 API Key를 로드한다."""
        # 환경변수 우선
        env_keys = os.environ.get("CHATBOT_API_KEYS", "").strip()
        if env_keys:
            self.api_keys = {k.strip() for k in env_keys.split(",") if k.strip()}
            self._enabled = True
            return

        # 설정 파일에서 로드
        config = _load_security_config()
        config_keys = config.get("api_keys", {})
        if config_keys.get("enabled", False):
            keys = config_keys.get("keys", [])
            if keys:
                self.api_keys = set(keys)
                self._enabled = True

    def init_app(self, app):
        """Flask 앱에 before_request 훅을 등록한다."""
        app.before_request(self._check_api_key)

    def _is_exempt(self, path):
        """경로가 인증 면제 대상인지 확인한다."""
        for exempt in self.exempt_paths:
            if path == exempt or path.startswith(exempt + "/"):
                return True
        return False

    def _check_api_key(self):
        """요청의 API Key를 검증한다."""
        if not self._enabled:
            return None

        path = request.path
        if self._is_exempt(path):
            return None

        # 헤더 또는 쿼리 파라미터에서 API Key 추출
        api_key = request.headers.get("X-API-Key") or request.args.get("api_key")

        if not api_key:
            return jsonify({"error": "API Key가 필요합니다."}), 401

        if api_key not in self.api_keys:
            return jsonify({"error": "유효하지 않은 API Key입니다."}), 403

        return None

    @property
    def enabled(self):
        return self._enabled


class RateLimiter:
    """IP 기반 레이트 리미터.

    메모리 기반 딕셔너리로 요청 횟수를 추적하며, TTL 자동 정리를 수행한다.
    """

    def __init__(self, max_requests=60, window_seconds=60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests = {}  # {ip: [timestamp, ...]}
        self._last_cleanup = time.time()
        self._cleanup_interval = 300  # 5분마다 정리

    def _cleanup(self):
        """만료된 요청 기록을 정리한다."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        cutoff = now - self.window_seconds
        expired_ips = []
        for ip, timestamps in self._requests.items():
            self._requests[ip] = [t for t in timestamps if t > cutoff]
            if not self._requests[ip]:
                expired_ips.append(ip)
        for ip in expired_ips:
            del self._requests[ip]
        self._last_cleanup = now

    def is_allowed(self, ip):
        """해당 IP의 요청이 허용되는지 확인한다."""
        now = time.time()
        self._cleanup()

        cutoff = now - self.window_seconds
        if ip not in self._requests:
            self._requests[ip] = []

        # 윈도우 내 요청만 유지
        self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]

        if len(self._requests[ip]) >= self.max_requests:
            return False

        self._requests[ip].append(now)
        return True

    def reset(self, ip=None):
        """레이트 리밋 기록을 초기화한다."""
        if ip:
            self._requests.pop(ip, None)
        else:
            self._requests.clear()


def sanitize_input(text, max_length=2000):
    """사용자 입력을 살균한다.

    - HTML 태그 제거
    - 제어 문자 제거 (탭, 개행 제외)
    - 연속 공백 정리
    - 최대 길이 제한

    Args:
        text: 원본 입력 문자열
        max_length: 최대 허용 길이 (기본 2000자)

    Returns:
        살균된 문자열
    """
    if not isinstance(text, str):
        return ""

    # HTML 태그 제거
    text = re.sub(r"<[^>]+>", "", text)

    # 제어 문자 제거 (탭 \t, 개행 \n, \r 제외)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # 연속 공백 정리 (개행은 유지)
    text = re.sub(r"[^\S\n]+", " ", text)

    # 앞뒤 공백 제거
    text = text.strip()

    # 최대 길이 제한
    if len(text) > max_length:
        text = text[:max_length]

    return text
