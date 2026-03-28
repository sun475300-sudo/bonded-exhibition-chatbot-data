#!/usr/bin/env python3
"""Benchmark core chatbot components without the web server.

Standalone script. Measures throughput (ops/sec) for key subsystems.

Usage:
    python tests/benchmark.py
    python tests/benchmark.py --iterations 5000
"""

import argparse
import json
import os
import sys
import time

# Ensure project root is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src.classifier import classify_query
from src.similarity import TFIDFMatcher
from src.bm25_ranker import BM25Ranker
from src.session import SessionManager
from src.spell_corrector import correct_query as spell_correct


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_FAQ_ITEMS = [
    {
        "id": "A",
        "category": "GENERAL",
        "question": "보세전시장이 무엇인가요?",
        "answer": "보세전시장은 박람회, 전람회, 견본품 전시회 등의 운영을 위해 외국물품을 장치·전시·사용할 수 있는 보세구역입니다.",
        "keywords": ["보세전시장", "정의", "개념", "보세구역", "뜻"],
    },
    {
        "id": "B",
        "category": "IMPORT_EXPORT",
        "question": "보세전시장에 물품을 반입하거나 반출하려면 신고가 필요한가요?",
        "answer": "네. 보세전시장 운영에 관한 고시에 따르면 반출입신고를 해야 합니다.",
        "keywords": ["반입", "반출", "신고", "반출입신고", "검사"],
    },
    {
        "id": "C",
        "category": "SALES",
        "question": "보세전시장에 전시한 물품을 현장에서 바로 판매할 수 있나요?",
        "answer": "현장 판매 자체를 검토할 수는 있지만, 통관 전 자유롭게 인도할 수 있는 것은 아닙니다.",
        "keywords": ["판매", "직매", "현장판매", "인도", "통관"],
    },
    {
        "id": "D",
        "category": "SAMPLE",
        "question": "전시물 일부를 견본품으로 밖에 가져가도 되나요?",
        "answer": "보세구역에 장치된 외국물품을 견본품으로 반출하려면 세관장의 허가가 필요합니다.",
        "keywords": ["견본품", "샘플", "반출", "허가", "홍보용"],
    },
    {
        "id": "E",
        "category": "FOOD_TASTING",
        "question": "시식용 식품을 보세전시장에 들여오는 경우 요건확인은 생략되나요?",
        "answer": "원칙적으로 수입식품은 세관장확인 생략이 불가합니다.",
        "keywords": ["시식", "식품", "요건확인", "세관장확인", "식약처"],
    },
    {
        "id": "F",
        "category": "PENALTIES",
        "question": "보세전시장 관련 위반 시 벌칙은?",
        "answer": "관세법에 따라 과태료, 특허 취소 등의 제재가 있을 수 있습니다.",
        "keywords": ["벌칙", "과태료", "위반", "처벌", "제재"],
    },
    {
        "id": "G",
        "category": "DOCUMENTS",
        "question": "보세전시장 관련 구비 서류는?",
        "answer": "반출입신고서, 특허신청서, 허가 관련 서류 등이 필요합니다.",
        "keywords": ["서류", "신고서", "양식", "제출", "구비서류"],
    },
    {
        "id": "H",
        "category": "LICENSE",
        "question": "보세전시장 특허 기간은 어떻게 되나요?",
        "answer": "보세전시장 설치·운영 특허 기간은 보통 10년 이내입니다.",
        "keywords": ["특허", "특허기간", "연장", "갱신", "운영"],
    },
]

SAMPLE_QUERIES = [
    "보세전시장이 무엇인가요?",
    "물품 반입 절차를 알려주세요",
    "현장에서 판매할 수 있나요?",
    "견본품 반출 허가",
    "시식용 식품 반입",
    "위반 시 벌칙",
    "필요한 서류",
    "특허 갱신 절차",
    "보세구역 차이",
    "관세법 규정",
]

SAMPLE_MISSPELLED = [
    "보세전시쟝",
    "반출입신거",
    "견본풍",
    "식품 요겅확인",
    "세관쟝확인",
    "과태료 금엑",
    "특허기갼",
    "반출입 겁사",
    "보세구옉",
    "관세법 시행렁",
]


