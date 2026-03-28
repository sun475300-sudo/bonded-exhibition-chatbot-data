"""Tests for A/B testing system."""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ab_testing import ABTestManager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def tmp_db(tmp_path):
    """Return a temporary database path."""
    return str(tmp_path / "ab_tests.db")


@pytest.fixture
def tmp_faq(tmp_path):
    """Create a temporary FAQ file with test data."""
    faq_data = {
        "faq_version": "3.0.0",
        "last_updated": "2026-01-01",
        "items": [
            {
                "id": "A",
                "category": "GENERAL",
                "question": "What is a bonded exhibition?",
                "answer": "Original answer A",
                "legal_basis": [],
                "notes": "",
                "keywords": ["bonded"],
            },
            {
                "id": "B",
                "category": "IMPORT_EXPORT",
                "question": "How to import?",
                "answer": "Original answer B",
                "legal_basis": [],
                "notes": "",
                "keywords": ["import"],
            },
        ],
    }
    faq_path = str(tmp_path / "faq.json")
    with open(faq_path, "w", encoding="utf-8") as f:
        json.dump(faq_data, f, ensure_ascii=False, indent=2)
    return faq_path


@pytest.fixture
def manager(tmp_db, tmp_faq):
    """Create a fresh ABTestManager with temp DB and FAQ."""
    return ABTestManager(db_path=tmp_db, faq_path=tmp_faq)


def _make_variants():
    return [
        {"name": "Control", "answer": "Answer version A"},
        {"name": "Variant B", "answer": "Answer version B"},
    ]


class TestCreateTest:
    def test_create_basic(self, manager):
        result = manager.create_test("Test 1", "A", _make_variants())
        assert result["name"] == "Test 1"
        assert result["faq_id"] == "A"
        assert result["active"] is True
        assert len(result["variants"]) == 2
        assert result["variants"][0]["name"] == "Control"
        assert result["variants"][1]["name"] == "Variant B"

    def test_create_with_three_variants(self, manager):
        variants = _make_variants() + [{"name": "Variant C", "answer": "Answer C"}]
        result = manager.create_test("Test 3v", "A", variants)
        assert len(result["variants"]) == 3

    def test_create_missing_name(self, manager):
        with pytest.raises(ValueError, match="name"):
            manager.create_test("", "A", _make_variants())

    def test_create_missing_faq_id(self, manager):
        with pytest.raises(ValueError, match="FAQ ID"):
            manager.create_test("Test", "", _make_variants())

    def test_create_too_few_variants(self, manager):
        with pytest.raises(ValueError, match="At least 2"):
            manager.create_test("Test", "A", [{"name": "Solo", "answer": "Only one"}])

    def test_create_no_variants(self, manager):
        with pytest.raises(ValueError):
            manager.create_test("Test", "A", [])

    def test_create_variant_missing_answer(self, manager):
        with pytest.raises(ValueError, match="answer"):
            manager.create_test("Test", "A", [
                {"name": "V1", "answer": "ok"},
                {"name": "V2"},
            ])


