"""Tests for the conversation summarization engine."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.conversation_summary import (
    ConversationKeywordExtractor,
    ConversationSummarizer,
    CATEGORY_NAMES,
)
from src.session import Session, SessionManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session_manager():
    return SessionManager()


@pytest.fixture
def summarizer(session_manager):
    return ConversationSummarizer(session_manager)


@pytest.fixture
def keyword_extractor():
    return ConversationKeywordExtractor()


@pytest.fixture
def session_with_history(session_manager):
    """Create a session with realistic bonded exhibition conversation."""
    session = session_manager.create_session()
    session.add_turn(
        "보세전시장이 무엇인가요?",
        "보세전시장은 외국물품을 관세 납부 없이 전시할 수 있는 특별 구역입니다.",
    )
    session.add_turn(
        "전시장에서 물품을 판매할 수 있나요?",
        "보세전시장에서 현장 판매가 가능합니다. 다만, 판매 시에는 관세를 납부해야 합니다.",
    )
    session.add_turn(
        "견본품은 어떻게 처리하나요?",
        "견본품은 일정 금액 이하인 경우 관세가 면제될 수 있습니다.",
    )
    return session


@pytest.fixture
def session_with_escalation(session_manager):
    """Create a session that triggers escalation."""
    session = session_manager.create_session()
    session.add_turn(
        "보세전시장 특허 신청은 어떻게 하나요?",
        "특허 신청은 세관에 신청서를 제출하시면 됩니다.",
    )
    session.add_turn(
        "담당자와 직접 상담하고 싶습니다. 전화번호를 알려주세요.",
        "고객지원 센터로 연락하시면 됩니다.",
    )
    return session


@pytest.fixture
def empty_session(session_manager):
    """Create an empty session with no history."""
    return session_manager.create_session()


# ---------------------------------------------------------------------------
# Session Summarization Tests
# ---------------------------------------------------------------------------

class TestSessionSummarization:
    def test_summarize_session_basic(self, summarizer, session_with_history):
        summary = summarizer.summarize_session(session_with_history.session_id)
        assert summary is not None
        assert summary["session_id"] == session_with_history.session_id
        assert summary["questions_asked"] == 3
        assert isinstance(summary["categories_covered"], list)
        assert len(summary["categories_covered"]) > 0
        assert isinstance(summary["satisfaction_score"], float)
        assert 0.0 <= summary["satisfaction_score"] <= 1.0

    def test_summarize_session_has_main_topic(self, summarizer, session_with_history):
        summary = summarizer.summarize_session(session_with_history.session_id)
        assert "main_topic" in summary
        assert summary["main_topic"] != ""

    def test_summarize_session_has_keywords(self, summarizer, session_with_history):
        summary = summarizer.summarize_session(session_with_history.session_id)
        assert "keywords" in summary
        assert isinstance(summary["keywords"], list)

    def test_summarize_session_has_duration(self, summarizer, session_with_history):
        summary = summarizer.summarize_session(session_with_history.session_id)
        assert "duration_seconds" in summary
        assert summary["duration_seconds"] >= 0.0

    def test_summarize_nonexistent_session(self, summarizer):
        result = summarizer.summarize_session("nonexistent-session-id")
        assert result is None

    def test_summarize_empty_session(self, summarizer, empty_session):
        summary = summarizer.summarize_session(empty_session.session_id)
        assert summary is not None
        assert summary["questions_asked"] == 0
        assert summary["main_topic"] == "대화 없음"
        assert summary["categories_covered"] == []
        assert summary["escalation_status"] is False

    def test_summarize_session_escalation_detected(self, summarizer, session_with_escalation):
        summary = summarizer.summarize_session(session_with_escalation.session_id)
        assert summary is not None
        # The session may or may not trigger escalation depending on rules;
        # just verify the field exists and is boolean
        assert isinstance(summary["escalation_status"], bool)
        assert "escalation_count" in summary


# ---------------------------------------------------------------------------
# Key Point Extraction Tests
# ---------------------------------------------------------------------------

class TestKeyPointExtraction:
    def test_extract_key_points_basic(self, summarizer):
        messages = [
            {"query": "보세전시장 특허 신청 방법은?", "answer": "세관에 신청서를 제출합니다."},
            {"query": "전시 가능한 물품은?", "answer": "외국물품을 전시할 수 있습니다."},
        ]
        points = summarizer.extract_key_points(messages)
        assert len(points) == 2
        assert "query" in points[0]
        assert "category" in points[0]
        assert "summary" in points[0]
        assert "category_name" in points[0]

    def test_extract_key_points_empty(self, summarizer):
        points = summarizer.extract_key_points([])
        assert points == []

    def test_extract_key_points_categories_assigned(self, summarizer):
        messages = [
            {"query": "벌칙이 궁금합니다", "answer": "위반 시 과태료가 부과됩니다."},
        ]
        points = summarizer.extract_key_points(messages)
        assert len(points) == 1
        assert points[0]["category"] == "PENALTIES"

    def test_extract_key_points_long_answer_truncated(self, summarizer):
        long_answer = "이것은 매우 긴 답변입니다. " * 20
        messages = [{"query": "질문", "answer": long_answer}]
        points = summarizer.extract_key_points(messages)
        assert points[0]["summary"].endswith("...")
        assert len(points[0]["summary"]) <= 103  # 100 chars + "..."

    def test_extract_key_points_skips_empty_query(self, summarizer):
        messages = [
            {"query": "", "answer": "답변"},
            {"query": "보세전시장이란?", "answer": "설명"},
        ]
        points = summarizer.extract_key_points(messages)
        assert len(points) == 1


# ---------------------------------------------------------------------------
# Category Detection Tests
# ---------------------------------------------------------------------------

class TestCategoryDetection:
    def test_get_categories_discussed(self, summarizer):
        messages = [
            {"query": "보세전시장 특허 신청은?", "answer": ""},
            {"query": "전시 물품 반입 방법은?", "answer": ""},
            {"query": "견본품 관세는?", "answer": ""},
        ]
        categories = summarizer.get_categories_discussed(messages)
        assert isinstance(categories, list)
        assert len(categories) > 0
        # All categories should be valid codes
        valid_codes = set(CATEGORY_NAMES.keys())
        for cat in categories:
            assert cat in valid_codes

    def test_get_categories_discussed_empty(self, summarizer):
        categories = summarizer.get_categories_discussed([])
        assert categories == []

    def test_get_categories_ordered_by_frequency(self, summarizer):
        messages = [
            {"query": "특허 신청 방법", "answer": ""},
            {"query": "특허 기간 연장", "answer": ""},
            {"query": "전시 물품", "answer": ""},
        ]
        categories = summarizer.get_categories_discussed(messages)
        # LICENSE should appear first since two queries match it
        assert len(categories) >= 1
        assert categories[0] == "LICENSE"

    def test_categories_detect_multiple_types(self, summarizer):
        messages = [
            {"query": "벌칙이 뭐가 있나요?", "answer": ""},
            {"query": "시식 식품 요건은?", "answer": ""},
            {"query": "서류 제출 방법은?", "answer": ""},
        ]
        categories = summarizer.get_categories_discussed(messages)
        assert "PENALTIES" in categories
        assert "FOOD_TASTING" in categories
        assert "DOCUMENTS" in categories


# ---------------------------------------------------------------------------
# Keyword Extraction Tests
# ---------------------------------------------------------------------------

class TestKeywordExtraction:
    def test_extract_keywords_basic(self, keyword_extractor):
        text = "보세전시장에서 외국물품을 전시하고 판매할 수 있습니다"
        keywords = keyword_extractor.extract_keywords(text, top_n=5)
        assert isinstance(keywords, list)
        assert len(keywords) <= 5
        for kw in keywords:
            assert "keyword" in kw
            assert "score" in kw
            assert kw["score"] > 0

    def test_extract_keywords_empty_text(self, keyword_extractor):
        assert keyword_extractor.extract_keywords("") == []
        assert keyword_extractor.extract_keywords("   ") == []

    def test_extract_keywords_top_n(self, keyword_extractor):
        text = "보세전시장 특허 신청 방법과 전시 물품 반입 반출 절차 서류 제출"
        kw3 = keyword_extractor.extract_keywords(text, top_n=3)
        kw5 = keyword_extractor.extract_keywords(text, top_n=5)
        assert len(kw3) <= 3
        assert len(kw5) <= 5

    def test_extract_keywords_domain_boost(self, keyword_extractor):
        text = "보세전시장 특허 신청과 일반적인 내용 설명"
        keywords = keyword_extractor.extract_keywords(text, top_n=10)
        # Domain terms should score higher due to boost
        keyword_strs = [kw["keyword"] for kw in keywords]
        assert len(keyword_strs) > 0

    def test_extract_topics_basic(self, keyword_extractor):
        messages = [
            {"query": "보세전시장 특허 신청은?", "answer": "세관에 제출합니다."},
            {"query": "전시 물품 반입 방법은?", "answer": "반입신고서를 작성합니다."},
        ]
        topics = keyword_extractor.extract_topics(messages)
        assert isinstance(topics, list)
        assert len(topics) > 0
        for topic in topics:
            assert "topic" in topic
            assert "category" in topic
            assert "relevance" in topic

    def test_extract_topics_empty(self, keyword_extractor):
        topics = keyword_extractor.extract_topics([])
        assert topics == []

    def test_extract_topics_has_keywords(self, keyword_extractor):
        messages = [
            {"query": "판매 가능한가요?", "answer": "현장 판매 가능합니다."},
        ]
        topics = keyword_extractor.extract_topics(messages)
        for topic in topics:
            assert "keywords" in topic
            assert isinstance(topic["keywords"], list)


# ---------------------------------------------------------------------------
# Batch Summarization Tests
# ---------------------------------------------------------------------------

class TestBatchSummarization:
    def test_summarize_batch_basic(self, summarizer, session_manager):
        s1 = session_manager.create_session()
        s1.add_turn("보세전시장이란?", "보세전시장은 특별 구역입니다.")
        s2 = session_manager.create_session()
        s2.add_turn("특허 신청 방법은?", "세관에 신청합니다.")

        results = summarizer.summarize_batch([s1.session_id, s2.session_id])
        assert len(results) == 2
        assert results[0]["session_id"] == s1.session_id
        assert results[1]["session_id"] == s2.session_id

    def test_summarize_batch_skips_nonexistent(self, summarizer, session_manager):
        s1 = session_manager.create_session()
        s1.add_turn("테스트", "답변")

        results = summarizer.summarize_batch([s1.session_id, "nonexistent-id"])
        assert len(results) == 1
        assert results[0]["session_id"] == s1.session_id

    def test_summarize_batch_empty(self, summarizer):
        results = summarizer.summarize_batch([])
        assert results == []

    def test_summarize_batch_all_nonexistent(self, summarizer):
        results = summarizer.summarize_batch(["bad-1", "bad-2", "bad-3"])
        assert results == []


# ---------------------------------------------------------------------------
# Session Report Tests
# ---------------------------------------------------------------------------

class TestSessionReport:
    def test_generate_session_report(self, summarizer, session_with_history):
        report = summarizer.generate_session_report(session_with_history.session_id)
        assert report is not None
        assert "session_id" in report
        assert "generated_at" in report
        assert "summary" in report
        assert "topics" in report
        assert "escalation_details" in report
        assert "turn_count" in report
        assert report["turn_count"] == 3

    def test_generate_session_report_nonexistent(self, summarizer):
        report = summarizer.generate_session_report("nonexistent")
        assert report is None

    def test_generate_session_report_has_timestamps(self, summarizer, session_with_history):
        report = summarizer.generate_session_report(session_with_history.session_id)
        assert "created_at" in report
        assert "last_active" in report
        assert report["last_active"] >= report["created_at"]


# ---------------------------------------------------------------------------
# Escalation Points Tests
# ---------------------------------------------------------------------------

class TestEscalationPoints:
    def test_get_escalation_points_empty(self, summarizer):
        points = summarizer.get_escalation_points([])
        assert points == []

    def test_get_escalation_points_structure(self, summarizer):
        messages = [
            {"query": "담당자 전화번호 알려주세요", "answer": "연락처입니다."},
        ]
        points = summarizer.get_escalation_points(messages)
        # Whether it triggers depends on escalation_rules.json;
        # verify structure if it does
        for point in points:
            assert "turn" in point
            assert "query" in point
            assert "rule" in point

    def test_get_escalation_points_turn_numbering(self, summarizer):
        messages = [
            {"query": "일반 질문", "answer": "답변"},
            {"query": "담당자와 직접 상담하고 싶습니다", "answer": "연락처"},
            {"query": "또 다른 질문", "answer": "답변"},
        ]
        points = summarizer.get_escalation_points(messages)
        for point in points:
            assert point["turn"] >= 1


# ---------------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------------

class TestConversationSummaryAPI:
    @pytest.fixture
    def client(self):
        from web_server import app
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_session_summary_endpoint(self, client):
        # Create a session
        res = client.post("/api/session/new")
        assert res.status_code == 201
        session_id = res.get_json()["session_id"]

        # Add some conversation via chat
        client.post("/api/chat", json={
            "session_id": session_id,
            "message": "보세전시장이 무엇인가요?",
        })

        # Get summary
        res = client.get(f"/api/session/{session_id}/summary")
        assert res.status_code == 200
        data = res.get_json()
        assert data["session_id"] == session_id
        assert "main_topic" in data
        assert "questions_asked" in data
        assert "categories_covered" in data

    def test_session_summary_not_found(self, client):
        res = client.get("/api/session/nonexistent-id/summary")
        assert res.status_code == 404

    def test_admin_sessions_summaries_endpoint(self, client):
        res = client.get("/api/admin/sessions/summaries")
        assert res.status_code == 200
        data = res.get_json()
        assert "count" in data
        assert "summaries" in data
        assert isinstance(data["summaries"], list)

    def test_admin_sessions_summaries_with_date(self, client):
        res = client.get("/api/admin/sessions/summaries?date=2099-01-01")
        assert res.status_code == 200
        data = res.get_json()
        assert data["count"] == 0

    def test_admin_sessions_topics_endpoint(self, client):
        res = client.get("/api/admin/sessions/topics")
        assert res.status_code == 200
        data = res.get_json()
        assert "count" in data
        assert "topics" in data
        assert isinstance(data["topics"], list)


# ---------------------------------------------------------------------------
# Satisfaction Score Tests
# ---------------------------------------------------------------------------

class TestSatisfactionScore:
    def test_satisfaction_score_normal_session(self, summarizer, session_with_history):
        summary = summarizer.summarize_session(session_with_history.session_id)
        score = summary["satisfaction_score"]
        assert 0.0 <= score <= 1.0
        # A normal session with good answers should score reasonably well
        assert score >= 0.5

    def test_satisfaction_score_empty(self, summarizer):
        score = summarizer._estimate_satisfaction([], [])
        assert score == 0.0

    def test_satisfaction_score_with_escalation(self, summarizer):
        messages = [
            {"query": "질문", "answer": "답변이 충분히 길어야 합니다."},
        ]
        escalations = [{"turn": 1, "query": "질문", "rule": {}}]
        score = summarizer._estimate_satisfaction(messages, escalations)
        # Escalation should reduce the score
        score_no_esc = summarizer._estimate_satisfaction(messages, [])
        assert score < score_no_esc
