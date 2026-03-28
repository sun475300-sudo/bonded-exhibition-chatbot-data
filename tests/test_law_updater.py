"""법령 업데이트 모듈 테스트.

LawVersionTracker, FAQUpdateNotifier, LawUpdateScheduler 및
관련 API 엔드포인트를 검증한다.
"""

import json
import os
import sys
import tempfile

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, "src"))

from src.law_updater import (
    FAQUpdateNotifier,
    LawUpdateScheduler,
    LawVersionTracker,
)
from web_server import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """임시 SQLite DB 경로를 반환한다."""
    return str(tmp_path / "test_law_versions.db")


@pytest.fixture
def tracker(tmp_db):
    """LawVersionTracker 인스턴스를 반환한다."""
    return LawVersionTracker(db_path=tmp_db)


@pytest.fixture
def tmp_faq(tmp_path):
    """테스트용 FAQ JSON 파일을 생성한다."""
    faq_data = {
        "faq_version": "1.0.0",
        "last_updated": "2026-01-01",
        "items": [
            {
                "id": "FAQ_A",
                "category": "GENERAL",
                "question": "보세전시장이란?",
                "answer": "보세전시장 설명",
                "legal_basis": ["관세법 제190조"],
                "keywords": ["보세전시장"],
            },
            {
                "id": "FAQ_B",
                "category": "SALES",
                "question": "판매 가능한가요?",
                "answer": "판매 설명",
                "legal_basis": [
                    "관세법 시행령 제101조(판매용품의 면허전 사용금지)",
                ],
                "keywords": ["판매"],
            },
            {
                "id": "FAQ_C",
                "category": "SAMPLE",
                "question": "견본품 반출?",
                "answer": "견본품 설명",
                "legal_basis": ["관세법 제161조(견본품 반출)"],
                "keywords": ["견본품"],
            },
        ],
    }
    faq_path = str(tmp_path / "faq.json")
    with open(faq_path, "w", encoding="utf-8") as f:
        json.dump(faq_data, f, ensure_ascii=False)
    return faq_path


@pytest.fixture
def notifier(tmp_db, tmp_faq):
    """FAQUpdateNotifier 인스턴스를 반환한다."""
    return FAQUpdateNotifier(faq_path=tmp_faq, db_path=tmp_db)


@pytest.fixture
def scheduler(tracker, notifier):
    """LawUpdateScheduler 인스턴스를 반환한다."""
    sched = LawUpdateScheduler(version_tracker=tracker, notifier=notifier)
    yield sched
    sched.stop()


