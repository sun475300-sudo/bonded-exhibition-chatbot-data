"""골든 테스트셋 분석 스크립트.

챗봇의 FAQ 매칭 정확도를 분석하고, 실패 케이스와 개선 방향을 출력한다.

사용법:
    python scripts/analyze_golden_testset.py
    python scripts/analyze_golden_testset.py --verbose
    python scripts/analyze_golden_testset.py --category SALES
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.similarity import TFIDFMatcher
from src.classifier import classify_query
from src.synonym_resolver import expand_query
from src.spell_corrector import correct_query
from src.utils import load_json, normalize_query

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_PATH = os.path.join(BASE_DIR, "data", "golden_testset.json")
FAQ_PATH = os.path.join(BASE_DIR, "data", "faq.json")

CATEGORY_ALIASES = {"DISPLAY_USE": "EXHIBITION"}

# 카테고리 보너스 및 임계값 (chatbot.py와 동일하게 유지)
CATEGORY_BONUS = 2
KEYWORD_SCORE_THRESHOLD = 3
TFIDF_SCORE_THRESHOLD = 0.1


def normalize_category(cat: str) -> str:
    return CATEGORY_ALIASES.get(cat, cat)


def run_analysis(filter_category: str = "", verbose: bool = False):
    golden = load_json("data/golden_testset.json")
    faq_data = load_json("data/faq.json")
    faq_items = faq_data.get("items", [])

    # TF-IDF 매처 초기화
    matcher = TFIDFMatcher(faq_items)

    total = 0
    correct_category = 0
    correct_faq = 0
    failures = []
    category_stats: dict[str, dict] = {}

    for item in golden.get("items", []):
        expected_cat = normalize_category(item.get("expected_category", ""))
        expected_faq = item.get("expected_faq_id", "")
        question = item.get("question", "")
        q_type = item.get("type", "exact")

        if filter_category and normalize_category(filter_category) != expected_cat:
            continue

        # 질문 전처리 (correct_query는 (str, list) 튜플 반환)
        corrected_result = correct_query(question)
        corrected = corrected_result[0] if isinstance(corrected_result, tuple) else corrected_result
        expanded = expand_query(corrected)
        normalized = normalize_query(expanded)

        # 카테고리 분류
        predicted_cats = classify_query(normalized)
        predicted_cat = predicted_cats[0] if predicted_cats else "UNKNOWN"

        # FAQ 매칭 (전체 + 카테고리 보너스)
        best_match = None
        best_score = 0.0

        # 키워드 점수 기반 매칭
        for faq in faq_items:
            score = 0
            faq_keywords = faq.get("keywords", [])
            faq_question = faq.get("question", "")
            faq_answer = faq.get("answer", "")
            faq_cat = faq.get("category", "")

            # 키워드 매칭
            for kw in faq_keywords:
                if kw in normalized:
                    score += 1
                elif kw in question:
                    score += 0.5

            # 카테고리 보너스
            if faq_cat == predicted_cat:
                score += CATEGORY_BONUS

            if score > best_score:
                best_score = score
                best_match = faq

        # TF-IDF 폴백
        if best_score < KEYWORD_SCORE_THRESHOLD:
            tfidf_result = matcher.find_best_match(normalized, top_k=1)
            if tfidf_result and tfidf_result[0]["score"] >= TFIDF_SCORE_THRESHOLD:
                best_match = tfidf_result[0]["item"]
                best_score = tfidf_result[0]["score"]

        predicted_faq = best_match.get("id") if best_match else None
        predicted_faq_cat = best_match.get("category", "") if best_match else ""

        cat_ok = predicted_cat == expected_cat
        faq_ok = predicted_faq == expected_faq

        # 카테고리 통계
        if expected_cat not in category_stats:
            category_stats[expected_cat] = {
                "total": 0, "cat_ok": 0, "faq_ok": 0, "failures": []
            }
        category_stats[expected_cat]["total"] += 1
        if cat_ok:
            category_stats[expected_cat]["cat_ok"] += 1
            correct_category += 1
        if faq_ok:
            category_stats[expected_cat]["faq_ok"] += 1
            correct_faq += 1

        total += 1

        if not faq_ok:
            failure = {
                "question": question,
                "type": q_type,
                "expected_category": expected_cat,
                "expected_faq": expected_faq,
                "predicted_category": predicted_cat,
                "predicted_faq": predicted_faq,
                "predicted_faq_category": predicted_faq_cat,
                "score": best_score,
            }
            failures.append(failure)
            category_stats[expected_cat]["failures"].append(failure)

    # 결과 출력
    print("=" * 70)
    print(f"  보세봇 골든 테스트셋 분석 결과")
    print("=" * 70)
    if filter_category:
        print(f"  필터: 카테고리 = {filter_category}")
    print(f"  총 테스트: {total}개")
    print(f"  카테고리 정확도: {correct_category}/{total} ({100*correct_category//max(total,1)}%)")
    print(f"  FAQ 매칭 정확도: {correct_faq}/{total} ({100*correct_faq//max(total,1)}%)")
    print()

    print("카테고리별 결과:")
    print("-" * 70)
    for cat, stats in sorted(category_stats.items()):
        t = stats["total"]
        faq_pct = 100 * stats["faq_ok"] // max(t, 1)
        cat_pct = 100 * stats["cat_ok"] // max(t, 1)
        status = "✓" if faq_pct >= 80 else ("△" if faq_pct >= 60 else "✗")
        print(f"  {status} {cat:<20} | 카테고리:{cat_pct:3d}% | FAQ:{faq_pct:3d}% | {stats['faq_ok']}/{t}개")

    if failures:
        print()
        print(f"실패 케이스 ({len(failures)}개):")
        print("-" * 70)
        for f in failures:
            print(f"\n  질문: {f['question']}")
            print(f"  유형: {f['type']}")
            print(f"  기대: 카테고리={f['expected_category']}, FAQ={f['expected_faq']}")
            print(f"  예측: 카테고리={f['predicted_category']}, FAQ={f['predicted_faq']} (점수:{f['score']:.2f})")
            if verbose:
                # 기대했던 FAQ의 키워드 출력
                for faq in load_json("data/faq.json").get("items", []):
                    if faq.get("id") == f["expected_faq"]:
                        print(f"  기대 FAQ 키워드: {faq.get('keywords', [])}")
                        break

    print()
    print("=" * 70)

    # 개선 제안 출력
    print("\n개선 제안:")
    for cat, stats in sorted(category_stats.items()):
        if stats["failures"]:
            faq_pct = 100 * stats["faq_ok"] // max(stats["total"], 1)
            if faq_pct < 80:
                print(f"\n  [{cat}] FAQ 매칭 {faq_pct}% — 다음 항목 개선 필요:")
                seen_faqs = set()
                for f in stats["failures"]:
                    faq_id = f["expected_faq"]
                    if faq_id not in seen_faqs:
                        seen_faqs.add(faq_id)
                        print(f"    - FAQ {faq_id}: 키워드 보강 또는 동의어 추가 필요")

    return {
        "total": total,
        "category_accuracy": correct_category / max(total, 1),
        "faq_accuracy": correct_faq / max(total, 1),
        "failures": failures,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="골든 테스트셋 분석")
    parser.add_argument("--category", default="", help="특정 카테고리만 분석")
    parser.add_argument("--verbose", action="store_true", help="상세 출력")
    args = parser.parse_args()

    result = run_analysis(filter_category=args.category, verbose=args.verbose)
    sys.exit(0 if result["faq_accuracy"] >= 0.7 else 1)
