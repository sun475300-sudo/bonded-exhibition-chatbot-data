"""Tests for WebhookManager and webhook API endpoints."""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.webhook_manager import WebhookManager, VALID_EVENTS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def manager(tmp_db):
    """Return a WebhookManager backed by a temporary database."""
    return WebhookManager(db_path=tmp_db)


@pytest.fixture
def client():
    """Flask test client."""
    from web_server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helper: tiny HTTP server that records received requests
# ---------------------------------------------------------------------------

class _RecordingHandler(BaseHTTPRequestHandler):
    """HTTP handler that records POST bodies and returns 200."""

    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        signature = self.headers.get("X-Webhook-Signature", "")
        _RecordingHandler.received.append({
            "body": body,
            "signature": signature,
            "path": self.path,
        })
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, fmt, *args):
        pass  # silence logs


@pytest.fixture
def webhook_server():
    """Start a local HTTP server that records webhook deliveries."""
    _RecordingHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}/hook", _RecordingHandler.received
    server.shutdown()


# ---------------------------------------------------------------------------
# Unit tests: WebhookManager
# ---------------------------------------------------------------------------

class TestRegisterUnregister:
    def test_register_returns_id(self, manager):
        sub_id = manager.register("http://example.com/hook", ["query.received"])
        assert isinstance(sub_id, str)
        assert len(sub_id) == 36  # UUID format

    def test_register_with_secret(self, manager):
        sub_id = manager.register(
            "http://example.com/hook", ["query.received"], secret="mysecret"
        )
        subs = manager.list_subscriptions()
        assert len(subs) == 1
        assert subs[0]["has_secret"] is True

    def test_register_invalid_event(self, manager):
        with pytest.raises(ValueError, match="invalid event type"):
            manager.register("http://example.com/hook", ["bogus.event"])

    def test_register_empty_url(self, manager):
        with pytest.raises(ValueError, match="url is required"):
            manager.register("", ["query.received"])

    def test_register_empty_events(self, manager):
        with pytest.raises(ValueError, match="at least one event"):
            manager.register("http://example.com/hook", [])

    def test_unregister_existing(self, manager):
        sub_id = manager.register("http://example.com/hook", ["query.received"])
        assert manager.unregister(sub_id) is True

    def test_unregister_nonexistent(self, manager):
        assert manager.unregister("nonexistent-id") is False

    def test_unregister_removes_from_list(self, manager):
        sub_id = manager.register("http://example.com/hook", ["query.received"])
        manager.unregister(sub_id)
        subs = manager.list_subscriptions()
        assert len(subs) == 0

    def test_double_unregister(self, manager):
        sub_id = manager.register("http://example.com/hook", ["query.received"])
        assert manager.unregister(sub_id) is True
        assert manager.unregister(sub_id) is False


class TestListSubscriptions:
    def test_list_empty(self, manager):
        assert manager.list_subscriptions() == []

    def test_list_multiple(self, manager):
        manager.register("http://a.com/hook", ["query.received"])
        manager.register("http://b.com/hook", ["query.matched", "faq.updated"])
        subs = manager.list_subscriptions()
        assert len(subs) == 2

    def test_list_fields(self, manager):
        manager.register("http://a.com/hook", ["query.received"], secret="s")
        sub = manager.list_subscriptions()[0]
        assert "id" in sub
        assert sub["url"] == "http://a.com/hook"
        assert sub["events"] == ["query.received"]
        assert sub["has_secret"] is True
        assert "created_at" in sub


