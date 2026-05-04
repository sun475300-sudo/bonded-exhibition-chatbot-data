"""정책 엔진: 위험도 기반 답변 제어 모듈.

사용자 질문의 위험도를 평가하여 필요한 면책조항, 에스컬레이션,
답변 필터링을 결정한다. 보세전시장 챗봇의 규정 준수 및 법적 보호를 담당한다.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from src.utils import load_json, normalize_query


# ============================================================================
# Enums and Data Classes
# ============================================================================

class RiskLevel(Enum):
    """위험도 레벨 정의."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    def __lt__(self, other):
        """위험도 순서: CRITICAL > HIGH > MEDIUM > LOW."""
        if not isinstance(other, RiskLevel):
            return NotImplemented
        order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        return order[self.value] < order[other.value]

    def __le__(self, other):
        if not isinstance(other, RiskLevel):
            return NotImplemented
        order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        return order[self.value] <= order[other.value]

    def __gt__(self, other):
        if not isinstance(other, RiskLevel):
            return NotImplemented
        order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        return order[self.value] > order[other.value]

    def __ge__(self, other):
        if not isinstance(other, RiskLevel):
            return NotImplemented
        order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        return order[self.value] >= order[other.value]


@dataclass
class PolicyRule:
    """정책 규칙 정의."""
    rule_id: str
    name: str
    condition: Callable[[str, str, list, dict], bool]
    action: str
    risk_level: RiskLevel
    message_template: str
    escalation_target: Optional[str] = None

    def __hash__(self):
        """룰을 세트에 저장 가능하도록."""
        return hash(self.rule_id)

    def __eq__(self, other):
        """룰 비교."""
        if not isinstance(other, PolicyRule):
            return NotImplemented
        return self.rule_id == other.rule_id


@dataclass
class PolicyDecision:
    """정책 평가 결과."""
    risk_level: RiskLevel
    disclaimers: list[str] = field(default_factory=list)
    requires_escalation: bool = False
    escalation_target: Optional[str] = None
    escalation_sla_minutes: Optional[int] = None
    filtered_answer: Optional[str] = None
    applied_rules: list[PolicyRule] = field(default_factory=list)
    confidence_override: float = 1.0
    audit_metadata: dict = field(default_factory=dict)


# ============================================================================
# Korean Disclaimers by Risk Level
# ============================================================================

KOREAN_DISCLAIMERS = {
    RiskLevel.LOW: "",
    RiskLevel.MEDIUM: (
        "본 안내는 일반적인 참고 정보이며, 구체적인 사실관계에 따라 달라질 수 있습니다. "
        "최종 처리는 관할 세관 또는 해당 소관기관 확인이 필요합니다."
    ),
    RiskLevel.HIGH: (
        "본 답변은 법적 효력이 없으며, 정확한 사항은 관할 세관에 문의하시기 바랍니다. "
        "구체적인 사실관계에 따라 다르게 적용될 수 있습니다."
    ),
    RiskLevel.CRITICAL: (
        "이 질문은 전문 상담이 필요합니다. 관할 세관 보세산업과(☎ 125)로 문의해 주시기 바랍니다. "
        "본 챗봇은 이 사항에 대해 최종 판단을 내릴 수 없습니다."
    ),
}

# ============================================================================
# Escalation Mapping
# ============================================================================

ESCALATION_TARGETS = {
    "local_customs": {
        "name": "관할 세관",
        "contact": "관할 세관 통관부서",
        "phone": "관세청 콜센터: 1344-5100",
        "sla_minutes": 120,
        "department": "Customs Bureau",
    },
    "bonded_division": {
        "name": "보세산업과",
        "contact": "관세청 통관국 보세산업과",
        "phone": "☎ 125 (관세청 고객지원센터)",
        "sla_minutes": 180,
        "department": "Bonded Industry Division",
    },
    "food_safety": {
        "name": "식약처",
        "contact": "식품의약품안전처",
        "phone": "1577-1255",
        "sla_minutes": 240,
        "department": "Food & Drug Safety Administration",
    },
    "customer_support": {
        "name": "관세청 고객지원센터",
        "contact": "관세청 고객지원센터",
        "phone": "☎ 125",
        "sla_minutes": 60,
        "department": "Customer Support Center",
    },
    "tech_support": {
        "name": "전자통관 기술지원센터",
        "contact": "전자통관 기술지원센터",
        "phone": "☎ 1544-1285",
        "sla_minutes": 90,
        "department": "E-Customs Technical Support",
    },
}


