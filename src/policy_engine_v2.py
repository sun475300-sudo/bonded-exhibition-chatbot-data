"""Policy Engine v2 - Risk-based Answer Control (self-contained).

A pure-Python, regex-based risk assessment engine for bonded-exhibition
chatbot queries. It returns a structured decision that can be consumed by
downstream response builders or admin UIs:

    {
        "risk_level": "low|medium|high|critical",
        "answer_policy": "direct|conditional|restricted|escalation_only",
        "restrictions": [...],
        "required_disclaimers": [...],
        "escalation_required": bool,
        "escalation_target": str | None,
        "confidence": float,
    }

The engine is deliberately self-contained: it has no dependency on the legacy
:mod:`src.policy_engine` module, on :mod:`src.utils`, or on any runtime state.
This makes it easy to test in isolation and safe to expose through admin-only
HTTP endpoints.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Ordering helpers
# ---------------------------------------------------------------------------

RISK_ORDER: Dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

# Policy level is derived from risk level (with possible overrides from rules)
DEFAULT_POLICY_FOR_RISK: Dict[str, str] = {
    "low": "direct",
    "medium": "conditional",
    "high": "restricted",
    "critical": "escalation_only",
}


# ---------------------------------------------------------------------------
# Disclaimers (Korean)
# ---------------------------------------------------------------------------

DISCLAIMERS: Dict[str, str] = {
    "low": "본 답변은 일반 안내이며 상세 내용은 공식 자료를 참고해 주세요.",
    "medium": "개별 사안은 관할 세관 확인 필요",
    "high": (
        "본 답변은 법적 효력이 없으며, 구체적 사안은 관할 세관 확인이 필요합니다."
    ),
    "critical": (
        "본 질의는 챗봇이 최종 판단할 수 없으며, 관할 세관 또는 전문가 상담이 필요합니다."
    ),
}


# ---------------------------------------------------------------------------
# Escalation contact directory
# ---------------------------------------------------------------------------

ESCALATION_DIRECTORY: Dict[str, Dict[str, str]] = {
    "관할 세관": {
        "name": "관할 세관",
        "phone": "1344-5100",
        "department": "관세청 관할 세관 통관부서",
        "sla": "2시간 이내",
    },
    "보세산업과": {
        "name": "관세청 통관국 보세산업과",
        "phone": "125",
        "department": "보세산업과",
        "sla": "3시간 이내",
    },
    "식약처": {
        "name": "식품의약품안전처",
        "phone": "1577-1255",
        "department": "식약처 민원실",
        "sla": "4시간 이내",
    },
    "125": {
        "name": "관세청 고객지원센터",
        "phone": "125",
        "department": "고객지원센터",
        "sla": "1시간 이내",
    },
}


# ---------------------------------------------------------------------------
# Regex dictionaries (compiled once at import time)
# ---------------------------------------------------------------------------

# Legal judgment / interpretation requests ("가능한가요?", "위법인가요?" ...)
LEGAL_JUDGMENT_PATTERNS: List[re.Pattern] = [
    re.compile(r"가능\s*한\s*가요"),
    re.compile(r"가능\s*한\s*지"),
    re.compile(r"가능\s*합\s*니\s*까"),
    re.compile(r"되\s*나요"),
    re.compile(r"되\s*는\s*지"),
    re.compile(r"되\s*는\s*가요"),
    re.compile(r"해도\s*되"),
    re.compile(r"위법\s*(인가요|입니까|이야|이지|인지)"),
    re.compile(r"불법\s*(인가요|입니까|이야|이지|인지)"),
    re.compile(r"합법\s*(인가요|입니까|이야|이지|인지)"),
    re.compile(r"법적\s*으?로?\s*(문제|판단|효력|가능)"),
    re.compile(r"유권\s*해석"),
    re.compile(r"법적\s*해석"),
]

# Tax determination keywords (관세 / 면세 / 과세)
TAX_KEYWORD_PATTERN = re.compile(
    r"(관세|면세|과세|세율|과세가격|세액|부가세|감세|세금\s*계산)"
)

# Multi-agency category tags and their downstream agencies
FOOD_CATEGORY_KEYWORDS = re.compile(r"(시식|식품|검역|위생|식음료|음식)")
HEALTH_CATEGORY_KEYWORDS = re.compile(r"(의약품|의료기기|화장품|건강기능식품)")

# Critical keywords - smuggling / tax evasion etc.
CRITICAL_KEYWORDS_PATTERN = re.compile(
    r"("
    r"밀수|밀반입|밀반출|탈세|탈루|부정\s*수출입|"
    r"허위\s*신고|거짓\s*신고|위장\s*거래|조세\s*포탈|"
    r"뇌물|매수|위조|변조|무신고"
    r")"
)

# PII-like patterns
PII_PATTERN = re.compile(
    r"(주민등록번호|여권번호|계좌번호|신용카드|카드번호|사업자등록번호)"
)


# ---------------------------------------------------------------------------
# Engine class
# ---------------------------------------------------------------------------


class PolicyEngineV2:
    """Risk-based answer controller.

    The engine evaluates an incoming user query (plus optional context such as
    intent id, extracted entities, and FAQ category) and returns a structured
    decision. It is stateless - construct once and reuse.
    """

    # Categories that imply food-safety escalation to 식약처
    FOOD_CATEGORY_TAGS = {"FOOD_TASTING", "FOOD", "TASTING", "시식", "식품"}

    def __init__(self) -> None:
        # Version is useful for admin/observability endpoints
        self.version = "2.0.0"

    # ---------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------

    def evaluate(
        self,
        query: str,
        intent_id: Optional[str] = None,
        entities: Optional[List[Dict]] = None,
        category: Optional[str] = None,
    ) -> Dict:
        """Evaluate a query and return a risk-control decision.

        Args:
            query: The user's raw query string.
            intent_id: Optional classifier intent id.
            entities: Optional list of entity dicts.
            category: Optional FAQ/intent category tag.

        Returns:
            Dictionary shaped as documented in the module docstring.
        """
        if not isinstance(query, str):
            query = "" if query is None else str(query)

        restrictions: List[str] = []
        disclaimers: List[str] = []
        escalation_target: Optional[str] = None
        reasons: List[str] = []

        risk_level = "low"
        confidence = 0.85

        # -- CRITICAL checks (highest priority) --
        if self._check_sensitive_keywords(query):
            risk_level = self._promote(risk_level, "critical")
            restrictions.append("no_definitive_judgment")
            restrictions.append("requires_manual_review")
            escalation_target = escalation_target or "125"
            reasons.append("sensitive_keyword")
            confidence = 0.98

        if self._check_pii(query):
            # PII-containing queries are treated as critical as well.
            risk_level = self._promote(risk_level, "critical")
            restrictions.append("pii_present")
            restrictions.append("no_definitive_judgment")
            escalation_target = escalation_target or "125"
            reasons.append("pii")
            confidence = max(confidence, 0.95)

        # -- HIGH checks --
        if self._check_legal_judgment_request(query):
            risk_level = self._promote(risk_level, "high")
            restrictions.append("no_definitive_judgment")
            restrictions.append("requires_agency_verification")
            escalation_target = escalation_target or "관할 세관"
            reasons.append("legal_judgment_request")
            confidence = max(confidence, 0.9)

        if self._check_tax_determination(query):
            risk_level = self._promote(risk_level, "high")
            restrictions.append("requires_agency_verification")
            escalation_target = escalation_target or "관할 세관"
            reasons.append("tax_determination")
            confidence = max(confidence, 0.9)

        # -- Multi-agency routing (medium or higher) --
        agency = self._check_multi_agency(query, category)
        if agency is not None:
            # Food tasting-ish categories go to 식약처 at medium minimum.
            risk_level = self._promote(risk_level, "medium")
            escalation_target = agency  # multi-agency target wins for routing
            restrictions.append("requires_agency_verification")
            reasons.append(f"multi_agency:{agency}")
            confidence = max(confidence, 0.88)

        # -- MEDIUM default for FAQ categories that always need a caveat --
        if self._is_default_medium_category(category):
            risk_level = self._promote(risk_level, "medium")
            reasons.append("category_default_medium")

        # De-duplicate restriction flags while keeping order.
        restrictions = list(dict.fromkeys(restrictions))

        # Build disclaimers from risk level.
        disclaimers.append(DISCLAIMERS[risk_level])
        # Ensure the explicitly requested "개별 사안은 관할 세관 확인 필요"
        # disclaimer appears whenever agency verification is required.
        if "requires_agency_verification" in restrictions:
            required = DISCLAIMERS["medium"]
            if required not in disclaimers:
                disclaimers.append(required)

        answer_policy = DEFAULT_POLICY_FOR_RISK[risk_level]
        escalation_required = risk_level in ("high", "critical") or bool(
            escalation_target
        )

        # Low-risk and no agency signal => no escalation target.
        if risk_level == "low" and not escalation_target:
            escalation_required = False

        return {
            "risk_level": risk_level,
            "answer_policy": answer_policy,
            "restrictions": restrictions,
            "required_disclaimers": disclaimers,
            "escalation_required": escalation_required,
            "escalation_target": escalation_target,
            "confidence": round(float(confidence), 4),
            "reasons": reasons,
            "intent_id": intent_id,
            "category": category,
        }

    # ---------------------------------------------------------------
    # Detection helpers
    # ---------------------------------------------------------------

    def _check_legal_judgment_request(self, query: str) -> bool:
        """Return True if the query asks for a yes/no legal judgment."""
        if not query:
            return False
        for pat in LEGAL_JUDGMENT_PATTERNS:
            if pat.search(query):
                return True
        return False

    def _check_tax_determination(self, query: str) -> bool:
        """Return True when the query concerns tax/duty determination."""
        if not query:
            return False
        return bool(TAX_KEYWORD_PATTERN.search(query))

    def _check_multi_agency(
        self, query: str, category: Optional[str]
    ) -> Optional[str]:
        """Return target agency when query/category spans multiple agencies.

        Example: a food-tasting FAQ category combined with a food-related
        query escalates to 식약처.
        """
        cat_upper = (category or "").upper()
        if cat_upper in self.FOOD_CATEGORY_TAGS:
            if query and FOOD_CATEGORY_KEYWORDS.search(query):
                return "식약처"
            # Even without explicit food keywords the category already spans
            # multiple agencies for things like hygiene - route to 식약처.
            return "식약처"

        # Query-only signal: explicit mention of 식약처 matters.
        if query and re.search(r"식약처", query):
            return "식약처"

        # Health/drug/cosmetic-like mentions default to 식약처 as well.
        if query and HEALTH_CATEGORY_KEYWORDS.search(query):
            return "식약처"

        return None

    def _check_sensitive_keywords(self, query: str) -> bool:
        """Return True on smuggling/tax-evasion/fraud signals."""
        if not query:
            return False
        return bool(CRITICAL_KEYWORDS_PATTERN.search(query))

    def _check_pii(self, query: str) -> bool:
        if not query:
            return False
        return bool(PII_PATTERN.search(query))

    # ---------------------------------------------------------------
    # Utility methods
    # ---------------------------------------------------------------

    def get_disclaimer(self, risk_level: str) -> str:
        """Return the Korean disclaimer for the given risk level."""
        key = (risk_level or "low").lower()
        if key not in DISCLAIMERS:
            key = "low"
        return DISCLAIMERS[key]

    def get_escalation_info(self, target: Optional[str]) -> Dict[str, str]:
        """Return a contact-info dict for the given escalation target."""
        if not target:
            return {}
        return dict(ESCALATION_DIRECTORY.get(target, {}))

    def get_rules(self) -> Dict[str, List[str]]:
        """Return a human-readable summary of the engine's rules.

        This is used by the admin UI endpoint ``/api/admin/policy/rules``.
        """
        return {
            "version": self.version,
            "risk_levels": ["low", "medium", "high", "critical"],
            "answer_policies": [
                "direct",
                "conditional",
                "restricted",
                "escalation_only",
            ],
            "legal_judgment_patterns": [p.pattern for p in LEGAL_JUDGMENT_PATTERNS],
            "tax_keywords_pattern": TAX_KEYWORD_PATTERN.pattern,
            "sensitive_keywords_pattern": CRITICAL_KEYWORDS_PATTERN.pattern,
            "food_category_keywords_pattern": FOOD_CATEGORY_KEYWORDS.pattern,
            "pii_pattern": PII_PATTERN.pattern,
            "disclaimers": dict(DISCLAIMERS),
            "escalation_directory": {
                k: dict(v) for k, v in ESCALATION_DIRECTORY.items()
            },
            "food_category_tags": sorted(self.FOOD_CATEGORY_TAGS),
        }

    # ---------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------

    @staticmethod
    def _promote(current: str, candidate: str) -> str:
        """Return whichever of ``current`` / ``candidate`` has higher risk."""
        cur = RISK_ORDER.get(current, 0)
        new = RISK_ORDER.get(candidate, 0)
        return candidate if new > cur else current

    @staticmethod
    def _is_default_medium_category(category: Optional[str]) -> bool:
        if not category:
            return False
        # Known medium-by-default categories (extension point)
        medium_cats = {"LEGAL", "TAX", "PENALTY", "COMPLIANCE"}
        return category.upper() in medium_cats


# ---------------------------------------------------------------------------
# Module-level singleton and helpers (handy for callers that don't need
# an explicit instance).
# ---------------------------------------------------------------------------

_singleton: Optional[PolicyEngineV2] = None


def get_policy_engine_v2() -> PolicyEngineV2:
    """Return a process-wide :class:`PolicyEngineV2` singleton."""
    global _singleton
    if _singleton is None:
        _singleton = PolicyEngineV2()
    return _singleton


def evaluate_query(
    query: str,
    intent_id: Optional[str] = None,
    entities: Optional[List[Dict]] = None,
    category: Optional[str] = None,
) -> Dict:
    """Shortcut wrapper around :meth:`PolicyEngineV2.evaluate`."""
    return get_policy_engine_v2().evaluate(
        query=query,
        intent_id=intent_id,
        entities=entities,
        category=category,
    )
