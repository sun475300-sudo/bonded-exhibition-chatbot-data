#!/usr/bin/env python3
"""Load testing tool for the bonded exhibition chatbot web server.

Standalone script (not pytest). Uses only Python stdlib.

Usage:
    python tests/load_test.py --scenario chat_simple --base-url http://localhost:8080
    python tests/load_test.py --scenario all --base-url http://localhost:8080
    python tests/load_test.py --list-scenarios
"""

import argparse
import json
import os
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class RequestResult:
    """Result of a single HTTP request."""

    status_code: int = 0
    response_time: float = 0.0
    error: str = ""
    success: bool = False


@dataclass
class ScenarioResult:
    """Aggregated results for a load test scenario."""

    name: str = ""
    num_requests: int = 0
    concurrency: int = 0
    total_time: float = 0.0
    response_times: list = field(default_factory=list)
    status_codes: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    successful: int = 0
    failed: int = 0

    def compute_stats(self) -> dict:
        """Compute statistics from collected results."""
        times = sorted(self.response_times) if self.response_times else [0.0]
        n = len(times)
        error_rate = self.failed / self.num_requests if self.num_requests > 0 else 0.0
        throughput = self.num_requests / self.total_time if self.total_time > 0 else 0.0

        return {
            "scenario": self.name,
            "num_requests": self.num_requests,
            "concurrency": self.concurrency,
            "total_time_sec": round(self.total_time, 3),
            "successful": self.successful,
            "failed": self.failed,
            "error_rate": round(error_rate, 4),
            "throughput_rps": round(throughput, 2),
            "response_times": {
                "min_ms": round(min(times) * 1000, 2),
                "max_ms": round(max(times) * 1000, 2),
                "avg_ms": round(statistics.mean(times) * 1000, 2),
                "p50_ms": round(self._percentile(times, 50) * 1000, 2),
                "p95_ms": round(self._percentile(times, 95) * 1000, 2),
                "p99_ms": round(self._percentile(times, 99) * 1000, 2),
            },
            "status_codes": dict(self.status_codes),
            "errors": self.errors[:20],  # cap error list
        }

    @staticmethod
    def _percentile(sorted_data: list, pct: float) -> float:
        """Compute percentile from sorted data."""
        if not sorted_data:
            return 0.0
        k = (len(sorted_data) - 1) * (pct / 100.0)
        f = int(k)
        c = f + 1
        if c >= len(sorted_data):
            return sorted_data[f]
        d = k - f
        return sorted_data[f] + d * (sorted_data[c] - sorted_data[f])


