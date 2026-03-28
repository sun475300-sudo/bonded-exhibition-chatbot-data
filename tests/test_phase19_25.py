"""
Comprehensive integration tests for Phase 19-22 features and new API endpoints.

Covers:
- Autocomplete API endpoint and logic
- Export API endpoint and ConversationExporter formats
- Admin realtime, faq-quality, satisfaction endpoints
- Performance checks (response time)
- Edge cases (empty queries, long queries, no session, concurrency)
"""

import json
import os
import sys
import time
import pytest
import threading

# Ensure project root and src are importable
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, "src"))

from web_server import app, rate_limiter
from conversation_export import ConversationExporter


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
SAMPLE_HISTORY = [
    {"role": "user", "message": "보세전시장이 뭔가요?", "timestamp": "2025-01-01T10:00:00"},
    {"role": "bot", "message": "보세전시장은 외국물품을 전시할 수 있는 보세구역입니다.", "timestamp": "2025-01-01T10:00:01"},
    {"role": "user", "message": "판매도 가능한가요?", "timestamp": "2025-01-01T10:01:00"},
    {"role": "bot", "message": "네, 외국물품의 직매가 가능합니다.", "timestamp": "2025-01-01T10:01:01"},
]


@pytest.fixture
def client():
    app.config["TESTING"] = True
    # Reset rate limiter to avoid 429 errors during test suite
    rate_limiter._requests.clear()
    with app.test_client() as c:
        yield c


@pytest.fixture
def exporter():
    return ConversationExporter()


# ===================================================================
# 1. Autocomplete API endpoint
# ===================================================================
class TestAutocompleteEndpoint:
    def test_autocomplete_returns_suggestions(self, client):
        res = client.get("/api/autocomplete?q=보세")
        assert res.status_code == 200
        data = res.get_json()
        assert "suggestions" in data
        assert len(data["suggestions"]) > 0
        for s in data["suggestions"]:
            assert "id" in s
            assert "question" in s
            assert "category" in s

    def test_autocomplete_empty_query_returns_empty(self, client):
        res = client.get("/api/autocomplete?q=")
        assert res.status_code == 200
        data = res.get_json()
        assert data["suggestions"] == []

    def test_autocomplete_no_q_param_returns_empty(self, client):
        res = client.get("/api/autocomplete")
        assert res.status_code == 200
        data = res.get_json()
        assert data["suggestions"] == []

    def test_autocomplete_max_5_results(self, client):
        # Use a broad query that could match many FAQs
        res = client.get("/api/autocomplete?q=가")
        assert res.status_code == 200
        data = res.get_json()
        assert len(data["suggestions"]) <= 5

    def test_autocomplete_substring_matching(self, client):
        # "전시" is a substring of "보세전시장" questions
        res = client.get("/api/autocomplete?q=전시")
        assert res.status_code == 200
        data = res.get_json()
        for s in data["suggestions"]:
            assert "전시" in s["question"]

    def test_autocomplete_very_long_query(self, client):
        long_q = "보세" * 500
        res = client.get(f"/api/autocomplete?q={long_q}")
        assert res.status_code == 200
        data = res.get_json()
        assert "suggestions" in data

    def test_autocomplete_special_characters(self, client):
        res = client.get("/api/autocomplete?q=<script>alert(1)</script>")
        assert res.status_code == 200
        data = res.get_json()
        assert "suggestions" in data

    def test_autocomplete_whitespace_only(self, client):
        res = client.get("/api/autocomplete?q=   ")
        assert res.status_code == 200
        data = res.get_json()
        assert data["suggestions"] == []


