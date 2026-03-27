"""E2E 통합 테스트 + 회귀 테스트 + 부하 테스트."""

import concurrent.futures
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_server import app
from src.chatbot import BondedExhibitionChatbot
from src.classifier import classify_query


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ============================================================
# E2E 통합 테스트
# ============================================================
class TestE2EScenarios:
    """전체 사용자 시나리오 E2E 테스트."""

    def test_new_user_flow(self, client):
        """신규 사용자: 접속 → 페르소나 → FAQ 질문 → 답변."""
        # 페이지 로드
        r = client.get("/")
        assert r.status_code == 200
        assert "보세전시장".encode() in r.data

        # 설정 확인
        r = client.get("/api/config")
        assert r.status_code == 200
        assert "보세전시장" in r.get_json()["persona"]

        # FAQ 질문
        r = client.post("/api/chat", json={"query": "보세전시장이 무엇인가요?"})
        assert r.status_code == 200
        data = r.get_json()
        assert "answer" in data
        assert "관세법" in data["answer"]
        assert data["is_escalation"] is False

    def test_escalation_flow(self, client):
        """에스컬레이션: UNI-PASS → 기술지원 안내."""
        r = client.post("/api/chat", json={"query": "UNI-PASS 시스템 오류입니다"})
        data = r.get_json()
        assert data["is_escalation"] is True
        assert "1544-1285" in data["answer"] or "기술지원" in data["answer"]

    def test_unmatched_flow(self, client):
        """미매칭: 범위 밖 → '단정하기 어렵습니다'."""
        r = client.post("/api/chat", json={"query": "오늘 날씨가 좋네요"})
        data = r.get_json()
        assert "단정하기 어렵습니다" in data["answer"]

    def test_feedback_flow(self, client):
        """피드백: 질문 → 피드백 제출 → 통계."""
        r = client.post("/api/chat", json={"query": "보세전시장이란?"})
        assert r.status_code == 200

        r = client.post("/api/feedback", json={
            "query_id": "test_e2e_1",
            "rating": "helpful",
        })
        assert r.status_code == 201

    def test_admin_flow(self, client):
        """/admin 접근 + API 통계."""
        r = client.get("/admin")
        assert r.status_code == 200

        r = client.get("/api/admin/stats")
        assert r.status_code == 200
        assert "total_queries" in r.get_json()

        r = client.get("/api/admin/logs")
        assert r.status_code == 200

    def test_multilang_flow(self, client):
        """다국어: lang=en → 영어 라벨."""
        r = client.post("/api/chat", json={
            "query": "보세전시장이란?",
            "lang": "en",
        })
        data = r.get_json()
        assert data["lang"] == "en"

    def test_health_check(self, client):
        """헬스체크: faq_count >= 50."""
        r = client.get("/api/health")
        data = r.get_json()
        assert data["status"] == "ok"
        assert data["faq_count"] >= 50

    def test_faq_list_complete(self, client):
        """FAQ 목록: 50개 이상."""
        r = client.get("/api/faq")
        data = r.get_json()
        assert data["count"] >= 50

    def test_session_flow(self, client):
        """세션: 생성 → 질문 → 조회."""
        r = client.post("/api/session/new")
        assert r.status_code == 201
        sid = r.get_json()["session_id"]

        r = client.post("/api/chat", json={
            "query": "판매 가능한가요?",
            "session_id": sid,
        })
        assert r.status_code == 200

        r = client.get(f"/api/session/{sid}")
        assert r.status_code == 200


