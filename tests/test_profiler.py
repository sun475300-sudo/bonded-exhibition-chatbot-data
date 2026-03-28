"""Performance profiler tests."""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.profiler import ComponentBenchmark, Profiler, RequestProfiler


# ---------------------------------------------------------------------------
# Profiler
# ---------------------------------------------------------------------------

class TestProfiler:
    def setup_method(self):
        self.profiler = Profiler()

    def test_profile_function_returns_stats(self):
        def add(a, b):
            return a + b

        result = self.profiler.profile_function(add, 1, 2)
        assert result["result"] == 3
        assert isinstance(result["stats"], list)
        assert result["total_calls"] > 0
        assert result["total_time"] >= 0

    def test_profile_function_with_kwargs(self):
        def greet(name="world"):
            return f"hello {name}"

        result = self.profiler.profile_function(greet, name="test")
        assert result["result"] == "hello test"

    def test_profile_function_stat_structure(self):
        def dummy():
            return sum(range(100))

        result = self.profiler.profile_function(dummy)
        for stat in result["stats"]:
            assert "file" in stat
            assert "line" in stat
            assert "function" in stat
            assert "ncalls" in stat
            assert "tottime" in stat
            assert "cumtime" in stat


# ---------------------------------------------------------------------------
# Bottleneck extraction
# ---------------------------------------------------------------------------

class TestBottleneckExtraction:
    def setup_method(self):
        self.profiler = Profiler()

    def test_get_bottlenecks_returns_top_n(self):
        stats = [
            {"file": "a.py", "line": 1, "function": "a", "ncalls": 1, "tottime": 0.1, "cumtime": 0.5},
            {"file": "b.py", "line": 2, "function": "b", "ncalls": 2, "tottime": 0.3, "cumtime": 0.8},
            {"file": "c.py", "line": 3, "function": "c", "ncalls": 3, "tottime": 0.2, "cumtime": 0.3},
        ]
        bottlenecks = self.profiler.get_bottlenecks(stats, top_n=2)
        assert len(bottlenecks) == 2
        assert bottlenecks[0]["function"] == "b"
        assert bottlenecks[1]["function"] == "a"

    def test_get_bottlenecks_with_empty_stats(self):
        bottlenecks = self.profiler.get_bottlenecks([], top_n=5)
        assert bottlenecks == []

    def test_get_bottlenecks_default_top_n(self):
        stats = [
            {"file": f"f{i}.py", "line": i, "function": f"fn{i}", "ncalls": 1, "tottime": 0.01, "cumtime": 0.01 * i}
            for i in range(15)
        ]
        bottlenecks = self.profiler.get_bottlenecks(stats)
        assert len(bottlenecks) == 10


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

class TestReportGeneration:
    def setup_method(self):
        self.profiler = Profiler()

    def test_generate_report_structure(self):
        def dummy():
            return 42

        profile_data = self.profiler.profile_function(dummy)
        report = self.profiler.generate_report(profile_data)
        assert "summary" in report
        assert "bottlenecks" in report
        assert "all_stats_count" in report
        assert report["summary"]["total_calls"] > 0

    def test_generate_report_with_request_info(self):
        profile_data = {
            "stats": [],
            "total_calls": 10,
            "total_time": 0.5,
            "endpoint": "/api/test",
            "method": "GET",
            "response_status": 200,
        }
        report = self.profiler.generate_report(profile_data)
        assert "request" in report
        assert report["request"]["endpoint"] == "/api/test"
        assert report["request"]["method"] == "GET"

    def test_export_report_creates_json(self):
        report = {
            "summary": {"total_calls": 5, "total_time": 0.1},
            "bottlenecks": [],
            "all_stats_count": 0,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "report.json")
            result = self.profiler.export_report(report, path)
            assert result == path
            assert os.path.exists(path)
            with open(path) as f:
                loaded = json.load(f)
            assert loaded["summary"]["total_calls"] == 5

    def test_export_report_creates_subdirs(self):
        report = {"summary": {}}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sub", "dir", "report.json")
            self.profiler.export_report(report, path)
            assert os.path.exists(path)


# ---------------------------------------------------------------------------
# RequestProfiler
# ---------------------------------------------------------------------------

