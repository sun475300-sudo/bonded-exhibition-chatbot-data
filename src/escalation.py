"""에스컬레이션 판단 모듈.

사용자 질문이 사람 상담으로 넘겨야 하는 경우를 판단한다.
"""

from src.utils import load_json


def load_escalation_rules() -> list[dict]:
    """에스컬레이션 규칙을 로드한다."""
    data = load_json("data/escalation_rules.json")
    return data.get("rules", [])


def check_escalation(query: str) -> dict | None:
    """사용자 질문이 에스컬레이션 대상인지 확인한다.

    Args:
        query: 사용자 질문 문자열

    Returns:
        매칭된 에스컬레이션 규칙 dict 또는 None
    """
    rules = load_escalation_rules()
    query_lower = query.lower()

    matched_rules = []
    for rule in rules:
        keywords = rule.get("keywords", [])
        match_count = sum(1 for kw in keywords if kw.lower() in query_lower)
        if match_count > 0:
            matched_rules.append((match_count, rule))

    if not matched_rules:
        return None

    matched_rules.sort(key=lambda x: x[0], reverse=True)
    return matched_rules[0][1]


def get_escalation_contact(rule: dict) -> dict:
    """에스컬레이션 규칙에 해당하는 연락처 정보를 반환한다."""
    config = load_json("config/chatbot_config.json")
    contacts = config.get("contacts", {})
    target = rule.get("target", "customer_support")
    return contacts.get(target, contacts.get("customer_support", {}))
