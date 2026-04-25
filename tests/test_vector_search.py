"""벡터 검색 모듈 테스트."""

import pytest

# VectorSearchEngine은 sentence-transformers가 설치되었을 때만 테스트.
# vector_search 모듈은 항상 import 가능하지만, 실제 임베딩 라이브러리가
# 없으면 클래스 인스턴스화 시 ImportError를 던지므로 런타임 플래그를 사용.
from src.vector_search import VectorSearchEngine, HAS_EMBEDDINGS  # noqa: E402


@pytest.fixture
def sample_faq_items():
    """테스트용 FAQ 항목."""
    return [
        {
            "id": "A",
            "category": "GENERAL",
            "question": "보세전시장이 무엇인가요?",
            "answer": "보세전시장은 박람회를 위해 외국물품을 전시할 수 있는 보세구역입니다.",
            "keywords": ["보세전시장", "정의", "개념"],
            "legal_basis": ["관세법 제190조"]
        },
        {
            "id": "B",
            "category": "IMPORT_EXPORT",
            "question": "보세전시장에 물품을 반입하려면?",
            "answer": "세관장에게 반출입신고를 해야 합니다.",
            "keywords": ["반입", "반출", "신고"],
            "legal_basis": ["보세전시장 운영에 관한 고시 제10조"]
        },
        {
            "id": "C",
            "category": "SALES",
            "question": "보세전시장에서 판매할 수 있나요?",
            "answer": "수입면허를 받기 전에는 판매할 수 없습니다.",
            "keywords": ["판매", "직매", "인도"],
            "legal_basis": ["관세법 시행령 제101조"]
        },
    ]


@pytest.mark.skipif(not HAS_EMBEDDINGS, reason="sentence-transformers not installed")
class TestVectorSearchEngine:
    """VectorSearchEngine 테스트."""

    def test_initialization(self, sample_faq_items):
        """VectorSearchEngine 초기화 테스트."""
        engine = VectorSearchEngine(sample_faq_items)

        assert engine.faq_items == sample_faq_items
        assert engine.embeddings is not None
        assert len(engine.embeddings) == len(sample_faq_items)

    def test_find_best_match(self, sample_faq_items):
        """최적 매칭 찾기 테스트."""
        engine = VectorSearchEngine(sample_faq_items)

        # 명확한 질문
        results = engine.find_best_match("보세전시장이란 무엇입니까?", top_k=1)

        assert len(results) > 0
        assert "item" in results[0]
        assert "score" in results[0]
        assert results[0]["score"] >= 0.0

    def test_find_best_match_with_category(self, sample_faq_items):
        """카테고리 필터를 사용한 매칭 테스트."""
        engine = VectorSearchEngine(sample_faq_items)

        # IMPORT_EXPORT 카테고리로 제한
        results = engine.find_best_match("물품 반입", category="IMPORT_EXPORT", top_k=3)

        if results:
            for result in results:
                assert result["item"]["category"] == "IMPORT_EXPORT"

    def test_find_suggestions(self, sample_faq_items):
        """제안 찾기 테스트."""
        engine = VectorSearchEngine(sample_faq_items)

        # SUGGESTION_THRESHOLD 범위의 결과
        suggestions = engine.find_suggestions("박람회 물품 반입", top_k=5)

        # 모든 제안이 임계값 범위 내인지 확인
        for suggestion in suggestions:
            score = suggestion["score"]
            assert engine.SUGGESTION_THRESHOLD <= score < engine.CONFIDENT_THRESHOLD

    def test_is_confident_match(self, sample_faq_items):
        """높은 신뢰도 판단 테스트."""
        engine = VectorSearchEngine(sample_faq_items)

        assert engine.is_confident_match(0.70)
        assert not engine.is_confident_match(0.60)

    def test_is_suggestion(self, sample_faq_items):
        """제안 범위 판단 테스트."""
        engine = VectorSearchEngine(sample_faq_items)

        assert engine.is_suggestion(0.55)
        assert not engine.is_suggestion(0.70)
        assert not engine.is_suggestion(0.40)

    def test_embedding_cache(self, sample_faq_items):
        """임베딩 캐시 테스트."""
        engine = VectorSearchEngine(sample_faq_items)

        query = "보세전시장 반입"

        # 첫 번째 호출
        results1 = engine.find_best_match(query)

        # 캐시에 저장됨
        assert len(engine.embedding_cache) > 0

        # 두 번째 호출 (캐시에서 로드)
        results2 = engine.find_best_match(query)

        # 동일한 결과
        assert len(results1) == len(results2)

    def test_clear_cache(self, sample_faq_items):
        """캐시 초기화 테스트."""
        engine = VectorSearchEngine(sample_faq_items)

        engine.find_best_match("보세전시장")
        assert len(engine.embedding_cache) > 0

        engine.clear_cache()
        assert len(engine.embedding_cache) == 0

    def test_get_cache_stats(self, sample_faq_items):
        """캐시 통계 테스트."""
        engine = VectorSearchEngine(sample_faq_items)

        stats = engine.get_cache_stats()

        assert "cached_queries" in stats
        assert "max_cache_size" in stats
        assert "model" in stats

    def test_empty_query(self, sample_faq_items):
        """빈 질문 처리 테스트."""
        engine = VectorSearchEngine(sample_faq_items)

        results = engine.find_best_match("")
        assert results == []

        results = engine.find_best_match("   ")
        assert results == []

    def test_empty_faq_items(self):
        """빈 FAQ 리스트 처리 테스트."""
        engine = VectorSearchEngine([])

        results = engine.find_best_match("질문")
        assert results == []

    def test_cosine_similarity(self, sample_faq_items):
        """코사인 유사도 계산 테스트."""
        import numpy as np
        engine = VectorSearchEngine(sample_faq_items)

        vec1 = np.array([1, 0, 0])
        vec2 = np.array([1, 0, 0])
        similarity = engine._cosine_similarity(vec1, vec2)
        assert similarity == pytest.approx(1.0)

        vec1 = np.array([1, 0, 0])
        vec2 = np.array([0, 1, 0])
        similarity = engine._cosine_similarity(vec1, vec2)
        assert similarity == pytest.approx(0.0)

    def test_cosine_similarity_edge_cases(self, sample_faq_items):
        """코사인 유사도 엣지 케이스 테스트."""
        import numpy as np
        engine = VectorSearchEngine(sample_faq_items)

        # 영벡터
        vec1 = np.array([0, 0, 0])
        vec2 = np.array([1, 1, 1])
        similarity = engine._cosine_similarity(vec1, vec2)
        assert similarity == 0.0

        # None 입력
        similarity = engine._cosine_similarity(None, vec2)
        assert similarity == 0.0

        similarity = engine._cosine_similarity(vec1, None)
        assert similarity == 0.0

    def test_semantic_relevance(self, sample_faq_items):
        """의미론적 유사도 테스트."""
        engine = VectorSearchEngine(sample_faq_items)

        # "박람회"와 "보세전시장"은 의미적으로 관련 있음
        results = engine.find_best_match("박람회에서 외국물품 전시", top_k=5)

        assert len(results) > 0
        # 첫 번째 결과가 관련 있어야 함
        top_score = results[0]["score"]
        assert top_score >= 0.3  # 어느 정도의 유사도는 있어야 함
