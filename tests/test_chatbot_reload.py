"""BondedExhibitionChatbot.reload_data 회귀 테스트.

국가법령정보센터 동기화 후 호출되는 hot-reload 경로가
faq.json 변경을 실행 중 봇에 즉시 반영하는지 검증한다.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from src.chatbot import BondedExhibitionChatbot


FAQ_PATH = os.path.join(ROOT_DIR, "data", "faq.json")


@pytest.fixture
def backup_faq():
    """faq.json을 임시로 백업·복원하는 fixture."""
    backup = FAQ_PATH + ".test_backup"
    shutil.copy(FAQ_PATH, backup)
    try:
        yield
    finally:
        shutil.copy(backup, FAQ_PATH)
        os.remove(backup)


class TestChatbotReload:
    def test_reload_returns_counts(self, backup_faq):
        bot = BondedExhibitionChatbot()
        result = bot.reload_data()

        assert result["faq_items"] > 0
        assert result["tfidf_rebuilt"] is True
        # legal_refs 파일이 있으면 갯수가 기록되어야 함
        assert "legal_refs" in result

    def test_reload_picks_up_new_faq_item(self, backup_faq):
        """faq.json에 새 항목을 추가한 뒤 reload_data를 호출하면
        새 항목으로 즉시 매칭된다."""
        bot = BondedExhibitionChatbot()
        original_count = len(bot.faq_items)

        # 새 FAQ 항목 추가
        with open(FAQ_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        new_item = {
            "id": "TEST_RELOAD_Z",
            "category": "GENERAL",
            "question": "핫리로드 테스트 질문",
            "answer": "핫리로드 테스트 전용 답변입니다. 이 문장이 응답에 "
                      "포함되면 reload_data가 정상 동작한 것입니다.",
            "legal_basis": [],
            "keywords": ["핫리로드", "핫리로드 테스트", "리로드 시그니처"],
        }
        data["items"].append(new_item)
        with open(FAQ_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        # reload 전: 새 항목을 모름
        before_resp = bot.process_query("핫리로드 테스트")
        assert "핫리로드 테스트 전용 답변입니다" not in before_resp

        # reload 후: 새 항목으로 응답
        result = bot.reload_data()
        assert result["faq_items"] == original_count + 1

        after_resp = bot.process_query("핫리로드 테스트")
        assert "핫리로드 테스트 전용 답변입니다" in after_resp

    def test_reload_clears_classifier_cache(self, backup_faq):
        bot = BondedExhibitionChatbot()
        bot._cached_classify("보세전시장이란?")
        assert len(bot._classifier_cache) > 0

        bot.reload_data()
        assert len(bot._classifier_cache) == 0
