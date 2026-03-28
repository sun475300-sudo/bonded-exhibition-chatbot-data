"""Tests for src/metrics.py -- counters, histograms, gauges, thread safety, text format."""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from src.metrics import MetricsCollector


@pytest.fixture
def collector():
    return MetricsCollector()


# ------------------------------------------------------------------
# Counter tests
# ------------------------------------------------------------------

class TestCounter:
    def test_increment_default(self, collector):
        collector.increment("request_count", {"endpoint": "/api/chat", "method": "POST", "status": "200"})
        output = collector.collect()
        assert 'request_count{endpoint="/api/chat",method="POST",status="200"} 1' in output

    def test_increment_custom_value(self, collector):
        collector.increment("request_count", {"endpoint": "/api/health", "method": "GET", "status": "200"}, value=5)
        output = collector.collect()
        assert 'request_count{endpoint="/api/health",method="GET",status="200"} 5' in output

    def test_increment_multiple(self, collector):
        labels = {"endpoint": "/api/chat", "method": "POST", "status": "200"}
        collector.increment("request_count", labels)
        collector.increment("request_count", labels)
        collector.increment("request_count", labels)
        output = collector.collect()
        assert 'request_count{endpoint="/api/chat",method="POST",status="200"} 3' in output

    def test_increment_no_labels(self, collector):
        collector.register_counter("my_counter", "A test counter")
        collector.increment("my_counter")
        collector.increment("my_counter")
        output = collector.collect()
        assert "my_counter 2" in output

    def test_counter_type_line(self, collector):
        collector.increment("request_count", {"endpoint": "/", "method": "GET", "status": "200"})
        output = collector.collect()
        assert "# TYPE request_count counter" in output
        assert "# HELP request_count" in output


# ------------------------------------------------------------------
# Histogram tests
# ------------------------------------------------------------------

class TestHistogram:
    def test_observe_single(self, collector):
        collector.observe("request_duration_seconds", 0.15, {"endpoint": "/api/chat"})
        output = collector.collect()
        assert "# TYPE request_duration_seconds histogram" in output
        assert "request_duration_seconds_count" in output
        assert "request_duration_seconds_sum" in output
        assert "request_duration_seconds_bucket" in output

    def test_observe_buckets(self, collector):
        collector.observe("request_duration_seconds", 0.05, {"endpoint": "/api/chat"})
        output = collector.collect()
        # 0.05 should fall into 0.05, 0.1, 0.25, ... buckets
        # The 0.005 and 0.01 buckets should be 0, the 0.025 bucket should be 0
        lines = output.split("\n")
        bucket_lines = [l for l in lines if "request_duration_seconds_bucket" in l and "endpoint" in l]
        assert len(bucket_lines) > 0

    def test_observe_count_and_sum(self, collector):
        collector.observe("request_duration_seconds", 0.1, {"endpoint": "/test"})
        collector.observe("request_duration_seconds", 0.3, {"endpoint": "/test"})
        output = collector.collect()
        assert 'request_duration_seconds_count{endpoint="/test"} 2' in output
        assert 'request_duration_seconds_sum{endpoint="/test"} 0.4' in output

    def test_observe_inf_bucket(self, collector):
        collector.observe("request_duration_seconds", 0.1, {"endpoint": "/x"})
        output = collector.collect()
        assert 'le="+Inf"' in output


# ------------------------------------------------------------------
# Gauge tests
# ------------------------------------------------------------------

class TestGauge:
    def test_set_gauge(self, collector):
        collector.set_gauge("active_sessions", 42)
        output = collector.collect()
        assert "active_sessions 42" in output

    def test_set_gauge_overwrite(self, collector):
        collector.set_gauge("faq_count", 10)
        collector.set_gauge("faq_count", 25)
        output = collector.collect()
        assert "faq_count 25" in output
        assert "faq_count 10" not in output

    def test_set_gauge_float(self, collector):
        collector.set_gauge("cache_hit_rate", 0.85)
        output = collector.collect()
        assert "cache_hit_rate 0.85" in output

    def test_gauge_type_line(self, collector):
        collector.set_gauge("active_sessions", 1)
        output = collector.collect()
        assert "# TYPE active_sessions gauge" in output


# ------------------------------------------------------------------
# Thread safety tests
# ------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_increments(self, collector):
        """Many threads incrementing the same counter should not lose counts."""
        num_threads = 20
        increments_per_thread = 500
        labels = {"endpoint": "/api/chat", "method": "POST", "status": "200"}
        barrier = threading.Barrier(num_threads)

        def worker():
            barrier.wait()
            for _ in range(increments_per_thread):
                collector.increment("request_count", labels)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        output = collector.collect()
        expected = num_threads * increments_per_thread
        assert f'request_count{{endpoint="/api/chat",method="POST",status="200"}} {expected}' in output

    def test_concurrent_observations(self, collector):
        """Many threads observing a histogram concurrently."""
        num_threads = 10
        obs_per_thread = 100
        labels = {"endpoint": "/api/chat"}
        barrier = threading.Barrier(num_threads)

        def worker():
            barrier.wait()
            for _ in range(obs_per_thread):
                collector.observe("request_duration_seconds", 0.05, labels)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        output = collector.collect()
        expected_count = num_threads * obs_per_thread
        assert f'request_duration_seconds_count{{endpoint="/api/chat"}} {expected_count}' in output


# ------------------------------------------------------------------
# Text format tests
# ------------------------------------------------------------------

class TestTextFormat:
    def test_empty_collect(self):
        """A fresh collector produces valid output (just metadata/empty)."""
        c = MetricsCollector()
        output = c.collect()
        # Should be a string (may be empty-ish, but no errors)
        assert isinstance(output, str)

    def test_full_format(self, collector):
        collector.increment("request_count", {"endpoint": "/api/chat", "method": "POST", "status": "200"})
        collector.observe("request_duration_seconds", 0.25, {"endpoint": "/api/chat"})
        collector.set_gauge("active_sessions", 5)
        collector.set_gauge("faq_count", 30)
        collector.set_gauge("cache_hit_rate", 0.92)

        output = collector.collect()

        # Contains all metric families
        assert "request_count" in output
        assert "request_duration_seconds" in output
        assert "active_sessions" in output
        assert "faq_count" in output
        assert "cache_hit_rate" in output

        # Contains HELP and TYPE for registered metrics
        assert "# HELP" in output
        assert "# TYPE" in output

    def test_multiple_label_sets(self, collector):
        collector.increment("request_count", {"endpoint": "/a", "method": "GET", "status": "200"})
        collector.increment("request_count", {"endpoint": "/b", "method": "POST", "status": "400"})
        output = collector.collect()
        assert 'endpoint="/a"' in output
        assert 'endpoint="/b"' in output
