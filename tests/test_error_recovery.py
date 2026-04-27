"""에러 복구 및 복원력 시스템 테스트."""

import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.error_recovery import (  # noqa: E402
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    ErrorLogger,
    ErrorRecovery,
)


@pytest.fixture
def tmp_db():
    """임시 DB 파일 경로를 반환한다."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.unlink(path)
        except PermissionError:
            pass  # Windows: file still locked by SQLite


@pytest.fixture
def error_logger(tmp_db):
    return ErrorLogger(db_path=tmp_db)


@pytest.fixture
def recovery(tmp_db):
    return ErrorRecovery(db_path=tmp_db)


# --- CircuitBreaker 테스트 ---


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED

    def test_successful_call(self):
        cb = CircuitBreaker(name="test")
        result = cb.call(lambda: 42)
        assert result == 42
        assert cb.state == CircuitState.CLOSED

    def test_failure_below_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        for _ in range(2):
            with pytest.raises(ValueError):
                cb.call(self._failing_func)
        assert cb.state == CircuitState.CLOSED

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        for _ in range(3):
            with pytest.raises(ValueError):
                cb.call(self._failing_func)
        assert cb.state == CircuitState.OPEN

    def test_open_rejects_calls(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)
        with pytest.raises(ValueError):
            cb.call(self._failing_func)
        assert cb.state == CircuitState.OPEN
        with pytest.raises(CircuitBreakerOpenError):
            cb.call(lambda: 42)

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=0.1)
        with pytest.raises(ValueError):
            cb.call(self._failing_func)
        assert cb.state == CircuitState.OPEN
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=0.1)
        with pytest.raises(ValueError):
            cb.call(self._failing_func)
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        result = cb.call(lambda: "ok")
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=0.1)
        with pytest.raises(ValueError):
            cb.call(self._failing_func)
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        with pytest.raises(ValueError):
            cb.call(self._failing_func)
        assert cb.state == CircuitState.OPEN

    def test_reset(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)
        with pytest.raises(ValueError):
            cb.call(self._failing_func)
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    def test_get_status(self):
        cb = CircuitBreaker(name="mybreaker", failure_threshold=5, reset_timeout=30)
        status = cb.get_status()
        assert status["name"] == "mybreaker"
        assert status["state"] == "closed"
        assert status["failure_threshold"] == 5
        assert status["reset_timeout"] == 30

    @staticmethod
    def _failing_func():
        raise ValueError("test error")


# --- ErrorLogger 테스트 ---


class TestErrorLogger:
    def test_log_and_retrieve(self, error_logger):
        error_logger.log_error("ValueError", "/api/chat", "something broke")
        errors = error_logger.get_recent_errors(limit=10)
        assert len(errors) == 1
        assert errors[0]["error_type"] == "ValueError"
        assert errors[0]["endpoint"] == "/api/chat"
        assert errors[0]["message"] == "something broke"

    def test_recent_errors_limit(self, error_logger):
        for i in range(10):
            error_logger.log_error("Error", "/test", f"error {i}")
        errors = error_logger.get_recent_errors(limit=5)
        assert len(errors) == 5

    def test_recent_errors_order(self, error_logger):
        error_logger.log_error("Error", "/test", "first")
        error_logger.log_error("Error", "/test", "second")
        errors = error_logger.get_recent_errors(limit=10)
        assert errors[0]["message"] == "second"  # most recent first
        assert errors[1]["message"] == "first"

    def test_error_rate(self, error_logger):
        for _ in range(5):
            error_logger.log_error("Error", "/test", "rate test")
        rate = error_logger.get_error_rate(minutes=60)
        assert rate["total_errors"] == 5
        assert rate["period_minutes"] == 60
        assert rate["errors_per_minute"] > 0

    def test_error_stats(self, error_logger):
        error_logger.log_error("ValueError", "/api/chat", "val error")
        error_logger.log_error("ValueError", "/api/chat", "val error 2")
        error_logger.log_error("TypeError", "/api/faq", "type error")
        stats = error_logger.get_error_stats()
        assert stats["by_type"]["ValueError"] == 2
        assert stats["by_type"]["TypeError"] == 1
        assert stats["by_endpoint"]["/api/chat"] == 2
        assert stats["by_endpoint"]["/api/faq"] == 1
        assert stats["total"] == 3

    def test_cleanup(self, error_logger):
        error_logger.log_error("Error", "/test", "old error")
        # Manually backdate the created_at
        conn = error_logger._get_conn()
        old_time = time.time() - (31 * 86400)  # 31 days ago
        conn.execute("UPDATE error_logs SET created_at = ?", (old_time,))
        conn.commit()
        deleted = error_logger.cleanup(days=30)
        assert deleted == 1
        errors = error_logger.get_recent_errors()
        assert len(errors) == 0

    def test_log_with_stack_trace(self, error_logger):
        error_logger.log_error(
            "RuntimeError", "/api/test", "boom", stack_trace="Traceback..."
        )
        errors = error_logger.get_recent_errors()
        assert errors[0]["stack_trace"] == "Traceback..."


# --- ErrorRecovery 테스트 ---


class TestRetry:
    def test_retry_succeeds_first_try(self, recovery):
        call_count = {"n": 0}

        def good_func():
            call_count["n"] += 1
            return "ok"

        wrapped = recovery.with_retry(good_func, max_retries=3, backoff=0.01)
        result = wrapped()
        assert result == "ok"
        assert call_count["n"] == 1

    def test_retry_succeeds_after_failures(self, recovery):
        call_count = {"n": 0}

        def flaky_func():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ValueError("not yet")
            return "success"

        wrapped = recovery.with_retry(flaky_func, max_retries=3, backoff=0.01)
        result = wrapped()
        assert result == "success"
        assert call_count["n"] == 3

    def test_retry_exhausted_raises(self, recovery):
        def bad_func():
            raise RuntimeError("always fails")

        wrapped = recovery.with_retry(bad_func, max_retries=2, backoff=0.01)
        with pytest.raises(RuntimeError, match="always fails"):
            wrapped()

    def test_retry_logs_error_on_exhaustion(self, recovery):
        def bad_func():
            raise RuntimeError("logged failure")

        wrapped = recovery.with_retry(bad_func, max_retries=1, backoff=0.01)
        with pytest.raises(RuntimeError):
            wrapped()
        errors = recovery.error_logger.get_recent_errors()
        assert len(errors) == 1
        assert errors[0]["error_type"] == "RuntimeError"

    def test_retry_as_decorator(self, recovery):
        call_count = {"n": 0}

        @recovery.with_retry(max_retries=2, backoff=0.01)
        def decorated():
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise ValueError("retry me")
            return "decorated ok"

        result = decorated()
        assert result == "decorated ok"


class TestFallback:
    def test_fallback_not_used_on_success(self, recovery):
        def primary():
            return "primary result"

        def fallback():
            return "fallback result"

        wrapped = recovery.with_fallback(primary, fallback)
        assert wrapped() == "primary result"

    def test_fallback_used_on_failure(self, recovery):
        def primary():
            raise ValueError("primary fails")

        def fallback():
            return "fallback result"

        wrapped = recovery.with_fallback(primary, fallback)
        assert wrapped() == "fallback result"

    def test_fallback_logs_error(self, recovery):
        def primary():
            raise TypeError("oops")

        wrapped = recovery.with_fallback(primary, lambda: "safe")
        wrapped()
        errors = recovery.error_logger.get_recent_errors()
        assert len(errors) == 1
        assert "fallback" in errors[0]["message"].lower()

    def test_fallback_passes_args(self, recovery):
        def primary(x, y):
            raise ValueError("fail")

        def fallback(x, y):
            return x + y

        wrapped = recovery.with_fallback(primary, fallback)
        assert wrapped(3, 4) == 7


class TestCircuitBreakerIntegration:
    def test_circuit_breaker_decorator(self, recovery):
        call_count = {"n": 0}

        @recovery.with_circuit_breaker(failure_threshold=2, reset_timeout=0.1)
        def fragile():
            call_count["n"] += 1
            raise ValueError("fail")

        for _ in range(2):
            with pytest.raises(ValueError):
                fragile()

        with pytest.raises(CircuitBreakerOpenError):
            fragile()

    def test_circuit_breaker_wrapper(self, recovery):
        def fragile():
            raise ValueError("fail")

        wrapped = recovery.with_circuit_breaker(
            fragile, name="test_svc", failure_threshold=2, reset_timeout=0.1
        )

        for _ in range(2):
            with pytest.raises(ValueError):
                wrapped()

        with pytest.raises(CircuitBreakerOpenError):
            wrapped()

    def test_get_circuit_status(self, recovery):
        @recovery.with_circuit_breaker(name="svc_a", failure_threshold=3, reset_timeout=30)
        def svc_a():
            return "ok"

        svc_a()
        status = recovery.get_circuit_status()
        assert "svc_a" in status
        assert status["svc_a"]["state"] == "closed"

    def test_get_error_stats(self, recovery):
        recovery.error_logger.log_error("Err1", "/a", "msg1")
        recovery.error_logger.log_error("Err2", "/b", "msg2")
        stats = recovery.get_error_stats()
        assert stats["total"] == 2


# --- API 엔드포인트 테스트 ---


class TestErrorRecoveryAPI:
    @pytest.fixture
    def client(self):
        from web_server import app

        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    @pytest.fixture
    def auth_header(self):
        from web_server import jwt_auth

        token = jwt_auth.generate_token("admin", role="admin")
        return {"Authorization": f"Bearer {token}"}

    def test_errors_endpoint(self, client, auth_header):
        res = client.get("/api/admin/errors", headers=auth_header)
        assert res.status_code == 200
        data = res.get_json()
        assert "errors" in data
        assert "count" in data

    def test_error_stats_endpoint(self, client, auth_header):
        res = client.get("/api/admin/errors/stats", headers=auth_header)
        assert res.status_code == 200
        data = res.get_json()
        assert "by_type" in data
        assert "error_rate" in data

    def test_circuits_endpoint(self, client, auth_header):
        res = client.get("/api/admin/circuits", headers=auth_header)
        assert res.status_code == 200
        data = res.get_json()
        assert "circuits" in data

    def test_errors_endpoint_no_auth_in_testing(self, client):
        # TESTING mode skips JWT auth, so endpoint should still return 200
        res = client.get("/api/admin/errors")
        assert res.status_code == 200

    def test_errors_with_limit_param(self, client, auth_header):
        res = client.get("/api/admin/errors?limit=5", headers=auth_header)
        assert res.status_code == 200
        data = res.get_json()
        assert "errors" in data

    def test_circuits_empty(self, client, auth_header):
        res = client.get("/api/admin/circuits", headers=auth_header)
        data = res.get_json()
        assert isinstance(data["circuits"], dict)
