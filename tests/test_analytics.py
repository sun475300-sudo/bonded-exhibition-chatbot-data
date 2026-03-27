"""QueryAnalytics 테스트."""

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analytics import QueryAnalytics
from src.feedback import FeedbackManager
from src.logger_db import ChatLogger


@pytest.fixture
def temp_dbs():
    """임시 DB 파일로 ChatLogger와 FeedbackManager를 생성한다."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_db = ChatLogger(db_path=os.path.join(tmpdir, "chat_logs.db"))
        fb_db = FeedbackManager(db_path=os.path.join(tmpdir, "feedback.db"))
        yield log_db, fb_db
        log_db.close()
        fb_db.close()


@pytest.fixture
def analytics(temp_dbs):
    """QueryAnalytics 인스턴스를 생성한다."""
    log_db, fb_db = temp_dbs
    return QueryAnalytics(log_db, fb_db)


@pytest.fixture
def analytics_with_data(temp_dbs):
    """데이터가 있는 QueryAnalytics 인스턴스를 생성한다."""
    log_db, fb_db = temp_dbs

    # 로그 데이터 삽입
    log_db.log_query("보세전시장이 무엇인가요?", category="GENERAL", faq_id="FAQ_001")
    log_db.log_query("물품 반입 절차는?", category="IMPORT_EXPORT", faq_id="FAQ_002")
    log_db.log_query("관세 납부 방법은?", category="TAX", faq_id=None)
    log_db.log_query("담당자와 통화하고 싶습니다", category="GENERAL",
                     faq_id=None, is_escalation=True)
    log_db.log_query("전시 기간은 얼마나 되나요?", category="EXHIBITION", faq_id="FAQ_003")

    # 피드백 데이터 삽입
    fb_db.save_feedback("Q1", "helpful")
    fb_db.save_feedback("Q2", "helpful")
    fb_db.save_feedback("Q3", "unhelpful")
    fb_db.save_feedback("Q4", "helpful")

    return QueryAnalytics(log_db, fb_db)


class TestQueryAnalyticsInit:
    """초기화 테스트."""

    def test_init(self, temp_dbs):
        log_db, fb_db = temp_dbs
        qa = QueryAnalytics(log_db, fb_db)
        assert qa.logger_db is log_db
        assert qa.feedback_db is fb_db


class TestGetTrendReport:
    """get_trend_report 테스트."""

    def test_empty_db(self, analytics):
        result = analytics.get_trend_report(days=7)
        assert "daily_counts" in result
        assert "category_trends" in result
        assert "escalation_trends" in result
        assert result["days"] == 7

    def test_with_data(self, analytics_with_data):
        result = analytics_with_data.get_trend_report(days=7)
        assert len(result["daily_counts"]) >= 1

    def test_daily_counts_structure(self, analytics_with_data):
        result = analytics_with_data.get_trend_report(days=7)
        for day in result["daily_counts"]:
            assert "date" in day
            assert "count" in day
            assert day["count"] > 0

    def test_category_trends_dict(self, analytics_with_data):
        result = analytics_with_data.get_trend_report(days=7)
        assert isinstance(result["category_trends"], dict)

    def test_escalation_trends_structure(self, analytics_with_data):
        result = analytics_with_data.get_trend_report(days=7)
        for esc in result["escalation_trends"]:
            assert "date" in esc
            assert "escalations" in esc
            assert "total" in esc
            assert "rate" in esc

    def test_custom_days(self, analytics_with_data):
        result = analytics_with_data.get_trend_report(days=30)
        assert result["days"] == 30

    def test_one_day(self, analytics_with_data):
        result = analytics_with_data.get_trend_report(days=1)
        assert result["days"] == 1


class TestGetQualityScore:
    """get_quality_score 테스트."""

    def test_empty_db(self, analytics):
        result = analytics.get_quality_score()
        assert "overall_score" in result
        assert "helpful_rate" in result
        assert "faq_match_rate" in result
        assert "escalation_rate" in result
        assert "breakdown" in result

    def test_with_data(self, analytics_with_data):
        result = analytics_with_data.get_quality_score()
        assert 0 <= result["overall_score"] <= 100
        assert result["helpful_rate"] == 75.0

    def test_faq_match_rate(self, analytics_with_data):
        result = analytics_with_data.get_quality_score()
        # 5개 중 3개 매칭 -> 60%
        assert result["faq_match_rate"] == 60.0

    def test_escalation_rate(self, analytics_with_data):
        result = analytics_with_data.get_quality_score()
        # 5개 중 1개 에스컬레이션 -> 20%
        assert result["escalation_rate"] == 20.0

    def test_breakdown_components(self, analytics_with_data):
        result = analytics_with_data.get_quality_score()
        breakdown = result["breakdown"]
        assert "helpful_component" in breakdown
        assert "faq_match_component" in breakdown
        assert "escalation_component" in breakdown

    def test_score_is_weighted_sum(self, analytics_with_data):
        result = analytics_with_data.get_quality_score()
        breakdown = result["breakdown"]
        expected = (
            breakdown["helpful_component"]
            + breakdown["faq_match_component"]
            + breakdown["escalation_component"]
        )
        assert abs(result["overall_score"] - expected) < 0.2

    def test_total_queries_count(self, analytics_with_data):
        result = analytics_with_data.get_quality_score()
        assert result["total_queries"] == 5

    def test_total_feedback_count(self, analytics_with_data):
        result = analytics_with_data.get_quality_score()
        assert result["total_feedback"] == 4


class TestGetPeakHours:
    """get_peak_hours 테스트."""

    def test_empty_db(self, analytics):
        result = analytics.get_peak_hours()
        assert "hours" in result
        assert "peak_hour" in result
        assert "peak_count" in result
        assert len(result["hours"]) == 24

    def test_with_data(self, analytics_with_data):
        result = analytics_with_data.get_peak_hours()
        assert result["peak_count"] >= 1
        assert 0 <= result["peak_hour"] <= 23

    def test_hours_structure(self, analytics_with_data):
        result = analytics_with_data.get_peak_hours()
        for entry in result["hours"]:
            assert "hour" in entry
            assert "count" in entry
            assert 0 <= entry["hour"] <= 23
            assert entry["count"] >= 0

    def test_all_24_hours_present(self, analytics):
        result = analytics.get_peak_hours()
        hours_set = {e["hour"] for e in result["hours"]}
        assert hours_set == set(range(24))

    def test_total_matches(self, analytics_with_data):
        result = analytics_with_data.get_peak_hours()
        total = sum(e["count"] for e in result["hours"])
        assert total == 5


class TestGetWeeklyReport:
    """get_weekly_report 테스트."""

    def test_empty_db(self, analytics):
        result = analytics.get_weekly_report()
        assert "trend" in result
        assert "quality" in result
        assert "peak_hours" in result
        assert "unmatched_queries" in result
        assert result["period"] == "weekly"

    def test_with_data(self, analytics_with_data):
        result = analytics_with_data.get_weekly_report()
        assert result["quality"]["total_queries"] == 5
        assert len(result["unmatched_queries"]) > 0

    def test_generated_at(self, analytics):
        result = analytics.get_weekly_report()
        assert "generated_at" in result
        # 파싱 가능한 날짜 형식이어야 함
        datetime.strptime(result["generated_at"], "%Y-%m-%d %H:%M:%S")

    def test_contains_all_sections(self, analytics_with_data):
        result = analytics_with_data.get_weekly_report()
        assert "daily_counts" in result["trend"]
        assert "overall_score" in result["quality"]
        assert "hours" in result["peak_hours"]


class TestGenerateReportText:
    """generate_report_text 테스트."""

    def test_empty_db(self, analytics):
        text = analytics.generate_report_text()
        assert isinstance(text, str)
        assert "보세전시장 챗봇 주간 리포트" in text

    def test_with_data(self, analytics_with_data):
        text = analytics_with_data.generate_report_text()
        assert "트렌드 요약" in text
        assert "답변 품질 점수" in text
        assert "피크 시간대" in text
        assert "미매칭 질문" in text

    def test_contains_quality_score(self, analytics_with_data):
        text = analytics_with_data.generate_report_text()
        assert "종합 점수" in text
        assert "사용자 만족도" in text

    def test_contains_faq_match_rate(self, analytics_with_data):
        text = analytics_with_data.generate_report_text()
        assert "FAQ 매칭률" in text

    def test_contains_escalation_info(self, analytics_with_data):
        text = analytics_with_data.generate_report_text()
        assert "에스컬레이션 비율" in text

    def test_multiline_format(self, analytics_with_data):
        text = analytics_with_data.generate_report_text()
        lines = text.split("\n")
        assert len(lines) > 10

    def test_no_unmatched_message(self, temp_dbs):
        """미매칭 질문이 없으면 '없음' 메시지가 나온다."""
        log_db, fb_db = temp_dbs
        log_db.log_query("테스트", category="GENERAL", faq_id="FAQ_001")
        qa = QueryAnalytics(log_db, fb_db)
        text = qa.generate_report_text()
        assert "미매칭 질문 없음" in text
