"""Performance regression tests for the chatbot web API.

Ensures response times, throughput, memory usage, and startup times
stay within acceptable bounds.  Thresholds are set at ~2x the expected
values to avoid flaky failures in CI.
"""

import gc
import os
import sys
import time
import tracemalloc

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ADMIN_AUTH_DISABLED"] = "true"
os.environ["TESTING"] = "true"
os.environ["CHATBOT_RATE_LIMIT"] = "100000"

from web_server import app, rate_limiter  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """Create a shared Flask test client for the whole module."""
    app.config["TESTING"] = True
    # Clear rate limiter to avoid 429s from prior test modules
    try:
        from web_server import advanced_rate_limiter
        if hasattr(advanced_rate_limiter, '_windows'):
            advanced_rate_limiter._windows.clear()
        if hasattr(advanced_rate_limiter, '_quotas_used'):
            advanced_rate_limiter._quotas_used.clear()
    except Exception:
        pass
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _timed_get(client, url, **kwargs):
    """Issue a GET and return (response, elapsed_seconds)."""
    start = time.monotonic()
    resp = client.get(url, **kwargs)
    elapsed = time.monotonic() - start
    return resp, elapsed


def _timed_post(client, url, **kwargs):
    """Issue a POST and return (response, elapsed_seconds)."""
    start = time.monotonic()
    resp = client.post(url, **kwargs)
    elapsed = time.monotonic() - start
    return resp, elapsed


# ===================================================================
# 1. TestResponseTimes  (~12 tests)
# ===================================================================

class TestResponseTimes:
    """Each endpoint must respond within a generous time bound."""

    def test_chat_simple_query(self, client):
        """/api/chat responds in <500ms (CI 변동성 고려)."""
        resp, elapsed = _timed_post(
            client, "/api/chat",
            json={"query": "보세전시장이 무엇인가요?"},
        )
        assert resp.status_code == 200
        assert elapsed < 0.500, f"/api/chat took {elapsed:.3f}s (limit 0.500s)"

    def test_faq_list(self, client):
        """/api/faq responds in <300ms."""
        resp, elapsed = _timed_get(client, "/api/faq")
        assert resp.status_code == 200
        assert elapsed < 0.300, f"/api/faq took {elapsed:.3f}s (limit 0.300s)"

    def test_autocomplete(self, client):
        """/api/autocomplete responds in <150ms."""
        resp, elapsed = _timed_get(client, "/api/autocomplete?q=보세")
        assert resp.status_code == 200
        assert elapsed < 0.150, f"/api/autocomplete took {elapsed:.3f}s (limit 0.150s)"

    def test_health(self, client):
        """/api/health responds in <100ms."""
        resp, elapsed = _timed_get(client, "/api/health")
        assert resp.status_code == 200
        assert elapsed < 0.100, f"/api/health took {elapsed:.3f}s (limit 0.100s)"

    def test_config(self, client):
        """/api/config responds in <150ms."""
        resp, elapsed = _timed_get(client, "/api/config")
        assert resp.status_code == 200
        assert elapsed < 0.150, f"/api/config took {elapsed:.3f}s (limit 0.150s)"

    def test_admin_stats(self, client):
        """/api/admin/stats responds in <500ms (CI runner variability)."""
        resp, elapsed = _timed_get(client, "/api/admin/stats")
        assert resp.status_code == 200
        assert elapsed < 0.500, f"/api/admin/stats took {elapsed:.3f}s (limit 0.500s)"

    def test_admin_charts_dashboard(self, client):
        """/api/admin/charts/dashboard responds in <750ms."""
        resp, elapsed = _timed_get(client, "/api/admin/charts/dashboard")
        assert resp.status_code == 200
        assert elapsed < 0.750, f"/api/admin/charts/dashboard took {elapsed:.3f}s (limit 0.750s)"

    def test_v2_faq_pagination(self, client):
        """/api/v2/faq with pagination responds in <400ms."""
        resp, elapsed = _timed_get(client, "/api/v2/faq?page=1&per_page=10")
        assert resp.status_code == 200
        assert elapsed < 0.400, f"/api/v2/faq took {elapsed:.3f}s (limit 0.400s)"

    def test_i18n_ko(self, client):
        """/api/i18n/ko responds in <150ms."""
        resp, elapsed = _timed_get(client, "/api/i18n/ko")
        assert resp.status_code == 200
        assert elapsed < 0.150, f"/api/i18n/ko took {elapsed:.3f}s (limit 0.150s)"

    def test_session_new(self, client):
        """/api/session/new responds in <150ms."""
        resp, elapsed = _timed_post(client, "/api/session/new")
        assert resp.status_code == 201
        assert elapsed < 0.150, f"/api/session/new took {elapsed:.3f}s (limit 0.150s)"

    def test_admin_knowledge_graph(self, client):
        """/api/admin/knowledge/graph responds in <1000ms."""
        resp, elapsed = _timed_get(client, "/api/admin/knowledge/graph")
        assert resp.status_code == 200
        assert elapsed < 1.000, f"/api/admin/knowledge/graph took {elapsed:.3f}s (limit 1.000s)"

    def test_admin_quality_scores(self, client):
        """/api/admin/quality/scores responds in <750ms."""
        resp, elapsed = _timed_get(client, "/api/admin/quality/scores")
        assert resp.status_code == 200
        assert elapsed < 0.750, f"/api/admin/quality/scores took {elapsed:.3f}s (limit 0.750s)"


