"""보안 모듈 테스트."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from src.security import APIKeyAuth, RateLimiter, sanitize_input


# ---------------------------------------------------------------------------
# sanitize_input 테스트
# ---------------------------------------------------------------------------
class TestSanitizeInput:
    def test_remove_html_tags(self):
        result = sanitize_input("<script>alert('xss')</script>hello")
        assert "<script>" not in result
        assert "alert" in result
        assert "hello" in result

    def test_remove_html_complex(self):
        result = sanitize_input('<b>bold</b> <a href="http://x">link</a>')
        assert "<b>" not in result
        assert "<a" not in result
        assert "bold" in result
        assert "link" in result

    def test_remove_control_characters(self):
        result = sanitize_input("hello\x00\x01\x02world")
        assert result == "helloworld"
        assert "\x00" not in result
        assert "\x01" not in result

    def test_preserve_tab_and_newline(self):
        result = sanitize_input("hello\nworld")
        assert "\n" in result

    def test_collapse_whitespace(self):
        result = sanitize_input("hello     world")
        assert result == "hello world"

    def test_max_length(self):
        long_text = "a" * 3000
        result = sanitize_input(long_text, max_length=2000)
        assert len(result) == 2000

    def test_default_max_length(self):
        long_text = "b" * 2500
        result = sanitize_input(long_text)
        assert len(result) == 2000

    def test_strip_whitespace(self):
        result = sanitize_input("  hello  ")
        assert result == "hello"

    def test_non_string_input(self):
        assert sanitize_input(None) == ""
        assert sanitize_input(123) == ""

    def test_empty_string(self):
        assert sanitize_input("") == ""

    def test_normal_text_unchanged(self):
        text = "보세전시장 반입 절차를 알려주세요."
        assert sanitize_input(text) == text


# ---------------------------------------------------------------------------
# APIKeyAuth 테스트
# ---------------------------------------------------------------------------
class TestAPIKeyAuth:
    def _make_app(self, api_keys=None, exempt_paths=None):
        app = Flask(__name__)
        app.config["TESTING"] = True

        auth = APIKeyAuth(app, api_keys=api_keys, exempt_paths=exempt_paths)

        @app.route("/api/chat", methods=["POST"])
        def chat():
            return {"message": "ok"}

        @app.route("/api/health")
        def health():
            return {"status": "ok"}

        @app.route("/")
        def index():
            return "index"

        return app, auth

    def test_disabled_when_no_keys(self):
        """API Key가 없으면 인증이 비활성화된다."""
        app, auth = self._make_app()
        assert not auth.enabled
        with app.test_client() as client:
            res = client.post("/api/chat", json={"query": "test"})
            assert res.status_code == 200

    def test_enabled_with_keys(self):
        """API Key가 설정되면 인증이 활성화된다."""
        app, auth = self._make_app(api_keys=["test-key-123"])
        assert auth.enabled

    def test_valid_key_header(self):
        """유효한 API Key(헤더)로 요청 시 통과한다."""
        app, _ = self._make_app(api_keys=["test-key-123"])
        with app.test_client() as client:
            res = client.post(
                "/api/chat",
                json={"query": "test"},
                headers={"X-API-Key": "test-key-123"},
            )
            assert res.status_code == 200

    def test_valid_key_query_param(self):
        """유효한 API Key(쿼리 파라미터)로 요청 시 통과한다."""
        app, _ = self._make_app(api_keys=["test-key-123"])
        with app.test_client() as client:
            res = client.post(
                "/api/chat?api_key=test-key-123",
                json={"query": "test"},
            )
            assert res.status_code == 200

    def test_missing_key_returns_401(self):
        """API Key 없이 요청 시 401을 반환한다."""
        app, _ = self._make_app(api_keys=["test-key-123"])
        with app.test_client() as client:
            res = client.post("/api/chat", json={"query": "test"})
            assert res.status_code == 401
            data = res.get_json()
            assert "API Key" in data["error"]

    def test_invalid_key_returns_403(self):
        """잘못된 API Key로 요청 시 403을 반환한다."""
        app, _ = self._make_app(api_keys=["test-key-123"])
        with app.test_client() as client:
            res = client.post(
                "/api/chat",
                json={"query": "test"},
                headers={"X-API-Key": "wrong-key"},
            )
            assert res.status_code == 403

    def test_exempt_paths_bypass_auth(self):
        """면제 경로는 인증 없이 접근 가능하다."""
        app, _ = self._make_app(api_keys=["test-key-123"])
        with app.test_client() as client:
            res = client.get("/api/health")
            assert res.status_code == 200

            res = client.get("/")
            assert res.status_code == 200

    def test_multiple_keys(self):
        """여러 API Key가 모두 인식된다."""
        app, _ = self._make_app(api_keys=["key-1", "key-2"])
        with app.test_client() as client:
            res1 = client.post(
                "/api/chat",
                json={"query": "test"},
                headers={"X-API-Key": "key-1"},
            )
            res2 = client.post(
                "/api/chat",
                json={"query": "test"},
                headers={"X-API-Key": "key-2"},
            )
            assert res1.status_code == 200
            assert res2.status_code == 200

    def test_env_variable_loading(self, monkeypatch):
        """환경변수 CHATBOT_API_KEYS에서 키를 로드한다."""
        monkeypatch.setenv("CHATBOT_API_KEYS", "env-key-1,env-key-2")
        auth = APIKeyAuth()
        assert auth.enabled
        assert "env-key-1" in auth.api_keys
        assert "env-key-2" in auth.api_keys


# ---------------------------------------------------------------------------
# RateLimiter 테스트
# ---------------------------------------------------------------------------
class TestRateLimiter:
    def test_allows_within_limit(self):
        """제한 내 요청은 허용된다."""
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        for _ in range(5):
            assert limiter.is_allowed("127.0.0.1") is True

    def test_blocks_over_limit(self):
        """제한 초과 시 요청을 차단한다."""
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            limiter.is_allowed("127.0.0.1")
        assert limiter.is_allowed("127.0.0.1") is False

    def test_different_ips_independent(self):
        """서로 다른 IP는 독립적으로 카운트된다."""
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        limiter.is_allowed("10.0.0.1")
        limiter.is_allowed("10.0.0.1")
        assert limiter.is_allowed("10.0.0.1") is False
        assert limiter.is_allowed("10.0.0.2") is True

    def test_window_expiry(self):
        """윈도우가 지나면 다시 허용된다."""
        limiter = RateLimiter(max_requests=2, window_seconds=1)
        limiter.is_allowed("127.0.0.1")
        limiter.is_allowed("127.0.0.1")
        assert limiter.is_allowed("127.0.0.1") is False
        time.sleep(1.1)
        assert limiter.is_allowed("127.0.0.1") is True

    def test_reset_specific_ip(self):
        """특정 IP의 기록을 초기화한다."""
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        limiter.is_allowed("127.0.0.1")
        limiter.is_allowed("127.0.0.1")
        limiter.reset("127.0.0.1")
        assert limiter.is_allowed("127.0.0.1") is True

    def test_reset_all(self):
        """모든 기록을 초기화한다."""
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        limiter.is_allowed("10.0.0.1")
        limiter.is_allowed("10.0.0.2")
        limiter.reset()
        assert limiter.is_allowed("10.0.0.1") is True
        assert limiter.is_allowed("10.0.0.2") is True

    def test_429_response_in_web_server(self):
        """웹 서버에서 Rate Limit 초과 시 429를 반환한다."""
        from web_server import app, rate_limiter

        rate_limiter.max_requests = 2
        rate_limiter.window_seconds = 60
        rate_limiter.reset()

        app.config["TESTING"] = True
        with app.test_client() as client:
            for _ in range(2):
                client.post("/api/chat", json={"query": "테스트"})
            res = client.post("/api/chat", json={"query": "테스트"})
            assert res.status_code == 429
            data = res.get_json()
            assert "요청이 너무 많습니다" in data["error"]

        # 테스트 후 원복
        rate_limiter.max_requests = 60
        rate_limiter.reset()
