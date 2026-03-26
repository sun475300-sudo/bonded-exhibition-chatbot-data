"""보세전시장 민원응대 챗봇 메인 모듈.

사용자 질문 → 분류 → FAQ 매칭 → 에스컬레이션 확인 → 답변 생성
"""

from src.classifier import classify_query, get_primary_category
from src.escalation import check_escalation, get_escalation_contact
from src.response_builder import build_response, build_unknown_response
from src.validator import get_needed_confirmations, format_confirmation_section
from src.utils import load_json, load_text, normalize_query


class BondedExhibitionChatbot:
    """보세전시장 민원응대 챗봇 클래스."""

    def __init__(self):
        self.config = load_json("config/chatbot_config.json")
        self.faq_data = load_json("data/faq.json")
        self.system_prompt = load_text("config/system_prompt.txt")
        self.faq_items = self.faq_data.get("items", [])

    def get_persona(self) -> str:
        """챗봇 페르소나 인사말을 반환한다."""
        return self.config.get("persona", "")

    def find_matching_faq(self, query: str, category: str) -> dict | None:
        """질문과 카테고리에 매칭되는 FAQ 항목을 찾는다.

        Args:
            query: 사용자 질문
            category: 분류된 카테고리

        Returns:
            매칭된 FAQ dict 또는 None
        """
        query_lower = normalize_query(query)
        best_match = None
        best_score = 0
        best_keyword_hits = 0

        for item in self.faq_items:
            score = 0
            keyword_hits = 0

            if item.get("category") == category:
                score += 2

            keywords = item.get("keywords", [])
            for kw in keywords:
                if kw in query_lower:
                    score += 1
                    keyword_hits += 1

            # 동점일 때 키워드 매칭 수가 더 많은 항목을 우선 선택
            if score > best_score or (score == best_score and keyword_hits > best_keyword_hits):
                best_score = score
                best_match = item
                best_keyword_hits = keyword_hits

        # 키워드 매칭이 최소 1개 이상이어야 유효한 FAQ 매칭으로 간주
        if best_score >= 1 and best_keyword_hits >= 1:
            return best_match
        return None

    def process_query(self, query: str) -> str:
        """사용자 질문을 처리하여 답변을 생성한다.

        Args:
            query: 사용자 질문 문자열

        Returns:
            포맷된 답변 문자열
        """
        if not query or not query.strip():
            return "질문을 입력해 주세요."

        categories = classify_query(query)
        primary_category = categories[0]

        escalation = check_escalation(query)

        faq_match = self.find_matching_faq(query, primary_category)

        # 에스컬레이션 전용 질문 판단: FAQ 매칭이 있더라도 에스컬레이션이
        # 트리거되고, FAQ 매칭이 카테고리 보너스만으로 잡힌 약한 매칭이면
        # 에스컬레이션을 우선한다.
        escalation_only = False
        if escalation and faq_match:
            faq_keywords = faq_match.get("keywords", [])
            query_lower = normalize_query(query)
            keyword_hits = sum(1 for kw in faq_keywords if kw in query_lower)
            if keyword_hits == 0:
                escalation_only = True

        if escalation and (not faq_match or escalation_only):
            contact = get_escalation_contact(escalation)
            contact_name = contact.get("name", "관세청 고객지원센터")
            contact_phone = contact.get("phone", "")
            phone_info = f"({contact_phone})" if contact_phone else ""

            return (
                f"{escalation['message']}\n\n"
                f"문의처: {contact_name} {phone_info}\n\n"
                "안내:\n"
                "- 본 답변은 일반적인 안내용 설명이며, 구체적인 사실관계에 따라 달라질 수 있습니다.\n"
                "- 최종 처리는 관할 세관 또는 해당 소관기관 확인이 필요합니다."
            )

        if faq_match:
            confirmations = get_needed_confirmations(primary_category, query)
            confirmation_texts = [c["question"] for c in confirmations]

            category_name = self._get_category_name(primary_category)

            response = build_response(
                topic=category_name,
                conclusion=faq_match["answer"].split(".")[0] + ".",
                explanation=[faq_match["answer"]],
                legal_basis=faq_match.get("legal_basis", []),
                confirmation_items=confirmation_texts if confirmation_texts else None,
                is_escalation=escalation is not None,
                escalation_message=escalation["message"] if escalation else "",
            )
            return response

        return build_unknown_response(query)

    def _get_category_name(self, category_code: str) -> str:
        """카테고리 코드를 한글 이름으로 변환한다."""
        for cat in self.config.get("categories", []):
            if cat.get("code") == category_code:
                return cat.get("name", category_code)
        return category_code