class TestEmitEvent:
    def test_emit_sends_to_matching_subscribers(self, manager, webhook_server):
        url, received = webhook_server
        manager.register(url, ["query.received"])
        manager.register(url, ["query.matched"])

        count = manager.emit("query.received", {"q": "test"})
        assert count == 1

        # Wait for async delivery
        time.sleep(1)
        assert len(received) >= 1
        payload = json.loads(received[0]["body"])
        assert payload["event"] == "query.received"
        assert payload["data"]["q"] == "test"

    def test_emit_no_matching_subscribers(self, manager):
        manager.register("http://example.com/hook", ["query.received"])
        count = manager.emit("faq.updated", {"id": "1"})
        assert count == 0

    def test_emit_invalid_event(self, manager):
        with pytest.raises(ValueError, match="invalid event type"):
            manager.emit("invalid.event", {})

    def test_emit_multiple_subscribers(self, manager, webhook_server):
        url, received = webhook_server
        manager.register(url, ["feedback.received"])
        manager.register(url, ["feedback.received"])
        count = manager.emit("feedback.received", {"rating": "good"})
        assert count == 2

        time.sleep(1)
        assert len(received) >= 2


class TestHMACSignature:
    def test_sign_payload(self):
        payload = b'{"event":"test"}'
        secret = "test-secret"
        expected = hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        result = WebhookManager._sign_payload(payload, secret)
        assert result == expected

    def test_webhook_includes_signature(self, manager, webhook_server):
        url, received = webhook_server
        manager.register(url, ["query.received"], secret="my-secret")
        manager.emit("query.received", {"q": "hello"})

        time.sleep(1)
        assert len(received) >= 1
        sig_header = received[0]["signature"]
        assert sig_header.startswith("sha256=")

        # Verify the signature is correct
        body = received[0]["body"]
        expected = hmac.new(
            b"my-secret", body, hashlib.sha256
        ).hexdigest()
        assert sig_header == f"sha256={expected}"

    def test_webhook_no_signature_without_secret(self, manager, webhook_server):
        url, received = webhook_server
        manager.register(url, ["query.received"])
        manager.emit("query.received", {"q": "hello"})

        time.sleep(1)
        assert len(received) >= 1
        assert received[0]["signature"] == ""


class TestRetryLogic:
    def test_retry_on_failure(self, manager, tmp_db):
        """Test that _retry attempts multiple times on failure."""
        call_count = {"n": 0}

        def mock_send(url, payload, secret=None):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ConnectionError("connection refused")
            return 200, "ok"

        manager._send_webhook = mock_send
        manager._retry(
            "http://fail.example.com/hook",
            {"event": "query.received", "timestamp": time.time(), "data": {}},
            secret=None,
            max_retries=3,
            subscription_id="test-sub",
        )
        assert call_count["n"] == 3

    def test_retry_gives_up_after_max(self, manager, tmp_db):
        """Test that _retry stops after max_retries."""
        call_count = {"n": 0}

        def mock_send(url, payload, secret=None):
            call_count["n"] += 1
            raise ConnectionError("connection refused")

        manager._send_webhook = mock_send
        manager._retry(
            "http://fail.example.com/hook",
            {"event": "query.received", "timestamp": time.time(), "data": {}},
            secret=None,
            max_retries=3,
            subscription_id="test-sub",
        )
        assert call_count["n"] == 3

    def test_retry_succeeds_on_first_try(self, manager, tmp_db):
        """Test that _retry does not retry on success."""
        call_count = {"n": 0}

        def mock_send(url, payload, secret=None):
            call_count["n"] += 1
            return 200, "ok"

        manager._send_webhook = mock_send
        manager._retry(
            "http://ok.example.com/hook",
            {"event": "query.received", "timestamp": time.time(), "data": {}},
            secret=None,
            max_retries=3,
            subscription_id="test-sub",
        )
        assert call_count["n"] == 1