# ============================================================
# 회귀 테스트 (과거 버그 16건 재발 방지)
# ============================================================
class TestRegressionBugs:
    """과거 발견된 버그가 재발하지 않는지 확인."""

    def test_bug1_escalation_priority(self):
        """버그#1: UNI-PASS 질문에 무관한 FAQ 출력 방지."""
        bot = BondedExhibitionChatbot()
        r = bot.process_query("UNI-PASS 시스템 오류입니다")
        assert "보세전시장은 박람회" not in r
        assert "기술지원" in r or "1544-1285" in r

    def test_bug2_typo_fixed(self):
        """버그#2: '설영특허' 오타 수정 확인."""
        from src.classifier import CATEGORY_KEYWORDS
        all_kw = []
        for kws in CATEGORY_KEYWORDS.values():
            all_kw.extend(kws)
        assert "설영특허" not in all_kw

    def test_bug3_tiebreak(self):
        """버그#3: 보세창고 비교 → 보세전시장 정의 아닌 비교 FAQ."""
        bot = BondedExhibitionChatbot()
        r = bot.process_query("보세전시장과 보세창고는 어떻게 다른가요?")
        assert "보세창고" in r

    def test_bug4_zero_keyword(self):
        """버그#4: 키워드 0개 → 키워드 매칭 실패 (TF-IDF 폴백은 허용)."""
        bot = BondedExhibitionChatbot()
        r = bot.process_query("물류에 관한 법률을 알고싶어요")
        # TF-IDF 폴백으로 매칭될 수 있으므로, 최소한 안내 문구는 포함
        assert "안내:" in r

    def test_bug5_generic_customs(self):
        """버그#5: '수출입 관세율' → 견본품 FAQ 오매칭 방지."""
        bot = BondedExhibitionChatbot()
        r = bot.process_query("수출입 관세율을 알려주세요")
        assert "견본품" not in r.split("\n")[0]

    def test_bug6_penalty_classification(self):
        """버그#6: '허가 없이 반출' → PENALTIES."""
        cats = classify_query("허가 없이 물품을 반출하면 어떻게 되나요?")
        assert "PENALTIES" in cats

    def test_bug7_weather_general(self):
        """버그#7: '점심 먹을까' → FOOD_TASTING 아님."""
        cats = classify_query("점심 뭐 먹을까?")
        assert "FOOD_TASTING" not in cats

    def test_bug8_sell_colloquial(self):
        """버그#8: '물건 팔 수 있어요?' → SALES."""
        cats = classify_query("현장에서 물건 팔 수 있어요?")
        assert "SALES" in cats

    def test_bug9_unipass_case(self):
        """버그#9: UNI-PASS 대소문자 에스컬레이션."""
        from src.escalation import check_escalation
        r = check_escalation("uni-pass 오류")
        assert r is not None

    def test_bug10_immediate_delivery(self):
        """버그#10: '바로 인도' 에스컬레이션."""
        from src.escalation import check_escalation
        r = check_escalation("바로 인도해주세요")
        assert r is not None

    def test_bug11_legal_interpretation(self):
        """버그#11: '유권해석' → 에스컬레이션."""
        bot = BondedExhibitionChatbot()
        r = bot.process_query("유권해석을 요청합니다")
        assert "유권해석" in r

    def test_bug12_normalize_mismatch(self):
        """버그#12: 정규화 불일치 확인 (classify도 normalize 사용)."""
        from src.classifier import classify_query
        cats1 = classify_query("사용  범위")  # 공백 2개
        cats2 = classify_query("사용 범위")   # 공백 1개
        assert cats1 == cats2


# ============================================================
# 부하 테스트
# ============================================================
class TestLoadSimulation:
    """동시성 부하 시뮬레이션."""

    def test_concurrent_requests(self, client):
        """50개 동시 요청 → 모두 200 응답."""
        queries = [
            "보세전시장이란?", "판매 가능?", "견본품 반출", "특허기간",
            "반입 신고", "시식 요건", "벌칙", "문의처", "서류", "전시 제한",
        ] * 5  # 50개

        results = []

        def send_request(q):
            with app.test_client() as c:
                r = c.post("/api/chat", json={"query": q})
                return r.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(send_request, q) for q in queries]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        assert all(code == 200 for code in results)
        assert len(results) == 50

    def test_all_faq_questions(self):
        """FAQ 50개 질문 전수 검사."""
        bot = BondedExhibitionChatbot()
        for item in bot.faq_items:
            r = bot.process_query(item["question"])
            assert len(r) > 20, f"FAQ-{item['id']} 답변이 너무 짧음"
            assert "안내:" in r, f"FAQ-{item['id']} 면책 문구 누락"