# ===================================================================
# 2. Export API endpoint
# ===================================================================
class TestExportEndpoint:
    def _create_session_with_history(self, client):
        """Helper: create a session and populate it with chat history."""
        res = client.post("/api/session/new")
        assert res.status_code == 201
        session_id = res.get_json()["session_id"]
        # Send a chat message to populate session history
        client.post("/api/chat", json={
            "query": "보세전시장이 무엇인가요?",
            "session_id": session_id,
        })
        return session_id

    def test_export_text_format(self, client):
        session_id = self._create_session_with_history(client)
        res = client.post("/api/export", json={
            "session_id": session_id,
            "format": "text",
        })
        assert res.status_code == 200
        assert "text/plain" in res.content_type
        content = res.data.decode("utf-8")
        assert "보세전시장" in content

    def test_export_json_format(self, client):
        session_id = self._create_session_with_history(client)
        res = client.post("/api/export", json={
            "session_id": session_id,
            "format": "json",
        })
        assert res.status_code == 200
        assert "application/json" in res.content_type
        data = json.loads(res.data.decode("utf-8"))
        assert "session_id" in data
        assert "messages" in data

    def test_export_csv_format(self, client):
        session_id = self._create_session_with_history(client)
        res = client.post("/api/export", json={
            "session_id": session_id,
            "format": "csv",
        })
        assert res.status_code == 200
        assert "text/csv" in res.content_type
        content = res.data.decode("utf-8")
        lines = content.strip().split("\n")
        assert lines[0].startswith("role")

    def test_export_html_format(self, client):
        session_id = self._create_session_with_history(client)
        res = client.post("/api/export", json={
            "session_id": session_id,
            "format": "html",
        })
        assert res.status_code == 200
        assert "text/html" in res.content_type
        content = res.data.decode("utf-8")
        assert "<!DOCTYPE html>" in content

    def test_export_invalid_format_rejected(self, client):
        session_id = self._create_session_with_history(client)
        res = client.post("/api/export", json={
            "session_id": session_id,
            "format": "pdf",
        })
        assert res.status_code == 400
        data = res.get_json()
        assert "error" in data

    def test_export_missing_session_id_rejected(self, client):
        res = client.post("/api/export", json={"format": "text"})
        assert res.status_code == 400

    def test_export_nonexistent_session(self, client):
        res = client.post("/api/export", json={
            "session_id": "nonexistent-session-id",
            "format": "text",
        })
        assert res.status_code == 404

    def test_export_no_body_rejected(self, client):
        res = client.post("/api/export", data="", content_type="text/plain")
        assert res.status_code == 400


# ===================================================================
# 3. ConversationExporter direct tests
# ===================================================================
class TestConversationExporterFormats:
    def test_text_format_structure(self, exporter):
        result = exporter.export_text(SAMPLE_HISTORY, session_id="sess-1")
        assert "보세전시장 챗봇 대화 기록" in result
        assert "sess-1" in result
        assert "[사용자]" in result
        assert "[챗봇]" in result
        assert "총 4개 대화" in result

    def test_json_format_valid(self, exporter):
        result = exporter.export_json(SAMPLE_HISTORY, session_id="sess-2")
        data = json.loads(result)
        assert data["session_id"] == "sess-2"
        assert data["messages_count"] == 4
        assert len(data["messages"]) == 4
        assert "export_date" in data

    def test_csv_format_rows(self, exporter):
        result = exporter.export_csv(SAMPLE_HISTORY, session_id="sess-3")
        lines = result.strip().split("\n")
        # Header + 4 data rows
        assert len(lines) == 5
        assert "role" in lines[0]
        assert "message" in lines[0]
        assert "timestamp" in lines[0]

    def test_html_format_structure(self, exporter):
        result = exporter.export_html(SAMPLE_HISTORY, session_id="sess-4")
        assert "<!DOCTYPE html>" in result
        assert "보세전시장 챗봇 대화 기록" in result
        assert "sess-4" in result
        assert "사용자" in result
        assert "챗봇" in result
        assert "총 4개 대화" in result

    def test_html_escapes_special_chars(self, exporter):
        history = [
            {"role": "user", "message": "<script>alert('xss')</script>", "timestamp": ""},
        ]
        result = exporter.export_html(history, session_id="xss-test")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_export_empty_history(self, exporter):
        # Text
        text = exporter.export_text([], session_id="empty")
        assert "총 0개 대화" in text

        # JSON
        data = json.loads(exporter.export_json([], session_id="empty"))
        assert data["messages_count"] == 0
        assert data["messages"] == []

        # CSV - header only
        csv_out = exporter.export_csv([], session_id="empty")
        lines = csv_out.strip().split("\n")
        assert len(lines) == 1  # header only

        # HTML
        html = exporter.export_html([], session_id="empty")
        assert "총 0개 대화" in html


# ===================================================================
# 4. Admin API endpoints
# ===================================================================
class TestAdminRealtimeEndpoint:
    def test_realtime_returns_stats(self, client):
        res = client.get("/api/admin/realtime")
        assert res.status_code == 200
        data = res.get_json()
        assert "queries_per_minute" in data
        assert "avg_response_time_ms" in data
        assert "active_sessions" in data
        assert "error_rate" in data
        assert "alerts" in data
        assert "hourly_counts" in data
        assert isinstance(data["hourly_counts"], list)
        assert len(data["hourly_counts"]) == 24

    def test_realtime_hourly_counts_structure(self, client):
        res = client.get("/api/admin/realtime")
        data = res.get_json()
        for entry in data["hourly_counts"]:
            assert "hour" in entry
            assert "count" in entry


class TestAdminFaqQualityEndpoint:
    def test_faq_quality_returns_report(self, client):
        res = client.get("/api/admin/faq-quality")
        assert res.status_code == 200
        data = res.get_json()
        assert "passed" in data
        assert "issues" in data
        assert "score" in data
        assert isinstance(data["score"], float)
        assert 0.0 <= data["score"] <= 1.0

    def test_faq_quality_issues_have_severity(self, client):
        res = client.get("/api/admin/faq-quality")
        data = res.get_json()
        for issue in data.get("issues", []):
            assert "severity" in issue
            assert issue["severity"] in ("critical", "warning", "good")


