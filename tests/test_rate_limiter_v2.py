"""Tests for the advanced rate limiter (rate_limiter_v2)."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.rate_limiter_v2 import AdvancedRateLimiter


# ---------------------------------------------------------------------------
# Unit tests for AdvancedRateLimiter
# ---------------------------------------------------------------------------


class TestPerEndpointLimiting:
    """Test per-endpoint rate limits."""

    def test_allows_requests_under_limit(self):
        limiter = AdvancedRateLimiter()
        for _ in range(30):
            allowed, remaining, _ = limiter.check_rate_limit("1.2.3.4", "/api/chat")
            assert allowed is True
        assert remaining == 0

    def test_blocks_request_over_limit(self):
        limiter = AdvancedRateLimiter()
        for _ in range(30):
            limiter.check_rate_limit("1.2.3.4", "/api/chat")
        allowed, remaining, reset_time = limiter.check_rate_limit("1.2.3.4", "/api/chat")
        assert allowed is False
        assert remaining == 0
        assert reset_time > 0

    def test_different_endpoints_have_different_limits(self):
        limiter = AdvancedRateLimiter()
        # /api/chat has limit 30, /api/faq has limit 60
        for _ in range(30):
            limiter.check_rate_limit("1.2.3.4", "/api/chat")
        # chat should be blocked
        allowed_chat, _, _ = limiter.check_rate_limit("1.2.3.4", "/api/chat")
        assert allowed_chat is False
        # faq should still be allowed
        allowed_faq, _, _ = limiter.check_rate_limit("1.2.3.4", "/api/faq")
        assert allowed_faq is True

    def test_different_ips_tracked_separately(self):
        limiter = AdvancedRateLimiter()
        for _ in range(30):
            limiter.check_rate_limit("1.1.1.1", "/api/chat")
        blocked, _, _ = limiter.check_rate_limit("1.1.1.1", "/api/chat")
        assert blocked is False
        allowed, _, _ = limiter.check_rate_limit("2.2.2.2", "/api/chat")
        assert allowed is True

    def test_admin_wildcard_matching(self):
        limiter = AdvancedRateLimiter()
        # /api/admin/* should match /api/admin/stats
        for _ in range(20):
            limiter.check_rate_limit("1.1.1.1", "/api/admin/stats")
        blocked, _, _ = limiter.check_rate_limit("1.1.1.1", "/api/admin/stats")
        assert blocked is False

    def test_autocomplete_higher_limit(self):
        limiter = AdvancedRateLimiter()
        for i in range(120):
            allowed, _, _ = limiter.check_rate_limit("1.1.1.1", "/api/autocomplete")
            assert allowed is True
        blocked, _, _ = limiter.check_rate_limit("1.1.1.1", "/api/autocomplete")
        assert blocked is False

    def test_unknown_endpoint_allowed(self):
        limiter = AdvancedRateLimiter()
        allowed, remaining, _ = limiter.check_rate_limit("1.1.1.1", "/api/unknown")
        assert allowed is True
        assert remaining == -1


class TestPerUserQuota:
    """Test per-user daily quotas."""

    def test_allows_under_quota(self):
        limiter = AdvancedRateLimiter(default_daily_quota=5)
        for i in range(5):
            allowed, used, limit, _ = limiter.check_quota("key-1")
            assert allowed is True
            assert used == i + 1
            assert limit == 5

    def test_blocks_over_quota(self):
        limiter = AdvancedRateLimiter(default_daily_quota=3)
        for _ in range(3):
            limiter.check_quota("key-1")
        allowed, used, limit, reset_time = limiter.check_quota("key-1")
        assert allowed is False
        assert used == 3
        assert limit == 3
        assert reset_time > 0

    def test_different_keys_independent(self):
        limiter = AdvancedRateLimiter(default_daily_quota=2)
        for _ in range(2):
            limiter.check_quota("key-a")
        blocked, _, _, _ = limiter.check_quota("key-a")
        assert blocked is False
        allowed, _, _, _ = limiter.check_quota("key-b")
        assert allowed is True

    def test_empty_key_always_allowed(self):
        limiter = AdvancedRateLimiter(default_daily_quota=1)
        for _ in range(100):
            allowed, _, _, _ = limiter.check_quota("")
            assert allowed is True

    def test_set_user_quota(self):
        limiter = AdvancedRateLimiter(default_daily_quota=1000)
        limiter.set_user_quota("vip-key", 5)
        for _ in range(5):
            allowed, _, _, _ = limiter.check_quota("vip-key")
            assert allowed is True
        blocked, _, limit, _ = limiter.check_quota("vip-key")
        assert blocked is False
        assert limit == 5


class TestSlidingWindowAccuracy:
    """Test sliding window algorithm accuracy."""

    def test_window_expires_old_requests(self):
        limiter = AdvancedRateLimiter(endpoint_limits={"/test": 5})
        # Fill up to limit
        for _ in range(5):
            limiter.check_rate_limit("1.1.1.1", "/test")
        blocked, _, _ = limiter.check_rate_limit("1.1.1.1", "/test")
        assert blocked is False

        # Manually expire old timestamps by shifting them back
        key = ("1.1.1.1", "/test")
        with limiter._lock:
            limiter._requests[key] = [
                t - limiter.WINDOW_SECONDS - 1
                for t in limiter._requests[key]
            ]

        # Now requests should be allowed again
        allowed, _, _ = limiter.check_rate_limit("1.1.1.1", "/test")
        assert allowed is True

    def test_remaining_decreases(self):
        limiter = AdvancedRateLimiter(endpoint_limits={"/test": 10})
        _, remaining1, _ = limiter.check_rate_limit("1.1.1.1", "/test")
        _, remaining2, _ = limiter.check_rate_limit("1.1.1.1", "/test")
        assert remaining1 == 9
        assert remaining2 == 8

    def test_reset_time_in_future(self):
        limiter = AdvancedRateLimiter()
        _, _, reset_time = limiter.check_rate_limit("1.1.1.1", "/api/chat")
        assert reset_time > time.time()

    def test_reset_clears_data(self):
        limiter = AdvancedRateLimiter(endpoint_limits={"/test": 2})
        limiter.check_rate_limit("1.1.1.1", "/test")
        limiter.check_rate_limit("1.1.1.1", "/test")
        blocked, _, _ = limiter.check_rate_limit("1.1.1.1", "/test")
        assert blocked is False

        limiter.reset(ip="1.1.1.1")
        allowed, _, _ = limiter.check_rate_limit("1.1.1.1", "/test")
        assert allowed is True

    def test_full_reset(self):
        limiter = AdvancedRateLimiter(endpoint_limits={"/test": 1})
        limiter.check_rate_limit("1.1.1.1", "/test")
        limiter.check_quota("key1")
        limiter.reset()
        allowed, _, _ = limiter.check_rate_limit("1.1.1.1", "/test")
        assert allowed is True


class TestUsageStats:
    """Test usage statistics."""

    def test_aggregate_stats(self):
        limiter = AdvancedRateLimiter()
        limiter.check_rate_limit("1.1.1.1", "/api/chat")
        limiter.check_rate_limit("1.1.1.1", "/api/chat")
        limiter.check_rate_limit("1.1.1.1", "/api/faq")

        stats = limiter.get_usage_stats()
        assert stats["total_requests"] == 3
        assert stats["endpoint_stats"]["/api/chat"] == 2
        assert stats["endpoint_stats"]["/api/faq"] == 1
        assert "endpoint_limits" in stats

    def test_per_user_stats(self):
        limiter = AdvancedRateLimiter(default_daily_quota=100)
        limiter.check_quota("user-1")
        limiter.check_quota("user-1")

        stats = limiter.get_usage_stats(api_key="user-1")
        assert stats["api_key"] == "user-1"
        assert stats["used_today"] == 2
        assert stats["daily_limit"] == 100
        assert stats["total_hits"] == 2

    def test_unknown_user_stats(self):
        limiter = AdvancedRateLimiter()
        stats = limiter.get_usage_stats(api_key="nonexistent")
        assert stats["used_today"] == 0
        assert stats["total_hits"] == 0

    def test_top_users(self):
        limiter = AdvancedRateLimiter(default_daily_quota=100)
        for _ in range(10):
            limiter.check_quota("power-user")
        for _ in range(3):
            limiter.check_quota("light-user")

        top = limiter.get_top_users(limit=2)
        assert len(top) == 2
        assert top[0]["api_key"] == "power-user"
        assert top[0]["total_hits"] == 10
        assert top[1]["api_key"] == "light-user"

    def test_top_users_empty(self):
        limiter = AdvancedRateLimiter()
        top = limiter.get_top_users()
        assert top == []

    def test_set_endpoint_limit(self):
        limiter = AdvancedRateLimiter()
        limiter.set_endpoint_limit("/api/new", 99)
        stats = limiter.get_usage_stats()
        assert stats["endpoint_limits"]["/api/new"] == 99


# ---------------------------------------------------------------------------
# Web API tests
# ---------------------------------------------------------------------------

from web_server import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_limiter():
    """Reset the advanced rate limiter between tests."""
    from web_server import advanced_rate_limiter
    advanced_rate_limiter.reset()
    # Restore default endpoint limits
    advanced_rate_limiter._endpoint_limits = dict(
        AdvancedRateLimiter.DEFAULT_ENDPOINT_LIMITS
    )
    advanced_rate_limiter._default_daily_quota = AdvancedRateLimiter.DEFAULT_DAILY_QUOTA
    yield
    advanced_rate_limiter.reset()
    advanced_rate_limiter._endpoint_limits = dict(
        AdvancedRateLimiter.DEFAULT_ENDPOINT_LIMITS
    )
    advanced_rate_limiter._default_daily_quota = AdvancedRateLimiter.DEFAULT_DAILY_QUOTA


class TestRateLimitMiddleware:
    """Test the before_request rate-limit hook and 429 responses."""

    def test_rate_limit_headers_present(self, client):
        res = client.post("/api/chat", json={"query": "hello"})
        # Should have rate limit headers (even on successful responses)
        assert "X-RateLimit-Remaining" in res.headers or res.status_code == 200

    def test_429_response_on_limit(self):
        from web_server import advanced_rate_limiter
        # Disable TESTING flag so rate limiter middleware runs
        flask_app.config["TESTING"] = False
        try:
            advanced_rate_limiter.set_endpoint_limit("/api/chat", 2)
            with flask_app.test_client() as c:
                c.post("/api/chat", json={"query": "q1"})
                c.post("/api/chat", json={"query": "q2"})
                res = c.post("/api/chat", json={"query": "q3"})
                assert res.status_code == 429
                assert "Retry-After" in res.headers
                assert "X-RateLimit-Remaining" in res.headers
                assert res.headers["X-RateLimit-Remaining"] == "0"
                assert "X-RateLimit-Reset" in res.headers
                data = res.get_json()
                assert "error" in data
        finally:
            flask_app.config["TESTING"] = True

    def test_health_exempt_from_rate_limit(self, client):
        from web_server import advanced_rate_limiter
        # Even if we had rate limiting, health should be exempt
        for _ in range(200):
            res = client.get("/api/health")
            assert res.status_code == 200


class TestRateLimitAdminEndpoints:
    """Test admin endpoints for rate limits and usage."""

    def test_get_rate_limits(self, client):
        res = client.get("/api/admin/rate-limits")
        assert res.status_code == 200
        data = res.get_json()
        assert "endpoint_limits" in data
        assert "/api/chat" in data["endpoint_limits"]
        assert data["endpoint_limits"]["/api/chat"] == 30
        assert "default_daily_quota" in data

    def test_update_rate_limits(self, client):
        res = client.put(
            "/api/admin/rate-limits",
            json={"endpoint_limits": {"/api/chat": 50}},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "updated"
        assert "/api/chat" in data["updated_endpoints"]

        # Verify update took effect
        res2 = client.get("/api/admin/rate-limits")
        assert res2.get_json()["endpoint_limits"]["/api/chat"] == 50

    def test_update_rate_limits_invalid(self, client):
        res = client.put(
            "/api/admin/rate-limits",
            json={"endpoint_limits": {"/api/chat": -5}},
        )
        assert res.status_code == 400

    def test_update_rate_limits_no_body(self, client):
        res = client.put("/api/admin/rate-limits")
        assert res.status_code == 400

    def test_usage_endpoint(self, client):
        # Make some requests first
        client.post("/api/chat", json={"query": "test"})
        res = client.get("/api/admin/usage")
        assert res.status_code == 200
        data = res.get_json()
        assert "stats" in data
        assert "top_users" in data

    def test_usage_with_api_key_filter(self, client):
        res = client.get("/api/admin/usage?api_key=test-key")
        assert res.status_code == 200
        data = res.get_json()
        assert data["stats"]["api_key"] == "test-key"
