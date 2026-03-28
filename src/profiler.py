"""Performance profiling system.

Provides function-level profiling, request profiling middleware,
and component benchmarking for the chatbot pipeline.
"""

import cProfile
import io
import json
import os
import pstats
import time
from typing import Any, Callable


class Profiler:
    """General-purpose profiling utility."""

    def profile_function(self, func: Callable, *args: Any, **kwargs: Any) -> dict:
        """Run *func* under cProfile and return profiling stats.

        Returns:
            dict with keys: result, stats (list of dicts), total_calls, total_time.
        """
        profiler = cProfile.Profile()
        profiler.enable()
        result = func(*args, **kwargs)
        profiler.disable()

        stream = io.StringIO()
        stats = pstats.Stats(profiler, stream=stream)
        stats.sort_stats("cumulative")

        raw_stats = stats.stats  # dict keyed by (file, line, name)
        stat_rows = []
        for (file, line, name), (cc, nc, tt, ct, callers) in raw_stats.items():
            stat_rows.append({
                "file": file,
                "line": line,
                "function": name,
                "ncalls": nc,
                "tottime": round(tt, 6),
                "cumtime": round(ct, 6),
            })

        stat_rows.sort(key=lambda r: r["cumtime"], reverse=True)

        return {
            "result": result,
            "stats": stat_rows,
            "total_calls": stats.total_calls,
            "total_time": round(stats.total_tt, 6),
        }

    def profile_request(self, endpoint: str, method: str, data: dict | None = None) -> dict:
        """Profile a single API request handler invocation.

        This imports the Flask app, builds a request context and dispatches
        the request through the test client, wrapping the call with cProfile.

        Returns:
            dict with profiling stats and response info.
        """
        from web_server import app

        client = app.test_client()

        def _make_request():
            if method.upper() == "GET":
                return client.get(endpoint)
            elif method.upper() == "POST":
                return client.post(endpoint, json=data or {})
            elif method.upper() == "PUT":
                return client.put(endpoint, json=data or {})
            elif method.upper() == "DELETE":
                return client.delete(endpoint)
            else:
                return client.get(endpoint)

        profile_data = self.profile_function(_make_request)
        response = profile_data.pop("result")
        profile_data["response_status"] = response.status_code
        profile_data["endpoint"] = endpoint
        profile_data["method"] = method
        return profile_data

    def get_bottlenecks(self, stats: list[dict], top_n: int = 10) -> list[dict]:
        """Extract the top N slowest functions from profiling stats.

        Args:
            stats: list of stat dicts (as returned by profile_function).
            top_n: how many to return.

        Returns:
            Top N entries sorted by cumtime descending.
        """
        sorted_stats = sorted(stats, key=lambda r: r["cumtime"], reverse=True)
        return sorted_stats[:top_n]

    def generate_report(self, profile_data: dict) -> dict:
        """Create a formatted profiling report dict.

        Args:
            profile_data: output from profile_function or profile_request.

        Returns:
            Formatted report dict.
        """
        stats = profile_data.get("stats", [])
        bottlenecks = self.get_bottlenecks(stats, top_n=10)

        report = {
            "summary": {
                "total_calls": profile_data.get("total_calls", 0),
                "total_time": profile_data.get("total_time", 0),
            },
            "bottlenecks": bottlenecks,
            "all_stats_count": len(stats),
        }

        if "endpoint" in profile_data:
            report["request"] = {
                "endpoint": profile_data["endpoint"],
                "method": profile_data["method"],
                "response_status": profile_data.get("response_status"),
            }

        return report

    def export_report(self, report: dict, output_path: str) -> str:
        """Save a report dict as JSON.

        Args:
            report: report dict from generate_report.
            output_path: filesystem path to write the JSON file.

        Returns:
            The output_path written.
        """
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return output_path


