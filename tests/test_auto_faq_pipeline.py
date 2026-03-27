"""AutoFAQPipeline 테스트."""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.auto_faq_pipeline import AutoFAQPipeline
from src.faq_recommender import FAQRecommender
from src.logger_db import ChatLogger


@pytest.fixture
def temp_env():
    """임시 디렉토리에 DB와 FAQ 파일을 생성한다."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "logs", "chat_logs.db")
        faq_path = os.path.join(tmpdir, "data", "faq.json")
        os.makedirs(os.path.join(tmpdir, "data"), exist_ok=True)

        # 초기 faq.json 생성
        initial_faq = {
            "faq_version": "3.0.0",
            "last_updated": "2026-03-27",
            "items": [
                {
                    "id": "A",
                    "category": "GENERAL",
                    "question": "보세전시장이 무엇인가요?",
                    "answer": "보세전시장은 보세구역입니다.",
                    "keywords": ["보세전시장"],
                }
            ],
        }
        with open(faq_path, "w", encoding="utf-8") as f:
            json.dump(initial_faq, f, ensure_ascii=False, indent=2)

        logger = ChatLogger(db_path=db_path)
        yield tmpdir, logger, faq_path
        logger.close()


@pytest.fixture
def pipeline_empty(temp_env):
    """미매칭 질문이 없는 파이프라인."""
    tmpdir, logger, faq_path = temp_env
    recommender = FAQRecommender(logger)
    pipeline = AutoFAQPipeline(recommender, faq_path=faq_path)
    yield pipeline
    pipeline.close()


@pytest.fixture
def pipeline_with_data(temp_env):
    """미매칭 질문이 있는 파이프라인."""
    tmpdir, logger, faq_path = temp_env

    # 빈도 3 이상의 미매칭 질문 삽입
    for _ in range(5):
        logger.log_query("보세전시장 차량 반입 가능한가요?",
                         category="IMPORT_EXPORT", faq_id=None)
    for _ in range(4):
        logger.log_query("전시 기간 연장 방법은?",
                         category="EXHIBITION", faq_id=None)
    for _ in range(3):
        logger.log_query("보세전시장 임대료는 얼마인가요?",
                         category="GENERAL", faq_id=None)
    # 빈도 2 -> min_frequency=3 미만이므로 후보가 되지 않아야 함
    for _ in range(2):
        logger.log_query("보세전시장 위치가 어디인가요?",
                         category="GENERAL", faq_id=None)

    recommender = FAQRecommender(logger)
    pipeline = AutoFAQPipeline(recommender, faq_path=faq_path)
    yield pipeline, faq_path
    pipeline.close()


class TestAutoFAQPipelineInit:
    """초기화 테스트."""

    def test_init(self, pipeline_empty):
        assert pipeline_empty.faq_recommender is not None
        assert pipeline_empty.faq_path is not None

    def test_db_created(self, pipeline_empty):
        assert os.path.exists(pipeline_empty.db_path)


class TestGetPendingCandidates:
    """get_pending_candidates 테스트."""

    def test_empty_db(self, pipeline_empty):
        result = pipeline_empty.get_pending_candidates()
        assert result == []

    def test_with_data(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        result = pipeline.get_pending_candidates(min_frequency=3)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_candidate_structure(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        result = pipeline.get_pending_candidates(min_frequency=3)
        if result:
            c = result[0]
            assert "id" in c
            assert "suggested_question" in c
            assert "frequency" in c
            assert "status" in c
            assert c["status"] == "pending"

    def test_min_frequency_filter(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        result = pipeline.get_pending_candidates(min_frequency=3)
        for c in result:
            assert c["frequency"] >= 3

    def test_sorted_by_frequency_desc(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        result = pipeline.get_pending_candidates(min_frequency=3)
        if len(result) >= 2:
            freqs = [c["frequency"] for c in result]
            assert freqs == sorted(freqs, reverse=True)

    def test_high_min_frequency_returns_empty(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        result = pipeline.get_pending_candidates(min_frequency=100)
        assert result == []


class TestApproveCandidate:
    """approve_candidate 테스트."""

    def test_approve(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        candidates = pipeline.get_pending_candidates(min_frequency=3)
        assert len(candidates) >= 1

        cid = candidates[0]["id"]
        result = pipeline.approve_candidate(cid)
        assert result["status"] == "approved"
        assert "faq_draft" in result

    def test_approve_adds_to_faq_json(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        candidates = pipeline.get_pending_candidates(min_frequency=3)
        cid = candidates[0]["id"]
        pipeline.approve_candidate(cid)

        with open(faq_path, "r", encoding="utf-8") as f:
            faq_data = json.load(f)

        # 초기 1개 + 추가 1개
        assert len(faq_data["items"]) == 2

    def test_approved_not_in_pending(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        candidates = pipeline.get_pending_candidates(min_frequency=3)
        cid = candidates[0]["id"]
        pipeline.approve_candidate(cid)

        pending = pipeline.get_pending_candidates(min_frequency=3)
        pending_ids = [c["id"] for c in pending]
        assert cid not in pending_ids

    def test_approve_nonexistent_raises(self, pipeline_empty):
        with pytest.raises(ValueError):
            pipeline_empty.approve_candidate(9999)

    def test_approve_already_approved_raises(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        candidates = pipeline.get_pending_candidates(min_frequency=3)
        cid = candidates[0]["id"]
        pipeline.approve_candidate(cid)
        with pytest.raises(ValueError):
            pipeline.approve_candidate(cid)


class TestRejectCandidate:
    """reject_candidate 테스트."""

    def test_reject(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        candidates = pipeline.get_pending_candidates(min_frequency=3)
        assert len(candidates) >= 1

        cid = candidates[0]["id"]
        result = pipeline.reject_candidate(cid)
        assert result["status"] == "rejected"

    def test_rejected_not_in_pending(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        candidates = pipeline.get_pending_candidates(min_frequency=3)
        cid = candidates[0]["id"]
        pipeline.reject_candidate(cid)

        pending = pipeline.get_pending_candidates(min_frequency=3)
        pending_ids = [c["id"] for c in pending]
        assert cid not in pending_ids

    def test_reject_does_not_modify_faq(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data

        with open(faq_path, "r", encoding="utf-8") as f:
            before = json.load(f)

        candidates = pipeline.get_pending_candidates(min_frequency=3)
        cid = candidates[0]["id"]
        pipeline.reject_candidate(cid)

        with open(faq_path, "r", encoding="utf-8") as f:
            after = json.load(f)

        assert len(before["items"]) == len(after["items"])

    def test_reject_nonexistent_raises(self, pipeline_empty):
        with pytest.raises(ValueError):
            pipeline_empty.reject_candidate(9999)

    def test_reject_already_rejected_raises(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        candidates = pipeline.get_pending_candidates(min_frequency=3)
        cid = candidates[0]["id"]
        pipeline.reject_candidate(cid)
        with pytest.raises(ValueError):
            pipeline.reject_candidate(cid)


class TestGetAllCandidates:
    """get_all_candidates 테스트."""

    def test_empty(self, pipeline_empty):
        result = pipeline_empty.get_all_candidates()
        assert result == []

    def test_includes_all_statuses(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        candidates = pipeline.get_pending_candidates(min_frequency=3)
        if len(candidates) >= 2:
            pipeline.approve_candidate(candidates[0]["id"])
            pipeline.reject_candidate(candidates[1]["id"])

            all_candidates = pipeline.get_all_candidates()
            statuses = {c["status"] for c in all_candidates}
            assert "approved" in statuses
            assert "rejected" in statuses


class TestFAQJsonIntegration:
    """faq.json 통합 테스트."""

    def test_added_faq_has_auto_id(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        candidates = pipeline.get_pending_candidates(min_frequency=3)
        pipeline.approve_candidate(candidates[0]["id"])

        with open(faq_path, "r", encoding="utf-8") as f:
            faq_data = json.load(f)

        new_item = faq_data["items"][-1]
        assert new_item["id"].startswith("AUTO_")

    def test_added_faq_has_answer_placeholder(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        candidates = pipeline.get_pending_candidates(min_frequency=3)
        pipeline.approve_candidate(candidates[0]["id"])

        with open(faq_path, "r", encoding="utf-8") as f:
            faq_data = json.load(f)

        new_item = faq_data["items"][-1]
        assert "작성해 주세요" in new_item["answer"]

    def test_faq_json_not_corrupted_after_approve(self, pipeline_with_data):
        pipeline, faq_path = pipeline_with_data
        candidates = pipeline.get_pending_candidates(min_frequency=3)
        pipeline.approve_candidate(candidates[0]["id"])

        # JSON이 유효한지 확인
        with open(faq_path, "r", encoding="utf-8") as f:
            faq_data = json.load(f)
        assert "items" in faq_data
        assert "faq_version" in faq_data

    def test_no_source_queries_in_faq(self, pipeline_with_data):
        """faq.json에 source_queries 필드가 포함되지 않아야 한다."""
        pipeline, faq_path = pipeline_with_data
        candidates = pipeline.get_pending_candidates(min_frequency=3)
        pipeline.approve_candidate(candidates[0]["id"])

        with open(faq_path, "r", encoding="utf-8") as f:
            faq_data = json.load(f)

        new_item = faq_data["items"][-1]
        assert "source_queries" not in new_item