# ===================================================================
# 2. TestThroughput  (~5 tests)
# ===================================================================

class TestThroughput:
    """Sequential request throughput must stay above minimum rates."""

    @staticmethod
    def _reset_rate_limiter():
        """Clear rate limiter state so throughput tests aren't throttled."""
        rate_limiter._requests.clear()
        rate_limiter.max_requests = 100000

    def test_chat_throughput(self, client):
        """100 sequential /api/chat requests in <30s (>3 req/s)."""
        self._reset_rate_limiter()
        payload = {"query": "보세전시장이 무엇인가요?"}
        start = time.monotonic()
        for _ in range(100):
            resp = client.post("/api/chat", json=payload)
            assert resp.status_code == 200
        elapsed = time.monotonic() - start
        assert elapsed < 30.0, (
            f"100 chat requests took {elapsed:.1f}s (limit 30s)"
        )

    def test_faq_throughput(self, client):
        """100 sequential /api/faq requests in <10s (>10 req/s)."""
        start = time.monotonic()
        for _ in range(100):
            resp = client.get("/api/faq")
            assert resp.status_code == 200
        elapsed = time.monotonic() - start
        assert elapsed < 10.0, (
            f"100 faq requests took {elapsed:.1f}s (limit 10s)"
        )

    def test_autocomplete_throughput(self, client):
        """100 sequential /api/autocomplete requests in <5s (>20 req/s)."""
        start = time.monotonic()
        for _ in range(100):
            resp = client.get("/api/autocomplete?q=보세")
            assert resp.status_code == 200
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, (
            f"100 autocomplete requests took {elapsed:.1f}s (limit 5s)"
        )

    def test_admin_stats_throughput(self, client):
        """50 sequential /api/admin/stats in <15s."""
        start = time.monotonic()
        for _ in range(50):
            resp = client.get("/api/admin/stats")
            assert resp.status_code == 200
        elapsed = time.monotonic() - start
        assert elapsed < 15.0, (
            f"50 admin/stats requests took {elapsed:.1f}s (limit 15s)"
        )

    def test_mixed_workload(self, client):
        """Mixed workload: 50 chat + 30 faq + 20 autocomplete in <20s."""
        self._reset_rate_limiter()
        chat_payload = {"query": "보세전시장이 무엇인가요?"}
        start = time.monotonic()

        for _ in range(50):
            resp = client.post("/api/chat", json=chat_payload)
            assert resp.status_code == 200

        for _ in range(30):
            resp = client.get("/api/faq")
            assert resp.status_code == 200

        for _ in range(20):
            resp = client.get("/api/autocomplete?q=보세")
            assert resp.status_code == 200

        elapsed = time.monotonic() - start
        assert elapsed < 20.0, (
            f"Mixed workload took {elapsed:.1f}s (limit 20s)"
        )


# ===================================================================
# 3. TestMemoryUsage  (~3 tests)
# ===================================================================

