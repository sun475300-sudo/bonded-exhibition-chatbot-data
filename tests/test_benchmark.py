"""Pytest-compatible tests to verify benchmark functions work correctly.

Runs each benchmark with a small iteration count (10) to validate
correctness without asserting on timing (to avoid flaky tests).
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.benchmark import (
    benchmark_classifier,
    benchmark_tfidf,
    benchmark_bm25,
    benchmark_session,
    benchmark_spell_correction,
    run_all_benchmarks,
    SAMPLE_FAQ_ITEMS,
)


SMALL_ITERATIONS = 10


class TestBenchmarkClassifier:
    """Verify the classifier benchmark returns reasonable results."""

    def test_returns_expected_keys(self):
        result = benchmark_classifier(SMALL_ITERATIONS)
        assert "name" in result
        assert "iterations" in result
        assert "elapsed_sec" in result
        assert "ops_per_sec" in result

    def test_iterations_match(self):
        result = benchmark_classifier(SMALL_ITERATIONS)
        assert result["iterations"] == SMALL_ITERATIONS

    def test_ops_per_sec_positive(self):
        result = benchmark_classifier(SMALL_ITERATIONS)
        assert result["ops_per_sec"] > 0

    def test_elapsed_non_negative(self):
        result = benchmark_classifier(SMALL_ITERATIONS)
        assert result["elapsed_sec"] >= 0


class TestBenchmarkTFIDF:
    """Verify the TF-IDF benchmark returns reasonable results."""

    def test_returns_expected_keys(self):
        result = benchmark_tfidf(SMALL_ITERATIONS, SAMPLE_FAQ_ITEMS)
        assert "name" in result
        assert "iterations" in result
        assert "elapsed_sec" in result
        assert "ops_per_sec" in result

    def test_iterations_match(self):
        result = benchmark_tfidf(SMALL_ITERATIONS, SAMPLE_FAQ_ITEMS)
        assert result["iterations"] == SMALL_ITERATIONS

    def test_ops_per_sec_positive(self):
        result = benchmark_tfidf(SMALL_ITERATIONS, SAMPLE_FAQ_ITEMS)
        assert result["ops_per_sec"] > 0


class TestBenchmarkBM25:
    """Verify the BM25 benchmark returns reasonable results."""

    def test_returns_expected_keys(self):
        result = benchmark_bm25(SMALL_ITERATIONS, SAMPLE_FAQ_ITEMS)
        assert "name" in result
        assert "iterations" in result
        assert "elapsed_sec" in result
        assert "ops_per_sec" in result

    def test_iterations_match(self):
        result = benchmark_bm25(SMALL_ITERATIONS, SAMPLE_FAQ_ITEMS)
        assert result["iterations"] == SMALL_ITERATIONS

    def test_ops_per_sec_positive(self):
        result = benchmark_bm25(SMALL_ITERATIONS, SAMPLE_FAQ_ITEMS)
        assert result["ops_per_sec"] > 0


class TestBenchmarkSession:
    """Verify the session benchmark returns reasonable results."""

    def test_returns_expected_keys(self):
        result = benchmark_session(SMALL_ITERATIONS)
        assert "name" in result
        assert "iterations" in result
        assert "elapsed_sec" in result
        assert "ops_per_sec" in result
        assert "detail" in result

    def test_detail_has_create_and_lookup(self):
        result = benchmark_session(SMALL_ITERATIONS)
        assert "create_ops_per_sec" in result["detail"]
        assert "lookup_ops_per_sec" in result["detail"]

    def test_iterations_match(self):
        result = benchmark_session(SMALL_ITERATIONS)
        assert result["iterations"] == SMALL_ITERATIONS

    def test_ops_per_sec_positive(self):
        result = benchmark_session(SMALL_ITERATIONS)
        assert result["ops_per_sec"] > 0
        assert result["detail"]["create_ops_per_sec"] > 0
        assert result["detail"]["lookup_ops_per_sec"] > 0


class TestBenchmarkSpellCorrection:
    """Verify the spell correction benchmark returns reasonable results."""

    def test_returns_expected_keys(self):
        result = benchmark_spell_correction(SMALL_ITERATIONS)
        assert "name" in result
        assert "iterations" in result
        assert "elapsed_sec" in result
        assert "ops_per_sec" in result

    def test_iterations_match(self):
        result = benchmark_spell_correction(SMALL_ITERATIONS)
        assert result["iterations"] == SMALL_ITERATIONS

    def test_ops_per_sec_positive(self):
        result = benchmark_spell_correction(SMALL_ITERATIONS)
        assert result["ops_per_sec"] > 0


class TestRunAllBenchmarks:
    """Verify the combined benchmark runner."""

    def test_returns_five_results(self):
        results = run_all_benchmarks(iterations=SMALL_ITERATIONS)
        assert len(results) == 5

    def test_all_results_have_required_keys(self):
        results = run_all_benchmarks(iterations=SMALL_ITERATIONS)
        for r in results:
            assert "name" in r
            assert "iterations" in r
            assert "elapsed_sec" in r
            assert "ops_per_sec" in r

    def test_all_ops_per_sec_positive(self):
        results = run_all_benchmarks(iterations=SMALL_ITERATIONS)
        for r in results:
            assert r["ops_per_sec"] > 0, f"{r['name']} had non-positive ops/sec"

    def test_all_names_unique(self):
        results = run_all_benchmarks(iterations=SMALL_ITERATIONS)
        names = [r["name"] for r in results]
        assert len(names) == len(set(names))
