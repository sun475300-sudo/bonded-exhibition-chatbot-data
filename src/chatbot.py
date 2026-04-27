"""보세전시장 민원응대 챗봇 메인 모듈.

사용자 질문 → 분류 → FAQ 매칭 → 에스컬레이션 확인 → 답변 생성

새 파이프라인:
1. 오타 교정 + 동의어 확장 (기존)
2. 의도 분류 (새 30-intent 시스템)
3. 엔티티 추출 (새)
4. 카테고리 매핑 (의도 → 기존 10-category)
5. FAQ 매칭 (기존 파이프라인)
6. 정책 평가 (새 - 위험도 확인)
7. 답변 필터링 (새 - 면책조항 추가)
8. 에스컬레이션 확인 (기존 + 강화)
9. 응답 구성 (기존 + 새 메타데이터)
"""

import logging
from collections import OrderedDict
from src.classifier import classify_query, classify_intent, get_intent_classifier
from src.clarification import ClarificationEngine
from src.escalation import check_escalation, get_escalation_contact
from src.entity_extractor import extract_entities
from src.policy_engine import (
    get_policy_engine,
    evaluate_policy,
    apply_answer_filter,
    should_escalate as should_escalate_policy,
)
from src.related_faq import RelatedFAQFinder
from src.response_builder import build_response, build_unknown_response
from src.session import SessionManager
from src.similarity import TFIDFMatcher
from src.smart_classifier import SmartClassifier
from src.spell_corrector import correct_query as spell_correct
from src.synonym_resolver import expand_query
from src.validator import get_needed_confirmations
from src.utils import load_json, load_text, normalize_query
from src.vector_search import VectorSearchEngine
from src.llm_fallback import generate_llm_response_with_disclaimer, is_llm_available
from src.pii_redactor import PIIRedactor
from src.prompt_defender import PromptDefender

logger = logging.getLogger(__name__)

# 카테고리 보너스 및 매칭 임계값 설정
CATEGORY_BONUS = 2
MIN_KEYWORD_HITS = 1
KEYWORD_SCORE_THRESHOLD = 3
TFIDF_SCORE_THRESHOLD = 0.1
CLASSIFIER_CACHE_MAX_SIZE = 100
MIN_CONCLUSION_LENGTH = 3