class RequestProfiler:
    """Middleware-style profiler that accumulates stats across requests."""

    def __init__(self):
        self._profiling = False
        self._profiler: cProfile.Profile | None = None
        self._request_count = 0
        self._start_time: float | None = None

    @property
    def is_profiling(self) -> bool:
        return self._profiling

    def start_profiling(self) -> None:
        """Enable profiling mode. Resets any previous accumulated data."""
        self._profiler = cProfile.Profile()
        self._profiling = True
        self._request_count = 0
        self._start_time = time.time()
        self._profiler.enable()

    def stop_profiling(self) -> dict:
        """Disable profiling mode and return accumulated results.

        Returns:
            dict with aggregated profiling summary.
        """
        if self._profiler is None or not self._profiling:
            return {"error": "Profiling is not active."}

        self._profiler.disable()
        self._profiling = False

        stream = io.StringIO()
        stats = pstats.Stats(self._profiler, stream=stream)
        stats.sort_stats("cumulative")

        raw_stats = stats.stats
        stat_rows = []
        for (file, line, name), (cc, nc, tt, ct, callers) in raw_stats.items():
            stat_rows.append({
                "file": file,
                "line": line,
                "function": name,
                "ncalls": nc,
                "tottime": round(tt, 6),
                "cumtime": round(ct, 6),
            })

        stat_rows.sort(key=lambda r: r["cumtime"], reverse=True)

        elapsed = time.time() - self._start_time if self._start_time else 0

        summary = {
            "total_calls": stats.total_calls,
            "total_time": round(stats.total_tt, 6),
            "wall_time": round(elapsed, 6),
            "request_count": self._request_count,
            "top_functions": stat_rows[:20],
        }

        self._profiler = None
        self._start_time = None

        return summary

    def record_request(self) -> None:
        """Increment the tracked request count (call from middleware)."""
        if self._profiling:
            self._request_count += 1

    def get_summary(self) -> dict:
        """Return current aggregated profiling summary without stopping.

        If profiling is not active, returns status info only.
        """
        if not self._profiling or self._profiler is None:
            return {"profiling": False, "message": "Profiling is not active."}

        # Temporarily disable to snapshot stats
        self._profiler.disable()

        stream = io.StringIO()
        stats = pstats.Stats(self._profiler, stream=stream)
        stats.sort_stats("cumulative")

        raw_stats = stats.stats
        stat_rows = []
        for (file, line, name), (cc, nc, tt, ct, callers) in raw_stats.items():
            stat_rows.append({
                "file": file,
                "line": line,
                "function": name,
                "ncalls": nc,
                "tottime": round(tt, 6),
                "cumtime": round(ct, 6),
            })

        stat_rows.sort(key=lambda r: r["cumtime"], reverse=True)

        elapsed = time.time() - self._start_time if self._start_time else 0

        # Re-enable
        self._profiler.enable()

        return {
            "profiling": True,
            "total_calls": stats.total_calls,
            "total_time": round(stats.total_tt, 6),
            "wall_time": round(elapsed, 6),
            "request_count": self._request_count,
            "top_functions": stat_rows[:20],
        }


