"""Tests for the audit logging system."""

import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.audit_logger import AuditLogger, VALID_ACTIONS, VALID_RESOURCE_TYPES


@pytest.fixture
def audit_db(tmp_path):
    """Create an AuditLogger with a temporary database."""
    db_path = str(tmp_path / "test_audit.db")
    al = AuditLogger(db_path=db_path)
    yield al
    al.close()


class TestAuditLoggerLogging:
    """Test logging events."""

    def test_log_basic_event(self, audit_db):
        entry_id = audit_db.log(
            actor="admin", action="create", resource_type="faq",
            resource_id="faq_001",
        )
        assert entry_id is not None
        assert entry_id > 0

    def test_log_with_details(self, audit_db):
        entry_id = audit_db.log(
            actor="admin", action="update", resource_type="faq",
            resource_id="faq_002",
            details={"question": "What is bonded exhibition?"},
        )
        logs = audit_db.get_logs()
        assert len(logs) == 1
        assert logs[0]["details"] == {"question": "What is bonded exhibition?"}

    def test_log_with_ip(self, audit_db):
        audit_db.log(
            actor="admin", action="login", resource_type="session",
            ip="192.168.1.100",
        )
        logs = audit_db.get_logs()
        assert logs[0]["ip_address"] == "192.168.1.100"

    def test_log_invalid_action_raises(self, audit_db):
        with pytest.raises(ValueError, match="Invalid action"):
            audit_db.log(
                actor="admin", action="invalid_action", resource_type="faq",
            )

    def test_log_invalid_resource_type_raises(self, audit_db):
        with pytest.raises(ValueError, match="Invalid resource_type"):
            audit_db.log(
                actor="admin", action="create", resource_type="invalid_type",
            )

    def test_log_all_valid_actions(self, audit_db):
        for action in VALID_ACTIONS:
            entry_id = audit_db.log(
                actor="admin", action=action, resource_type="faq",
            )
            assert entry_id > 0

    def test_log_all_valid_resource_types(self, audit_db):
        for rt in VALID_RESOURCE_TYPES:
            entry_id = audit_db.log(
                actor="admin", action="create", resource_type=rt,
            )
            assert entry_id > 0

    def test_log_entry_has_timestamp(self, audit_db):
        audit_db.log(actor="admin", action="create", resource_type="faq")
        logs = audit_db.get_logs()
        assert "timestamp" in logs[0]
        assert "T" in logs[0]["timestamp"]  # ISO format


class TestAuditLoggerQuerying:
    """Test querying with filters."""

    def test_get_logs_no_filter(self, audit_db):
        audit_db.log(actor="admin", action="create", resource_type="faq")
        audit_db.log(actor="admin", action="update", resource_type="faq")
        logs = audit_db.get_logs()
        assert len(logs) == 2

    def test_get_logs_filter_by_actor(self, audit_db):
        audit_db.log(actor="admin1", action="create", resource_type="faq")
        audit_db.log(actor="admin2", action="update", resource_type="faq")
        logs = audit_db.get_logs(actor="admin1")
        assert len(logs) == 1
        assert logs[0]["actor"] == "admin1"

    def test_get_logs_filter_by_action(self, audit_db):
        audit_db.log(actor="admin", action="create", resource_type="faq")
        audit_db.log(actor="admin", action="delete", resource_type="faq")
        audit_db.log(actor="admin", action="create", resource_type="tenant")
        logs = audit_db.get_logs(action="create")
        assert len(logs) == 2

    def test_get_logs_filter_by_resource_type(self, audit_db):
        audit_db.log(actor="admin", action="create", resource_type="faq")
        audit_db.log(actor="admin", action="create", resource_type="tenant")
        logs = audit_db.get_logs(resource_type="tenant")
        assert len(logs) == 1

    def test_get_logs_filter_by_since(self, audit_db):
        audit_db.log(actor="admin", action="create", resource_type="faq")
        # Use a future timestamp to filter everything out
        logs = audit_db.get_logs(since="2099-01-01T00:00:00.000000Z")
        assert len(logs) == 0

    def test_get_logs_with_limit(self, audit_db):
        for i in range(10):
            audit_db.log(actor="admin", action="create", resource_type="faq",
                         resource_id=f"faq_{i}")
        logs = audit_db.get_logs(limit=5)
        assert len(logs) == 5

    def test_get_logs_ordered_desc(self, audit_db):
        audit_db.log(actor="admin", action="create", resource_type="faq",
                     resource_id="first")
        audit_db.log(actor="admin", action="create", resource_type="faq",
                     resource_id="second")
        logs = audit_db.get_logs()
        assert logs[0]["resource_id"] == "second"
        assert logs[1]["resource_id"] == "first"

    def test_get_logs_combined_filters(self, audit_db):
        audit_db.log(actor="admin1", action="create", resource_type="faq")
        audit_db.log(actor="admin1", action="delete", resource_type="faq")
        audit_db.log(actor="admin2", action="create", resource_type="faq")
        logs = audit_db.get_logs(actor="admin1", action="create")
        assert len(logs) == 1


class TestAuditLoggerCount:
    """Test counting events."""

    def test_get_log_count(self, audit_db):
        assert audit_db.get_log_count() == 0
        audit_db.log(actor="admin", action="create", resource_type="faq")
        audit_db.log(actor="admin", action="update", resource_type="faq")
        assert audit_db.get_log_count() == 2

    def test_get_log_count_with_since(self, audit_db):
        audit_db.log(actor="admin", action="create", resource_type="faq")
        count = audit_db.get_log_count(since="2099-01-01T00:00:00.000000Z")
        assert count == 0
        count = audit_db.get_log_count(since="2000-01-01T00:00:00.000000Z")
        assert count == 1


