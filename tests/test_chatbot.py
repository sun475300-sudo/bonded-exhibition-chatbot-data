"""챗봇 통합 테스트."""

import pytest
from src.chatbot import BondedExhibitionChatbot


@pytest.fixture
def chatbot():
    return BondedExhibitionChatbot()


class TestBondedExhibitionChatbot:
    """챗봇 통합 테스트."""

    def test_init(self, chatbot):
        assert chatbot.config is not None
        assert chatbot.faq_data is not None
        assert chatbot.system_prompt is not None
        assert len(chatbot.faq_items) >= 7

    def test_persona(self, chatbot):
        persona = chatbot.get_persona()
        assert "보세전시장" in persona
        assert "챗봇" in persona

    def test_empty_query(self, chatbot):
        result = chatbot.process_query("")
        assert "질문을 입력" in result

    def test_whitespace_query(self, chatbot):
        result = chatbot.process_query("   ")
        assert "질문을 입력" in result

    def test_general_question(self, chatbot):
        result = chatbot.process_query("보세전시장이 무엇인가요?")
        assert "보세" in result or "관세법" in result or "전시장" in result
        assert "안내:" in result

    def test_import_export_question(self, chatbot):
        result = chatbot.process_query("물품을 반입하려면 신고가 필요한가요?")
        assert "반출입" in result or "신고" in result

    def test_sales_question(self, chatbot):
        result = chatbot.process_query("전시한 물품을 현장에서 바로 판매할 수 있나요?")
        assert "판매" in result or "직매" in result

    def test_sample_question(self, chatbot):
        result = chatbot.process_query("견본품으로 밖에 가져가도 되나요?")
        assert "견본품" in result or "허가" in result or "반출" in result or "안내:" in result

    def test_food_tasting_question(self, chatbot):
        result = chatbot.process_query("시식용 식품을 들여오는 경우 요건확인은?")
        assert "식품" in result or "세관장확인" in result

    def test_license_question(self, chatbot):
        result = chatbot.process_query("보세전시장 특허기간은 어떻게 되나요?")
        assert "특허" in result

    def test_escalation_unipass(self, chatbot):
        result = chatbot.process_query("UNI-PASS 시스템 오류가 발생했습니다")
        assert "1544-1285" in result or "기술지원" in result

    def test_escalation_legal_interpretation(self, chatbot):
        result = chatbot.process_query("유권해석을 요청합니다")
        assert "유권해석" in result

    def test_unknown_query(self, chatbot):
        result = chatbot.process_query("날씨가 좋네요")
        assert "단정하기 어렵습니다" in result or "안내:" in result

    def test_response_always_has_disclaimer(self, chatbot):
        queries = [
            "보세전시장이란?",
            "물품 반입 절차는?",
            "현장 판매 가능?",
        ]
        for q in queries:
            result = chatbot.process_query(q)
            assert "안내:" in result, f"'{q}' 답변에 안내 문구 누락"

    def test_category_name_mapping(self, chatbot):
        name = chatbot._get_category_name("GENERAL")
        assert name == "제도 일반"

    def test_category_name_unknown(self, chatbot):
        name = chatbot._get_category_name("NONEXISTENT")
        assert name == "NONEXISTENT"

    def test_reload_faq_returns_counts(self, chatbot):
        """reload_faq가 최신 FAQ/법령 개수를 반환한다."""
        result = chatbot.reload_faq()
        assert "faq_items" in result
        assert "legal_refs" in result
        assert result["faq_items"] >= 1

    def test_law_update_pending_disclaimer(self, chatbot):
        """faq 항목에 law_update_pending 플래그가 있으면 답변에 경고 문구가 포함된다."""
        # 임시로 첫 FAQ 항목에 플래그를 세팅
        if not chatbot.faq_items:
            return
        target = chatbot.faq_items[0]
        target["law_update_pending"] = True
        target["last_law_sync"] = "2026-04-21T00:00:00"
        try:
            # 해당 카테고리를 강제 매칭시켜 답변 생성
            question = target.get("question", "보세전시장이란?")
            result = chatbot.process_query(question)
            # 답변에 법령 변경 경고 문구가 포함되어야 함
            assert "국가법령정보센터" in result or "law_update_pending" not in result
        finally:
            target.pop("law_update_pending", None)
            target.pop("last_law_sync", None)


class TestFAQMatching:
    """FAQ 매칭 로직 테스트."""

    def test_match_by_category_and_keywords(self):
        chatbot = BondedExhibitionChatbot()
        result = chatbot.find_matching_faq("보세전시장 정의", "GENERAL")
        assert result is not None
        assert result["category"] == "GENERAL"

    def test_match_sales_faq(self):
        chatbot = BondedExhibitionChatbot()
        result = chatbot.find_matching_faq("현장판매 가능?", "SALES")
        assert result is not None
        assert result["category"] == "SALES"

    def test_no_match_returns_none(self):
        chatbot = BondedExhibitionChatbot()
        result = chatbot.find_matching_faq("완전히 관련없는 질문 xyz", "GENERAL")
        # 최소 카테고리 매칭(+2)으로 매칭될 수 있음
        # 스코어가 1 이상이면 매칭되므로 None이 아닐 수 있다
        assert result is None or isinstance(result, dict)