class TestRequestProfiler:
    def setup_method(self):
        self.rp = RequestProfiler()

    def test_initial_state(self):
        assert not self.rp.is_profiling

    def test_start_stop(self):
        self.rp.start_profiling()
        assert self.rp.is_profiling
        result = self.rp.stop_profiling()
        assert not self.rp.is_profiling
        assert "total_calls" in result
        assert "total_time" in result

    def test_stop_without_start(self):
        result = self.rp.stop_profiling()
        assert "error" in result

    def test_record_request_increments_count(self):
        self.rp.start_profiling()
        self.rp.record_request()
        self.rp.record_request()
        summary = self.rp.get_summary()
        assert summary["request_count"] == 2
        self.rp.stop_profiling()

    def test_get_summary_when_inactive(self):
        summary = self.rp.get_summary()
        assert summary["profiling"] is False

    def test_get_summary_when_active(self):
        self.rp.start_profiling()
        summary = self.rp.get_summary()
        assert summary["profiling"] is True
        assert "total_calls" in summary
        assert "wall_time" in summary
        self.rp.stop_profiling()

    def test_start_resets_previous_data(self):
        self.rp.start_profiling()
        self.rp.record_request()
        self.rp.record_request()
        self.rp.stop_profiling()
        self.rp.start_profiling()
        summary = self.rp.get_summary()
        assert summary["request_count"] == 0
        self.rp.stop_profiling()


# ---------------------------------------------------------------------------
# ComponentBenchmark (small iterations)
# ---------------------------------------------------------------------------

class TestComponentBenchmark:
    def setup_method(self):
        self.bench = ComponentBenchmark()

    def _check_benchmark_result(self, result):
        assert "avg_time" in result
        assert "min_time" in result
        assert "max_time" in result
        assert "p95" in result
        assert "calls_breakdown" in result
        assert result["avg_time"] >= 0
        assert result["min_time"] <= result["avg_time"]
        assert result["max_time"] >= result["avg_time"]

    def test_benchmark_classifier(self):
        result = self.bench.benchmark_classifier(iterations=2)
        self._check_benchmark_result(result)
        assert result["total_iterations"] == 2

    def test_benchmark_tfidf(self):
        result = self.bench.benchmark_tfidf(iterations=2)
        self._check_benchmark_result(result)

    def test_benchmark_bm25(self):
        result = self.bench.benchmark_bm25(iterations=2)
        self._check_benchmark_result(result)

    def test_benchmark_full_pipeline(self):
        result = self.bench.benchmark_full_pipeline(iterations=2)
        self._check_benchmark_result(result)
        assert result["total_iterations"] == 2


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestProfilerAPI:
    @pytest.fixture(autouse=True)
    def setup_client(self):
        from web_server import app, request_profiler
        app.config["TESTING"] = True
        # Ensure profiler is stopped before each test
        if request_profiler.is_profiling:
            request_profiler.stop_profiling()
        self.client = app.test_client()
        self.request_profiler = request_profiler

    def test_status_initially_off(self):
        res = self.client.get("/api/admin/profiler/status")
        assert res.status_code == 200
        data = res.get_json()
        assert data["profiling"] is False

    def test_start_profiling(self):
        res = self.client.post("/api/admin/profiler/start")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        # Cleanup
        self.request_profiler.stop_profiling()

    def test_start_when_already_active(self):
        self.client.post("/api/admin/profiler/start")
        res = self.client.post("/api/admin/profiler/start")
        assert res.status_code == 200
        data = res.get_json()
        assert "already active" in data["message"].lower()
        self.request_profiler.stop_profiling()

    def test_stop_profiling(self):
        self.client.post("/api/admin/profiler/start")
        res = self.client.post("/api/admin/profiler/stop")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "results" in data

    def test_stop_when_not_active(self):
        res = self.client.post("/api/admin/profiler/stop")
        assert res.status_code == 400

    def test_benchmark_endpoint(self):
        res = self.client.get("/api/admin/profiler/benchmark?iterations=1")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "benchmarks" in data
        for component in ("classifier", "tfidf", "bm25", "full_pipeline"):
            assert component in data["benchmarks"]

    def test_status_after_start(self):
        self.client.post("/api/admin/profiler/start")
        res = self.client.get("/api/admin/profiler/status")
        data = res.get_json()
        assert data["profiling"] is True
        self.request_profiler.stop_profiling()