class TestListTests:
    def test_list_empty(self, manager):
        tests = manager.list_tests()
        assert tests == []

    def test_list_active_only(self, manager):
        manager.create_test("Active", "A", _make_variants())
        t2 = manager.create_test("To Stop", "B", _make_variants())
        manager.stop_test(t2["id"])

        active = manager.list_tests(active_only=True)
        assert len(active) == 1
        assert active[0]["name"] == "Active"

    def test_list_all(self, manager):
        manager.create_test("T1", "A", _make_variants())
        t2 = manager.create_test("T2", "B", _make_variants())
        manager.stop_test(t2["id"])

        all_tests = manager.list_tests(active_only=False)
        assert len(all_tests) == 2

    def test_list_includes_variant_count(self, manager):
        manager.create_test("Test", "A", _make_variants())
        tests = manager.list_tests()
        assert tests[0]["variant_count"] == 2

    def test_list_includes_total_impressions(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        vid = result["variants"][0]["id"]
        manager.record_impression(result["id"], vid, "sess1")
        tests = manager.list_tests()
        assert tests[0]["total_impressions"] == 1


class TestStopTest:
    def test_stop_active(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        assert manager.stop_test(result["id"]) is True

    def test_stop_already_stopped(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        manager.stop_test(result["id"])
        assert manager.stop_test(result["id"]) is False

    def test_stop_nonexistent(self, manager):
        assert manager.stop_test("nonexistent") is False

    def test_stopped_test_has_timestamp(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        manager.stop_test(result["id"])
        results = manager.get_results(result["id"])
        assert results["stopped_at"] is not None
        assert results["active"] is False


class TestVariantAssignment:
    def test_consistent_assignment(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        test_id = result["id"]

        # Same session should always get same variant
        v1 = manager.get_variant(test_id, "session_abc")
        v2 = manager.get_variant(test_id, "session_abc")
        assert v1["id"] == v2["id"]
        assert v1["answer"] == v2["answer"]

    def test_different_sessions_can_differ(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        test_id = result["id"]

        # With enough sessions, both variants should be assigned
        assigned_ids = set()
        for i in range(100):
            v = manager.get_variant(test_id, f"session_{i}")
            assigned_ids.add(v["id"])

        assert len(assigned_ids) == 2, "Both variants should be assigned across sessions"

    def test_inactive_test_returns_none(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        manager.stop_test(result["id"])
        assert manager.get_variant(result["id"], "session_1") is None

    def test_nonexistent_test_returns_none(self, manager):
        assert manager.get_variant("nonexistent", "session_1") is None


class TestImpressionRecording:
    def test_record_impression(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        vid = result["variants"][0]["id"]
        assert manager.record_impression(result["id"], vid, "sess1") is True

    def test_record_multiple_impressions(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        vid = result["variants"][0]["id"]
        manager.record_impression(result["id"], vid, "sess1")
        manager.record_impression(result["id"], vid, "sess2")
        manager.record_impression(result["id"], vid, "sess3")

        results = manager.get_results(result["id"])
        v = [v for v in results["variants"] if v["id"] == vid][0]
        assert v["impressions"] == 3

    def test_impression_nonexistent_test(self, manager):
        assert manager.record_impression("bad_id", "vid", "sess") is False


class TestConversionRecording:
    def test_record_conversion(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        vid = result["variants"][0]["id"]
        assert manager.record_conversion(result["id"], vid, "sess1", "helpful_rate") is True

    def test_record_invalid_metric(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        vid = result["variants"][0]["id"]
        with pytest.raises(ValueError, match="Invalid metric"):
            manager.record_conversion(result["id"], vid, "sess1", "bad_metric")

    def test_conversion_nonexistent_test(self, manager):
        assert manager.record_conversion("bad_id", "vid", "sess", "helpful_rate") is False

    def test_all_valid_metrics(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        vid = result["variants"][0]["id"]
        for metric in ["helpful_rate", "escalation_rate", "follow_up_rate"]:
            assert manager.record_conversion(result["id"], vid, "sess1", metric) is True


class TestResults:
    def test_results_structure(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        results = manager.get_results(result["id"])
        assert "test_id" in results
        assert "name" in results
        assert "faq_id" in results
        assert "active" in results
        assert "variants" in results
        assert "significant" in results
        assert "confidence" in results

    def test_results_variant_structure(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        vid = result["variants"][0]["id"]
        manager.record_impression(result["id"], vid, "sess1")

        results = manager.get_results(result["id"])
        v = results["variants"][0]
        assert "id" in v
        assert "name" in v
        assert "answer" in v
        assert "impressions" in v
        assert "conversions" in v
        assert "conversion_rate" in v
        assert "metrics" in v

    def test_conversion_rate_calculation(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        vid = result["variants"][0]["id"]

        # 10 impressions, 3 conversions => 0.3
        for i in range(10):
            manager.record_impression(result["id"], vid, f"sess_{i}")
        for i in range(3):
            manager.record_conversion(result["id"], vid, f"sess_{i}", "helpful_rate")

        results = manager.get_results(result["id"])
        v = [v for v in results["variants"] if v["id"] == vid][0]
        assert v["impressions"] == 10
        assert v["conversions"] == 3
        assert v["conversion_rate"] == 0.3

    def test_zero_impressions(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        results = manager.get_results(result["id"])
        for v in results["variants"]:
            assert v["impressions"] == 0
            assert v["conversion_rate"] == 0.0

    def test_metrics_breakdown(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        vid = result["variants"][0]["id"]
        manager.record_conversion(result["id"], vid, "s1", "helpful_rate")
        manager.record_conversion(result["id"], vid, "s2", "helpful_rate")
        manager.record_conversion(result["id"], vid, "s3", "escalation_rate")

        results = manager.get_results(result["id"])
        v = [v for v in results["variants"] if v["id"] == vid][0]
        assert v["metrics"]["helpful_rate"] == 2
        assert v["metrics"]["escalation_rate"] == 1
        assert v["metrics"]["follow_up_rate"] == 0

    def test_nonexistent_test_results(self, manager):
        assert manager.get_results("nonexistent") is None


class TestWinnerDetection:
    def test_winner_basic(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        v0 = result["variants"][0]["id"]
        v1 = result["variants"][1]["id"]

        # v0: 10 impressions, 2 conversions (20%)
        for i in range(10):
            manager.record_impression(result["id"], v0, f"a_{i}")
        for i in range(2):
            manager.record_conversion(result["id"], v0, f"a_{i}", "helpful_rate")

        # v1: 10 impressions, 7 conversions (70%)
        for i in range(10):
            manager.record_impression(result["id"], v1, f"b_{i}")
        for i in range(7):
            manager.record_conversion(result["id"], v1, f"b_{i}", "helpful_rate")

        winner = manager.get_winner(result["id"])
        assert winner is not None
        assert winner["variant"]["id"] == v1
        assert winner["variant"]["conversion_rate"] == 0.7

    def test_winner_no_impressions(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        winner = manager.get_winner(result["id"])
        assert winner is None

    def test_winner_nonexistent_test(self, manager):
        assert manager.get_winner("nonexistent") is None

    def test_winner_with_significance(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        v0 = result["variants"][0]["id"]
        v1 = result["variants"][1]["id"]

        # Large sample with clear difference for significance
        for i in range(200):
            manager.record_impression(result["id"], v0, f"a_{i}")
        for i in range(20):
            manager.record_conversion(result["id"], v0, f"a_{i}", "helpful_rate")

        for i in range(200):
            manager.record_impression(result["id"], v1, f"b_{i}")
        for i in range(120):
            manager.record_conversion(result["id"], v1, f"b_{i}", "helpful_rate")

        winner = manager.get_winner(result["id"])
        assert winner is not None
        assert winner["significant"] is True
        assert winner["confidence"] >= 0.95


class TestApplyWinner:
    def test_apply_winner(self, manager, tmp_faq):
        result = manager.create_test("Test", "A", _make_variants())
        v0 = result["variants"][0]["id"]
        v1 = result["variants"][1]["id"]

        # Make v1 the winner
        for i in range(10):
            manager.record_impression(result["id"], v0, f"a_{i}")
            manager.record_impression(result["id"], v1, f"b_{i}")
        for i in range(2):
            manager.record_conversion(result["id"], v0, f"a_{i}", "helpful_rate")
        for i in range(8):
            manager.record_conversion(result["id"], v1, f"b_{i}", "helpful_rate")

        applied = manager.apply_winner(result["id"])
        assert applied["faq_id"] == "A"
        assert applied["applied_variant"]["id"] == v1

        # Check FAQ file was updated
        with open(tmp_faq, "r") as f:
            faq_data = json.load(f)
        item_a = [i for i in faq_data["items"] if i["id"] == "A"][0]
        assert item_a["answer"] == "Answer version B"

    def test_apply_winner_stops_test(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        v0 = result["variants"][0]["id"]
        for i in range(5):
            manager.record_impression(result["id"], v0, f"s_{i}")
        manager.record_conversion(result["id"], v0, "s_0", "helpful_rate")

        manager.apply_winner(result["id"])
        results = manager.get_results(result["id"])
        assert results["active"] is False

    def test_apply_winner_no_impressions(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        with pytest.raises(ValueError, match="No winner"):
            manager.apply_winner(result["id"])

    def test_apply_winner_nonexistent_faq(self, manager, tmp_faq):
        result = manager.create_test("Test", "NONEXISTENT", _make_variants())
        vid = result["variants"][0]["id"]
        manager.record_impression(result["id"], vid, "s1")
        manager.record_conversion(result["id"], vid, "s1", "helpful_rate")
        with pytest.raises(ValueError, match="not found"):
            manager.apply_winner(result["id"])


class TestStatisticalSignificance:
    def test_no_data_not_significant(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        results = manager.get_results(result["id"])
        assert results["significant"] is False
        assert results["confidence"] == 0.0

    def test_equal_rates_not_significant(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        v0 = result["variants"][0]["id"]
        v1 = result["variants"][1]["id"]

        # Equal conversion rates
        for i in range(50):
            manager.record_impression(result["id"], v0, f"a_{i}")
            manager.record_impression(result["id"], v1, f"b_{i}")
            manager.record_conversion(result["id"], v0, f"a_{i}", "helpful_rate")
            manager.record_conversion(result["id"], v1, f"b_{i}", "helpful_rate")

        results = manager.get_results(result["id"])
        # Equal rates => should not be significant
        assert results["significant"] is False

    def test_large_difference_significant(self, manager):
        result = manager.create_test("Test", "A", _make_variants())
        v0 = result["variants"][0]["id"]
        v1 = result["variants"][1]["id"]

        # Very different rates with large sample
        for i in range(300):
            manager.record_impression(result["id"], v0, f"a_{i}")
            manager.record_impression(result["id"], v1, f"b_{i}")
        for i in range(30):
            manager.record_conversion(result["id"], v0, f"a_{i}", "helpful_rate")
        for i in range(210):
            manager.record_conversion(result["id"], v1, f"b_{i}", "helpful_rate")

        results = manager.get_results(result["id"])
        assert results["significant"] is True
        assert results["confidence"] >= 0.95


# ── API Endpoint Tests ───────────────────────────────────────────────────

from web_server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client with temporary A/B test DB."""
    import web_server

    tmp_db = str(tmp_path / "ab_tests_api.db")
    tmp_faq = str(tmp_path / "faq.json")

    # Write temp FAQ
    faq_data = {
        "faq_version": "3.0.0",
        "last_updated": "2026-01-01",
        "items": [
            {
                "id": "A",
                "category": "GENERAL",
                "question": "Test Q",
                "answer": "Original",
                "legal_basis": [],
                "notes": "",
                "keywords": [],
            }
        ],
    }
    with open(tmp_faq, "w") as f:
        json.dump(faq_data, f)

    test_manager = ABTestManager(db_path=tmp_db, faq_path=tmp_faq)
    monkeypatch.setattr(web_server, "ab_test_manager", test_manager)

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestAPICreateTest:
    def test_create_test(self, client):
        res = client.post("/api/admin/ab-tests", json={
            "name": "API Test",
            "faq_id": "A",
            "variants": _make_variants(),
        })
        assert res.status_code == 201
        data = res.get_json()
        assert data["name"] == "API Test"
        assert len(data["variants"]) == 2

    def test_create_test_validation_error(self, client):
        res = client.post("/api/admin/ab-tests", json={
            "name": "",
            "faq_id": "A",
            "variants": _make_variants(),
        })
        assert res.status_code == 400

    def test_create_test_no_body(self, client):
        res = client.post("/api/admin/ab-tests", content_type="application/json")
        assert res.status_code == 400


class TestAPIListTests:
    def test_list_empty(self, client):
        res = client.get("/api/admin/ab-tests")
        assert res.status_code == 200
        data = res.get_json()
        assert data["count"] == 0
        assert data["tests"] == []

    def test_list_with_tests(self, client):
        client.post("/api/admin/ab-tests", json={
            "name": "T1", "faq_id": "A", "variants": _make_variants(),
        })
        res = client.get("/api/admin/ab-tests")
        data = res.get_json()
        assert data["count"] == 1

    def test_list_all_including_stopped(self, client):
        r = client.post("/api/admin/ab-tests", json={
            "name": "T1", "faq_id": "A", "variants": _make_variants(),
        })
        test_id = r.get_json()["id"]
        client.post(f"/api/admin/ab-tests/{test_id}/stop")

        res = client.get("/api/admin/ab-tests?active_only=false")
        data = res.get_json()
        assert data["count"] == 1


class TestAPIResults:
    def test_get_results(self, client):
        r = client.post("/api/admin/ab-tests", json={
            "name": "T1", "faq_id": "A", "variants": _make_variants(),
        })
        test_id = r.get_json()["id"]
        res = client.get(f"/api/admin/ab-tests/{test_id}/results")
        assert res.status_code == 200
        data = res.get_json()
        assert data["test_id"] == test_id

    def test_results_not_found(self, client):
        res = client.get("/api/admin/ab-tests/nonexistent/results")
        assert res.status_code == 404


class TestAPIStop:
    def test_stop_test(self, client):
        r = client.post("/api/admin/ab-tests", json={
            "name": "T1", "faq_id": "A", "variants": _make_variants(),
        })
        test_id = r.get_json()["id"]
        res = client.post(f"/api/admin/ab-tests/{test_id}/stop")
        assert res.status_code == 200
        assert res.get_json()["success"] is True

    def test_stop_not_found(self, client):
        res = client.post("/api/admin/ab-tests/nonexistent/stop")
        assert res.status_code == 404


class TestAPIApplyWinner:
    def test_apply_winner_no_data(self, client):
        r = client.post("/api/admin/ab-tests", json={
            "name": "T1", "faq_id": "A", "variants": _make_variants(),
        })
        test_id = r.get_json()["id"]
        res = client.post(f"/api/admin/ab-tests/{test_id}/apply-winner")
        assert res.status_code == 400
