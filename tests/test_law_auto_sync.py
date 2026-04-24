"""LawAutoSyncOrchestrator 테스트.

국가법령정보센터 API → legal_references.json → FAQ 메타데이터 전파 →
챗봇 hot-reload 파이프라인 전체를 검증한다.

법령 API 자체는 네트워크 의존이므로 LawSyncManager의 check_all을
모의(mocking)하여 격리된 환경에서 동작을 확인한다.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from src.law_api_sync import LawSyncManager
from src.law_auto_sync import LawAutoSyncOrchestrator
from src.law_updater import FAQUpdateNotifier, LawVersionTracker


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def tmp_faq(tmp_path):
    data = {
        "faq_version": "test",
        "last_updated": "2026-01-01",
        "items": [
            {
                "id": "A",
                "category": "GENERAL",
                "question": "보세전시장이란?",
                "answer": "보세전시장은 박람회, 전람회 등을 위한 보세구역입니다.",
                "legal_basis": ["관세법 제190조"],
                "keywords": ["보세전시장", "정의"],
            },
            {
                "id": "B",
                "category": "SAMPLE",
                "question": "견본품 반출?",
                "answer": "견본품 반출에는 세관장 허가가 필요합니다.",
                "legal_basis": ["관세법 제161조(견본품 반출)"],
                "keywords": ["견본품", "반출"],
            },
            {
                "id": "C",
                "category": "GENERAL",
                "question": "다른 FAQ",
                "answer": "관련 없음",
                "legal_basis": ["관세법 제226조"],
                "keywords": ["기타"],
            },
        ],
    }
    p = tmp_path / "faq.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


@pytest.fixture
def tmp_legal_refs(tmp_path):
    data = {
        "references": [
            {
                "id": "customs_act_190",
                "law_name": "관세법",
                "article": "제190조",
                "title": "보세전시장",
                "summary": "원본 요약",
            },
        ]
    }
    p = tmp_path / "legal_references.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


@pytest.fixture
def orch(tmp_path, tmp_faq, tmp_legal_refs):
    """법령 API 호출을 모의한 Orchestrator."""
    sync_mgr = LawSyncManager(db_path=str(tmp_path / "sync.db"))
    notifier = FAQUpdateNotifier(
        faq_path=tmp_faq, db_path=str(tmp_path / "notifier.db")
    )
    tracker = LawVersionTracker(db_path=str(tmp_path / "tracker.db"))

    # 네트워크 호출 회피: check_all이 고정된 결과를 반환하도록 패치
    def fake_check_all():
        return {
            "checked_at": "2026-04-24T00:00:00",
            "total_checked": 3,
            "changes_detected": 1,
            "errors": 0,
            "details": [
                {
                    "law_name": "관세법",
                    "article": "제190조",
                    "status": "changed",
                    "content_preview": "(개정) 보세전시장은 … 새 조문 내용",
                },
                {
                    "law_name": "관세법",
                    "article": "제161조",
                    "status": "unchanged",
                    "content_preview": "견본품 반출 …",
                },
                {
                    "law_name": "관세법",
                    "article": "제226조",
                    "status": "unchanged",
                    "content_preview": "…",
                },
            ],
        }

    def fake_update_refs():
        # 실제 legal_references.json 파일을 갱신해 오케스트레이터의
        # legal_refs_updated 카운트를 현실성 있게 만든다.
        with open(tmp_legal_refs, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["references"][0]["summary"] = "(개정) 보세전시장 새 요약"
        with open(tmp_legal_refs, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return {"updated": 1, "total": 1}

    sync_mgr.check_all = fake_check_all
    sync_mgr.update_legal_references = fake_update_refs

    return LawAutoSyncOrchestrator(
        sync_manager=sync_mgr,
        notifier=notifier,
        version_tracker=tracker,
        faq_path=tmp_faq,
        legal_ref_path=tmp_legal_refs,
    )


# --------------------------------------------------------------------------
# run_full_sync
# --------------------------------------------------------------------------

class TestRunFullSync:
    def test_happy_path_updates_faq_metadata(self, orch, tmp_faq):
        result = orch.run_full_sync()

        assert result["api_check"]["checked"] == 3
        assert result["api_check"]["changes"] == 1
        assert result["legal_refs_updated"] == 1
        # 관세법 제190조를 legal_basis로 가진 FAQ A 한 개가 터치되어야 함
        assert result["faq_items_touched"] == 1
        assert result["notifications_created"] == 1
        assert result["errors"] == []

    def test_faq_file_is_updated_with_law_summary(self, orch, tmp_faq):
        orch.run_full_sync()
        with open(tmp_faq, "r", encoding="utf-8") as f:
            data = json.load(f)

        item_a = next(i for i in data["items"] if i["id"] == "A")
        assert "last_law_synced" in item_a
        assert "law_summary" in item_a
        assert any("제190조" in k for k in item_a["law_summary"].keys())

        # 영향 없는 FAQ는 건드리지 않음
        item_b = next(i for i in data["items"] if i["id"] == "B")
        assert "last_law_synced" not in item_b

    def test_no_changes_no_faq_write(self, orch, tmp_faq, monkeypatch):
        # 변경이 없는 시나리오
        def no_changes():
            return {
                "checked_at": "2026-04-24T00:00:00",
                "total_checked": 1,
                "changes_detected": 0,
                "errors": 0,
                "details": [
                    {
                        "law_name": "관세법",
                        "article": "제190조",
                        "status": "unchanged",
                        "content_preview": "…",
                    }
                ],
            }

        orch.sync_manager.check_all = no_changes
        orch.sync_manager.update_legal_references = lambda: {"updated": 0, "total": 1}

        result = orch.run_full_sync()
        assert result["faq_items_touched"] == 0

    def test_api_error_is_captured(self, orch):
        def broken():
            raise RuntimeError("network down")

        orch.sync_manager.check_all = broken
        result = orch.run_full_sync()
        assert any("api_check" in e for e in result["errors"])


# --------------------------------------------------------------------------
# Chatbot hot-reload
# --------------------------------------------------------------------------

class TestChatbotReload:
    def test_reload_calls_bot_reload_data(self, orch):
        calls = []

        class FakeBot:
            def reload_data(self):
                calls.append("reload")

        orch._reload_chatbot(FakeBot())
        assert calls == ["reload"]

    def test_reload_returns_true_on_success(self, orch):
        class FakeBot:
            def reload_data(self):
                return {"faq_items": 10}

        assert orch._reload_chatbot(FakeBot()) is True

    def test_run_full_sync_triggers_reload(self, orch):
        calls = []

        class FakeBot:
            def reload_data(self):
                calls.append("r")

        result = orch.run_full_sync(chatbot=FakeBot())
        assert result["bot_reloaded"] is True
        assert calls == ["r"]


# --------------------------------------------------------------------------
# Scheduler
# --------------------------------------------------------------------------

class TestScheduler:
    def test_start_and_stop_scheduler(self, orch):
        orch.start_periodic_sync(interval_hours=1)
        assert orch._running is True
        assert orch._timer is not None
        orch.stop()
        assert orch._running is False
        assert orch._timer is None
