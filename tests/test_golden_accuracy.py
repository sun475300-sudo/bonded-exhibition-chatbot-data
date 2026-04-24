"""Golden test set 정확도 회귀 방지 테스트.

find_matching_faq 의 TF-IDF 타이브레이커 및 faq.json 키워드 개선이
이후 변경으로 인해 회귀하지 않도록 고정한다.

- FAQ id 매칭 정확도는 100% 를 유지해야 한다.
- 기존 변형·경계 질문 중 일부는 개별 단위 테스트로도 명시.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.accuracy_benchmark import AccuracyBenchmark


TRICKY_CASES = [
    ("견본품 반출 후 반환 의무가 있나요?", "AG"),
    ("샘플 반출 허가 어디서 받나요?", "M"),
    ("견본품 반출 허가를 받으면 관세를 내야 하나요?", "Z"),
    ("시식용 식품은 행사 후 어떻게 처리해야 하나요?", "Y"),
    ("시식용 식품의 수량 제한이 있나요?", "AH"),
    ("보세전시장에서 허가 없이 물품을 반출하면 어떻게 되나요?", "N"),
    ("보세전시장 운영인이 의무를 위반하면 어떤 처분을 받나요?", "P"),
    ("특허가 취소될 수 있는 경우는?", "AK"),
    ("수입면허 신청에 필요한 서류는?", "AO"),
    ("전시물을 중간에 바꿀 수 있나요?", "AM"),
    ("반입 물품의 가액 신고는 어떻게 하나요?", "AL"),
    ("다른 보세전시장으로 물품을 옮길 수 있나요?", "AS"),
    ("보세전시장 특허 신청하려면 어디를 봐야 하나요?", "G"),
    ("보세전시장 특허 신청 수수료가 있나요?", "AT"),
]


@pytest.fixture(scope="module")
def chatbot():
    from src.chatbot import BondedExhibitionChatbot
    return BondedExhibitionChatbot()


class TestGoldenSetAccuracy:
    def test_full_faq_accuracy_is_perfect(self, tmp_path):
        bench = AccuracyBenchmark(history_db=str(tmp_path / "bench.db"))
        metrics = bench.run_benchmark("data/golden_testset.json", persist=False)
        assert metrics["total"] == 100
        assert metrics["faq_accuracy"] == 1.0, (
            f"FAQ 정확도 회귀. 실패 건수: {len(metrics['failures'])}\n"
            + "\n".join(
                f"  #{f['index']} {f['question']} "
                f"-> expected {f['expected']['faq_id']}, "
                f"actual {f['actual']['faq_id']}"
                for f in metrics["failures"]
                if f["actual"]["faq_id"] != f["expected"]["faq_id"]
            )
        )


class TestTrickyFAQMatches:
    @pytest.mark.parametrize("question, expected_faq_id", TRICKY_CASES)
    def test_matches_expected_faq(self, chatbot, question, expected_faq_id):
        # 전체 카테고리에 걸쳐 올바른 FAQ 로 매칭되는지 확인한다.
        result = chatbot.process_query(question, include_metadata=True)
        category = result.get("category", "GENERAL")
        match = chatbot.find_matching_faq(question, category)
        assert match is not None, f"No match for: {question}"
        assert match.get("id") == expected_faq_id, (
            f"Wrong FAQ for '{question}': expected {expected_faq_id}, got {match.get('id')}"
        )