class LoadTester:
    """HTTP load testing engine using threading."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _make_request(self, endpoint: str, method: str = "GET",
                      payload: dict | None = None) -> RequestResult:
        """Execute a single HTTP request and return the result."""
        result = RequestResult()
        url = f"{self.base_url}{endpoint}"

        try:
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url, data=data, method=method,
                    headers={"Content-Type": "application/json"},
                )
            else:
                req = urllib.request.Request(url, method=method)

            start = time.monotonic()
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp.read()
                result.status_code = resp.status
                result.response_time = time.monotonic() - start
                result.success = 200 <= resp.status < 400
        except urllib.error.HTTPError as e:
            result.status_code = e.code
            result.response_time = time.monotonic() - start
            result.error = f"HTTP {e.code}: {e.reason}"
            result.success = False
        except Exception as e:
            result.response_time = time.monotonic() - start if 'start' in locals() else 0.0
            result.error = str(e)
            result.success = False

        return result

    def run_scenario(self, name: str, endpoint: str, method: str = "GET",
                     payload: dict | None = None, num_requests: int = 100,
                     concurrency: int = 10,
                     payload_generator=None) -> ScenarioResult:
        """Execute a load test scenario.

        Args:
            name: Scenario name for reporting.
            endpoint: URL path (e.g. /api/chat).
            method: HTTP method.
            payload: Static JSON payload for POST requests.
            num_requests: Total number of requests to send.
            concurrency: Number of concurrent threads.
            payload_generator: Optional callable that returns a payload dict
                               for each request. Overrides static payload.

        Returns:
            ScenarioResult with aggregated statistics.
        """
        scenario = ScenarioResult(
            name=name, num_requests=num_requests, concurrency=concurrency,
        )
        results: list[RequestResult] = []
        lock = threading.Lock()

        request_index = [0]  # mutable counter for threads
        index_lock = threading.Lock()

        def worker():
            while True:
                with index_lock:
                    idx = request_index[0]
                    if idx >= num_requests:
                        return
                    request_index[0] += 1

                current_payload = payload
                if payload_generator is not None:
                    current_payload = payload_generator(idx)

                result = self._make_request(endpoint, method, current_payload)
                with lock:
                    results.append(result)

        threads = []
        start_time = time.monotonic()

        for _ in range(min(concurrency, num_requests)):
            t = threading.Thread(target=worker, daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        scenario.total_time = time.monotonic() - start_time

        # Aggregate results
        for r in results:
            scenario.response_times.append(r.response_time)
            code_key = str(r.status_code) if r.status_code else "error"
            scenario.status_codes[code_key] = scenario.status_codes.get(code_key, 0) + 1
            if r.success:
                scenario.successful += 1
            else:
                scenario.failed += 1
                if r.error:
                    scenario.errors.append(r.error)

        return scenario


# ---------------------------------------------------------------------------
# Pre-defined scenarios
# ---------------------------------------------------------------------------

CHAT_QUESTIONS = [
    "보세전시장이 무엇인가요?",
    "보세전시장 특허 기간은 어떻게 되나요?",
    "물품 반입 절차를 알려주세요",
    "전시 물품을 판매할 수 있나요?",
    "견본품 반출 허가가 필요한가요?",
    "시식용 식품 반입 요건이 궁금합니다",
    "필요한 서류가 무엇인가요?",
    "위반 시 벌칙은 어떻게 되나요?",
    "문의할 곳이 어디인가요?",
    "보세구역과 보세창고의 차이는?",
    "현장 판매 후 즉시 인도가 가능한가요?",
    "보세전시장 설치 특허 신청 방법은?",
    "반출입 신고서 양식은 어디서 받나요?",
    "과태료 금액은 얼마인가요?",
    "외국물품 전시 가능한 품목은?",
    "특허 갱신 절차를 알려주세요",
    "반입 검사는 누가 하나요?",
    "전시 기간 중 물품 관리 방법은?",
    "면세 조건이 있나요?",
    "관세법 관련 규정을 알려주세요",
]


def _get_scenarios(tester: LoadTester) -> dict:
    """Return all pre-defined scenario definitions."""

    def chat_varied_payload(idx: int) -> dict:
        q = CHAT_QUESTIONS[idx % len(CHAT_QUESTIONS)]
        return {"query": q}

    scenarios = {
        "chat_simple": lambda: tester.run_scenario(
            name="chat_simple",
            endpoint="/api/chat",
            method="POST",
            payload={"query": "보세전시장이 무엇인가요?"},
            num_requests=100,
            concurrency=10,
        ),
        "chat_varied": lambda: tester.run_scenario(
            name="chat_varied",
            endpoint="/api/chat",
            method="POST",
            num_requests=200,
            concurrency=20,
            payload_generator=chat_varied_payload,
        ),
        "faq_list": lambda: tester.run_scenario(
            name="faq_list",
            endpoint="/api/faq",
            method="GET",
            num_requests=100,
            concurrency=10,
        ),
        "autocomplete": lambda: tester.run_scenario(
            name="autocomplete",
            endpoint="/api/autocomplete?q=보세",
            method="GET",
            num_requests=100,
            concurrency=10,
        ),
        "health_check": lambda: tester.run_scenario(
            name="health_check",
            endpoint="/api/health",
            method="GET",
            num_requests=200,
            concurrency=50,
        ),
    }

    def mixed_workload():
        """70% chat + 20% faq + 10% autocomplete."""
        total = 100
        results_list = []

        # Build a shuffled list of request types
        request_types = (
            ["chat"] * int(total * 0.70)
            + ["faq"] * int(total * 0.20)
            + ["autocomplete"] * int(total * 0.10)
        )
        random.shuffle(request_types)

        lock = threading.Lock()
        all_results: list[RequestResult] = []
        type_index = [0]
        type_lock = threading.Lock()
        concurrency = 20

        def mixed_worker():
            while True:
                with type_lock:
                    idx = type_index[0]
                    if idx >= len(request_types):
                        return
                    req_type = request_types[idx]
                    type_index[0] += 1

                if req_type == "chat":
                    q = random.choice(CHAT_QUESTIONS)
                    result = tester._make_request(
                        "/api/chat", "POST", {"query": q},
                    )
                elif req_type == "faq":
                    result = tester._make_request("/api/faq", "GET")
                else:
                    result = tester._make_request(
                        "/api/autocomplete?q=보세", "GET",
                    )

                with lock:
                    all_results.append(result)

        start_time = time.monotonic()
        threads = []
        for _ in range(concurrency):
            t = threading.Thread(target=mixed_worker, daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        total_time = time.monotonic() - start_time

        scenario = ScenarioResult(
            name="mixed_workload",
            num_requests=total,
            concurrency=concurrency,
            total_time=total_time,
        )
        for r in all_results:
            scenario.response_times.append(r.response_time)
            code_key = str(r.status_code) if r.status_code else "error"
            scenario.status_codes[code_key] = scenario.status_codes.get(code_key, 0) + 1
            if r.success:
                scenario.successful += 1
            else:
                scenario.failed += 1
                if r.error:
                    scenario.errors.append(r.error)

        return scenario

    scenarios["mixed_workload"] = mixed_workload
    return scenarios


def _print_results_table(stats_list: list[dict]) -> None:
    """Print a formatted results table to stdout."""
    print("\n" + "=" * 90)
    print("LOAD TEST RESULTS")
    print("=" * 90)

    for stats in stats_list:
        rt = stats["response_times"]
        print(f"\n--- {stats['scenario']} ---")
        print(f"  Requests:      {stats['num_requests']}")
        print(f"  Concurrency:   {stats['concurrency']}")
        print(f"  Total time:    {stats['total_time_sec']:.3f}s")
        print(f"  Throughput:    {stats['throughput_rps']:.2f} req/s")
        print(f"  Success/Fail:  {stats['successful']}/{stats['failed']}")
        print(f"  Error rate:    {stats['error_rate'] * 100:.2f}%")
        print(f"  Response times (ms):")
        print(f"    Min:  {rt['min_ms']:.2f}")
        print(f"    Max:  {rt['max_ms']:.2f}")
        print(f"    Avg:  {rt['avg_ms']:.2f}")
        print(f"    P50:  {rt['p50_ms']:.2f}")
        print(f"    P95:  {rt['p95_ms']:.2f}")
        print(f"    P99:  {rt['p99_ms']:.2f}")
        print(f"  Status codes:  {stats['status_codes']}")
        if stats["errors"]:
            print(f"  First errors:  {stats['errors'][:3]}")

    print("\n" + "=" * 90)


def _save_report(stats_list: list[dict], report_path: str) -> None:
    """Save JSON report to file."""
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": stats_list,
    }
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nReport saved to: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="Load test the chatbot web server")
    parser.add_argument(
        "--scenario", default="all",
        help="Scenario to run (chat_simple, chat_varied, faq_list, autocomplete, "
             "health_check, mixed_workload, all). Default: all",
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8080",
        help="Base URL of the web server. Default: http://localhost:8080",
    )
    parser.add_argument(
        "--report-path", default=None,
        help="Path to save JSON report. Default: reports/load_test_results.json",
    )
    parser.add_argument(
        "--list-scenarios", action="store_true",
        help="List available scenarios and exit.",
    )

    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    report_path = args.report_path or os.path.join(base_dir, "reports", "load_test_results.json")

    tester = LoadTester(base_url=args.base_url)
    scenarios = _get_scenarios(tester)

    if args.list_scenarios:
        print("Available scenarios:")
        for name in scenarios:
            print(f"  - {name}")
        return

    if args.scenario == "all":
        to_run = list(scenarios.keys())
    elif args.scenario in scenarios:
        to_run = [args.scenario]
    else:
        print(f"Unknown scenario: {args.scenario}")
        print(f"Available: {', '.join(scenarios.keys())}, all")
        sys.exit(1)

    print(f"Running load tests against {args.base_url}")
    print(f"Scenarios: {', '.join(to_run)}")

    stats_list = []
    for name in to_run:
        print(f"\nRunning scenario: {name} ...")
        result = scenarios[name]()
        stats = result.compute_stats()
        stats_list.append(stats)

    _print_results_table(stats_list)
    _save_report(stats_list, report_path)


if __name__ == "__main__":
    main()
