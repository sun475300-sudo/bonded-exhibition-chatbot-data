"""스마트 제안 엔진 모듈.

사용자 대화 흐름에 따라 후속 질문, 명확화 프롬프트,
온보딩 제안, 카테고리별 팁을 제공한다.
"""

from __future__ import annotations

from typing import Any

from src.knowledge_graph import KnowledgeGraph
from src.question_cluster import QuestionClusterer
from src.related_faq import RelatedFAQFinder


# 카테고리별 온보딩/팁 정보
CATEGORY_TIPS: dict[str, list[str]] = {
    "GENERAL": [
        "보세전시장은 외국물품을 '수입신고 전' 상태로 전시할 수 있는 자유구역입니다.",
        "보세창고와 달리 전시 목적의 일시적 장치에 최적화되어 있습니다.",
    ],
    "LICENSE": [
        "행사 성격에 따라 특허 기간이 달라지며, 종료 후 정리기간까지 고려해야 합니다.",
        "운영인의 결격사유나 자본금 요건이 특허 유지의 핵심입니다.",
    ],
    "IMPORT_EXPORT": [
        "반출입 신고 시 품명과 수량이 인보이스와 일치하지 않으면 반입이 반려될 수 있습니다.",
        "반출 기한 도과 시 보세구역 외 반출 죄로 처벌될 수 있으니 주의하세요.",
    ],
    "EXHIBITION": [
        "전시 중인 물품을 임의로 견본용으로 소모하거나 훼손하면 멸실로 간주됩니다.",
        "세관장의 승인 없이 시설물을 무단으로 변경하면 안 됩니다.",
    ],
    "SALES": [
        "현장 판매 물품은 수입 성격에 따라 세관장의 '판매 허가'가 선행되어야 함을 잊지 마세요.",
        "수입신고 수리(면허) 전 물품을 인도하는 행위는 관세법 위반 형사처벌 대상입니다.",
    ],
    "SAMPLE": [
        "견본품은 '무상 배포'를 전제로 하며, 유상 판매 시에는 일반 통관 절차를 따라야 합니다.",
        "배포 허가 수량을 초과하면 무단 반출로 간주되어 과태료가 부과됩니다.",
    ],
    "FOOD_TASTING": [
        "시식용 식품은 검역 대상 여부를 반드시 먼저 확인해야 합니다.",
        "시식 후 남은 잔량은 재수출하거나 참관 하에 폐기 승인을 받아야 합니다.",
    ],
    "DOCUMENTS": [
        "UNI-PASS를 통한 전자신고가 원칙이며, 위반 시 행정 제재가 있을 수 있습니다.",
        "서류 보존 의무(보통 5년)를 준수하지 않으면 법적 불이익이 발생합니다.",
    ],
    "PENALTIES": [
        "실수로 인한 누락도 관세법상 과태료 대상이 될 수 있으므로 즉시 자진 신고하세요.",
        "도난/분실 시 즉시 보고하지 않으면 운영인의 관리 소홀 책임이 가중됩니다.",
    ],
    "CONTACT": [
        "긴급한 법령 해석은 관세청 법령정보포털이나 관할 세관 통관지원과에 문의하세요.",
        "복합적인 실무는 전문 관세사의 조력을 받는 것을 권장합니다.",
    ],
}

# 카테고리 간 일반적 후속 패턴 (카테고리 A 질문 후 자주 묻는 카테고리 B)
COMMON_FOLLOWUP_PATTERNS: dict[str, list[str]] = {
    "GENERAL": ["LICENSE", "EXHIBITION", "IMPORT_EXPORT"],
    "LICENSE": ["DOCUMENTS", "PENALTIES", "GENERAL"],
    "IMPORT_EXPORT": ["DOCUMENTS", "EXHIBITION", "PENALTIES"],
    "EXHIBITION": ["SALES", "SAMPLE", "IMPORT_EXPORT"],
    "SALES": ["DOCUMENTS", "IMPORT_EXPORT", "PENALTIES"],
    "SAMPLE": ["FOOD_TASTING", "DOCUMENTS", "IMPORT_EXPORT"],
    "FOOD_TASTING": ["DOCUMENTS", "SAMPLE", "IMPORT_EXPORT"],
    "DOCUMENTS": ["IMPORT_EXPORT", "LICENSE", "CONTACT"],
    "PENALTIES": ["CONTACT", "DOCUMENTS", "LICENSE"],
    "CONTACT": ["GENERAL", "DOCUMENTS", "LICENSE"],
}

