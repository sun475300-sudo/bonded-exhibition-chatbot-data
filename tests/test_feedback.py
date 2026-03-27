"""사용자 피드백 시스템 테스트."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.feedback import FeedbackManager


@pytest.fixture
def manager():
    """임시 DB 파일로 FeedbackManager 인스턴스를 생성한다."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_feedback.db")
        fm = FeedbackManager(db_path=db_path)
        yield fm
        fm.close()


class TestSaveFeedback:
    """피드백 저장 테스트."""

    def test_save_helpful(self, manager):
        fid = manager.save_feedback("Q001", "helpful")
        assert fid is not None
        assert fid >= 1

    def test_save_unhelpful(self, manager):
        fid = manager.save_feedback("Q002", "unhelpful", comment="답변이 부정확해요")
        assert fid >= 1

    def test_save_with_empty_comment(self, manager):
        fid = manager.save_feedback("Q003", "helpful", comment="")
        assert fid >= 1

    def test_invalid_rating_raises(self, manager):
        with pytest.raises(ValueError):
            manager.save_feedback("Q004", "bad_rating")

    def test_invalid_rating_good(self, manager):
        with pytest.raises(ValueError):
            manager.save_feedback("Q005", "good")

    def test_save_multiple(self, manager):
        for i in range(5):
            manager.save_feedback(f"Q{i}", "helpful")
        stats = manager.get_feedback_stats()
        assert stats["total"] == 5

    def test_returns_incrementing_ids(self, manager):
        id1 = manager.save_feedback("Q1", "helpful")
        id2 = manager.save_feedback("Q2", "unhelpful")
        assert id2 > id1


class TestFeedbackStats:
    """피드백 통계 테스트."""

    def test_empty_stats(self, manager):
        stats = manager.get_feedback_stats()
        assert stats["total"] == 0
        assert stats["helpful_count"] == 0
        assert stats["unhelpful_count"] == 0
        assert stats["helpful_rate"] == 0
        assert stats["daily_stats"] == []

    def test_all_helpful(self, manager):
        for i in range(4):
            manager.save_feedback(f"Q{i}", "helpful")
        stats = manager.get_feedback_stats()
        assert stats["total"] == 4
        assert stats["helpful_count"] == 4
        assert stats["unhelpful_count"] == 0
        assert stats["helpful_rate"] == 100.0

    def test_all_unhelpful(self, manager):
        for i in range(3):
            manager.save_feedback(f"Q{i}", "unhelpful")
        stats = manager.get_feedback_stats()
        assert stats["total"] == 3
        assert stats["helpful_count"] == 0
        assert stats["unhelpful_count"] == 3
        assert stats["helpful_rate"] == 0.0

    def test_mixed_feedback(self, manager):
        manager.save_feedback("Q1", "helpful")
        manager.save_feedback("Q2", "helpful")
        manager.save_feedback("Q3", "unhelpful")
        manager.save_feedback("Q4", "helpful")
        stats = manager.get_feedback_stats()
        assert stats["total"] == 4
        assert stats["helpful_count"] == 3
        assert stats["unhelpful_count"] == 1
        assert stats["helpful_rate"] == 75.0

    def test_daily_stats_present(self, manager):
        manager.save_feedback("Q1", "helpful")
        manager.save_feedback("Q2", "unhelpful")
        stats = manager.get_feedback_stats()
        assert len(stats["daily_stats"]) >= 1
        day = stats["daily_stats"][0]
        assert "date" in day
        assert "helpful" in day
        assert "unhelpful" in day
        assert "total" in day

    def test_daily_stats_counts(self, manager):
        manager.save_feedback("Q1", "helpful")
        manager.save_feedback("Q2", "helpful")
        manager.save_feedback("Q3", "unhelpful")
        stats = manager.get_feedback_stats()
        today = stats["daily_stats"][0]
        assert today["helpful"] == 2
        assert today["unhelpful"] == 1
        assert today["total"] == 3


class TestLowRatedQueries:
    """낮은 평가 질문 목록 테스트."""

    def test_empty(self, manager):
        result = manager.get_low_rated_queries()
        assert result == []

    def test_only_unhelpful_returned(self, manager):
        manager.save_feedback("Q1", "helpful")
        manager.save_feedback("Q2", "unhelpful", comment="부정확")
        manager.save_feedback("Q3", "helpful")
        result = manager.get_low_rated_queries()
        assert len(result) == 1
        assert result[0]["query_id"] == "Q2"
        assert result[0]["comment"] == "부정확"

    def test_limit(self, manager):
        for i in range(10):
            manager.save_feedback(f"Q{i}", "unhelpful")
        result = manager.get_low_rated_queries(limit=3)
        assert len(result) == 3

    def test_order_desc(self, manager):
        manager.save_feedback("Q_OLD", "unhelpful")
        manager.save_feedback("Q_NEW", "unhelpful")
        result = manager.get_low_rated_queries(limit=10)
        assert result[0]["query_id"] == "Q_NEW"

    def test_has_required_fields(self, manager):
        manager.save_feedback("Q1", "unhelpful", comment="테스트")
        result = manager.get_low_rated_queries(limit=1)
        item = result[0]
        assert "query_id" in item
        assert "timestamp" in item
        assert "comment" in item

    def test_no_unhelpful(self, manager):
        for i in range(5):
            manager.save_feedback(f"Q{i}", "helpful")
        result = manager.get_low_rated_queries()
        assert len(result) == 0


class TestDbInit:
    """DB 초기화 테스트."""

    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "sub", "dir", "feedback.db")
            fm = FeedbackManager(db_path=nested)
            assert os.path.isdir(os.path.join(tmpdir, "sub", "dir"))
            fm.close()

    def test_close_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            fm1 = FeedbackManager(db_path=db_path)
            fm1.save_feedback("Q1", "helpful")
            fm1.close()

            fm2 = FeedbackManager(db_path=db_path)
            stats = fm2.get_feedback_stats()
            assert stats["total"] == 1
            fm2.close()
