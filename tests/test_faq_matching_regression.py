"""FAQ 매칭 정확도 회귀 테스트.

오답 보고된 질문들이 의도한 FAQ 항목으로 매칭되는지 보장한다.
각 케이스는 (expected_faq_id, query) 쌍으로 정의한다.

이 테스트가 회귀하면 spell_corrector·synonym_resolver·faq.json·classifier
중 하나에서 커버리지가 깨졌다는 뜻이므로 즉시 조사해야 한다.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from src.chatbot import BondedExhibitionChatbot


# 과거 오답으로 보고된 핵심 회귀 케이스 모음
REGRESSION_CASES = [
    # LICENSE 카테고리 - 수수료 FAQ (AT)
    ("AT", "보세전시장 특허 수수료는 얼마인가요?"),
    ("AT", "보세전시장 특허 신청 수수료"),
    # SALES - 판매 대금 정산 (AD)
    ("AD", "판매 대금 정산"),
    ("AD", "대금 결제는 어떻게?"),
    # IMPORT_EXPORT - 가액 신고 (AL)
    ("AL", "가액 신고는?"),
    ("AL", "물품 가액 기재"),
    # DOCUMENTS - 수입면허 서류 (AO)
    ("AO", "수입면허 서류?"),
    ("AO", "수입면허 신청에 필요한 서류는?"),
    # DOCUMENTS - 종료 보고 (AW)
    ("AW", "종료 보고서는 어떻게?"),
    ("AW", "운영 종료 보고서"),
    # EXHIBITION - 촬영·홍보 (AU)
    ("AU", "촬영 가능한가요?"),
    ("AU", "전시장에서 촬영해도 되나요?"),
    # IMPORT_EXPORT - 보세전시장 간 이동 (AS)
    ("AS", "보세전시장 간 이동"),
    ("AS", "다른 보세구역으로 이동"),
    ("AS", "보세운송 다른 전시장"),
    # SALES - 판매 계약만 체결 (X)
    ("X", "판매 계약만 체결하고 나중 통관"),
    ("X", "계약만 먼저 체결해도 되나요?"),
    # FOOD_TASTING - 시식 수량 (AH)
    ("AH", "시식 수량 얼마나"),
    # SAMPLE - 견본품 반환 의무 (AG)
    ("AG", "견본품 반환 의무"),
    # EXHIBITION - 전시장 내 보관 (AN)
    ("AN", "전시장 내 보관 주의사항"),
    # 핵심 FAQ들이 정답대로 매칭되는지 기본 확인
    ("A", "보세전시장이 무엇인가요?"),
    ("F", "보세전시장 특허기간은?"),
    ("D", "견본품 반출 가능한가요?"),
    ("C", "보세전시장에서 판매할 수 있나요?"),
    ("E", "시식용 식품 요건확인"),
    ("T", "보세전시장과 보세창고 차이"),
    ("K", "반출입신고서 양식"),
    ("M", "견본품 허가 신청은 어떻게?"),
    ("L", "특허 신청 서류"),
    ("N", "허가 없이 반출하면 벌칙은?"),
    ("O", "수입면허 없이 사용하면?"),
    ("AP", "과태료 얼마인가요?"),
    ("AQ", "관세 불복 절차"),
    ("AX", "관세사에게 대행 가능한가요?"),
    ("AV", "시식용 식품 한글 라벨"),
    ("AK", "특허 취소 사유"),
    ("AM", "전시 물품 교체"),
    ("I", "보세전시장 장치 물품 사용 범위"),
    ("J", "보세전시장에서 시연 가능한가요?"),
    ("G", "보세전시장 특허 장소"),
    ("P", "보세전시장 운영인 의무위반"),
    ("Z", "견본품 반출 관세"),
    ("W", "전시 종료 후 물품 남으면"),
    ("AB", "반입 물품 세관 검사"),
    ("AC", "내국물품 전시 가능?"),
    # 구어체 오답 케이스 (라운드 2)
    ("Z", "견본품 반출 세금 내야 해?"),
    ("AF", "견본품 몇 개까지 가능"),
    ("AG", "견본품 돌려줘야 하나요?"),
    ("AH", "시식 식품 몇 개"),
    ("AI", "시식 행사 전에 신고해야 하나요?"),
    ("AM", "전시 중 물품 바꾸기"),
    ("AT", "보세전시장 특허 비용"),
    ("AV", "시식 식품 한글 표시"),
    ("X", "계약만 체결, 통관 나중에"),
    ("T", "보세전시장 vs 보세창고"),
    ("S", "보세산업과 연락처"),
]


@pytest.fixture(scope="module")
def bot():
    """모듈 단위로 재사용하는 챗봇 인스턴스."""
    return BondedExhibitionChatbot()


def _detect_matched_faq_id(bot, response: str) -> str:
    """응답 텍스트에서 어떤 FAQ가 매칭되었는지 역추적한다.

    가장 긴 answer prefix가 응답에 포함된 항목을 선택한다.
    """
    best_id = "?"
    best_len = 0
    for item in bot.faq_items:
        snippet = item.get("answer", "")[:60]
        if snippet and snippet in response and len(snippet) > best_len:
            best_id = item.get("id", "?")
            best_len = len(snippet)
    return best_id


@pytest.mark.parametrize("expected,query", REGRESSION_CASES)
def test_faq_matches_expected(bot, expected, query):
    response = bot.process_query(query)
    matched = _detect_matched_faq_id(bot, response)
    assert matched == expected, (
        f"query={query!r} expected FAQ {expected} but got {matched}"
    )