class BondedExhibitionChatbot:
    """보세전시장 민원응대 챗봇 클래스."""

    def __init__(self):
        self.config = load_json("config/chatbot_config.json")
        self.faq_data = load_json("data/faq.json")
        try:
            self.legal_refs = load_json("data/legal_references.json").get("references", [])
        except Exception as e:
            logger.warning(f"Failed to load legal_references.json: {e}")
            self.legal_refs = []
        self.system_prompt = load_text("config/system_prompt.txt")
        self.faq_items = self._normalize_faq_items(self.faq_data.get("items", []))
        self.tfidf_matcher = TFIDFMatcher(self.faq_items)
        self.session_manager = SessionManager()
        self.smart_classifier = SmartClassifier()
        self.clarification_engine = ClarificationEngine()
        self.related_faq_finder = RelatedFAQFinder(self.faq_items)
        self._classifier_cache: OrderedDict[str, list[str]] = OrderedDict()

        # 새 파이프라인 컴포넌트 초기화
        self.intent_classifier = get_intent_classifier()
        self.policy_engine = get_policy_engine()

        # 벡터 검색 및 LLM 폴백 초기화
        self.vector_search_enabled = False
        try:
            self.vector_search = VectorSearchEngine(self.faq_items)
            self.vector_search_enabled = True
        except ImportError:
            # sentence-transformers가 설치되지 않은 경우 비활성화
            logger.warning("VectorSearchEngine disabled: sentence-transformers not installed")
            self.vector_search = None
        except Exception as e:
            logger.error(f"Failed to initialize VectorSearchEngine: {e}", exc_info=True)
            self.vector_search = None

        # LLM 가용성 확인
        self.llm_enabled = False
        try:
            self.llm_enabled = is_llm_available()
        except Exception as e:
            logger.error(f"Failed to check LLM availability: {e}", exc_info=True)
            self.llm_enabled = False

        # 보안 애드온 (Phase 62-63)
        self.pii_redactor = PIIRedactor(enabled=True)
        self.prompt_defender = PromptDefender(enabled=True)

        # 지식 그래프 (선택적)
        self.knowledge_graph = None
        try:
            from src.knowledge_graph import KnowledgeGraph
            self.knowledge_graph = KnowledgeGraph.build_from_faq(self.faq_items)
        except Exception:
            pass

    @staticmethod
    def _normalize_faq_items(items: list[dict]) -> list[dict]:
        """FAQ 항목을 정규화하여 신/구 포맷 모두 호환되도록 한다.

        신규 포맷 (v4): canonical_question, answer_short, answer_long, citations
        기존 포맷 (v3): question, answer, legal_basis

        정규화 후 모든 항목은 양쪽 키를 모두 갖는다.
        """
        normalized = []
        for item in items:
            n = dict(item)
            # question ↔ canonical_question
            if "canonical_question" in n and "question" not in n:
                n["question"] = n["canonical_question"]
            elif "question" in n and "canonical_question" not in n:
                n["canonical_question"] = n["question"]
            # answer ↔ answer_long
            if "answer_long" in n and "answer" not in n:
                n["answer"] = n["answer_long"]
            elif "answer" in n and "answer_long" not in n:
                n["answer_long"] = n["answer"]
            # legal_basis ↔ citations
            if "citations" in n and "legal_basis" not in n:
                n["legal_basis"] = n["citations"]
            elif "legal_basis" in n and "citations" not in n:
                n["citations"] = n["legal_basis"]
            normalized.append(n)
        return normalized

    def _cached_classify(self, query: str) -> list[str]:
        """Classify a query with LRU caching (max 100 entries)."""
        if query in self._classifier_cache:
            # Move to end (most recently used)
            self._classifier_cache.move_to_end(query)
            return self._classifier_cache[query]
        result = classify_query(query)
        self._classifier_cache[query] = result
        if len(self._classifier_cache) > CLASSIFIER_CACHE_MAX_SIZE:
            self._classifier_cache.popitem(last=False)
        return result

    def get_persona(self) -> str:
        """챗봇 페르소나 인사말을 반환한다."""
        return self.config.get("persona", "")

    def find_matching_faq(self, query: str, category: str) -> dict | None:
        """질문과 카테고리에 매칭되는 FAQ 항목을 찾는다.

        파이프라인:
        1. 키워드 매칭
        2. TF-IDF 유사도
        3. BM25 랭킹
        4. 벡터 검색 (의미론적 매칭)
        5. LLM 폴백 (마지막 수단)
        """
        query_lower = normalize_query(query)
        best_match = None
        best_score = 0
        best_keyword_hits = 0

        # 1단계: 키워드 매칭
        for item in self.faq_items:
            score = 0
            keyword_hits = 0

            if item.get("category") == category:
                score += CATEGORY_BONUS

            keywords = item.get("keywords", [])
            for kw in keywords:
                if kw.lower() in query_lower:
                    score += 1
                    keyword_hits += 1

            if score > best_score or (score == best_score and keyword_hits > best_keyword_hits):
                best_score = score
                best_match = item
                best_keyword_hits = keyword_hits

        if best_score >= KEYWORD_SCORE_THRESHOLD and best_keyword_hits >= MIN_KEYWORD_HITS:
            return best_match

        # 2단계: TF-IDF 유사도 폴백
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

        # 3단계: 벡터 검색 (의미론적 매칭)
        if self.vector_search_enabled:
            vector_results = self.vector_search.find_best_match(query, category=category, top_k=3)
            if vector_results and vector_results[0]["score"] >= self.vector_search.CONFIDENT_THRESHOLD:
                # 높은 신뢰도 매칭 반환
                return vector_results[0]["item"]

            # 중간 신뢰도 매칭은 LLM으로 검증
            if vector_results and vector_results[0]["score"] >= self.vector_search.SUGGESTION_THRESHOLD:
                # LLM 폴백으로 진행 (아래 참고)
                return None

        return None

    def find_matching_faq_with_llm_fallback(self, query: str, category: str) -> dict | str | None:
        """질문에 매칭되는 FAQ를 찾거나 LLM 폴백으로 답변을 생성한다.

        Returns:
            FAQ 항목 딕셔너리 또는 LLM 답변 문자열 또는 None.
        """
        # 일반 FAQ 매칭 시도
        faq_match = self.find_matching_faq(query, category)
        if faq_match:
            return faq_match

        # FAQ 매칭 실패 → 벡터 검색으로 관련 FAQ 수집
        faq_context = []
        if self.vector_search_enabled:
            vector_results = self.vector_search.find_best_match(query, category=None, top_k=3)
            faq_context = vector_results

        # LLM 폴백 시도
        if self.llm_enabled and faq_context:
            llm_response = generate_llm_response_with_disclaimer(query, faq_context)
            if llm_response:
                return llm_response

        return None

    def _extract_conclusion(self, answer: str) -> str:
        """FAQ 답변에서 의미 있는 결론을 추출한다."""
        sentences = answer.replace("·", ",").split(".")
        # 첫 문장이 너무 짧으면 (MIN_CONCLUSION_LENGTH글자 이하, 예: "네") 두 번째 문장까지 포함
        first = sentences[0].strip()
        if len(first) <= MIN_CONCLUSION_LENGTH and len(sentences) > 1:
            return (first + ". " + sentences[1].strip()).strip() + "."
        return first + "."

    def process_query(self, query: str, session_id: str | None = None, include_metadata: bool = False) -> dict | str:
        """사용자 질문을 처리하여 답변을 생성한다.

        Args:
            query: 사용자 질문 문자열.
            session_id: 세션 ID (선택). 제공 시 멀티턴 대화를 지원한다.
            include_metadata: True이면 메타데이터를 포함한 dict 반환,
                             False이면 답변 텍스트만 반환 (기존 동작, 기본값)

        Returns:
            include_metadata=False (기본값):
                답변 문자열 (기존 동작과 호환)

            include_metadata=True:
                {
                    "response": str,  # 답변 텍스트
                    "intent_id": str,  # 분류된 의도 ID (예: "sysqual_001")
                    "intent_confidence": float,  # 의도 신뢰도 (0.0~1.0)
                    "category": str,  # 기존 10-category 분류
                    "entities": dict,  # 추출된 엔티티
                    "risk_level": str,  # 위험도 ("low"/"medium"/"high"/"critical")
                    "policy_decision": dict,  # 정책 평가 결과
                    "escalation_triggered": bool,  # 에스컬레이션 여부
                }
        """
        if not query or not query.strip():
            result_dict = self._build_empty_response()
            return result_dict if include_metadata else result_dict["response"]

        # Phase 63: 악의적 프롬프트 차단
        if self.prompt_defender.is_malicious(query):
            logger.warning(f"Malicious prompt detected and blocked. (query length: {len(query)})")
            result_dict = self._wrap_result(
                "허용되지 않는 접근 방식이거나 악의적인 문자열이 감지되어 요청이 차단되었습니다.",
                "unknown", 0.0, "GENERAL", {}, "critical", {}, True
            )
            return result_dict if include_metadata else result_dict["response"]

        # Phase 62: PII 마스킹 처리 (이후 모든 처리는 마스킹된 텍스트 기반)
        safe_query = self.pii_redactor.redact(query)

        # 세션이 있으면 멀티턴 처리 시도
        session = None
        if session_id:
            session = self.session_manager.get_session(session_id)

        if session and session.has_pending():
            result = self._process_confirmation_turn(session, safe_query)
            # 세션 기반 응답은 단순 문자열이므로 래핑
            result_dict = self._wrap_result(result, "unknown", 0.0, "GENERAL", {}, "low", {}, False)
            return result_dict if include_metadata else result_dict["response"]

        result_dict = self._process_new_query(safe_query, session)
        return result_dict if include_metadata else result_dict["response"]

    def _preprocess_query(self, query: str) -> tuple[str, list[dict]]:
        """질문 전처리: 오타 교정 + 동의어 확장.

        Returns:
            (처리된 질문, 교정 목록) 튜플.
        """
        corrected, corrections = spell_correct(query)
        expanded = expand_query(corrected)
        return expanded, corrections

    def _build_empty_response(self) -> dict:
        """빈 쿼리에 대한 표준 응답을 생성한다."""
        return {
            "response": "질문을 입력해 주세요.",
            "intent_id": "unknown",
            "intent_confidence": 0.0,
            "category": "GENERAL",
            "entities": {},
            "risk_level": "low",
            "policy_decision": {},
            "escalation_triggered": False,
        }

    def _wrap_result(
        self,
        response: str,
        intent_id: str,
        intent_confidence: float,
        category: str,
        entities: dict,
        risk_level: str,
        policy_decision: dict,
        escalation_triggered: bool,
    ) -> dict:
        """응답 결과를 표준 형식으로 감싼다."""
        return {
            "response": response,
            "intent_id": intent_id,
            "intent_confidence": intent_confidence,
            "category": category,
            "entities": entities,
            "risk_level": risk_level,
            "policy_decision": policy_decision,
            "escalation_triggered": escalation_triggered,
        }

    def _map_intent_to_category(self, intent_id: str) -> str:
        """의도 ID를 기존 10-category 시스템으로 매핑한다."""
        if not intent_id or intent_id == "unknown":
            return "GENERAL"

        try:
            return self.intent_classifier.get_intent_category(intent_id)
        except Exception as e:
            logger.warning(f"Failed to map intent {intent_id} to category: {e}")
            return "GENERAL"

    def _process_new_query(self, query: str, session=None) -> dict:
        """새 질문을 처리한다. 새 파이프라인을 사용한다.

        파이프라인:
        1. 오타 교정 + 동의어 확장
        2. 의도 분류 (새 30-intent 시스템)
        3. 엔티티 추출
        4. 카테고리 매핑 (의도 → 기존 10-category)
        5. FAQ 매칭
        6. 정책 평가
        7. 답변 필터링
        8. 에스컬레이션 확인
        9. 응답 구성
        """
        processed_query, corrections = self._preprocess_query(query)

        # 2단계: 의도 분류 (새 30-intent 시스템)
        intent_id, intent_confidence = classify_intent(processed_query)

        # 3단계: 엔티티 추출
        entities = extract_entities(processed_query)

        # 4단계: 카테고리 매핑 (의도 → 기존 10-category)
        mapped_category = self._map_intent_to_category(intent_id)

        # 세션이 있으면 컨텍스트 기반 분류도 시도
        if session:
            categories = self.smart_classifier.classify_with_context(processed_query, session)
        else:
            categories = self._cached_classify(processed_query)

        if not categories:
            categories = ["GENERAL"]
        primary_category = categories[0]

        # mapped_category가 더 우선도가 높으면 사용
        # 단, 의도 분류 신뢰도가 임계값(0.3) 이상인 경우에만 적용
        # 신뢰도가 낙으면 기존 키워드 기반 분류를 유지하여 오매칭을 방지
        INTENT_CONFIDENCE_THRESHOLD = 0.3
        if mapped_category != "GENERAL" and intent_confidence >= INTENT_CONFIDENCE_THRESHOLD:
            primary_category = mapped_category

        # 5단계: FAQ 매칭
        escalation = check_escalation(processed_query)
        faq_match = self.find_matching_faq(processed_query, primary_category)

        # 6단계: 정책 평가
        policy_decision = evaluate_policy(intent_id, processed_query)
        risk_level = policy_decision.get("risk_level", "low")

        # 기존 에스컬레이션과 정책 기반 에스컬레이션 병합
        escalation_trigger_from_policy = should_escalate_policy(
            risk_level, policy_decision.get("escalation_trigger", False), processed_query
        )
        escalation_triggered = escalation is not None or escalation_trigger_from_policy

        # 에스컬레이션 우선: FAQ 매칭이 없거나 에스컬레이션만 트리거된 경우
        if escalation_triggered and not faq_match:
            if escalation:
                response = self._build_escalation_response(escalation)
            else:
                response = self._build_escalation_response_from_policy(policy_decision)

            if session:
                session.add_turn(query, response)

            return self._wrap_result(
                response, intent_id, intent_confidence, primary_category, entities,
                risk_level, policy_decision, True,
            )

        # FAQ 매칭 실패 시 LLM 폴백 시도
        if not faq_match:
            llm_response = None
            if self.vector_search_enabled and self.llm_enabled:
                llm_result = self.find_matching_faq_with_llm_fallback(processed_query, primary_category)
                if isinstance(llm_result, str):
                    llm_response = llm_result
                elif isinstance(llm_result, dict):
                    faq_match = llm_result

            if llm_response:
                # 7단계: 답변 필터링 (면책조항 추가)
                filtered_response = apply_answer_filter(llm_response, risk_level)

                if session:
                    session.add_turn(query, filtered_response)

                return self._wrap_result(
                    filtered_response, intent_id, intent_confidence, primary_category, entities,
                    risk_level, policy_decision, escalation_triggered,
                )

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
                session.context["intent_id"] = intent_id
                session.context["risk_level"] = risk_level
                session.context["policy_decision"] = policy_decision
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

                return self._wrap_result(
                    intro_response, intent_id, intent_confidence, primary_category, entities,
                    risk_level, policy_decision, escalation_triggered,
                )

            # 법령 가이드 요약 추출 (지식 그래프 연계)
            legal_guide = []
            if self.knowledge_graph:
                for basis in faq_match.get("legal_basis", []):
                    law_node_id = f"law_{basis}"
                    if law_node_id in self.knowledge_graph.nodes:
                        node_data = self.knowledge_graph.nodes[law_node_id].get("data", {})
                        summary = node_data.get("summary")
                        if summary:
                            legal_guide.append(f"{basis}: {summary}")

            response = build_response(
                topic=category_name,
                conclusion=self._extract_conclusion(faq_match.get("answer", "")),
                explanation=[faq_match.get("answer", "")],
                legal_basis=faq_match.get("legal_basis", []),
                confirmation_items=confirmation_texts if confirmation_texts else None,
                is_escalation=escalation_triggered,
                escalation_message=escalation["message"] if escalation else "",
                legal_guide=legal_guide if legal_guide else None,
            )

            # 7단계: 답변 필터링 (면책조항 추가)
            filtered_response = apply_answer_filter(response, risk_level)

            if session:
                session.add_turn(query, filtered_response)

            return self._wrap_result(
                filtered_response, intent_id, intent_confidence, primary_category, entities,
                risk_level, policy_decision, escalation_triggered,
            )

        # 매칭 실패
        response = build_unknown_response()

        # 7단계: 답변 필터링 (면책조항 추가)
        filtered_response = apply_answer_filter(response, risk_level)

        if session:
            session.add_turn(query, filtered_response)

        return self._wrap_result(
            filtered_response, intent_id, intent_confidence, primary_category, entities,
            risk_level, policy_decision, escalation_triggered,
        )

    def _process_confirmation_turn(self, session, query: str) -> str:
        """확인 질문에 대한 사용자 응답을 처리한다.

        Note: 이 메서드는 단순 문자열을 반환하며, 호출자가 _wrap_result로 감싼다.
        """
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
        risk_level = ctx.get("risk_level", "low")

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
        response_with_summary = response + summary

        # 정책 기반 답변 필터링 (면책조항 추가)
        filtered_response = apply_answer_filter(response_with_summary, risk_level)

        session.add_turn(last_query, filtered_response)
        # 확인 완료 후 컨텍스트 초기화
        session.context = {}
        session.confirmed = {}
        return filtered_response

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

    def _build_escalation_response_from_policy(self, policy_decision: dict) -> str:
        """정책 평가 결과로부터 에스컬레이션 응답을 생성한다."""
        policy_decision.get("escalation_target")
        risk_level = policy_decision.get("risk_level", "high")
        disclaimers = policy_decision.get("disclaimers", [])

        # 위험도에 따른 기본 메시지
        if risk_level == "critical":
            base_msg = "죄송합니다. 이 사항은 전문 상담이 필요합니다."
        elif risk_level == "high":
            base_msg = "죄송합니다. 추가 검토가 필요한 사항입니다."
        else:
            base_msg = "죄송합니다. 정확한 안내를 위해 전문가 상담을 권장합니다."

        # 연락처 정보
        contact_info = "문의처: 관세청 고객지원센터 (☎ 125 또는 1344-5100)\n"

        # 면책조항
        disclaimer_text = "\n".join(disclaimers) if disclaimers else (
            "본 답변은 일반적인 안내용입니다. "
            "최종 처리는 관할 세관 또는 해당 소관기관 확인이 필요합니다."
        )

        return (
            f"{base_msg}\n\n"
            f"{contact_info}\n"
            f"안내:\n- {disclaimer_text}"
        )

    def _get_category_name(self, category_code: str) -> str:
        """카테고리 코드를 한글 이름으로 변환한다."""
        for cat in self.config.get("categories", []):
            if cat.get("code") == category_code:
                return cat.get("name", category_code)
        return category_code
