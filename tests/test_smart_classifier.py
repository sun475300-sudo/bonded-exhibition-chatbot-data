"""SmartClassifier 테스트."""

import pytest
from src.smart_classifier import SmartClassifier, FOLLOW_UP_PATTERNS
from src.classifier import classify_query
from src.session import Session


@pytest.fixture
def classifier():
    return SmartClassifier()


@pytest.fixture
def session_with_sales_history():
    """SALES 카테고리 히스토리가 있는 세션."""
    session = Session(session_id="test-1")
    session.add_turn(
        "현장에서 판매 가능한가요?",
        "판매 관련 답변입니다."
    )
    return session


@pytest.fixture
def session_with_import_history():
    """IMPORT_EXPORT 카테고리 히스토리가 있는 세션."""
    session = Session(session_id="test-2")
    session.add_turn(
        "물품을 반입하려면 어떻게 하나요?",
        "반입 관련 답변입니다."
    )
    return session


@pytest.fixture
def empty_session():
    """히스토리가 없는 빈 세션."""
    return Session(session_id="test-empty")


class TestSmartClassifierBasic:
    """기본 동작 테스트."""

    def test_no_session_returns_base_classification(self, classifier):
        """세션 없이 호출하면 기존 classify_query와 동일하게 동작한다."""
        result = classifier.classify_with_context("보세전시장이 무엇인가요?")
        expected = classify_query("보세전시장이 무엇인가요?")
        assert result == expected

    def test_none_session(self, classifier):
        """session=None이면 기본 분류를 반환한다."""
        result = classifier.classify_with_context("특허기간은?", session=None)
        expected = classify_query("특허기간은?")
        assert result == expected

    def test_empty_session_returns_base(self, classifier, empty_session):
        """히스토리가 없는 세션은 기본 분류를 반환한다."""
        result = classifier.classify_with_context("보세전시장이란?", session=empty_session)
        expected = classify_query("보세전시장이란?")
        assert result == expected

    def test_returns_list(self, classifier):
        """항상 리스트를 반환한다."""
        result = classifier.classify_with_context("안녕하세요")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_empty_query(self, classifier):
        """빈 질문은 GENERAL을 반환한다."""
        result = classifier.classify_with_context("")
        assert result == ["GENERAL"]


class TestFollowUpDetection:
    """후속 질문 패턴 감지 테스트."""

    def test_detect_follow_up_그러면(self, classifier):
        assert classifier._detect_follow_up("그러면 어떻게 해야 하나요?") is True

    def test_detect_follow_up_그건(self, classifier):
        assert classifier._detect_follow_up("그건 어디서 하나요?") is True

    def test_detect_follow_up_또(self, classifier):
        assert classifier._detect_follow_up("또 다른 방법은?") is True

    def test_detect_follow_up_추가로(self, classifier):
        assert classifier._detect_follow_up("추가로 필요한 서류는?") is True

    def test_detect_follow_up_그리고(self, classifier):
        assert classifier._detect_follow_up("그리고 비용은 얼마인가요?") is True

    def test_detect_follow_up_아까(self, classifier):
        assert classifier._detect_follow_up("아까 말씀하신 것 중에") is True

    def test_no_follow_up_plain_question(self, classifier):
        assert classifier._detect_follow_up("보세전시장이 무엇인가요?") is False

    def test_no_follow_up_keyword_question(self, classifier):
        assert classifier._detect_follow_up("특허 신청은 어떻게 하나요?") is False


class TestContextAdjustment:
    """세션 컨텍스트 보정 테스트."""

    def test_follow_up_inherits_category(self, classifier, session_with_sales_history):
        """후속 질문 패턴 + GENERAL 분류 -> 이전 카테고리 상속."""
        result = classifier.classify_with_context(
            "그러면 기한은 며칠인가요?",
            session=session_with_sales_history
        )
        assert result[0] == "SALES"

    def test_ambiguous_query_gets_context(self, classifier, session_with_import_history):
        """모호한 질문(GENERAL)에 이전 카테고리가 힌트로 제공된다."""
        result = classifier.classify_with_context(
            "기간은 얼마나 걸리나요?",
            session=session_with_import_history
        )
        # GENERAL이 아닌 이전 카테고리가 포함되어야 함
        assert "IMPORT_EXPORT" in result

    def test_specific_query_keeps_own_category(self, classifier, session_with_sales_history):
        """명확한 질문은 자체 분류를 유지한다."""
        result = classifier.classify_with_context(
            "위반하면 벌칙이 있나요?",
            session=session_with_sales_history
        )
        assert result[0] == "PENALTIES"

    def test_follow_up_with_different_clear_category(self, classifier, session_with_sales_history):
        """후속 패턴이 있어도 명확한 다른 카테고리면 그것을 우선한다."""
        result = classifier.classify_with_context(
            "그러면 벌칙은 어떻게 되나요?",
            session=session_with_sales_history
        )
        assert "PENALTIES" in result

    def test_multi_turn_context(self, classifier):
        """여러 턴의 히스토리가 있을 때 최근 카테고리를 참고한다."""
        session = Session(session_id="multi")
        session.add_turn("보세전시장이란?", "일반 답변")
        session.add_turn("물품 반입 절차는?", "반입 답변")

        result = classifier.classify_with_context(
            "그러면 기한은?", session=session
        )
        # 최근 질문이 IMPORT_EXPORT이므로 그것을 우선
        assert result[0] == "IMPORT_EXPORT"


class TestBackwardCompatibility:
    """기존 classify_query와의 하위 호환성 테스트."""

    def test_general_question_compat(self, classifier):
        base = classify_query("보세전시장이 무엇인가요?")
        smart = classifier.classify_with_context("보세전시장이 무엇인가요?")
        assert base == smart

    def test_penalty_question_compat(self, classifier):
        base = classify_query("위반하면 벌칙이 있나요?")
        smart = classifier.classify_with_context("위반하면 벌칙이 있나요?")
        assert base == smart

    def test_food_question_compat(self, classifier):
        base = classify_query("시식용 식품을 들여오는 경우 요건확인은?")
        smart = classifier.classify_with_context("시식용 식품을 들여오는 경우 요건확인은?")
        assert base == smart

    def test_sales_question_compat(self, classifier):
        base = classify_query("현장에서 판매 가능한가요?")
        smart = classifier.classify_with_context("현장에서 판매 가능한가요?")
        assert base == smart

    def test_documents_question_compat(self, classifier):
        base = classify_query("신고서 양식은 어디서 받나요?")
        smart = classifier.classify_with_context("신고서 양식은 어디서 받나요?")
        assert base == smart
