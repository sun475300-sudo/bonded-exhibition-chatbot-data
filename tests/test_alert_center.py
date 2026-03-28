"""Tests for the alert center module."""

import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.alert_center import AlertCenter, AlertRuleEngine, SEVERITY_LEVELS, CATEGORIES


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test_alerts.db")


@pytest.fixture
def center(tmp_db):
    return AlertCenter(db_path=tmp_db)


@pytest.fixture
def populated_center(center):
    """Center with a handful of pre-created alerts."""
    center.create_alert("Info alert", "Just info", "info", "system_error")
    center.create_alert("Warning alert", "Something off", "warning", "unmatched_surge")
    center.create_alert("Critical alert", "Very bad", "critical", "satisfaction_drop")
    return center


# ── CRUD Tests ──────────────────────────────────────────────────────────────

class TestAlertCRUD:
    def test_create_alert(self, center):
        alert = center.create_alert("Test", "Test message", "info", "system_error")
        assert alert["title"] == "Test"
        assert alert["message"] == "Test message"
        assert alert["severity"] == "info"
        assert alert["category"] == "system_error"
        assert alert["is_read"] is False
        assert "id" in alert
        assert "created_at" in alert

    def test_create_alert_with_metadata(self, center):
        meta = {"key": "value", "count": 42}
        alert = center.create_alert("Meta", "Has metadata", "warning", "security", metadata=meta)
        assert alert["metadata"] == meta

    def test_create_alert_invalid_severity(self, center):
        with pytest.raises(ValueError, match="Invalid severity"):
            center.create_alert("Bad", "bad", "urgent", "system_error")

    def test_create_alert_invalid_category(self, center):
        with pytest.raises(ValueError, match="Invalid category"):
            center.create_alert("Bad", "bad", "info", "unknown_category")

    def test_get_alerts_returns_all(self, populated_center):
        alerts = populated_center.get_alerts()
        assert len(alerts) == 3

    def test_get_alerts_ordered_by_created_at_desc(self, populated_center):
        alerts = populated_center.get_alerts()
        timestamps = [a["created_at"] for a in alerts]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_get_alerts_limit(self, populated_center):
        alerts = populated_center.get_alerts(limit=2)
        assert len(alerts) == 2

    def test_delete_alert(self, center):
        alert = center.create_alert("To delete", "bye", "info", "system_error")
        assert center.delete_alert(alert["id"]) is True
        assert len(center.get_alerts()) == 0

    def test_delete_nonexistent_alert(self, center):
        assert center.delete_alert("nonexistent") is False


# ── Filtering Tests ─────────────────────────────────────────────────────────

class TestAlertFiltering:
    def test_filter_by_severity(self, populated_center):
        alerts = populated_center.get_alerts(severity="critical")
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "critical"

    def test_filter_by_category(self, populated_center):
        alerts = populated_center.get_alerts(category="unmatched_surge")
        assert len(alerts) == 1
        assert alerts[0]["category"] == "unmatched_surge"

    def test_filter_unread_only(self, populated_center):
        # All 3 are unread initially
        alerts = populated_center.get_alerts(unread_only=True)
        assert len(alerts) == 3

        # Mark one as read
        populated_center.mark_read(alerts[0]["id"])
        unread = populated_center.get_alerts(unread_only=True)
        assert len(unread) == 2

    def test_combined_filters(self, populated_center):
        alerts = populated_center.get_alerts(severity="info", category="system_error")
        assert len(alerts) == 1
        assert alerts[0]["title"] == "Info alert"


# ── Mark Read/Unread Tests ──────────────────────────────────────────────────

class TestMarkRead:
    def test_mark_read(self, center):
        alert = center.create_alert("Read me", "msg", "info", "system_error")
        assert center.mark_read(alert["id"]) is True
        alerts = center.get_alerts()
        assert alerts[0]["is_read"] is True

    def test_mark_read_nonexistent(self, center):
        assert center.mark_read("no-such-id") is False

    def test_mark_all_read(self, populated_center):
        count = populated_center.mark_all_read()
        assert count == 3
        unread = populated_center.get_alerts(unread_only=True)
        assert len(unread) == 0

    def test_mark_all_read_when_already_read(self, populated_center):
        populated_center.mark_all_read()
        count = populated_center.mark_all_read()
        assert count == 0


# ── Unread Count Tests ──────────────────────────────────────────────────────

class TestUnreadCount:
    def test_unread_count_initial(self, populated_center):
        assert populated_center.get_unread_count() == 3

    def test_unread_count_after_mark_read(self, populated_center):
        alerts = populated_center.get_alerts()
        populated_center.mark_read(alerts[0]["id"])
        assert populated_center.get_unread_count() == 2

    def test_unread_count_empty(self, center):
        assert center.get_unread_count() == 0


# ── Cleanup Tests ───────────────────────────────────────────────────────────

class TestCleanup:
    def test_cleanup_removes_old_alerts(self, center):
        # Insert an alert with an old timestamp directly
        import sqlite3
        old_date = (datetime.now() - timedelta(days=60)).isoformat()
        with sqlite3.connect(center.db_path) as conn:
            conn.execute(
                "INSERT INTO alerts (id, title, message, severity, category, metadata, is_read, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                ("old1", "Old alert", "old", "info", "system_error", None, old_date),
            )
            conn.commit()

        # Also create a recent alert
        center.create_alert("New alert", "new", "info", "system_error")

        removed = center.cleanup(days=30)
        assert removed == 1
        alerts = center.get_alerts()
        assert len(alerts) == 1
        assert alerts[0]["title"] == "New alert"

    def test_cleanup_no_old_alerts(self, populated_center):
        removed = populated_center.cleanup(days=30)
        assert removed == 0


