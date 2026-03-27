"""보세전시장 민원응대 챗봇 메인 모듈.

사용자 질문 → 분류 → FAQ 매칭 → 에스컬레이션 확인 → 답변 생성
"""

from src.classifier import classify_query
from src.escalation import check_escalation, get_escalation_contact
from src.response_builder import build_response, build_unknown_response
from src.validator import get_needed_confirmations
from src.utils import load_json, load_text, normalize_query

# 카테고리 보너스 및 매칭 임계값 설정
CATEGORY_BONUS = 2
MIN_KEYWORD_HITS = 1


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
        """질문과 카테고리에 매칭되는 FAQ 항목을 찾는다."""
        query_lower = normalize_query(query)
        best_match = None
        best_score = 0
        best_keyword_hits = 0

        for item in self.faq_items:
            score = 0
            keyword_hits = 0

            if item.get("category") == category:
                score += CATEGORY_BONUS

            keywords = item.get("keywords", [])
            for kw in keywords:
                if kw in query_lower:
                    score += 1
                    keyword_hits += 1

            if score > best_score or (score == best_score and keyword_hits > best_keyword_hits):
                best_score = score
                best_match = item
                best_keyword_hits = keyword_hits

        if best_score >= 1 and best_keyword_hits >= MIN_KEYWORD_HITS:
            return best_match
        return None

    def _extract_conclusion(self, answer: str) -> str:
        """FAQ 답변에서 의미 있는 결론을 추출한다."""
        sentences = answer.replace("·", ",").split(".")
        # 첫 문장이 너무 짧으면 (3글자 이하, 예: "네") 두 번째 문장까지 포함
        first = sentences[0].strip()
        if len(first) <= 3 and len(sentences) > 1:
            return (first + ". " + sentences[1].strip()).strip() + "."
        return first + "."

    def process_query(self, query: str) -> str:
        """사용자 질문을 처리하여 답변을 생성한다."""
        if not query or not query.strip():
            return "질문을 입력해 주세요."

        categories = classify_query(query)
        if not categories:
            categories = ["GENERAL"]
        primary_category = categories[0]

        escalation = check_escalation(query)
        faq_match = self.find_matching_faq(query, primary_category)

        # 에스컬레이션 우선: FAQ 매칭이 없거나 에스컬레이션만 트리거된 경우
        if escalation and not faq_match:
            return self._build_escalation_response(escalation)

        if faq_match:
            confirmations = get_needed_confirmations(primary_category, query)
            confirmation_texts = [c["question"] for c in confirmations]

            category_name = self._get_category_name(primary_category)

            response = build_response(
                topic=category_name,
                conclusion=self._extract_conclusion(faq_match.get("answer", "")),
                explanation=[faq_match.get("answer", "")],
                legal_basis=faq_match.get("legal_basis", []),
                confirmation_items=confirmation_texts if confirmation_texts else None,
                is_escalation=escalation is not None,
                escalation_message=escalation["message"] if escalation else "",
            )
            return response

        return build_unknown_response()

    def _build_escalation_response(self, escalation: dict) -> str:
        """에스컬레이션 전용 응답을 생성한다."""
        contact = get_escalation_contact(escalation)
        contact_name = contact.get("name", "관세청 고객지원센터")
        contact_phone = contact.get("phone", "")
        phone_info = f"({contact_phone})" if contact_phone else ""

        return (
            f"{escalation.get('message', '')}\n\n"
            f"문의처: {contact_name} {phone_info}\n\n"
            "안내:\n"
            "- 본 답변은 일반적인 안내용 설명이며, 구체적인 사실관계에 따라 달라질 수 있습니다.\n"
            "- 최종 처리는 관할 세관 또는 해당 소관기관 확인이 필요합니다."
        )

    def _get_category_name(self, category_code: str) -> str:
        """카테고리 코드를 한글 이름으로 변환한다."""
        for cat in self.config.get("categories", []):
            if cat.get("code") == category_code:
                return cat.get("name", category_code)
        return category_code
