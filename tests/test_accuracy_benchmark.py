"""Tests for the answer-accuracy benchmark.

Covers:
    - benchmark runs (with a stub chatbot so tests are fast and deterministic)
    - metrics structure (keys, aggregates, by-category breakdown, failures)
    - regression detection via :meth:`AccuracyBenchmark.compare_results`
    - HTML export
    - Admin API endpoints ``/api/admin/benchmark/run`` and
      ``/api/admin/benchmark/history``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.accuracy_benchmark import AccuracyBenchmark, DEFAULT_CATEGORY_ALIASES


GOLDEN_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "golden_testset.json",
)


class _StubChatbot:
    """Deterministic chatbot stub driven by a lookup table.

    ``responses`` is a mapping of question -> (category, faq_id). Any question
    missing from the map falls back to ``default``. This keeps the benchmark
    tests fast and independent of ML/TF-IDF heuristics.
    """

    def __init__(self, responses: dict[str, tuple[str, str]], default=("GENERAL", "A")):
        self._responses = responses
        self._default = default

    def process_query(self, query: str, include_metadata: bool = False):
        cat, _ = self._responses.get(query, self._default)
        result = {
            "response": "stub",
            "intent_id": "stub",
            "intent_confidence": 1.0,
            "category": cat,
            "entities": {},
            "risk_level": "low",
            "policy_decision": {},
            "escalation_triggered": False,
        }
        return result if include_metadata else result["response"]

    def find_matching_faq(self, query: str, category: str):
        _, faq_id = self._responses.get(query, self._default)
        return {"id": faq_id} if faq_id else None


def _make_testset(tmp_path, items):
    payload = {
        "version": "test-1",
        "category_aliases": {"DISPLAY_USE": "EXHIBITION"},
        "items": items,
    }
    path = tmp_path / "testset.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _fresh_benchmark(tmp_path, chatbot):
    db = tmp_path / "bench.db"
    return AccuracyBenchmark(chatbot=chatbot, history_db=str(db))


class TestBenchmarkRuns:
    def test_perfect_run(self, tmp_path):
        items = [
            {"question": "q1", "expected_category": "GENERAL", "expected_faq_id": "A"},
            {"question": "q2", "expected_category": "SALES", "expected_faq_id": "C"},
        ]
        testset = _make_testset(tmp_path, items)
        stub = _StubChatbot({"q1": ("GENERAL", "A"), "q2": ("SALES", "C")})
        bench = _fresh_benchmark(tmp_path, stub)

        metrics = bench.run_benchmark(testset)
        assert metrics["total"] == 2
        assert metrics["correct_category"] == 2
        assert metrics["correct_faq"] == 2
        assert metrics["category_accuracy"] == 1.0
        assert metrics["faq_accuracy"] == 1.0
        assert metrics["failures"] == []

    def test_partial_failures_recorded(self, tmp_path):
        items = [
            {"question": "q1", "expected_category": "GENERAL", "expected_faq_id": "A"},
            {"question": "q2", "expected_category": "SALES", "expected_faq_id": "C"},
            {"question": "q3", "expected_category": "LICENSE", "expected_faq_id": "F"},
        ]
        testset = _make_testset(tmp_path, items)
        stub = _StubChatbot({
            "q1": ("GENERAL", "A"),     # both ok
            "q2": ("SALES", "X"),       # faq wrong
            "q3": ("GENERAL", "F"),     # category wrong (faq still checked against expected)
        })
        bench = _fresh_benchmark(tmp_path, stub)

        metrics = bench.run_benchmark(testset)
        assert metrics["total"] == 3
        assert metrics["correct_category"] == 2
        assert metrics["correct_faq"] == 2  # q1 faq=A, q3 faq=F match
        assert len(metrics["failures"]) == 2

        failure_qs = {f["question"] for f in metrics["failures"]}
        assert failure_qs == {"q2", "q3"}

    def test_alias_resolution(self, tmp_path):
        """DISPLAY_USE should be compared against EXHIBITION under the default alias."""
        items = [
            {"question": "q1", "expected_category": "DISPLAY_USE", "expected_faq_id": "H"},
        ]
        testset = _make_testset(tmp_path, items)
        stub = _StubChatbot({"q1": ("EXHIBITION", "H")})
        bench = _fresh_benchmark(tmp_path, stub)

        metrics = bench.run_benchmark(testset)
        assert metrics["correct_category"] == 1
        assert metrics["correct_faq"] == 1
        # by_category retains the original key from the testset
        assert "DISPLAY_USE" in metrics["by_category"]

    def test_runs_against_real_golden_testset(self, tmp_path):
        """The bundled golden testset should load and evaluate without errors."""
        stub = _StubChatbot({}, default=("GENERAL", "A"))
        bench = _fresh_benchmark(tmp_path, stub)
        metrics = bench.run_benchmark(GOLDEN_PATH)
        # golden_testset may grow as new edge cases are added; require the
        # original 10 categories × 10 items baseline (100) as a floor.
        assert metrics["total"] >= 100
        assert len(metrics["by_category"]) == 10


class TestMetricsStructure:
    def test_metrics_keys_present(self, tmp_path):
        items = [{"question": "q1", "expected_category": "GENERAL", "expected_faq_id": "A"}]
        testset = _make_testset(tmp_path, items)
        stub = _StubChatbot({"q1": ("GENERAL", "A")})
        bench = _fresh_benchmark(tmp_path, stub)
        metrics = bench.run_benchmark(testset)

        for key in (
            "total", "correct_category", "correct_faq",
            "category_accuracy", "faq_accuracy",
            "by_category", "failures",
            "testset_path", "testset_version",
            "timestamp", "duration_sec",
        ):
            assert key in metrics, f"missing key: {key}"

    def test_by_category_structure(self, tmp_path):
        items = [
            {"question": "q1", "expected_category": "GENERAL", "expected_faq_id": "A"},
            {"question": "q2", "expected_category": "GENERAL", "expected_faq_id": "T"},
            {"question": "q3", "expected_category": "SALES", "expected_faq_id": "C"},
        ]
        testset = _make_testset(tmp_path, items)
        stub = _StubChatbot({
            "q1": ("GENERAL", "A"),
            "q2": ("GENERAL", "T"),
            "q3": ("SALES", "X"),
        })
        bench = _fresh_benchmark(tmp_path, stub)
        metrics = bench.run_benchmark(testset)

        general = metrics["by_category"]["GENERAL"]
        sales = metrics["by_category"]["SALES"]
        assert general == {
            "total": 2,
            "correct_category": 2,
            "correct_faq": 2,
            "category_accuracy": 1.0,
            "faq_accuracy": 1.0,
            "accuracy": 1.0,
        }
        assert sales["total"] == 1
        assert sales["correct_category"] == 1
        assert sales["correct_faq"] == 0
        assert sales["faq_accuracy"] == 0.0

    def test_failure_entries_have_expected_and_actual(self, tmp_path):
        items = [{"question": "q1", "expected_category": "GENERAL", "expected_faq_id": "A"}]
        testset = _make_testset(tmp_path, items)
        stub = _StubChatbot({"q1": ("SALES", "X")})
        bench = _fresh_benchmark(tmp_path, stub)

        metrics = bench.run_benchmark(testset)
        assert len(metrics["failures"]) == 1
        failure = metrics["failures"][0]
        assert failure["expected"] == {"category": "GENERAL", "faq_id": "A"}
        assert failure["actual"] == {"category": "SALES", "faq_id": "X"}
        assert failure["category_ok"] is False
        assert failure["faq_ok"] is False
        assert failure["question"] == "q1"


class TestRegressionDetection:
    def _metrics(self, cat_acc, faq_acc, by_cat=None):
        return {
            "total": 100,
            "correct_category": int(cat_acc * 100),
            "correct_faq": int(faq_acc * 100),
            "category_accuracy": cat_acc,
            "faq_accuracy": faq_acc,
            "by_category": by_cat or {},
            "failures": [],
        }

    def test_no_previous_means_no_regression(self):
        bench = AccuracyBenchmark(chatbot=_StubChatbot({}),
                                  history_db=os.path.join(tempfile.mkdtemp(), "h.db"))
        cur = self._metrics(0.9, 0.8)
        result = bench.compare_results(cur, None)
        assert result["regression"] is False
        assert "no previous" in result["summary"]

    def test_improvement_not_flagged(self):
        bench = AccuracyBenchmark(chatbot=_StubChatbot({}),
                                  history_db=os.path.join(tempfile.mkdtemp(), "h.db"))
        prev = self._metrics(0.8, 0.7)
        cur = self._metrics(0.9, 0.75)
        result = bench.compare_results(cur, prev)
        assert result["regression"] is False
        assert result["category_delta"] > 0
        assert result["faq_delta"] > 0

    def test_regression_detected_on_category_drop(self):
        bench = AccuracyBenchmark(chatbot=_StubChatbot({}),
                                  history_db=os.path.join(tempfile.mkdtemp(), "h.db"))
        prev = self._metrics(0.9, 0.8)
        cur = self._metrics(0.85, 0.8)
        result = bench.compare_results(cur, prev)
        assert result["regression"] is True
        assert result["category_delta"] < 0

    def test_per_category_regression(self):
        bench = AccuracyBenchmark(chatbot=_StubChatbot({}),
                                  history_db=os.path.join(tempfile.mkdtemp(), "h.db"))
        prev_by = {"GENERAL": {"category_accuracy": 0.9, "faq_accuracy": 0.9}}
        cur_by = {"GENERAL": {"category_accuracy": 0.7, "faq_accuracy": 0.8}}
        prev = self._metrics(0.9, 0.9, prev_by)
        cur = self._metrics(0.9, 0.9, cur_by)
        result = bench.compare_results(cur, prev)
        assert result["regression"] is True
        assert result["regressed_categories"]
        assert result["regressed_categories"][0]["category"] == "GENERAL"


class TestReportExport:
    def test_export_report_creates_html(self, tmp_path):
        items = [{"question": "q1", "expected_category": "GENERAL", "expected_faq_id": "A"}]
        testset = _make_testset(tmp_path, items)
        stub = _StubChatbot({"q1": ("SALES", "X")})
        bench = _fresh_benchmark(tmp_path, stub)
        metrics = bench.run_benchmark(testset)

        output = tmp_path / "report.html"
        path = bench.export_report(metrics, str(output))
        assert os.path.exists(path)
        content = output.read_text(encoding="utf-8")
        assert "<html" in content.lower()
        assert "Accuracy Benchmark" in content
        assert "SALES" in content  # failure row

    def test_export_report_writes_summary_numbers(self, tmp_path):
        items = [{"question": "q1", "expected_category": "GENERAL", "expected_faq_id": "A"}]
        testset = _make_testset(tmp_path, items)
        stub = _StubChatbot({"q1": ("GENERAL", "A")})
        bench = _fresh_benchmark(tmp_path, stub)
        metrics = bench.run_benchmark(testset)

        output = tmp_path / "report.html"
        bench.export_report(metrics, str(output))
        content = output.read_text(encoding="utf-8")
        assert "100.00%" in content


class TestHistoryPersistence:
    def test_runs_are_persisted_and_sorted(self, tmp_path):
        items = [{"question": "q1", "expected_category": "GENERAL", "expected_faq_id": "A"}]
        testset = _make_testset(tmp_path, items)
        stub = _StubChatbot({"q1": ("GENERAL", "A")})
        bench = _fresh_benchmark(tmp_path, stub)

        bench.run_benchmark(testset)
        bench.run_benchmark(testset)

        history = bench.get_history(limit=5)
        assert len(history) == 2
        assert history[0]["id"] > history[1]["id"]
        latest = bench.get_latest()
        assert latest is not None
        assert latest["total"] == 1


class TestAdminApi:
    @pytest.fixture
    def client(self, monkeypatch, tmp_path):
        # Use a throwaway history db to keep test isolation
        import web_server
        throwaway = AccuracyBenchmark(
            chatbot=_StubChatbot({}, default=("GENERAL", "A")),
            history_db=str(tmp_path / "bench_api.db"),
        )
        monkeypatch.setattr(web_server, "accuracy_benchmark", throwaway)
        web_server.app.config["TESTING"] = True
        with web_server.app.test_client() as c:
            yield c

    def test_run_endpoint(self, client):
        # Point the benchmark at the real golden testset
        testset_path = GOLDEN_PATH
        res = client.post(
            "/api/admin/benchmark/run",
            json={"testset_path": testset_path, "persist": True},
        )
        assert res.status_code == 200, res.get_data(as_text=True)
        data = res.get_json()
        assert "metrics" in data
        assert "comparison" in data
        metrics = data["metrics"]
        # golden_testset grows as colloquial/edge cases are added;
        # keep 100 as the minimum baseline.
        assert metrics["total"] >= 100
        assert "category_accuracy" in metrics

    def test_run_endpoint_missing_file(self, client):
        res = client.post(
            "/api/admin/benchmark/run",
            json={"testset_path": "/no/such/file.json"},
        )
        assert res.status_code == 404

    def test_history_endpoint(self, client):
        # Seed one run first
        client.post(
            "/api/admin/benchmark/run",
            json={"testset_path": GOLDEN_PATH, "persist": True},
        )
        res = client.get("/api/admin/benchmark/history?limit=5")
        assert res.status_code == 200
        data = res.get_json()
        assert "history" in data
        assert "count" in data
        assert data["count"] >= 1
        entry = data["history"][0]
        for key in ("id", "timestamp", "total", "category_accuracy", "faq_accuracy"):
            assert key in entry


def test_default_category_aliases_contains_display_use():
    assert DEFAULT_CATEGORY_ALIASES.get("DISPLAY_USE") == "EXHIBITION"
