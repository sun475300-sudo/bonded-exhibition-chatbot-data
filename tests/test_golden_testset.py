"""Golden testset 회귀 테스트.

`data/golden_testset.json` 의 100개 질문-FAQ 기대값 매핑이 보세봇
FAQ 매처에 의해 정확히 매칭되는지 검증한다.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.chatbot import BondedExhibitionChatbot
from src.classifier import classify_query
from src.utils import load_json


@pytest.fixture(scope="module")
def bot():
    return BondedExhibitionChatbot()


@pytest.fixture(scope="module")
def golden():
    return load_json("data/golden_testset.json")


def _match_id(bot: BondedExhibitionChatbot, question: str) -> str | None:
    """FAQ 매처로 예상 FAQ id 를 반환한다 (None 이면 매칭 실패)."""
    cats = classify_query(question) or ["GENERAL"]
    match = bot.find_matching_faq(question, cats[0])
    return match["id"] if match else None


class TestGoldenTestset:
    def test_all_golden_questions_match_expected_faq(self, bot, golden):
        fails = []
        for item in golden.get("items", []):
            q = item["question"]
            expected = item["expected_faq_id"]
            got = _match_id(bot, q)
            if got != expected:
                fails.append({
                    "question": q,
                    "expected": expected,
                    "got": got,
                    "type": item.get("type", "?"),
                })
        assert not fails, (
            f"{len(fails)}개의 골든 테스트 오매칭:\n"
            + "\n".join(
                f"  [{f['type']}] 예상={f['expected']} 실제={f['got']} | {f['question']}"
                for f in fails
            )
        )

    def test_exact_questions_always_match(self, bot, golden):
        """exact 타입 질문은 반드시 정답과 일치해야 한다."""
        fails = []
        for item in golden.get("items", []):
            if item.get("type") != "exact":
                continue
            got = _match_id(bot, item["question"])
            if got != item["expected_faq_id"]:
                fails.append((item["question"], item["expected_faq_id"], got))
        assert not fails, f"exact 타입 {len(fails)}개 불일치: {fails[:5]}"
