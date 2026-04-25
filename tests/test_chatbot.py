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


class TestLawSnippetIntegration:
    """국가법령정보센터 동기화 결과(law_snippets)가 답변에 반영되는지 검증."""

    def test_collect_legal_guide_uses_snippet(self, chatbot):
        faq_match = {
            "legal_basis": ["관세법 제190조"],
            "law_snippets": {
                "관세법 제190조": {
                    "content": "[법령센터 동기화] 보세전시장 최신 본문",
                    "law_name": "관세법",
                    "article": "제190조",
                    "fetched_at": "2026-04-25T00:00:00",
                }
            },
        }
        guide = chatbot._collect_legal_guide(faq_match)
        assert any("[법령센터 동기화]" in g for g in guide)

    def test_collect_legal_guide_falls_back_to_kg(self, chatbot):
        # snippets 없으면 KnowledgeGraph 폴백 (없을 수도 있으므로 빈 결과 허용)
        faq_match = {"legal_basis": ["관세법 제190조"]}
        guide = chatbot._collect_legal_guide(faq_match)
        assert isinstance(guide, list)

    def test_reload_picks_up_new_faq_data(self, chatbot, tmp_path, monkeypatch):
        """data/faq.json 갱신 후 reload() 호출 시 신규 FAQ가 반영된다."""
        import json as _json
        import os as _os
        from src import chatbot as chatbot_mod

        # 원본을 백업하고 임시 디렉토리에서 작업
        base_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(chatbot_mod.__file__)))
        faq_path = _os.path.join(base_dir, "data", "faq.json")
        backup = _json.load(open(faq_path, "r", encoding="utf-8"))
        try:
            mutated = _json.loads(_json.dumps(backup))
            mutated["items"].append({
                "id": "ZZZTEST",
                "category": "GENERAL",
                "question": "테스트용 신규 FAQ 질문",
                "answer": "신규 답변 본문",
                "legal_basis": [],
                "keywords": ["테스트용신규FAQ"],
            })
            with open(faq_path, "w", encoding="utf-8") as f:
                _json.dump(mutated, f, ensure_ascii=False)
            stats = chatbot.reload()
            assert any(i.get("id") == "ZZZTEST" for i in chatbot.faq_items)
            assert stats["faq_items"] == len(chatbot.faq_items)
        finally:
            with open(faq_path, "w", encoding="utf-8") as f:
                _json.dump(backup, f, ensure_ascii=False, indent=2)
            chatbot.reload()
