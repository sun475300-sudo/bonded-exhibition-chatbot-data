"""사용자 맞춤 FAQ 추천 시스템 테스트."""

import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.user_recommender import UserRecommender


@pytest.fixture
def recommender(tmp_path):
    """임시 DB를 사용하는 UserRecommender 인스턴스."""
    db_path = str(tmp_path / "test_user_profiles.db")
    return UserRecommender(db_path=db_path)


@pytest.fixture
def populated_recommender(recommender):
    """테스트 데이터가 포함된 UserRecommender."""
    # Session A: 관세 관련 질문
    recommender.record_query("session_a", "관세율이 어떻게 되나요?", "TARIFF", "FAQ_001")
    recommender.record_query("session_a", "관세 납부 방법은?", "TARIFF", "FAQ_002")
    recommender.record_query("session_a", "수입 절차가 궁금합니다", "IMPORT", "FAQ_010")

    # Session B: 관세 + 수출 관련 질문 (session_a와 TARIFF 카테고리 공유)
    recommender.record_query("session_b", "관세 감면 조건은?", "TARIFF", "FAQ_003")
    recommender.record_query("session_b", "수출 신고 방법", "EXPORT", "FAQ_020")
    recommender.record_query("session_b", "수출 통관 절차", "EXPORT", "FAQ_021")

    # Session C: 전시장 관련 질문
    recommender.record_query("session_c", "전시장 이용 시간", "EXHIBITION", "FAQ_030")
    recommender.record_query("session_c", "전시장 위치 안내", "EXHIBITION", "FAQ_031")

    return recommender


class TestRecordQuery:
    """질문 기록 테스트."""

    def test_record_basic_query(self, recommender):
        """기본 질문 기록이 정상 동작하는지 확인."""
        recommender.record_query("sess1", "관세율이 어떻게 되나요?", "TARIFF", "FAQ_001")
        profile = recommender.get_user_profile("sess1")
        assert profile["visit_count"] == 1
        assert profile["preferred_categories"] == ["TARIFF"]

    def test_record_query_without_faq_id(self, recommender):
        """FAQ ID 없이 질문 기록이 가능한지 확인."""
        recommender.record_query("sess1", "미매칭 질문입니다", "GENERAL", None)
        profile = recommender.get_user_profile("sess1")
        assert profile["visit_count"] == 1

    def test_record_multiple_queries(self, recommender):
        """여러 질문 기록이 정상 동작하는지 확인."""
        recommender.record_query("sess1", "질문1", "TARIFF", "FAQ_001")
        recommender.record_query("sess1", "질문2", "IMPORT", "FAQ_010")
        recommender.record_query("sess1", "질문3", "TARIFF", "FAQ_002")
        profile = recommender.get_user_profile("sess1")
        assert profile["visit_count"] == 3
        # TARIFF가 2번으로 더 많으므로 첫 번째
        assert profile["preferred_categories"][0] == "TARIFF"


class TestGetRecommendations:
    """개인화 추천 테스트."""

    def test_empty_history_returns_popular(self, populated_recommender):
        """이력이 없는 세션은 인기 FAQ를 반환."""
        recs = populated_recommender.get_recommendations("new_session")
        # Should return popular FAQs since no history
        assert isinstance(recs, list)

    def test_recommendations_based_on_history(self, populated_recommender):
        """이력 기반 추천이 동작하는지 확인."""
        recs = populated_recommender.get_recommendations("session_a", top_n=5)
        assert isinstance(recs, list)
        # session_a has seen FAQ_001, FAQ_002, FAQ_010
        # Should recommend FAQs from TARIFF/IMPORT that session_a hasn't seen
        rec_faq_ids = {r["faq_id"] for r in recs}
        # FAQ_001, FAQ_002, FAQ_010 should NOT be in recommendations (already visited)
        assert "FAQ_001" not in rec_faq_ids
        assert "FAQ_002" not in rec_faq_ids
        assert "FAQ_010" not in rec_faq_ids

    def test_recommendations_top_n_limit(self, populated_recommender):
        """top_n 제한이 적용되는지 확인."""
        recs = populated_recommender.get_recommendations("session_a", top_n=2)
        assert len(recs) <= 2

    def test_recommendations_have_score(self, populated_recommender):
        """추천에 점수가 포함되는지 확인."""
        recs = populated_recommender.get_recommendations("session_a")
        for rec in recs:
            assert "faq_id" in rec
            assert "category" in rec
            assert "score" in rec