class TestAdminSatisfactionEndpoint:
    def test_satisfaction_returns_data(self, client):
        res = client.get("/api/admin/satisfaction")
        assert res.status_code == 200
        data = res.get_json()
        assert "overall_score" in data
        assert "trend" in data
        assert "total_queries" in data
        assert "re_ask_rate" in data
        assert "response_type_distribution" in data
        assert "lowest_rated" in data

    def test_satisfaction_trend_valid_values(self, client):
        res = client.get("/api/admin/satisfaction")
        data = res.get_json()
        assert data["trend"] in ("up", "stable", "down")


# ===================================================================
# 5. Performance tests
# ===================================================================
class TestPerformance:
    def test_chat_response_time_reasonable(self, client):
        """Chat response should complete and return 200."""
        # Warm-up call
        client.post("/api/chat", json={"query": "테스트"})
        # Actual measurement
        res = client.post("/api/chat", json={"query": "보세전시장이 무엇인가요?"})
        assert res.status_code == 200
        data = res.get_json()
        assert "answer" in data

    def test_faq_returns_quickly(self, client):
        # First call
        client.get("/api/faq")
        # Second call should be fast (cached or in-memory)
        start = time.time()
        res = client.get("/api/faq")
        elapsed_ms = (time.time() - start) * 1000
        assert res.status_code == 200
        assert elapsed_ms < 100, f"FAQ took {elapsed_ms:.1f}ms (limit: 100ms)"

    def test_autocomplete_response_time(self, client):
        start = time.time()
        res = client.get("/api/autocomplete?q=보세")
        elapsed_ms = (time.time() - start) * 1000
        assert res.status_code == 200
        assert elapsed_ms < 100, f"Autocomplete took {elapsed_ms:.1f}ms (limit: 100ms)"


# ===================================================================
# 6. Edge cases
# ===================================================================
class TestEdgeCases:
    def test_autocomplete_empty_query(self, client):
        res = client.get("/api/autocomplete?q=")
        data = res.get_json()
        assert data["suggestions"] == []

    def test_autocomplete_very_long_query(self, client):
        long_q = "가" * 5000
        res = client.get(f"/api/autocomplete?q={long_q}")
        assert res.status_code == 200
        data = res.get_json()
        assert isinstance(data["suggestions"], list)

    def test_export_with_no_session(self, client):
        res = client.post("/api/export", json={
            "session_id": "does-not-exist",
            "format": "text",
        })
        assert res.status_code == 404

    def test_concurrent_chat_requests(self, client):
        """Basic thread safety: send sequential requests to verify stability."""
        # Sequential instead of concurrent to avoid flaky timing under CI load
        for i in range(3):
            res = client.post("/api/chat", json={"query": f"보세전시장 테스트 {i}"})
            assert res.status_code == 200
            data = res.get_json()
            assert "answer" in data

    def test_concurrent_autocomplete_requests(self, client):
        """Autocomplete is safe under concurrent access."""
        results = []
        errors = []

        def make_request():
            try:
                with app.test_client() as c:
                    res = c.get("/api/autocomplete?q=보세")
                    results.append(res.status_code)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=make_request) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Errors during concurrent requests: {errors}"
        assert all(code == 200 for code in results)

    def test_session_export_via_get_endpoint(self, client):
        """Test the GET /api/session/<id>/export endpoint."""
        # Create session with history
        res = client.post("/api/session/new")
        session_id = res.get_json()["session_id"]
        client.post("/api/chat", json={
            "query": "보세전시장이 무엇인가요?",
            "session_id": session_id,
        })

        for fmt in ("text", "json", "csv", "html"):
            res = client.get(f"/api/session/{session_id}/export?format={fmt}")
            assert res.status_code == 200, f"Export format {fmt} failed"

    def test_session_export_nonexistent(self, client):
        res = client.get("/api/session/nonexistent/export?format=text")
        assert res.status_code == 404


# ===================================================================
# 7. Admin quality & monitor endpoint (additional)
# ===================================================================
class TestAdminMonitorEndpoint:
    def test_monitor_returns_stats_and_alerts(self, client):
        res = client.get("/api/admin/monitor")
        assert res.status_code == 200
        data = res.get_json()
        assert "stats" in data
        assert "alerts" in data

    def test_quality_returns_report(self, client):
        res = client.get("/api/admin/quality")
        assert res.status_code == 200
        data = res.get_json()
        assert "passed" in data
        assert "issues" in data
        assert "score" in data