class TestDeliveryLog:
    def test_delivery_log_recorded(self, manager, webhook_server):
        url, received = webhook_server
        sub_id = manager.register(url, ["query.received"])
        manager.emit("query.received", {"q": "test"})

        time.sleep(1.5)
        log = manager.get_delivery_log(subscription_id=sub_id)
        assert len(log) >= 1
        entry = log[0]
        assert entry["subscription_id"] == sub_id
        assert entry["event_type"] == "query.received"
        assert entry["success"] is True
        assert entry["url"] == url

    def test_delivery_log_all(self, manager, webhook_server):
        url, received = webhook_server
        manager.register(url, ["query.received"])
        manager.register(url, ["query.matched"])
        manager.emit("query.received", {"q": "a"})
        manager.emit("query.matched", {"q": "b"})

        time.sleep(1.5)
        log = manager.get_delivery_log()
        assert len(log) >= 2

    def test_delivery_log_limit(self, manager, webhook_server):
        url, received = webhook_server
        manager.register(url, ["query.received"])
        for i in range(5):
            manager.emit("query.received", {"i": i})

        time.sleep(2)
        log = manager.get_delivery_log(limit=3)
        assert len(log) <= 3

    def test_delivery_log_empty(self, manager):
        log = manager.get_delivery_log()
        assert log == []


class TestValidEvents:
    def test_all_valid_events(self):
        expected = {
            "query.received",
            "query.matched",
            "query.unmatched",
            "escalation.triggered",
            "feedback.received",
            "faq.updated",
        }
        assert VALID_EVENTS == expected

    def test_register_all_events(self, manager):
        for evt in VALID_EVENTS:
            sub_id = manager.register(f"http://example.com/{evt}", [evt])
            assert sub_id


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestWebhookAPIEndpoints:
    def test_register_webhook(self, client):
        res = client.post(
            "/api/admin/webhooks",
            json={"url": "http://example.com/hook", "events": ["query.received"]},
        )
        assert res.status_code == 201
        data = res.get_json()
        assert data["success"] is True
        assert "subscription_id" in data

    def test_register_webhook_missing_fields(self, client):
        res = client.post("/api/admin/webhooks", json={"url": "http://example.com"})
        assert res.status_code == 400

    def test_register_webhook_invalid_event(self, client):
        res = client.post(
            "/api/admin/webhooks",
            json={"url": "http://example.com/hook", "events": ["invalid"]},
        )
        assert res.status_code == 400

    def test_register_webhook_empty_events(self, client):
        res = client.post(
            "/api/admin/webhooks",
            json={"url": "http://example.com/hook", "events": []},
        )
        assert res.status_code == 400

    def test_list_webhooks(self, client):
        # Register one first
        client.post(
            "/api/admin/webhooks",
            json={"url": "http://example.com/hook", "events": ["query.received"]},
        )
        res = client.get("/api/admin/webhooks")
        assert res.status_code == 200
        data = res.get_json()
        assert "subscriptions" in data
        assert "count" in data

    def test_unregister_webhook(self, client):
        # Register then unregister
        reg = client.post(
            "/api/admin/webhooks",
            json={"url": "http://example.com/hook", "events": ["query.received"]},
        )
        sub_id = reg.get_json()["subscription_id"]

        res = client.delete(f"/api/admin/webhooks/{sub_id}")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True

    def test_unregister_nonexistent(self, client):
        res = client.delete("/api/admin/webhooks/nonexistent-id")
        assert res.status_code == 404

    def test_delivery_log_endpoint(self, client):
        reg = client.post(
            "/api/admin/webhooks",
            json={"url": "http://example.com/hook", "events": ["query.received"]},
        )
        sub_id = reg.get_json()["subscription_id"]

        res = client.get(f"/api/admin/webhooks/{sub_id}/deliveries")
        assert res.status_code == 200
        data = res.get_json()
        assert "deliveries" in data
        assert "count" in data

    def test_test_webhook_endpoint(self, client):
        res = client.post(
            "/api/admin/webhooks/test",
            json={"event_type": "query.received"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "subscribers_notified" in data

    def test_test_webhook_invalid_event(self, client):
        res = client.post(
            "/api/admin/webhooks/test",
            json={"event_type": "bogus.event"},
        )
        assert res.status_code == 400