class TestMemoryUsage:
    """Verify that repeated operations do not leak memory."""

    @staticmethod
    def _reset_rate_limiter():
        """Clear rate limiter state so memory tests aren't throttled."""
        rate_limiter._requests.clear()
        rate_limiter.max_requests = 100000

    def test_chat_no_memory_leak(self, client):
        """1000 sequential chat requests don't leak memory."""
        self._reset_rate_limiter()
        payload = {"query": "보세전시장이 무엇인가요?"}

        # Warm up
        for _ in range(10):
            client.post("/api/chat", json=payload)
        gc.collect()

        tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()

        for _ in range(1000):
            client.post("/api/chat", json=payload)

        gc.collect()
        snapshot_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snapshot_after.compare_to(snapshot_before, "lineno")
        # Sum the top 20 growth lines to get total growth
        total_growth = sum(s.size_diff for s in stats[:20] if s.size_diff > 0)
        # Allow up to 50 MB growth for 1000 requests — very generous
        max_growth_bytes = 50 * 1024 * 1024
        assert total_growth < max_growth_bytes, (
            f"Memory grew by {total_growth / 1024 / 1024:.1f} MB over 1000 "
            f"chat requests (limit {max_growth_bytes / 1024 / 1024:.0f} MB)"
        )

    def test_session_create_delete_no_leak(self, client):
        """Session creation/deletion doesn't leak memory."""
        # Warm up
        for _ in range(5):
            resp = client.post("/api/session/new")
            sid = resp.get_json()["session_id"]
            client.get(f"/api/session/{sid}")
        gc.collect()

        tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()

        for _ in range(500):
            resp = client.post("/api/session/new")
            sid = resp.get_json()["session_id"]
            # Access then discard — simulates create/use/abandon cycle
            client.get(f"/api/session/{sid}")

        gc.collect()
        snapshot_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snapshot_after.compare_to(snapshot_before, "lineno")
        total_growth = sum(s.size_diff for s in stats[:20] if s.size_diff > 0)
        max_growth_bytes = 30 * 1024 * 1024
        assert total_growth < max_growth_bytes, (
            f"Memory grew by {total_growth / 1024 / 1024:.1f} MB over 500 "
            f"session cycles (limit {max_growth_bytes / 1024 / 1024:.0f} MB)"
        )

    def test_faq_cache_bounded(self, client):
        """FAQ cache doesn't grow unbounded after many varied requests."""
        queries = [
            "보세전시장", "반입신고", "보세운송", "수입통관", "관세",
            "세관", "물류", "전시품", "반출", "허가",
        ]

        # Warm up
        for q in queries:
            client.get(f"/api/autocomplete?q={q}")
            client.get("/api/faq")
        gc.collect()

        tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()

        for _ in range(100):
            for q in queries:
                client.get(f"/api/autocomplete?q={q}")
            client.get("/api/faq")

        gc.collect()
        snapshot_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snapshot_after.compare_to(snapshot_before, "lineno")
        total_growth = sum(s.size_diff for s in stats[:20] if s.size_diff > 0)
        max_growth_bytes = 20 * 1024 * 1024
        assert total_growth < max_growth_bytes, (
            f"Memory grew by {total_growth / 1024 / 1024:.1f} MB over 1000 "
            f"FAQ/autocomplete requests (limit {max_growth_bytes / 1024 / 1024:.0f} MB)"
        )


# ===================================================================
# 4. TestStartupTime  (~2 tests)
# ===================================================================

class TestStartupTime:
    """Application startup must be fast."""

    def test_app_import_time(self):
        """App import + initialization completes in <5s.

        We measure by importing web_server in a subprocess so module-level
        init is included.  Since the module is already cached in *this*
        process we use subprocess for an accurate measurement.
        """
        import subprocess

        cmd = [
            sys.executable, "-c",
            (
                "import time; s=time.monotonic(); "
                "import os; os.environ['ADMIN_AUTH_DISABLED']='true'; "
                "os.environ['TESTING']='true'; "
                f"import sys; sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))!r}); "
                "from web_server import app; "
                "print(time.monotonic()-s)"
            ),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert result.returncode == 0, (
            f"Import failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        elapsed = float(result.stdout.strip().splitlines()[-1])
        assert elapsed < 5.0, (
            f"App import took {elapsed:.2f}s (limit 5.0s)"
        )

    def test_faq_data_loading_time(self):
        """FAQ data loading completes in <1s."""
        from src.utils import load_json

        start = time.monotonic()
        data = load_json("data/faq.json")
        elapsed = time.monotonic() - start

        assert data is not None, "faq.json failed to load"
        assert elapsed < 1.0, (
            f"FAQ data loading took {elapsed:.3f}s (limit 1.0s)"
        )
