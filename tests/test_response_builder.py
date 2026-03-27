"""답변 생성기 테스트."""

import pytest
from src.response_builder import build_response, build_unknown_response


class TestBuildResponse:
    """build_response 함수 테스트."""

    def test_basic_response_structure(self):
        result = build_response(
            topic="제도 일반",
            conclusion="보세전시장은 외국물품을 전시할 수 있는 보세구역입니다.",
            explanation=["보세전시장 제도 설명"],
            legal_basis=["관세법 제190조"],
        )
        assert "문의하신 내용은 [제도 일반]에 관한 사항입니다." in result
        assert "결론:" in result
        assert "설명:" in result
        assert "근거:" in result
        assert "안내:" in result

    def test_response_includes_disclaimer(self):
        result = build_response(
            topic="테스트",
            conclusion="결론입니다.",
            explanation=["설명입니다."],
            legal_basis=["관세법 제190조"],
        )
        assert "일반적인 안내용 설명" in result
        assert "관할 세관" in result

    def test_response_with_confirmation_items(self):
        result = build_response(
            topic="판매/직매",
            conclusion="통관 전 인도 불가.",
            explanation=["설명"],
            legal_basis=["관세법 시행령 제101조"],
            confirmation_items=["물품이 외국물품인지", "판매 목적인지"],
        )
        assert "민원인이 확인할 사항:" in result
        assert "물품이 외국물품인지" in result

    def test_response_with_escalation(self):
        result = build_response(
            topic="판매/직매",
            conclusion="결론.",
            explanation=["설명"],
            legal_basis=["관세법 시행령 제101조"],
            is_escalation=True,
            escalation_message="관할 세관에 확인이 필요합니다.",
        )
        assert "추가 안내:" in result
        assert "관할 세관에 확인이 필요합니다." in result

    def test_response_without_escalation_omits_section(self):
        result = build_response(
            topic="테스트",
            conclusion="결론.",
            explanation=["설명"],
            legal_basis=["관세법"],
            is_escalation=False,
        )
        assert "추가 안내:" not in result

    def test_no_explanation_omits_section(self):
        result = build_response(
            topic="테스트",
            conclusion="결론.",
            explanation=[],
            legal_basis=["관세법"],
        )
        assert "설명:" not in result

    def test_no_legal_basis_omits_section(self):
        result = build_response(
            topic="테스트",
            conclusion="결론.",
            explanation=["설명"],
            legal_basis=[],
        )
        assert "근거:" not in result


class TestBuildUnknownResponse:
    """build_unknown_response 함수 테스트."""

    def test_unknown_response_has_disclaimer(self):
        result = build_unknown_response()
        assert "단정하기 어렵습니다" in result
        assert "고객지원센터" in result

    def test_unknown_response_has_guidance(self):
        result = build_unknown_response()
        assert "관할 세관" in result