def _try_load_real_faq() -> list[dict]:
    """Attempt to load real FAQ data; fall back to sample."""
    faq_path = os.path.join(BASE_DIR, "data", "faq.json")
    try:
        with open(faq_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items", [])
        if items:
            return items
    except Exception:
        pass
    return SAMPLE_FAQ_ITEMS


# ---------------------------------------------------------------------------
# Benchmark functions
# ---------------------------------------------------------------------------

def benchmark_classifier(iterations: int) -> dict:
    """Benchmark the keyword classifier."""
    queries = SAMPLE_QUERIES
    start = time.perf_counter()
    for i in range(iterations):
        classify_query(queries[i % len(queries)])
    elapsed = time.perf_counter() - start
    ops_per_sec = iterations / elapsed if elapsed > 0 else 0
    return {
        "name": "Classifier",
        "iterations": iterations,
        "elapsed_sec": round(elapsed, 4),
        "ops_per_sec": round(ops_per_sec, 2),
    }


def benchmark_tfidf(iterations: int, faq_items: list[dict]) -> dict:
    """Benchmark TF-IDF matching."""
    matcher = TFIDFMatcher(faq_items)
    queries = SAMPLE_QUERIES
    start = time.perf_counter()
    for i in range(iterations):
        matcher.find_best_match(queries[i % len(queries)])
    elapsed = time.perf_counter() - start
    ops_per_sec = iterations / elapsed if elapsed > 0 else 0
    return {
        "name": "TF-IDF Matching",
        "iterations": iterations,
        "elapsed_sec": round(elapsed, 4),
        "ops_per_sec": round(ops_per_sec, 2),
    }


def benchmark_bm25(iterations: int, faq_items: list[dict]) -> dict:
    """Benchmark BM25 ranking."""
    ranker = BM25Ranker(faq_items)
    queries = SAMPLE_QUERIES
    start = time.perf_counter()
    for i in range(iterations):
        ranker.rank(queries[i % len(queries)])
    elapsed = time.perf_counter() - start
    ops_per_sec = iterations / elapsed if elapsed > 0 else 0
    return {
        "name": "BM25 Ranking",
        "iterations": iterations,
        "elapsed_sec": round(elapsed, 4),
        "ops_per_sec": round(ops_per_sec, 2),
    }


def benchmark_session(iterations: int) -> dict:
    """Benchmark session creation and lookup."""
    manager = SessionManager()
    session_ids = []

    # Create sessions
    start = time.perf_counter()
    for _ in range(iterations):
        session = manager.create_session()
        session_ids.append(session.session_id)
    create_elapsed = time.perf_counter() - start

    # Lookup sessions
    start = time.perf_counter()
    for sid in session_ids:
        manager.get_session(sid)
    lookup_elapsed = time.perf_counter() - start

    total_elapsed = create_elapsed + lookup_elapsed
    total_ops = iterations * 2  # create + lookup
    ops_per_sec = total_ops / total_elapsed if total_elapsed > 0 else 0

    return {
        "name": "Session Create+Lookup",
        "iterations": iterations,
        "elapsed_sec": round(total_elapsed, 4),
        "ops_per_sec": round(ops_per_sec, 2),
        "detail": {
            "create_ops_per_sec": round(iterations / create_elapsed, 2) if create_elapsed > 0 else 0,
            "lookup_ops_per_sec": round(iterations / lookup_elapsed, 2) if lookup_elapsed > 0 else 0,
        },
    }


def benchmark_spell_correction(iterations: int) -> dict:
    """Benchmark spell correction."""
    queries = SAMPLE_MISSPELLED
    start = time.perf_counter()
    for i in range(iterations):
        spell_correct(queries[i % len(queries)])
    elapsed = time.perf_counter() - start
    ops_per_sec = iterations / elapsed if elapsed > 0 else 0
    return {
        "name": "Spell Correction",
        "iterations": iterations,
        "elapsed_sec": round(elapsed, 4),
        "ops_per_sec": round(ops_per_sec, 2),
    }


def run_all_benchmarks(iterations: int = 1000) -> list[dict]:
    """Run all benchmarks and return results list."""
    faq_items = _try_load_real_faq()

    results = [
        benchmark_classifier(iterations),
        benchmark_tfidf(iterations, faq_items),
        benchmark_bm25(iterations, faq_items),
        benchmark_session(iterations),
        benchmark_spell_correction(iterations),
    ]
    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _print_results_table(results: list[dict]) -> None:
    """Print formatted benchmark results."""
    print("\n" + "=" * 70)
    print("BENCHMARK RESULTS")
    print("=" * 70)
    print(f"{'Component':<25} {'Iterations':>10} {'Time (s)':>10} {'Ops/sec':>12}")
    print("-" * 70)
    for r in results:
        print(f"{r['name']:<25} {r['iterations']:>10} {r['elapsed_sec']:>10.4f} {r['ops_per_sec']:>12.2f}")
        if "detail" in r:
            for key, val in r["detail"].items():
                label = key.replace("_", " ").replace("ops per sec", "")
                print(f"  {label:<23} {'':>10} {'':>10} {val:>12.2f}")
    print("=" * 70)


def _save_report(results: list[dict], report_path: str) -> None:
    """Save JSON report."""
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": results,
    }
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nReport saved to: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="Benchmark chatbot components")
    parser.add_argument(
        "--iterations", type=int, default=1000,
        help="Number of iterations per benchmark. Default: 1000",
    )
    parser.add_argument(
        "--report-path", default=None,
        help="Path to save JSON report. Default: reports/benchmark_results.json",
    )

    args = parser.parse_args()
    report_path = args.report_path or os.path.join(BASE_DIR, "reports", "benchmark_results.json")

    print(f"Running benchmarks with {args.iterations} iterations each...")
    results = run_all_benchmarks(args.iterations)

    _print_results_table(results)
    _save_report(results, report_path)


if __name__ == "__main__":
    main()
