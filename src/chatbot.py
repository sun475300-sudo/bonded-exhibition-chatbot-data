"""보세전시장 민원응대 챗봇 메인 모듈.

사용자 질문 → 분류 → FAQ 매칭 → 에스컬레이션 확인 → 답변 생성
"""

from src.classifier import classify_query
from src.escalation import check_escalation, get_escalation_contact
from src.response_builder import build_response, build_unknown_response
from src.session import SessionManager
from src.similarity import TFIDFMatcher
from src.smart_classifier import SmartClassifier
from src.validator import get_needed_confirmations
from src.utils import load_json, load_text, normalize_query

# 카테고리 보너스 및 매칭 임계값 설정
CATEGORY_BONUS = 2
MIN_KEYWORD_HITS = 1
KEYWORD_SCORE_THRESHOLD = 3
TFIDF_SCORE_THRESHOLD = 0.1


class BondedExhibitionChatbot:
    """보세전시장 민원응대 챗봇 클래스."""

    def __init__(self):
        self.config = load_json("config/chatbot_config.json")
        self.faq_data = load_json("data/faq.json")
        self.system_prompt = load_text("config/system_prompt.txt")
        self.faq_items = self.faq_data.get("items", [])
        self.tfidf_matcher = TFIDFMatcher(self.faq_items)
        self.session_manager = SessionManager()
        self.smart_classifier = SmartClassifier()

    def get_persona(self) -> str:
        """챗봇 페르소나 인사말을 반환한다."""
        return self.config.get("persona", "")

    def find_matching_faq(self, query: str, category: str) -> dict | None:
        """질문과 카테고리에 매칭되는 FAQ 항목을 찾는다.

        키워드 매칭을 우선 시도하고, 점수가 낮으면(< 3) TF-IDF 유사도로 폴백한다.
        """
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

        if best_score >= KEYWORD_SCORE_THRESHOLD and best_keyword_hits >= MIN_KEYWORD_HITS:
            return best_match

        # 키워드 매칭 점수가 낮으면 TF-IDF 유사도로 폴백
        tfidf_results = self.tfidf_matcher.find_best_match(
            query, category=category, top_k=1
        )
        if tfidf_results and tfidf_results[0]["score"] >= TFIDF_SCORE_THRESHOLD:
            return tfidf_results[0]["item"]

        # 카테고리 필터 없이 전체 검색 폴백
        if category:
            tfidf_results = self.tfidf_matcher.find_best_match(
                query, category=None, top_k=1
            )
            if tfidf_results and tfidf_results[0]["score"] >= TFIDF_SCORE_THRESHOLD:
                return tfidf_results[0]["item"]

        # 기존 키워드 매칭이라도 최소 기준을 충족하면 반환
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

    def process_query(self, query: str, session_id: str | None = None) -> str:
        """사용자 질문을 처리하여 답변을 생성한다.

        Args:
            query: 사용자 질문 문자열.
            session_id: 세션 ID (선택). 제공 시 멀티턴 대화를 지원한다.

        Returns:
            답변 문자열.
        """
        if not query or not query.strip():
            return "질문을 입력해 주세요."

        # 세션이 있으면 멀티턴 처리 시도
        session = None
        if session_id:
            session = self.session_manager.get_session(session_id)

        if session and session.has_pending():
            return self._process_confirmation_turn(session, query)

        return self._process_new_query(query, session)

    def _process_new_query(self, query: str, session=None) -> str:
        """새 질문을 처리한다. 세션이 있으면 대화 기록 및 확인 질문을 관리한다."""
        if session:
            categories = self.smart_classifier.classify_with_context(query, session)
        else:
            categories = classify_query(query)
        if not categories:
            categories = ["GENERAL"]
        primary_category = categories[0]

        escalation = check_escalation(query)
        faq_match = self.find_matching_faq(query, primary_category)

        # 에스컬레이션 우선: FAQ 매칭이 없거나 에스컬레이션만 트리거된 경우
        if escalation and not faq_match:
            response = self._build_escalation_response(escalation)
            if session:
                session.add_turn(query, response)
            return response

        if faq_match:
            confirmations = get_needed_confirmations(primary_category, query)
            confirmation_texts = [c["question"] for c in confirmations]

            category_name = self._get_category_name(primary_category)

            # 세션이 있으면 컨텍스트 저장 및 확인 질문 큐 설정
            if session and confirmations:
                session.context["category"] = primary_category
                session.context["category_name"] = category_name
                session.context["faq_match"] = faq_match
                session.context["escalation"] = escalation
                session.set_pending_confirmations(confirmations)

                # 첫 번째 확인 질문으로 응답
                first_q = confirmations[0]
                intro_response = (
                    f"문의하신 내용은 [{category_name}]에 관한 사항입니다.\n\n"
                    f"정확한 안내를 위해 몇 가지 확인이 필요합니다.\n\n"
                    f"{first_q['question']}\n"
                    f"({first_q['why']})"
                )
                session.add_turn(query, intro_response)
                return intro_response

            response = build_response(
                topic=category_name,
                conclusion=self._extract_conclusion(faq_match.get("answer", "")),
                explanation=[faq_match.get("answer", "")],
                legal_basis=faq_match.get("legal_basis", []),
                confirmation_items=confirmation_texts if confirmation_texts else None,
                is_escalation=escalation is not None,
                escalation_message=escalation["message"] if escalation else "",
            )
            if session:
                session.add_turn(query, response)
            return response

        response = build_unknown_response()
        if session:
            session.add_turn(query, response)
        return response

    def _process_confirmation_turn(self, session, query: str) -> str:
        """확인 질문에 대한 사용자 응답을 처리한다."""
        next_confirmation = session.process_confirmation_response(query)

        if next_confirmation:
            # 다음 확인 질문
            response = (
                f"확인했습니다. 다음 질문입니다.\n\n"
                f"{next_confirmation['question']}\n"
                f"({next_confirmation['why']})"
            )
            session.add_turn(query, response)
            return response

        # 모든 확인 완료 - 맞춤 답변 생성
        return self._build_confirmed_response(session, query)

    def _build_confirmed_response(self, session, last_query: str) -> str:
        """모든 확인이 완료된 후 맞춤 답변을 생성한다."""
        ctx = session.context
        faq_match = ctx.get("faq_match", {})
        category_name = ctx.get("category_name", "")
        escalation = ctx.get("escalation")

        confirmed = session.confirmed
        tailored_notes = []
        for question, is_positive in confirmed.items():
            if is_positive:
                tailored_notes.append(f"- {question} -> 예")
            else:
                tailored_notes.append(f"- {question} -> 아니요")

        explanation_parts = [faq_match.get("answer", "")]

        # 확인 결과에 따른 추가 안내
        additional = self._get_tailored_advice(confirmed)
        if additional:
            explanation_parts.extend(additional)

        response = build_response(
            topic=category_name,
            conclusion=self._extract_conclusion(faq_match.get("answer", "")),
            explanation=explanation_parts,
            legal_basis=faq_match.get("legal_basis", []),
            confirmation_items=None,
            is_escalation=escalation is not None,
            escalation_message=escalation["message"] if escalation else "",
        )

        # 확인 결과 요약 추가
        summary = "\n\n확인 결과:\n" + "\n".join(tailored_notes)
        full_response = response + summary

        session.add_turn(last_query, full_response)
        # 확인 완료 후 컨텍스트 초기화
        session.context = {}
        session.confirmed = {}
        return full_response

    def _get_tailored_advice(self, confirmed: dict) -> list[str]:
        """확인 결과에 따른 추가 안내를 생성한다."""
        advice = []

        for question, is_positive in confirmed.items():
            if "외국물품" in question and not is_positive:
                advice.append(
                    "내국물품의 경우 보세전시장 제도가 아닌 일반 전시 절차가 적용됩니다."
                )
            if "특허" in question and not is_positive:
                advice.append(
                    "보세전시장 특허가 없는 장소는 임시개청 또는 장외장치허가 등 "
                    "별도 절차가 필요합니다."
                )
            if "재반출" in question and is_positive:
                advice.append(
                    "재반출 시에는 반출신고를 통해 관세 면제 혜택을 받을 수 있습니다."
                )
            if "타법 요건" in question and is_positive:
                advice.append(
                    "식약처, 검역 등 관계기관의 요건확인이 별도로 필요합니다."
                )
            if "사전 협의" in question and is_positive:
                advice.append(
                    "사전 협의를 이미 진행하셨다면 협의 내용에 따라 진행하시면 됩니다."
                )

        return advice

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
