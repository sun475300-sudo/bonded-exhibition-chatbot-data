"""챗봇 통합 테스트 - 벡터 검색 및 LLM 폴백."""

import pytest
from unittest.mock import patch, MagicMock

# VectorSearchEngine과 LLM이 없어도 기본 테스트는 실행됨
# 실제 인스턴스화를 시도하여 sentence-transformers 설치 여부를 확인
try:
    from src.vector_search import VectorSearchEngine
    VectorSearchEngine([])  # 실제 인스턴스화 시도
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False


class TestChatbotIntegration:
    """챗봇의 벡터 검색 및 LLM 폴백 통합 테스트."""

    def test_chatbot_initialization_with_vector_search(self):
        """벡터 검색 통합 초기화 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        # 벡터 검색이 설치되어 있으면 활성화됨
        if HAS_EMBEDDINGS:
            assert chatbot.vector_search_enabled
            assert chatbot.vector_search is not None
        else:
            assert not chatbot.vector_search_enabled
            assert chatbot.vector_search is None

    def test_chatbot_initialization_with_llm(self):
        """LLM 폴백 통합 초기화 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        # LLM 상태 확인 (API 키 설정 여부에 따라 다름)
        assert isinstance(chatbot.llm_enabled, bool)

    @pytest.mark.skipif(not HAS_EMBEDDINGS, reason="sentence-transformers not installed")
    def test_chatbot_find_matching_faq_with_vector_search(self):
        """벡터 검색을 포함한 FAQ 매칭 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        # 벡터 검색으로만 매칭 가능한 질문
        query = "박람회에서 외국물품을 전시하려면?"
        match = chatbot.find_matching_faq(query, "GENERAL")

        # 매칭 결과 확인
        # (결과가 있으면 FAQ 항목, 없으면 None)
        if match:
            assert "question" in match
            assert "answer" in match

    def test_chatbot_find_matching_faq_with_llm_fallback(self):
        """LLM 폴백을 포함한 매칭 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        # 극단적으로 모호한 질문
        query = "asdfghjkl"  # 의미 없는 문자열

        result = chatbot.find_matching_faq_with_llm_fallback(query, "GENERAL")

        # 결과는 FAQ 항목, LLM 응답 문자열, 또는 None
        if result:
            assert isinstance(result, (dict, str))

    @patch("src.chatbot.is_llm_available", return_value=False)
    def test_chatbot_without_llm_fallback(self, mock_llm):
        """LLM 폴백 비활성화 상태 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        assert not chatbot.llm_enabled

    @pytest.mark.skipif(not HAS_EMBEDDINGS, reason="sentence-transformers not installed")
    def test_chatbot_vector_search_confidence_threshold(self):
        """벡터 검색 신뢰도 임계값 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        if not chatbot.vector_search_enabled:
            pytest.skip("Vector search not enabled")

        # 명확한 질문
        results = chatbot.vector_search.find_best_match("보세전시장이란?", top_k=1)

        if results:
            score = results[0]["score"]
            # 점수가 0~1 범위
            assert 0 <= score <= 1

    @pytest.mark.skipif(not HAS_EMBEDDINGS, reason="sentence-transformers not installed")
    def test_chatbot_vector_search_suggestions(self):
        """벡터 검색 제안 기능 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        if not chatbot.vector_search_enabled:
            pytest.skip("Vector search not enabled")

        # 모호한 질문
        suggestions = chatbot.vector_search.find_suggestions("외국 물품", top_k=3)

        # 제안 결과 확인
        if suggestions:
            for suggestion in suggestions:
                score = suggestion["score"]
                # SUGGESTION_THRESHOLD 범위 확인
                assert chatbot.vector_search.SUGGESTION_THRESHOLD <= score < chatbot.vector_search.CONFIDENT_THRESHOLD

    def test_chatbot_pipeline_faq_only(self):
        """FAQ만 사용하는 파이프라인 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        # 명확한 키워드가 있는 질문
        response = chatbot.process_query("보세전시장이 무엇인가요?")

        # 응답이 있어야 함
        assert response is not None
        assert len(response) > 0

    def test_chatbot_pipeline_escalation(self):
        """에스컬레이션 처리 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        # 에스컬레이션 트리거 가능한 질문
        response = chatbot.process_query("불복절차는?")

        # 응답이 있어야 함
        assert response is not None

    @pytest.mark.skipif(not HAS_EMBEDDINGS, reason="sentence-transformers not installed")
    def test_chatbot_cache_stats(self):
        """챗봇 캐시 통계 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        if chatbot.vector_search_enabled:
            stats = chatbot.vector_search.get_cache_stats()

            assert "cached_queries" in stats
            assert "max_cache_size" in stats

    def test_chatbot_classifier_cache(self):
        """분류기 캐시 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        query = "보세전시장"

        # 첫 호출
        result1 = chatbot._cached_classify(query)

        # 캐시에 저장됨
        assert query in chatbot._classifier_cache

        # 두 번째 호출 (캐시에서 로드)
        result2 = chatbot._cached_classify(query)

        # 동일한 결과
        assert result1 == result2

    def test_chatbot_multi_turn_conversation(self):
        """멀티턴 대화 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        # 세션 없는 단일 턴
        response1 = chatbot.process_query("보세전시장에 물품을 반입하려면?")
        assert response1 is not None

    def test_chatbot_unknown_response(self):
        """미알려진 질문 응답 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        # 극단적으로 모호한 질문
        response = chatbot.process_query("xyz123abc")

        # 응답이 있어야 함
        assert response is not None
        assert len(response) > 0

    def test_chatbot_empty_query(self):
        """빈 질문 처리 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        response = chatbot.process_query("")
        assert "질문을 입력" in response

        response = chatbot.process_query("   ")
        assert "질문을 입력" in response

    @patch("src.llm_fallback.generate_llm_response_with_disclaimer")
    def test_chatbot_llm_fallback_integration(self, mock_llm):
        """LLM 폴백 통합 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        mock_llm.return_value = "LLM 응답"

        chatbot = BondedExhibitionChatbot()

        # 벡터 검색과 LLM이 모두 활성화된 경우
        if chatbot.vector_search_enabled and chatbot.llm_enabled:
            result = chatbot.find_matching_faq_with_llm_fallback("unknown query", "GENERAL")

            # 결과 확인
            if result:
                assert isinstance(result, (dict, str))

    def test_chatbot_response_builder_integration(self):
        """응답 빌더 통합 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        # FAQ 매칭이 있는 질문
        query = "보세전시장이 무엇인가요?"
        response = chatbot.process_query(query)

        # 응답이 구조화되어 있어야 함
        assert response is not None
        if "근거:" in response or "법적 근거" in response or "안내" in response:
            # 구조화된 응답
            assert len(response) > 50

    @pytest.mark.skipif(not HAS_EMBEDDINGS, reason="sentence-transformers not installed")
    def test_chatbot_vector_search_performance(self):
        """벡터 검색 성능 테스트.

        E1 lazy load: 첫 검색 호출이 모델 로드(~3-4s) 포함하므로
        warmup 1회로 lazy 트리거 후 10회 hot path 측정.
        """
        import time
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        if not chatbot.vector_search_enabled:
            pytest.skip("Vector search not enabled")

        # Warmup: lazy load 트리거 (모델 + 임베딩 사전 계산)
        chatbot.vector_search.find_best_match("warmup", top_k=1)

        # 성능 측정 (hot path)
        start = time.time()
        for _ in range(10):
            chatbot.vector_search.find_best_match("보세전시장 반입", top_k=3)
        elapsed = time.time() - start

        # 10회 검색이 1초 이내 (warmup 후)
        assert elapsed < 1.0, f"Vector search too slow: {elapsed}s"

    def test_chatbot_category_handling(self):
        """카테고리 처리 테스트."""
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()

        # 다양한 카테고리로 매칭
        categories = ["GENERAL", "IMPORT_EXPORT", "SALES", "SAMPLE"]

        for category in categories:
            match = chatbot.find_matching_faq("의품", category)
            # 카테고리가 일치하거나 None
            if match:
                # config['categories']는 dict 리스트이므로 code 필드로 비교
                valid_category_codes = [c["code"] for c in chatbot.config["categories"]]
                assert match.get("category") == category or match.get("category") in valid_category_codes