class TestGetPopularFaqs:
    """인기 FAQ 테스트."""

    def test_popular_empty_db(self, recommender):
        """빈 DB에서 인기 FAQ 조회가 빈 리스트를 반환."""
        popular = recommender.get_popular_faqs()
        assert popular == []

    def test_popular_returns_ordered(self, populated_recommender):
        """인기 FAQ가 조회 수 기준 내림차순으로 반환."""
        popular = populated_recommender.get_popular_faqs()
        assert isinstance(popular, list)
        assert len(popular) > 0
        # Check order: counts should be non-increasing
        for i in range(len(popular) - 1):
            assert popular[i]["count"] >= popular[i + 1]["count"]

    def test_popular_limit(self, populated_recommender):
        """limit 파라미터가 적용되는지 확인."""
        popular = populated_recommender.get_popular_faqs(limit=2)
        assert len(popular) <= 2

    def test_popular_has_required_fields(self, populated_recommender):
        """인기 FAQ 항목에 필수 필드가 포함."""
        popular = populated_recommender.get_popular_faqs()
        for item in popular:
            assert "faq_id" in item
            assert "category" in item
            assert "count" in item


class TestGetTrendingTopics:
    """트렌딩 토픽 테스트."""

    def test_trending_empty_db(self, recommender):
        """빈 DB에서 트렌딩 조회가 빈 리스트를 반환."""
        trending = recommender.get_trending_topics()
        assert trending == []

    def test_trending_returns_recent(self, populated_recommender):
        """트렌딩이 최근 데이터를 반환."""
        trending = populated_recommender.get_trending_topics(hours=24)
        assert isinstance(trending, list)
        assert len(trending) > 0

    def test_trending_has_required_fields(self, populated_recommender):
        """트렌딩 항목에 필수 필드가 포함."""
        trending = populated_recommender.get_trending_topics()
        for item in trending:
            assert "category" in item
            assert "count" in item
            assert "trend_score" in item

    def test_trending_limit(self, populated_recommender):
        """limit 파라미터가 적용되는지 확인."""
        trending = populated_recommender.get_trending_topics(limit=2)
        assert len(trending) <= 2

    def test_trending_old_data_excluded(self, recommender):
        """오래된 데이터가 hours 범위에서 제외되는지 확인."""
        # Record a query with old timestamp by directly inserting
        import sqlite3
        conn = sqlite3.connect(recommender.db_path)
        old_time = time.time() - 48 * 3600  # 48 hours ago
        conn.execute(
            "INSERT INTO query_history (session_id, query, category, faq_id, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            ("old_sess", "오래된 질문", "OLD_CAT", "FAQ_999", old_time),
        )
        conn.commit()
        conn.close()

        trending = recommender.get_trending_topics(hours=24)
        categories = [t["category"] for t in trending]
        assert "OLD_CAT" not in categories


class TestGetUserProfile:
    """사용자 프로필 테스트."""

    def test_empty_profile(self, recommender):
        """이력이 없는 사용자의 프로필."""
        profile = recommender.get_user_profile("nonexistent")
        assert profile["session_id"] == "nonexistent"
        assert profile["visit_count"] == 0
        assert profile["preferred_categories"] == []
        assert profile["recent_queries"] == []
        assert profile["first_visit"] is None
        assert profile["last_visit"] is None

    def test_profile_with_history(self, populated_recommender):
        """이력이 있는 사용자의 프로필."""
        profile = populated_recommender.get_user_profile("session_a")
        assert profile["session_id"] == "session_a"
        assert profile["visit_count"] == 3
        assert "TARIFF" in profile["preferred_categories"]
        assert "IMPORT" in profile["preferred_categories"]
        assert len(profile["recent_queries"]) == 3
        assert profile["first_visit"] is not None
        assert profile["last_visit"] is not None
        assert profile["last_visit"] >= profile["first_visit"]

    def test_preferred_categories_order(self, populated_recommender):
        """선호 카테고리가 빈도순으로 정렬되는지 확인."""
        profile = populated_recommender.get_user_profile("session_a")
        # TARIFF: 2회, IMPORT: 1회
        assert profile["preferred_categories"][0] == "TARIFF"

    def test_recent_queries_order(self, populated_recommender):
        """최근 질문이 시간 역순으로 정렬되는지 확인."""
        profile = populated_recommender.get_user_profile("session_a")
        queries = profile["recent_queries"]
        for i in range(len(queries) - 1):
            assert queries[i]["timestamp"] >= queries[i + 1]["timestamp"]