# ============================================================================
# Built-in Policy Rules
# ============================================================================

def _build_builtin_rules() -> list[PolicyRule]:
    """내장 정책 규칙을 생성한다."""

    # 조건 함수 정의
    def has_pii(query: str, intent: str, entities: list, faq_item: dict) -> bool:
        """개인정보를 포함하는지 확인."""
        pii_keywords = ["주민등록번호", "여권번호", "카드번호", "계좌번호",
                       "사업자등록번호", "이름", "주소", "전화", "이메일"]
        query_lower = normalize_query(query)
        return any(kw in query_lower for kw in pii_keywords)

    def has_strategic_goods(query: str, intent: str, entities: list, faq_item: dict) -> bool:
        """전략물자 관련 질문인지 확인."""
        keywords = ["전략물자", "군수물자", "첨단기술", "미사일", "방위사업",
                   "핵기술", "유도탄", "대량살상"]
        query_lower = normalize_query(query)
        return any(kw in query_lower for kw in keywords)

    def is_smuggling_related(query: str, intent: str, entities: list, faq_item: dict) -> bool:
        """밀수/부정행위 관련 질문인지 확인."""
        keywords = ["밀수", "밀입", "부정행위", "과세회피", "위조", "변조",
                   "거짓신고", "뇌물", "뇌뢰", "적발", "단속"]
        query_lower = normalize_query(query)
        return any(kw in query_lower for kw in keywords)

    def is_tax_calculation(query: str, intent: str, entities: list, faq_item: dict) -> bool:
        """세금/관세 계산 질문인지 확인."""
        keywords = ["관세", "부가세", "세금", "과세가격", "세율", "계산",
                   "과세", "세액", "면세", "감세", "면세점"]
        query_lower = normalize_query(query)
        return any(kw in query_lower for kw in keywords)

    def is_penalty_question(query: str, intent: str, entities: list, faq_item: dict) -> bool:
        """벌칙/처벌 질문인지 확인."""
        keywords = ["벌칙", "제재", "과태료", "벌금", "처벌", "처분",
                   "위반", "과징금", "업무정지", "특허취소"]
        query_lower = normalize_query(query)
        return any(kw in query_lower for kw in keywords)

    def is_legal_interpretation(query: str, intent: str, entities: list, faq_item: dict) -> bool:
        """법적 해석 요청인지 확인."""
        keywords = ["유권해석", "법적 판단", "최종 판단", "공식 해석",
                   "법적 효력", "해석", "판단", "판결", "결정"]
        query_lower = normalize_query(query)
        return any(kw in query_lower for kw in keywords)

    def is_food_safety(query: str, intent: str, entities: list, faq_item: dict) -> bool:
        """식품안전 관련 질문인지 확인."""
        keywords = ["시식", "식품", "검역", "식약처", "위생", "식품안전",
                   "잔량", "폐기", "식품검역"]
        query_lower = normalize_query(query)
        return any(kw in query_lower for kw in keywords)

    def is_patent_termination(query: str, intent: str, entities: list, faq_item: dict) -> bool:
        """특허 취소/정지 관련 질문인지 확인."""
        keywords = ["특허취소", "특허정지", "특허 취소", "특허 정지",
                   "특허 취소", "설치특허 취소", "설치특허 정지"]
        query_lower = normalize_query(query)
        return any(kw in query_lower for kw in keywords)

    def is_price_or_amount(query: str, intent: str, entities: list, faq_item: dict) -> bool:
        """금액/과세 가격 관련 질문인지 확인."""
        keywords = ["금액", "가격", "과세가격", "산정", "평가", "계산",
                   "얼마", "비용", "원가", "가격결정", "가격산정"]
        query_lower = normalize_query(query)
        return any(kw in query_lower for kw in keywords)

    def is_simple_guidance(query: str, intent: str, entities: list, faq_item: dict) -> bool:
        """단순 안내 질문인지 확인."""
        keywords = ["어디", "누구", "언제", "뭐", "뭐지", "뜻", "정의",
                   "개념", "제도", "안내", "설명", "알려", "궁금"]
        query_lower = normalize_query(query)
        return any(kw in query_lower for kw in keywords)

    # 규칙 생성
    rules = [
        # CRITICAL 규칙들
        PolicyRule(
            rule_id="POL_001",
            name="개인정보 포함 검출",
            condition=has_pii,
            action="REJECT_AND_ESCALATE",
            risk_level=RiskLevel.CRITICAL,
            message_template="개인정보는 챗봇에 입력하지 않아 주세요.",
            escalation_target="customer_support",
        ),
        PolicyRule(
            rule_id="POL_002",
            name="전략물자 관련 질문",
            condition=has_strategic_goods,
            action="BLOCK_AND_ESCALATE",
            risk_level=RiskLevel.CRITICAL,
            message_template="전략물자 관련 사항은 전담 부서에 문의하셔야 합니다.",
            escalation_target="bonded_division",
        ),
        PolicyRule(
            rule_id="POL_003",
            name="밀수/부정행위 관련",
            condition=is_smuggling_related,
            action="ESCALATE_WITH_WARNING",
            risk_level=RiskLevel.CRITICAL,
            message_template="밀수 및 부정행위는 법적 처벌을 받을 수 있습니다. 세관(☎ 125)에 문의하시기 바랍니다.",
            escalation_target="local_customs",
        ),

        # HIGH 규칙들
        PolicyRule(
            rule_id="POL_004",
            name="세금/관세 계산",
            condition=is_tax_calculation,
            action="ADD_DISCLAIMER",
            risk_level=RiskLevel.HIGH,
            message_template="정확한 과세가격 계산은 관할 세관에 확인이 필요합니다.",
            escalation_target="local_customs",
        ),
        PolicyRule(
            rule_id="POL_005",
            name="벌칙/처벌 질문",
            condition=is_penalty_question,
            action="ADD_DISCLAIMER_AND_ESCALATE",
            risk_level=RiskLevel.HIGH,
            message_template="구체적인 처벌 규정은 법조인 또는 관세청에 문의하시기 바랍니다.",
            escalation_target="bonded_division",
        ),
        PolicyRule(
            rule_id="POL_006",
            name="금액/과세 가격 질문",
            condition=is_price_or_amount,
            action="ADD_DISCLAIMER",
            risk_level=RiskLevel.HIGH,
            message_template="정확한 금액은 관할 세관 확인이 필수입니다.",
            escalation_target="local_customs",
        ),
        PolicyRule(
            rule_id="POL_007",
            name="특허 취소/정지",
            condition=is_patent_termination,
            action="ADD_DISCLAIMER_AND_ESCALATE",
            risk_level=RiskLevel.HIGH,
            message_template="특허 취소/정지는 법적 자문이 필요합니다.",
            escalation_target="bonded_division",
        ),

        # MEDIUM 규칙들
        PolicyRule(
            rule_id="POL_008",
            name="법적 해석 요청",
            condition=is_legal_interpretation,
            action="ADD_DISCLAIMER",
            risk_level=RiskLevel.MEDIUM,
            message_template="법적 해석은 관세청 또는 법조인에 문의하시기 바랍니다.",
            escalation_target="bonded_division",
        ),
        PolicyRule(
            rule_id="POL_009",
            name="식품안전 관련",
            condition=is_food_safety,
            action="ADD_DISCLAIMER",
            risk_level=RiskLevel.MEDIUM,
            message_template="식품안전은 식약처(☎ 1577-1255) 확인이 필요합니다.",
            escalation_target="food_safety",
        ),

        # LOW 규칙
        PolicyRule(
            rule_id="POL_010",
            name="단순 안내",
            condition=is_simple_guidance,
            action="NO_ACTION",
            risk_level=RiskLevel.LOW,
            message_template="",
            escalation_target=None,
        ),
    ]

    return rules