# 기본 온보딩 질문
DEFAULT_ONBOARDING_QUESTIONS: list[str] = [
    "보세전시장이 무엇인가요?",
    "보세전시장 설치·운영 특허는 어떻게 받나요?",
    "보세전시장에서 물품을 판매할 수 있나요?",
]


class SmartSuggestionEngine:
    """사용자 대화 맥락을 기반으로 스마트 제안을 제공하는 엔진."""

    def __init__(
        self,
        faq_items: list[dict],
        knowledge_graph: KnowledgeGraph | None = None,
        question_clusterer: QuestionClusterer | None = None,
        related_faq_finder: RelatedFAQFinder | None = None,
    ):
        """SmartSuggestionEngine을 초기화한다.

        Args:
            faq_items: FAQ 항목 리스트.
            knowledge_graph: 지식 그래프 인스턴스 (선택).
            question_clusterer: 질문 클러스터러 인스턴스 (선택).
            related_faq_finder: 관련 FAQ 검색기 인스턴스 (선택).
        """
        self.faq_items = faq_items
        self.knowledge_graph = knowledge_graph
        self.question_clusterer = question_clusterer or QuestionClusterer(faq_items)
        self.related_faq_finder = related_faq_finder or RelatedFAQFinder(faq_items)

        # 카테고리별 FAQ 인덱스 구성
        self._category_faq_map: dict[str, list[dict]] = {}
        for item in faq_items:
            cat = item.get("category", "GENERAL")
            self._category_faq_map.setdefault(cat, []).append(item)

    def get_follow_up_suggestions(
        self,
        query: str,
        answer: str,
        category: str,
        session_history: list[str] | None = None,
    ) -> list[str]:
        """후속 질문 제안을 반환한다.

        3가지 소스에서 후보를 수집한다:
        1. 같은 카테고리의 아직 묻지 않은 FAQ
        2. 지식 그래프에서의 관련 카테고리 FAQ
        3. 일반적 후속 패턴의 FAQ

        Args:
            query: 사용자의 원본 질문.
            answer: 챗봇이 반환한 답변.
            category: 질문의 분류 카테고리.
            session_history: 이미 질문한 쿼리 리스트 (선택).

        Returns:
            최대 3개의 후속 질문 문자열 리스트.
        """
        asked = set(session_history or [])
        candidates: list[dict[str, Any]] = []

        # 소스 1: 같은 카테고리의 FAQ (높은 우선순위)
        same_cat_faqs = self._category_faq_map.get(category, [])
        for faq in same_cat_faqs:
            q = faq.get("question", "")
            if q and q not in asked and q != query:
                candidates.append({"question": q, "score": 3.0, "source": "same_category"})

        # 소스 2: 지식 그래프 관련 카테고리
        if self.knowledge_graph:
            related_categories = self._get_related_categories_from_graph(category)
            for rel_cat in related_categories:
                for faq in self._category_faq_map.get(rel_cat, []):
                    q = faq.get("question", "")
                    if q and q not in asked and q != query:
                        candidates.append({"question": q, "score": 2.0, "source": "knowledge_graph"})

        # 소스 3: 대화 분석 기반 일반 후속 패턴
        followup_cats = COMMON_FOLLOWUP_PATTERNS.get(category, [])
        for fc in followup_cats:
            for faq in self._category_faq_map.get(fc, []):
                q = faq.get("question", "")
                if q and q not in asked and q != query:
                    candidates.append({"question": q, "score": 1.0, "source": "followup_pattern"})

        # 관련 FAQ 유사도로 보너스 점수 추가
        related = self.related_faq_finder.find_related_by_query(query, top_k=5)
        related_questions = {r["question"]: r["similarity"] for r in related}
        for cand in candidates:
            if cand["question"] in related_questions:
                cand["score"] += related_questions[cand["question"]]

        # 중복 제거 후 점수 순 정렬
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for c in candidates:
            if c["question"] not in seen:
                seen.add(c["question"])
                unique.append(c)

        unique.sort(key=lambda x: x["score"], reverse=True)
        return [c["question"] for c in unique[:3]]

    def _get_related_categories_from_graph(self, category: str) -> list[str]:
        """지식 그래프에서 관련 카테고리를 찾는다.

        Args:
            category: 기준 카테고리.

        Returns:
            관련 카테고리 이름 리스트.
        """
        if not self.knowledge_graph:
            return []

        cat_node_id = f"cat_{category}"
        if cat_node_id not in self.knowledge_graph.nodes:
            return []

        try:
            neighbors = self.knowledge_graph.get_neighbors(cat_node_id, depth=2)
            related_cats: list[str] = []
            for node in neighbors:
                if node.get("type") == "category":
                    cat_name = node.get("data", {}).get("name", "")
                    if cat_name and cat_name != category:
                        related_cats.append(cat_name)
            return related_cats
        except (KeyError, Exception):
            return []

    def get_clarification_prompts(
        self, query: str, matches: list[dict]
    ) -> list[str]:
        """모호한 쿼리에 대해 명확화 질문을 생성한다.

        여러 FAQ가 비슷한 점수로 매칭될 때, 사용자가 의도를 좁힐 수
        있도록 돕는 질문을 제안한다.

        Args:
            query: 사용자의 원본 질문.
            matches: 매칭된 FAQ 리스트 (각각 question, category 키 포함).

        Returns:
            명확화 질문 리스트.
        """
        if not matches:
            return ["좀 더 구체적으로 질문해 주시겠어요?"]

        prompts: list[str] = []

        # 매칭된 카테고리가 여러 개면 카테고리별 구분 질문
        categories = list({m.get("category", "") for m in matches if m.get("category")})
        if len(categories) > 1:
            cat_names = ", ".join(categories[:3])
            prompts.append(
                f"다음 중 어떤 분야에 대해 알고 싶으신가요? ({cat_names})"
            )

        # 매칭된 FAQ 질문을 선택지로 제시
        for match in matches[:3]:
            q = match.get("question", "")
            if q:
                prompts.append(f"혹시 이 질문이신가요: {q}")

        return prompts[:3]

    def get_onboarding_suggestions(self) -> list[str]:
        """새 사용자를 위한 시작 질문을 반환한다.

        FAQ 데이터에서 주요 카테고리의 대표 질문을 선별한다.

        Returns:
            3개의 추천 시작 질문 리스트.
        """
        suggestions: list[str] = []

        # 주요 카테고리에서 첫 번째 FAQ 질문을 추출
        priority_categories = ["GENERAL", "LICENSE", "EXHIBITION", "SALES", "IMPORT_EXPORT"]
        for cat in priority_categories:
            faqs = self._category_faq_map.get(cat, [])
            if faqs:
                q = faqs[0].get("question", "")
                if q:
                    suggestions.append(q)
            if len(suggestions) >= 3:
                break

        # 부족하면 기본 질문으로 채움
        if len(suggestions) < 3:
            for default_q in DEFAULT_ONBOARDING_QUESTIONS:
                if default_q not in suggestions:
                    suggestions.append(default_q)
                if len(suggestions) >= 3:
                    break

        return suggestions[:3]

    def get_contextual_tips(self, category: str) -> list[str]:
        """카테고리에 관련된 도움말 팁을 반환한다.

        Args:
            category: 카테고리 코드.

        Returns:
            관련 팁 문자열 리스트.
        """
        return CATEGORY_TIPS.get(category, CATEGORY_TIPS.get("GENERAL", []))

    def rank_suggestions(
        self,
        suggestions: list[str],
        session_history: list[str],
    ) -> list[str]:
        """제안을 관련성으로 랭킹하고, 이미 질문한 항목은 제외한다.

        랭킹 기준:
        - 세션 기록에 없는 질문 우선
        - 클러스터 유사도 기반 관련성

        Args:
            suggestions: 후보 제안 질문 리스트.
            session_history: 이미 질문한 쿼리 리스트.

        Returns:
            랭킹된 제안 리스트 (이미 질문한 항목 제외).
        """
        asked = set(session_history)

        # 이미 질문한 항목 제외
        filtered = [s for s in suggestions if s not in asked]

        if not filtered:
            return []

        # 세션 기록의 마지막 질문과의 유사도로 랭킹
        if session_history:
            last_query = session_history[-1]
            scored: list[tuple[str, float]] = []
            for suggestion in filtered:
                sim = self.question_clusterer.compute_similarity(last_query, suggestion)
                scored.append((suggestion, sim))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [s for s, _ in scored]

        return filtered