class TestAuditLoggerActorActivity:
    """Test actor activity queries."""

    def test_get_actor_activity(self, audit_db):
        audit_db.log(actor="admin1", action="create", resource_type="faq")
        audit_db.log(actor="admin1", action="update", resource_type="tenant")
        audit_db.log(actor="admin2", action="delete", resource_type="faq")

        activity = audit_db.get_actor_activity("admin1")
        assert len(activity) == 2
        assert all(a["actor"] == "admin1" for a in activity)

    def test_get_actor_activity_empty(self, audit_db):
        activity = audit_db.get_actor_activity("nonexistent")
        assert len(activity) == 0


class TestAuditLoggerResourceHistory:
    """Test resource history queries."""

    def test_get_resource_history(self, audit_db):
        audit_db.log(actor="admin", action="create", resource_type="faq",
                     resource_id="faq_001")
        audit_db.log(actor="admin", action="update", resource_type="faq",
                     resource_id="faq_001")
        audit_db.log(actor="admin", action="create", resource_type="faq",
                     resource_id="faq_002")

        history = audit_db.get_resource_history("faq", "faq_001")
        assert len(history) == 2
        assert all(h["resource_id"] == "faq_001" for h in history)

    def test_get_resource_history_empty(self, audit_db):
        history = audit_db.get_resource_history("faq", "nonexistent")
        assert len(history) == 0


class TestAuditLoggerCleanup:
    """Test cleanup of old logs."""

    def test_cleanup_removes_old_entries(self, audit_db):
        # Insert an entry with an old timestamp directly
        conn = audit_db._get_conn()
        conn.execute(
            """INSERT INTO audit_logs
               (timestamp, actor, action, resource_type, resource_id)
               VALUES (?, ?, ?, ?, ?)""",
            ("2020-01-01T00:00:00.000000Z", "admin", "create", "faq", "old_one"),
        )
        conn.commit()

        # Insert a recent entry
        audit_db.log(actor="admin", action="create", resource_type="faq",
                     resource_id="new_one")

        assert audit_db.get_log_count() == 2
        deleted = audit_db.cleanup(days=90)
        assert deleted == 1
        assert audit_db.get_log_count() == 1

        logs = audit_db.get_logs()
        assert logs[0]["resource_id"] == "new_one"

    def test_cleanup_keeps_recent_entries(self, audit_db):
        audit_db.log(actor="admin", action="create", resource_type="faq")
        deleted = audit_db.cleanup(days=90)
        assert deleted == 0
        assert audit_db.get_log_count() == 1


class TestAuditLoggerStats:
    """Test statistics."""

    def test_get_stats(self, audit_db):
        audit_db.log(actor="admin1", action="create", resource_type="faq")
        audit_db.log(actor="admin1", action="update", resource_type="faq")
        audit_db.log(actor="admin2", action="delete", resource_type="tenant")

        stats = audit_db.get_stats()
        assert stats["total"] == 3
        assert len(stats["actions_per_day"]) >= 1
        assert len(stats["top_actors"]) == 2
        assert len(stats["action_breakdown"]) == 3

    def test_get_stats_with_since(self, audit_db):
        audit_db.log(actor="admin", action="create", resource_type="faq")
        stats = audit_db.get_stats(since="2099-01-01T00:00:00.000000Z")
        assert stats["total"] == 0


# --- API endpoint tests ---

from web_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestAuditAPIEndpoints:
    """Test the audit log API endpoints."""

    def test_get_audit_logs(self, client):
        # First create some audit entries via an admin action
        # Login creates an audit entry
        client.post("/api/auth/login", json={
            "username": "admin", "password": "admin123",
        })
        res = client.get("/api/admin/audit")
        assert res.status_code == 200
        data = res.get_json()
        assert "logs" in data
        assert "count" in data

    def test_get_audit_logs_with_filters(self, client):
        res = client.get("/api/admin/audit?action=login&limit=10")
        assert res.status_code == 200
        data = res.get_json()
        assert "logs" in data
        for log_entry in data["logs"]:
            assert log_entry["action"] == "login"

    def test_get_audit_stats(self, client):
        res = client.get("/api/admin/audit/stats")
        assert res.status_code == 200
        data = res.get_json()
        assert "total" in data
        assert "actions_per_day" in data
        assert "top_actors" in data
        assert "action_breakdown" in data

    def test_faq_create_generates_audit_log(self, client):
        # Create a FAQ item
        client.post("/api/admin/faq", json={
            "id": "audit_test_faq",
            "question": "Audit test question?",
            "answer": "Audit test answer.",
            "category": "GENERAL",
        })
        # Check audit logs for this action
        res = client.get("/api/admin/audit?action=create&resource_type=faq")
        data = res.get_json()
        faq_creates = [
            l for l in data["logs"] if l.get("resource_id") == "audit_test_faq"
        ]
        assert len(faq_creates) >= 1

    def test_faq_delete_generates_audit_log(self, client):
        # Create then delete a FAQ item
        client.post("/api/admin/faq", json={
            "id": "audit_del_faq",
            "question": "To be deleted?",
            "answer": "Yes.",
            "category": "GENERAL",
        })
        client.delete("/api/admin/faq/audit_del_faq")
        res = client.get("/api/admin/audit?action=delete&resource_type=faq")
        data = res.get_json()
        faq_deletes = [
            l for l in data["logs"] if l.get("resource_id") == "audit_del_faq"
        ]
        assert len(faq_deletes) >= 1