class ComponentBenchmark:
    """Benchmarks individual pipeline components."""

    def __init__(self):
        self._sample_queries = [
            "보세전시장 특허 신청 방법은?",
            "전시물품 반입 절차를 알려주세요",
            "보세전시장에서 판매가 가능한가요?",
            "견본품 관세는 어떻게 되나요?",
            "시식용 식품 반입 요건이 뭔가요?",
        ]
        self._sample_faq_items = [
            {
                "id": "faq_bench_1",
                "question": "보세전시장 특허 신청은 어떻게 하나요?",
                "keywords": ["특허", "신청", "설치"],
                "category": "LICENSE",
                "answer": "보세전시장 설치·운영 특허를 받으려면 관할 세관장에게 신청서를 제출해야 합니다.",
            },
            {
                "id": "faq_bench_2",
                "question": "전시물품 반입 절차는?",
                "keywords": ["반입", "절차", "물품"],
                "category": "IMPORT_EXPORT",
                "answer": "전시물품을 반입하려면 반입신고서를 작성하여 세관에 제출합니다.",
            },
            {
                "id": "faq_bench_3",
                "question": "보세전시장에서 물품을 판매할 수 있나요?",
                "keywords": ["판매", "직매", "현장판매"],
                "category": "SALES",
                "answer": "보세전시장에서 전시물품의 현장판매는 제한적으로 허용됩니다.",
            },
            {
                "id": "faq_bench_4",
                "question": "견본품 관세는 어떻게 되나요?",
                "keywords": ["견본품", "샘플", "관세"],
                "category": "SAMPLE",
                "answer": "일정 가액 이하의 견본품은 관세가 면제될 수 있습니다.",
            },
            {
                "id": "faq_bench_5",
                "question": "시식용 식품 반입 요건은?",
                "keywords": ["시식", "식품", "요건"],
                "category": "FOOD_TASTING",
                "answer": "시식용 식품을 반입하려면 식약처 요건확인을 받아야 합니다.",
            },
        ]

    @staticmethod
    def _compute_stats(times: list[float]) -> dict:
        """Compute timing statistics from a list of durations."""
        if not times:
            return {"avg_time": 0, "min_time": 0, "max_time": 0, "p95": 0}
        sorted_t = sorted(times)
        n = len(sorted_t)
        p95_idx = min(int(n * 0.95), n - 1)
        return {
            "avg_time": round(sum(sorted_t) / n, 6),
            "min_time": round(sorted_t[0], 6),
            "max_time": round(sorted_t[-1], 6),
            "p95": round(sorted_t[p95_idx], 6),
        }

    def benchmark_classifier(self, iterations: int = 100) -> dict:
        """Profile the keyword classifier over multiple iterations.

        Returns:
            dict with avg_time, min_time, max_time, p95, calls.
        """
        from src.classifier import classify_query

        profiler = Profiler()
        times: list[float] = []
        all_calls = 0

        for _ in range(iterations):
            for query in self._sample_queries:
                start = time.perf_counter()
                result = profiler.profile_function(classify_query, query)
                elapsed = time.perf_counter() - start
                times.append(elapsed)
                all_calls += result.get("total_calls", 0)

        stats = self._compute_stats(times)
        stats["total_iterations"] = iterations
        stats["total_invocations"] = len(times)
        stats["calls_breakdown"] = {"total_profiled_calls": all_calls}
        return stats

    def benchmark_tfidf(self, iterations: int = 100) -> dict:
        """Profile TF-IDF matcher over multiple iterations.

        Returns:
            dict with timing stats and call breakdown.
        """
        from src.similarity import TFIDFMatcher

        matcher = TFIDFMatcher(self._sample_faq_items)
        profiler = Profiler()
        times: list[float] = []
        all_calls = 0

        for _ in range(iterations):
            for query in self._sample_queries:
                start = time.perf_counter()
                result = profiler.profile_function(matcher.find_best_match, query)
                elapsed = time.perf_counter() - start
                times.append(elapsed)
                all_calls += result.get("total_calls", 0)

        stats = self._compute_stats(times)
        stats["total_iterations"] = iterations
        stats["total_invocations"] = len(times)
        stats["calls_breakdown"] = {"total_profiled_calls": all_calls}
        return stats

    def benchmark_bm25(self, iterations: int = 100) -> dict:
        """Profile BM25 ranker over multiple iterations.

        Returns:
            dict with timing stats and call breakdown.
        """
        from src.bm25_ranker import BM25Ranker

        ranker = BM25Ranker(self._sample_faq_items)
        profiler = Profiler()
        times: list[float] = []
        all_calls = 0

        for _ in range(iterations):
            for query in self._sample_queries:
                start = time.perf_counter()
                result = profiler.profile_function(ranker.rank, query)
                elapsed = time.perf_counter() - start
                times.append(elapsed)
                all_calls += result.get("total_calls", 0)

        stats = self._compute_stats(times)
        stats["total_iterations"] = iterations
        stats["total_invocations"] = len(times)
        stats["calls_breakdown"] = {"total_profiled_calls": all_calls}
        return stats

    def benchmark_full_pipeline(self, iterations: int = 50) -> dict:
        """Profile the entire query pipeline (classify + match).

        Returns:
            dict with timing stats and call breakdown.
        """
        from src.classifier import classify_query
        from src.similarity import TFIDFMatcher
        from src.bm25_ranker import BM25Ranker

        matcher = TFIDFMatcher(self._sample_faq_items)
        ranker = BM25Ranker(self._sample_faq_items)
        profiler = Profiler()
        times: list[float] = []
        all_calls = 0

        def _run_pipeline(query: str):
            categories = classify_query(query)
            category = categories[0] if categories else "GENERAL"
            tfidf_results = matcher.find_best_match(query, category=category)
            bm25_results = ranker.rank(query, category=category)
            return {
                "categories": categories,
                "tfidf_results": tfidf_results,
                "bm25_results": bm25_results,
            }

        for _ in range(iterations):
            for query in self._sample_queries:
                start = time.perf_counter()
                result = profiler.profile_function(_run_pipeline, query)
                elapsed = time.perf_counter() - start
                times.append(elapsed)
                all_calls += result.get("total_calls", 0)

        stats = self._compute_stats(times)
        stats["total_iterations"] = iterations
        stats["total_invocations"] = len(times)
        stats["calls_breakdown"] = {"total_profiled_calls": all_calls}
        return stats