@pytest.fixture
def client():
    """Flask 테스트 클라이언트를 반환한다."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# LawVersionTracker 테스트
# ---------------------------------------------------------------------------

class TestLawVersionTracker:
    def test_record_new_version(self, tracker):
        """새로운 법령 조항을 처음 기록하면 True를 반환한다."""
        result = tracker.record_version("관세법", "제190조", "보세전시장 정의 텍스트")
        assert result is True

    def test_record_same_version_no_change(self, tracker):
        """동일한 텍스트를 다시 기록하면 False를 반환한다."""
        tracker.record_version("관세법", "제190조", "원본 텍스트")
        result = tracker.record_version("관세법", "제190조", "원본 텍스트")
        assert result is False

    def test_record_changed_version(self, tracker):
        """텍스트가 변경되면 True를 반환하고 이전 텍스트를 저장한다."""
        tracker.record_version("관세법", "제190조", "원본 텍스트")
        result = tracker.record_version("관세법", "제190조", "변경된 텍스트")
        assert result is True

        changes = tracker.get_all_versions()
        assert len(changes) == 2
        latest = changes[0]  # DESC order
        assert latest["previous_text"] == "원본 텍스트"
        assert latest["current_text"] == "변경된 텍스트"

    def test_get_changes_since(self, tracker):
        """지정 날짜 이후 변경만 반환한다."""
        tracker.record_version("관세법", "제190조", "텍스트 A")
        tracker.record_version("관세법", "제190조", "텍스트 B")

        # 모든 변경은 오늘 발생 -> 미래 날짜 이후 변경은 없음
        changes = tracker.get_changes_since("2099-01-01")
        assert len(changes) == 0

        # 과거 날짜 이후 변경은 전부 반환
        changes = tracker.get_changes_since("2020-01-01")
        assert len(changes) == 2

    def test_multiple_articles_tracked_independently(self, tracker):
        """서로 다른 조항은 독립적으로 추적된다."""
        tracker.record_version("관세법", "제190조", "텍스트 A")
        tracker.record_version("관세법", "제161조", "텍스트 B")

        # 같은 텍스트를 다시 기록 -> 둘 다 False
        assert tracker.record_version("관세법", "제190조", "텍스트 A") is False
        assert tracker.record_version("관세법", "제161조", "텍스트 B") is False

        # 하나만 변경
        assert tracker.record_version("관세법", "제190조", "변경됨") is True
        assert tracker.record_version("관세법", "제161조", "텍스트 B") is False


# ---------------------------------------------------------------------------
# FAQUpdateNotifier 테스트
# ---------------------------------------------------------------------------

class TestFAQUpdateNotifier:
    def test_analyze_impact_matching(self, notifier):
        """법령명과 조항이 legal_basis에 포함된 FAQ를 찾는다."""
        affected = notifier.analyze_impact("관세법", "제190조")
        assert len(affected) == 1
        assert affected[0]["faq_id"] == "FAQ_A"
        assert affected[0]["affected_field"] == "legal_basis"
        assert affected[0]["reason"] == "법령 변경 감지"

    def test_analyze_impact_no_match(self, notifier):
        """매칭되는 FAQ가 없으면 빈 리스트를 반환한다."""
        affected = notifier.analyze_impact("존재하지 않는 법", "제999조")
        assert affected == []

    def test_create_notifications(self, notifier):
        """알림을 생성하고 DB에 저장한다."""
        notifications = notifier.create_notifications("관세법", "제190조")
        assert len(notifications) == 1
        assert notifications[0]["faq_id"] == "FAQ_A"
        assert notifications[0]["acknowledged"] is False
        assert "id" in notifications[0]

    def test_get_pending_notifications(self, notifier):
        """미확인 알림만 반환한다."""
        notifier.create_notifications("관세법", "제190조")
        pending = notifier.get_pending_notifications()
        assert len(pending) == 1
        assert pending[0]["faq_id"] == "FAQ_A"

    def test_acknowledge_notification(self, notifier):
        """알림을 확인 처리하면 pending에서 제거된다."""
        notifications = notifier.create_notifications("관세법", "제190조")
        notification_id = notifications[0]["id"]

        result = notifier.acknowledge(notification_id)
        assert result is True

        pending = notifier.get_pending_notifications()
        assert len(pending) == 0

    def test_acknowledge_nonexistent(self, notifier):
        """존재하지 않는 알림 확인 시 False를 반환한다."""
        result = notifier.acknowledge("nonexistent-id")
        assert result is False

    def test_acknowledge_already_acknowledged(self, notifier):
        """이미 확인된 알림 재확인 시 False를 반환한다."""
        notifications = notifier.create_notifications("관세법", "제190조")
        notification_id = notifications[0]["id"]

        notifier.acknowledge(notification_id)
        result = notifier.acknowledge(notification_id)
        assert result is False

    def test_impact_analysis_with_article_in_parentheses(self, notifier):
        """조항명이 괄호와 함께 legal_basis에 있는 경우도 매칭한다."""
        affected = notifier.analyze_impact("관세법 시행령", "제101조")
        assert len(affected) == 1
        assert affected[0]["faq_id"] == "FAQ_B"


# ---------------------------------------------------------------------------
# LawUpdateScheduler 테스트
# ---------------------------------------------------------------------------

class TestLawUpdateScheduler:
    def test_schedule_check_sets_running(self, scheduler):
        """schedule_check 호출 후 스케줄러가 실행 상태가 된다."""
        scheduler.schedule_check(interval_hours=24)
        assert scheduler._running is True
        assert scheduler._timer is not None
        scheduler.stop()
        assert scheduler._running is False

    def test_check_for_updates_first_run(self, scheduler):
        """첫 실행 시 모든 법령이 새로운 변경으로 감지된다."""
        result = scheduler.check_for_updates()
        assert "checked_at" in result
        assert result["changes_detected"] > 0
        assert isinstance(result["details"], list)

    def test_check_for_updates_no_change_second_run(self, scheduler):
        """두 번째 실행 시 변경이 없으면 0을 반환한다."""
        scheduler.check_for_updates()
        result = scheduler.check_for_updates()
        assert result["changes_detected"] == 0

    def test_get_update_history(self, scheduler):
        """업데이트 이력이 누적된다."""
        assert len(scheduler.get_update_history()) == 0
        scheduler.check_for_updates()
        assert len(scheduler.get_update_history()) == 1
        scheduler.check_for_updates()
        assert len(scheduler.get_update_history()) == 2

    def test_stop_cancels_timer(self, scheduler):
        """stop 호출 시 타이머가 취소된다."""
        scheduler.schedule_check(interval_hours=1)
        timer = scheduler._timer
        scheduler.stop()
        assert scheduler._running is False
        # Timer should be cancelled (no assertion on internal state,
        # but _timer should be None)
        assert scheduler._timer is None


# ---------------------------------------------------------------------------
# API 엔드포인트 테스트
# ---------------------------------------------------------------------------

class TestLawUpdateAPI:
    def test_get_law_updates(self, client):
        """GET /api/admin/law-updates 는 변경/알림/이력을 반환한다."""
        res = client.get("/api/admin/law-updates")
        assert res.status_code == 200
        data = res.get_json()
        assert "changes" in data
        assert "pending_notifications" in data
        assert "update_history" in data

    def test_trigger_manual_check(self, client):
        """POST /api/admin/law-updates/check 는 검사 결과를 반환한다."""
        res = client.post("/api/admin/law-updates/check")
        assert res.status_code == 200
        data = res.get_json()
        assert "checked_at" in data
        assert "changes_detected" in data

    def test_acknowledge_missing_id(self, client):
        """notification_id 없이 요청하면 400을 반환한다."""
        res = client.post(
            "/api/admin/law-updates/acknowledge",
            json={},
        )
        assert res.status_code == 400
        data = res.get_json()
        assert "error" in data

    def test_acknowledge_nonexistent_id(self, client):
        """존재하지 않는 notification_id로 요청하면 404를 반환한다."""
        res = client.post(
            "/api/admin/law-updates/acknowledge",
            json={"notification_id": "nonexistent-id-12345"},
        )
        assert res.status_code == 404

    def test_get_law_updates_with_since(self, client):
        """since 파라미터로 필터링할 수 있다."""
        res = client.get("/api/admin/law-updates?since=2099-01-01")
        assert res.status_code == 200
        data = res.get_json()
        assert data["changes"] == []