# ── AlertRuleEngine Tests (with mocks) ──────────────────────────────────────

class TestAlertRuleEngine:
    def _make_engine(self, tmp_db, monitor_stats=None, satisfaction_stats=None, quality_report=None):
        center = AlertCenter(db_path=tmp_db)
        monitor = None
        satisfaction = None
        quality = None

        if monitor_stats is not None:
            monitor = MagicMock()
            monitor.get_live_stats.return_value = monitor_stats

        if satisfaction_stats is not None:
            satisfaction = MagicMock()
            satisfaction.get_satisfaction_stats.return_value = satisfaction_stats

        if quality_report is not None:
            quality = MagicMock()
            quality.check_all.return_value = quality_report

        engine = AlertRuleEngine(center, monitor, satisfaction, quality)
        return engine, center

    def test_unmatched_surge_triggers(self, tmp_db):
        engine, center = self._make_engine(
            tmp_db, monitor_stats={"unmatched_rate": 0.30}  # 30%
        )
        result = engine.check_unmatched_surge(threshold_pct=20)
        assert result is not None
        assert result["category"] == "unmatched_surge"
        assert result["severity"] == "warning"

    def test_unmatched_surge_no_trigger(self, tmp_db):
        engine, center = self._make_engine(
            tmp_db, monitor_stats={"unmatched_rate": 0.10}  # 10%
        )
        result = engine.check_unmatched_surge(threshold_pct=20)
        assert result is None

    def test_unmatched_surge_no_monitor(self, tmp_db):
        engine, center = self._make_engine(tmp_db)
        result = engine.check_unmatched_surge()
        assert result is None

    def test_satisfaction_drop_triggers(self, tmp_db):
        engine, center = self._make_engine(
            tmp_db, satisfaction_stats={"avg_satisfaction_score": 0.3}
        )
        result = engine.check_satisfaction_drop(threshold=0.5)
        assert result is not None
        assert result["category"] == "satisfaction_drop"
        assert result["severity"] == "critical"

    def test_satisfaction_drop_no_trigger(self, tmp_db):
        engine, center = self._make_engine(
            tmp_db, satisfaction_stats={"avg_satisfaction_score": 0.8}
        )
        result = engine.check_satisfaction_drop(threshold=0.5)
        assert result is None

    def test_faq_quality_triggers(self, tmp_db):
        engine, center = self._make_engine(
            tmp_db, quality_report={"score": 0.4, "issues": [{"check": "a"}]}
        )
        result = engine.check_faq_quality(min_score=70)
        assert result is not None
        assert result["category"] == "faq_quality"

    def test_faq_quality_no_trigger(self, tmp_db):
        engine, center = self._make_engine(
            tmp_db, quality_report={"score": 0.9, "issues": []}
        )
        result = engine.check_faq_quality(min_score=70)
        assert result is None

    def test_run_all_checks(self, tmp_db):
        engine, center = self._make_engine(
            tmp_db,
            monitor_stats={"unmatched_rate": 0.50},
            satisfaction_stats={"avg_satisfaction_score": 0.2},
            quality_report={"score": 0.2, "issues": [{"check": "x"}]},
        )
        results = engine.run_all_checks()
        assert len(results) == 3
        categories = {r["category"] for r in results}
        assert categories == {"unmatched_surge", "satisfaction_drop", "faq_quality"}

    def test_run_all_checks_none_triggered(self, tmp_db):
        engine, center = self._make_engine(
            tmp_db,
            monitor_stats={"unmatched_rate": 0.05},
            satisfaction_stats={"avg_satisfaction_score": 0.9},
            quality_report={"score": 1.0, "issues": []},
        )
        results = engine.run_all_checks()
        assert results == []


# ── API Endpoint Tests ──────────────────────────────────────────────────────

@pytest.fixture
def client():
    from web_server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth_headers():
    """Generate a valid JWT token for admin access."""
    from src.auth import JWTAuth
    jwt = JWTAuth()
    token = jwt.generate_token("admin")
    return {"Authorization": f"Bearer {token}"}


class TestAlertAPIEndpoints:
    def test_list_alerts(self, client, auth_headers):
        res = client.get("/api/admin/alerts", headers=auth_headers)
        assert res.status_code == 200
        data = res.get_json()
        assert "alerts" in data
        assert "count" in data

    def test_unread_count(self, client, auth_headers):
        res = client.get("/api/admin/alerts/count", headers=auth_headers)
        assert res.status_code == 200
        data = res.get_json()
        assert "unread_count" in data

    def test_mark_read_not_found(self, client, auth_headers):
        res = client.post("/api/admin/alerts/nonexistent/read", headers=auth_headers)
        assert res.status_code == 404

    def test_mark_all_read(self, client, auth_headers):
        res = client.post("/api/admin/alerts/read-all", headers=auth_headers)
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True

    def test_delete_not_found(self, client, auth_headers):
        res = client.delete("/api/admin/alerts/nonexistent", headers=auth_headers)
        assert res.status_code == 404

    def test_run_checks(self, client, auth_headers):
        res = client.post("/api/admin/alerts/check", headers=auth_headers)
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "new_alerts" in data

    def test_unauthenticated_access(self):
        from web_server import app
        app.config["TESTING"] = True
        app.config["AUTH_TESTING"] = True
        with app.test_client() as c:
            res = c.get("/api/admin/alerts")
            assert res.status_code == 401
        app.config["AUTH_TESTING"] = False