class TestCollaborativeFiltering:
    """협업 필터링 테스트."""

    def test_collaborative_recommendations(self, populated_recommender):
        """유사 세션 기반 추천이 동작하는지 확인."""
        # session_a: TARIFF, IMPORT
        # session_b: TARIFF, EXPORT (TARIFF 공유)
        # session_b has FAQ_020, FAQ_021 in EXPORT
        recs = populated_recommender.get_recommendations("session_a", top_n=10)
        rec_faq_ids = {r["faq_id"] for r in recs}
        # session_b's EXPORT FAQs should be recommended via collaborative filtering
        # (session_b shares TARIFF category with session_a)
        assert "FAQ_020" in rec_faq_ids or "FAQ_021" in rec_faq_ids

    def test_no_collaborative_for_isolated_session(self, recommender):
        """다른 세션과 겹치지 않는 세션은 협업 필터링 추천이 없음."""
        recommender.record_query("isolated", "독특한 질문", "UNIQUE_CAT", "FAQ_UNIQUE")
        recs = recommender.get_recommendations("isolated")
        # Only popular FAQs or nothing, no collaborative recommendations
        assert isinstance(recs, list)


class TestGetRelatedByHistory:
    """이력 기반 관련 FAQ 추천 테스트."""

    def test_related_from_other_categories(self, populated_recommender):
        """현재 카테고리 외의 관련 FAQ가 추천되는지 확인."""
        related = populated_recommender.get_related_by_history("session_a", "TARIFF")
        # session_a also visited IMPORT, so IMPORT FAQs should appear
        assert isinstance(related, list)
        if related:
            categories = {r["category"] for r in related}
            assert "TARIFF" not in categories

    def test_no_related_for_single_category_user(self, recommender):
        """단일 카테고리만 방문한 사용자는 관련 추천이 없음."""
        recommender.record_query("mono", "질문1", "TARIFF", "FAQ_001")
        recommender.record_query("mono", "질문2", "TARIFF", "FAQ_002")
        related = recommender.get_related_by_history("mono", "TARIFF")
        assert related == []


class TestAPIEndpoints:
    """API 엔드포인트 테스트."""

    @pytest.fixture
    def client(self):
        from web_server import app
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_recommendations_endpoint_requires_session_id(self, client):
        """session_id 없이 추천 API 호출 시 400 에러."""
        res = client.get("/api/recommendations")
        assert res.status_code == 400
        data = res.get_json()
        assert "error" in data

    def test_recommendations_endpoint_with_session_id(self, client):
        """session_id로 추천 API 호출이 성공."""
        res = client.get("/api/recommendations?session_id=test_sess")
        assert res.status_code == 200
        data = res.get_json()
        assert "session_id" in data
        assert "recommendations" in data
        assert isinstance(data["recommendations"], list)

    def test_popular_endpoint(self, client):
        """인기 FAQ API 호출이 성공."""
        res = client.get("/api/popular")
        assert res.status_code == 200
        data = res.get_json()
        assert "popular" in data
        assert isinstance(data["popular"], list)

    def test_popular_endpoint_with_limit(self, client):
        """인기 FAQ API에 limit 파라미터가 적용."""
        res = client.get("/api/popular?limit=3")
        assert res.status_code == 200
        data = res.get_json()
        assert len(data["popular"]) <= 3

    def test_trending_endpoint(self, client):
        """트렌딩 API 호출이 성공."""
        res = client.get("/api/trending")
        assert res.status_code == 200
        data = res.get_json()
        assert "trending" in data
        assert isinstance(data["trending"], list)

    def test_trending_endpoint_with_params(self, client):
        """트렌딩 API에 파라미터가 적용."""
        res = client.get("/api/trending?hours=48&limit=3")
        assert res.status_code == 200
        data = res.get_json()
        assert len(data["trending"]) <= 3

    def test_chat_includes_recommended_field(self, client):
        """채팅 응답에 recommended 필드가 포함되는지 확인."""
        res = client.post(
            "/api/chat",
            json={"query": "관세율이 어떻게 되나요?", "session_id": "rec_test_sess"},
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert "recommended" in data
        assert isinstance(data["recommended"], list)
