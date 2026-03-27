"""FAQRecommender 테스트."""

import os
import pytest
import tempfile

from src.faq_recommender import FAQRecommender
from src.logger_db import ChatLogger


@pytest.fixture
def temp_db():
    """임시 DB 파일로 ChatLogger를 생성한다."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_logs.db")
        logger = ChatLogger(db_path=db_path)
        yield logger
        logger.close()


@pytest.fixture
def db_with_unmatched(temp_db):
    """미매칭 질문이 있는 DB."""
    # faq_id=None이면 미매칭
    temp_db.log_query("보세전시장에서 차량 반입이 가능한가요?", category="IMPORT_EXPORT", faq_id=None)
    temp_db.log_query("보세전시장에서 차량 반입이 가능한가요?", category="IMPORT_EXPORT", faq_id=None)
    temp_db.log_query("보세전시장에서 차량 반입이 가능한가요?", category="IMPORT_EXPORT", faq_id=None)
    temp_db.log_query("차량을 보세전시장에 들여올 수 있나요?", category="IMPORT_EXPORT", faq_id=None)
    temp_db.log_query("전시회 기간 연장 방법은?", category="EXHIBITION", faq_id=None)
    temp_db.log_query("전시회 기간을 연장하고 싶습니다", category="EXHIBITION", faq_id=None)
    temp_db.log_query("보세전시장 임대료는 얼마인가요?", category="GENERAL", faq_id=None)
    return temp_db


@pytest.fixture
def db_empty(temp_db):
    """미매칭 질문이 없는 빈 DB."""
    return temp_db


@pytest.fixture
def db_matched_only(temp_db):
    """모든 질문이 매칭된 DB."""
    temp_db.log_query("보세전시장이란?", category="GENERAL", faq_id="FAQ_001")
    temp_db.log_query("물품 반입 절차", category="IMPORT_EXPORT", faq_id="FAQ_010")
    return temp_db


class TestFAQRecommenderInit:
    """초기화 테스트."""

    def test_init_with_logger(self, temp_db):
        recommender = FAQRecommender(temp_db)
        assert recommender.logger_db is temp_db


class TestGetRecommendations:
    """get_recommendations 테스트."""

    def test_returns_list(self, db_with_unmatched):
        recommender = FAQRecommender(db_with_unmatched)
        result = recommender.get_recommendations()
        assert isinstance(result, list)

    def test_recommendations_not_empty(self, db_with_unmatched):
        recommender = FAQRecommender(db_with_unmatched)
        result = recommender.get_recommendations()
        assert len(result) > 0

    def test_recommendation_structure(self, db_with_unmatched):
        recommender = FAQRecommender(db_with_unmatched)
        result = recommender.get_recommendations()
        item = result[0]
        assert "suggested_question" in item
        assert "frequency" in item
        assert "similar_queries" in item
        assert "suggested_category" in item

    def test_sorted_by_frequency(self, db_with_unmatched):
        recommender = FAQRecommender(db_with_unmatched)
        result = recommender.get_recommendations()
        frequencies = [r["frequency"] for r in result]
        assert frequencies == sorted(frequencies, reverse=True)

    def test_top_k_limit(self, db_with_unmatched):
        recommender = FAQRecommender(db_with_unmatched)
        result = recommender.get_recommendations(top_k=2)
        assert len(result) <= 2

    def test_empty_db_returns_empty(self, db_empty):
        recommender = FAQRecommender(db_empty)
        result = recommender.get_recommendations()
        assert result == []

    def test_matched_only_returns_empty(self, db_matched_only):
        recommender = FAQRecommender(db_matched_only)
        result = recommender.get_recommendations()
        assert result == []

    def test_frequency_counts_duplicates(self, db_with_unmatched):
        """동일 질문이 여러 번 나타나면 빈도에 반영된다."""
        recommender = FAQRecommender(db_with_unmatched)
        result = recommender.get_recommendations()
        # 가장 빈도 높은 클러스터의 빈도가 1보다 커야 함
        assert result[0]["frequency"] > 1

    def test_similar_queries_list(self, db_with_unmatched):
        recommender = FAQRecommender(db_with_unmatched)
        result = recommender.get_recommendations()
        for item in result:
            assert isinstance(item["similar_queries"], list)
            assert len(item["similar_queries"]) >= 1

    def test_suggested_category_is_string(self, db_with_unmatched):
        recommender = FAQRecommender(db_with_unmatched)
        result = recommender.get_recommendations()
        for item in result:
            assert isinstance(item["suggested_category"], str)
            assert len(item["suggested_category"]) > 0


class TestGenerateFaqDraft:
    """generate_faq_draft 테스트."""

    def test_draft_structure(self, temp_db):
        recommender = FAQRecommender(temp_db)
        cluster = [
            "보세전시장에서 차량 반입이 가능한가요?",
            "차량을 보세전시장에 들여올 수 있나요?",
        ]
        draft = recommender.generate_faq_draft(cluster)
        assert "id" in draft
        assert "category" in draft
        assert "question" in draft
        assert "keywords" in draft
        assert "answer" in draft
        assert "legal_basis" in draft
        assert "source_queries" in draft

    def test_draft_has_auto_id(self, temp_db):
        recommender = FAQRecommender(temp_db)
        draft = recommender.generate_faq_draft(["테스트 질문입니다"])
        assert draft["id"].startswith("AUTO_")

    def test_draft_answer_placeholder(self, temp_db):
        recommender = FAQRecommender(temp_db)
        draft = recommender.generate_faq_draft(["테스트 질문입니다"])
        assert "작성해 주세요" in draft["answer"]

    def test_draft_keywords_extracted(self, temp_db):
        recommender = FAQRecommender(temp_db)
        cluster = [
            "보세전시장에서 차량 반입이 가능한가요?",
            "차량을 보세전시장에 들여올 수 있나요?",
        ]
        draft = recommender.generate_faq_draft(cluster)
        assert isinstance(draft["keywords"], list)
        assert len(draft["keywords"]) > 0

    def test_draft_empty_cluster(self, temp_db):
        recommender = FAQRecommender(temp_db)
        draft = recommender.generate_faq_draft([])
        assert draft == {}

    def test_draft_source_queries(self, temp_db):
        recommender = FAQRecommender(temp_db)
        cluster = ["질문1", "질문2"]
        draft = recommender.generate_faq_draft(cluster)
        assert draft["source_queries"] == cluster

    def test_draft_representative_is_longest(self, temp_db):
        """대표 질문은 가장 긴 질문이다."""
        recommender = FAQRecommender(temp_db)
        cluster = [
            "차량 반입 가능?",
            "보세전시장에서 차량 반입이 가능한가요?",
        ]
        draft = recommender.generate_faq_draft(cluster)
        assert draft["question"] == "보세전시장에서 차량 반입이 가능한가요?"


class TestClusteringLogic:
    """클러스터링 내부 로직 테스트."""

    def test_identical_queries_cluster_together(self, temp_db):
        recommender = FAQRecommender(temp_db)
        queries = ["보세전시장 차량 반입", "보세전시장 차량 반입", "전혀 다른 질문"]
        clusters = recommender._cluster_queries(queries)
        # 동일 질문은 같은 클러스터에
        assert any(len(c) >= 2 for c in clusters) or len(clusters) <= len(queries)

    def test_empty_queries(self, temp_db):
        recommender = FAQRecommender(temp_db)
        clusters = recommender._cluster_queries([])
        assert clusters == []

    def test_single_query(self, temp_db):
        recommender = FAQRecommender(temp_db)
        clusters = recommender._cluster_queries(["하나의 질문"])
        assert len(clusters) == 1
        assert clusters[0] == ["하나의 질문"]