# ============================================================================
# PolicyEngine Class
# ============================================================================

class PolicyEngine:
    """위험도 기반 답변 제어 엔진."""

    def __init__(self, rules_path: str = "data/escalation_rules.json"):
        """엔진 초기화.

        Args:
            rules_path: 에스컬레이션 규칙 JSON 파일 경로.
        """
        self.logger = logging.getLogger(__name__)
        self.rules_path = rules_path

        # 내장 규칙 로드
        self.builtin_rules = _build_builtin_rules()

        # 외부 규칙 로드 시도
        self.external_rules = self._load_external_rules()

        # 모든 규칙 병합
        self.all_rules = self.builtin_rules + self.external_rules

        # 로깅 디렉토리 설정
        self.audit_log_dir = Path("logs") / "policy"
        self.audit_log_dir.mkdir(parents=True, exist_ok=True)

    def _load_external_rules(self) -> list[PolicyRule]:
        """외부 JSON 파일에서 규칙을 로드한다.

        Returns:
            PolicyRule 객체 리스트.
        """
        try:
            if not os.path.exists(self.rules_path):
                self.logger.warning(f"외부 규칙 파일 없음: {self.rules_path}")
                return []

            data = load_json(self.rules_path)
            rules = data.get("rules", [])
            self.logger.info(f"외부 규칙 로드 완료: {len(rules)}개")
            # Note: 외부 규칙은 현재 condition 함수를 JSON에서 정의할 수 없으므로
            # 메타데이터만 로드. 실제 조건 평가는 builtin_rules로 수행.
            return []
        except Exception as e:
            self.logger.error(f"외부 규칙 로드 실패: {e}")
            return []

    def evaluate(
        self,
        query: str,
        intent: str = "",
        entities: Optional[list] = None,
        faq_item: Optional[dict] = None,
    ) -> PolicyDecision:
        """모든 규칙을 평가하고 정책 결정을 내린다.

        Args:
            query: 사용자 질문 문자열.
            intent: 질문 의도 (분류 결과).
            entities: 추출된 엔터티 리스트.
            faq_item: 매칭된 FAQ 항목.

        Returns:
            PolicyDecision 객체.
        """
        if entities is None:
            entities = []
        if faq_item is None:
            faq_item = {}

        applied_rules = []
        highest_risk = RiskLevel.LOW

        # 모든 규칙 평가
        for rule in self.all_rules:
            try:
                if rule.condition(query, intent, entities, faq_item):
                    applied_rules.append(rule)
                    if rule.risk_level > highest_risk:
                        highest_risk = rule.risk_level
            except Exception as e:
                self.logger.error(f"규칙 평가 실패 {rule.rule_id}: {e}")

        # 정책 결정 생성
        decision = PolicyDecision(
            risk_level=highest_risk,
            disclaimers=self._build_disclaimers(applied_rules, highest_risk),
            requires_escalation=self._should_escalate(applied_rules),
            escalation_target=self._get_escalation_target(applied_rules),
            escalation_sla_minutes=self._get_sla_minutes(applied_rules),
            applied_rules=applied_rules,
            audit_metadata={
                "query": query,
                "intent": intent,
                "timestamp": datetime.utcnow().isoformat(),
                "rule_ids": [r.rule_id for r in applied_rules],
            },
        )

        # 감사 로깅
        self.log_policy_decision(decision)

        return decision

    def _build_disclaimers(
        self,
        applied_rules: list[PolicyRule],
        highest_risk: RiskLevel,
    ) -> list[str]:
        """적용할 면책조항을 생성한다.

        Args:
            applied_rules: 적용된 규칙 리스트.
            highest_risk: 최고 위험도.

        Returns:
            면책조항 리스트.
        """
        disclaimers = []

        # 위험도 기반 면책조항 추가
        if highest_risk in KOREAN_DISCLAIMERS:
            disclaimer = KOREAN_DISCLAIMERS[highest_risk]
            if disclaimer:
                disclaimers.append(disclaimer)

        # 규칙별 맞춤 메시지 추가
        for rule in applied_rules:
            if rule.message_template and rule.message_template not in disclaimers:
                disclaimers.append(rule.message_template)

        return disclaimers

    def _should_escalate(self, applied_rules: list[PolicyRule]) -> bool:
        """에스컬레이션 필요 여부를 판단한다.

        Args:
            applied_rules: 적용된 규칙 리스트.

        Returns:
            에스컬레이션 필요 여부.
        """
        for rule in applied_rules:
            if "ESCALATE" in rule.action:
                return True
        return False

    def _get_escalation_target(self, applied_rules: list[PolicyRule]) -> Optional[str]:
        """에스켈레이션 대상을 결정한다.

        우선순위: CRITICAL > HIGH > MEDIUM
        같은 위험도 내에서: 첫 번째 규칙의 대상.

        Args:
            applied_rules: 적용된 규칙 리스트.

        Returns:
            에스켈레이션 대상 키.
        """
        # 위험도 순서로 규칙 정렬
        # key = (risk_score, -index), reverse=True이므로:
        # - risk_score가 클수록 앞에 오고 (CRITICAL 우선)
        # - 같은 risk_score에서는 -index가 클수록 앞에 오고, -index가 클수록 index가 작음
        # 따라서 같은 위험도 내에서는 첫 번째 규칙(index=0)이 우선적으로 선택됨
        sorted_rules = sorted(
            applied_rules,
            key=lambda r: (
                {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}[r.risk_level.value],
                -applied_rules.index(r),  # 같은 위험도 내에서 첫 번째 규칙 우선
            ),
            reverse=True,
        )

        for rule in sorted_rules:
            if rule.escalation_target:
                return rule.escalation_target

        return None

    def _get_sla_minutes(self, applied_rules: list[PolicyRule]) -> Optional[int]:
        """에스컬레이션 SLA를 가져온다.

        Args:
            applied_rules: 적용된 규칙 리스트.

        Returns:
            SLA 분(minutes).
        """
        target = self._get_escalation_target(applied_rules)
        if target and target in ESCALATION_TARGETS:
            return ESCALATION_TARGETS[target]["sla_minutes"]
        return None

    def get_disclaimer(self, risk_level: RiskLevel) -> str:
        """위험도에 해당하는 한글 면책조항을 반환한다.

        Args:
            risk_level: 위험도 레벨.

        Returns:
            면책조항 문자열.
        """
        return KOREAN_DISCLAIMERS.get(risk_level, "")

    def apply_answer_filter(self, answer: str, risk_level: RiskLevel) -> str:
        """답변에 적절한 면책조항/경고를 추가한다.

        Args:
            answer: 원본 답변 문자열.
            risk_level: 위험도 레벨.

        Returns:
            필터링된 답변 문자열.
        """
        if not answer:
            return answer

        disclaimer = self.get_disclaimer(risk_level)
        if not disclaimer:
            return answer

        # 답변 뒤에 면책조항 추가
        return f"{answer}\n\n[면책조항] {disclaimer}"

    def get_escalation_info(self, decision: PolicyDecision) -> dict:
        """에스컬레이션 정보를 반환한다.

        Args:
            decision: 정책 결정.

        Returns:
            에스컬레이션 정보 딕셔너리:
                {
                    "target": "bonded_division",
                    "department": "Bonded Industry Division",
                    "contact": "관세청 통관국 보세산업과",
                    "phone": "☎ 125",
                    "sla_minutes": 180,
                }
        """
        if not decision.escalation_target:
            return {}

        target_info = ESCALATION_TARGETS.get(
            decision.escalation_target,
            {},
        )

        return {
            "target": decision.escalation_target,
            "department": target_info.get("department", ""),
            "contact": target_info.get("contact", ""),
            "phone": target_info.get("phone", ""),
            "sla_minutes": target_info.get("sla_minutes", 0),
            "name": target_info.get("name", ""),
        }

    def log_policy_decision(self, decision: PolicyDecision) -> None:
        """정책 결정을 감사 로그에 기록한다.

        Args:
            decision: 정책 결정.
        """
        try:
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "risk_level": decision.risk_level.value,
                "requires_escalation": decision.requires_escalation,
                "escalation_target": decision.escalation_target,
                "applied_rules": [r.rule_id for r in decision.applied_rules],
                "metadata": decision.audit_metadata,
            }

            # 일일 로그 파일에 기록
            log_date = datetime.utcnow().strftime("%Y-%m-%d")
            log_file = self.audit_log_dir / f"policy_{log_date}.jsonl"

            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

            self.logger.debug(f"정책 결정 로깅 완료: {decision.risk_level.value}")
        except Exception as e:
            self.logger.error(f"정책 결정 로깅 실패: {e}")

    def get_all_rules(self) -> list[PolicyRule]:
        """모든 정책 규칙을 반환한다.

        Returns:
            PolicyRule 객체 리스트.
        """
        return self.all_rules

    def get_rules_by_risk_level(self, risk_level: RiskLevel) -> list[PolicyRule]:
        """특정 위험도의 규칙들을 반환한다.

        Args:
            risk_level: 위험도 레벨.

        Returns:
            해당 위험도의 PolicyRule 리스트.
        """
        return [r for r in self.all_rules if r.risk_level == risk_level]

    def should_escalate(self, decision: PolicyDecision) -> bool:
        """정책 결정이 에스컬레이션을 요구하는지 확인한다.

        Args:
            decision: 정책 결정.

        Returns:
            에스컬레이션 필요 여부.
        """
        return decision.requires_escalation

    def get_audit_log_summary(self, days: int = 7) -> dict:
        """최근 감사 로그 요약을 생성한다.

        Args:
            days: 조회할 일 수.

        Returns:
            요약 딕셔너리:
                {
                    "total_decisions": int,
                    "by_risk_level": {RiskLevel: count},
                    "escalations": int,
                    "top_rules": [(rule_id, count)],
                }
        """
        from datetime import timedelta

        summary = {
            "total_decisions": 0,
            "by_risk_level": {rl.value: 0 for rl in RiskLevel},
            "escalations": 0,
            "top_rules": [],
            "period_days": days,
        }

        rule_counts = {}
        (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

        try:
            for log_file in self.audit_log_dir.glob("policy_*.jsonl"):
                if log_file.stat().st_mtime < (datetime.utcnow() - timedelta(days=days)).timestamp():
                    continue

                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            summary["total_decisions"] += 1
                            risk_level = entry.get("risk_level", "LOW")
                            summary["by_risk_level"][risk_level] += 1
                            if entry.get("requires_escalation"):
                                summary["escalations"] += 1

                            for rule_id in entry.get("applied_rules", []):
                                rule_counts[rule_id] = rule_counts.get(rule_id, 0) + 1
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            self.logger.error(f"감사 로그 요약 생성 실패: {e}")

        # 상위 규칙 정렬
        summary["top_rules"] = sorted(
            rule_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:10]

        return summary


# ============================================================================
# Module-level functions for convenience
# ============================================================================

_policy_engine_singleton = None


def get_policy_engine() -> PolicyEngine:
    """전역 PolicyEngine 인스턴스를 반환한다 (싱글톤)."""
    global _policy_engine_singleton
    if _policy_engine_singleton is None:
        _policy_engine_singleton = PolicyEngine()
    return _policy_engine_singleton


def evaluate_policy(intent_id: str, query: str, entities: dict = None, faq_item: dict = None) -> dict:
    """정책을 평가한다.

    Args:
        intent_id: 의도 ID
        query: 사용자 질문
        entities: 추출된 엔티티 (선택)
        faq_item: FAQ 항목 (선택)

    Returns:
        {
            "risk_level": str,  # "low", "medium", "high", "critical"
            "escalation_trigger": bool,
            "escalation_target": str or None,
            "requires_escalation": bool,
            "disclaimers": list[str],
        }
    """
    engine = get_policy_engine()
    decision = engine.evaluate(
        query=query,
        intent=intent_id,
        entities=entities or [],
        faq_item=faq_item or {}
    )

    return {
        "risk_level": decision.risk_level.value.lower(),
        "escalation_trigger": decision.requires_escalation,
        "escalation_target": decision.escalation_target,
        "requires_escalation": decision.requires_escalation,
        "disclaimers": decision.disclaimers,
    }


def apply_answer_filter(answer: str, risk_level: str) -> str:
    """답변에 필터를 적용한다 (면책조항 추가).

    Args:
        answer: 원본 답변
        risk_level: 위험도 레벨 ("low", "medium", "high", "critical")

    Returns:
        필터링된 답변
    """
    engine = get_policy_engine()

    # risk_level을 RiskLevel enum으로 변환
    level_map = {
        "low": RiskLevel.LOW,
        "medium": RiskLevel.MEDIUM,
        "high": RiskLevel.HIGH,
        "critical": RiskLevel.CRITICAL,
    }
    level = level_map.get(risk_level.lower(), RiskLevel.LOW)

    return engine.apply_answer_filter(answer, level)


def should_escalate(risk_level: str, escalation_trigger: bool, query: str) -> bool:
    """에스컬레이션 여부를 판단한다.

    Args:
        risk_level: 위험도 레벨
        escalation_trigger: 에스컬레이션 트리거 플래그
        query: 사용자 질문

    Returns:
        True if should escalate
    """
    # 직접 판단 로직 (PolicyEngine의 _should_escalate와 유사)
    if escalation_trigger:
        return True

    level_map = {
        "low": RiskLevel.LOW,
        "medium": RiskLevel.MEDIUM,
        "high": RiskLevel.HIGH,
        "critical": RiskLevel.CRITICAL,
    }
    level = level_map.get(risk_level.lower(), RiskLevel.LOW)

    if level >= RiskLevel.HIGH:
        return True

    escalation_keywords = [
        "소송", "법적", "처벌", "벌칙", "형사", "민사", "손해배상",
        "긴급", "위급", "응급",
    ]
    query_lower = normalize_query(query)
    if any(kw in query_lower for kw in escalation_keywords):
        return True

    return False
