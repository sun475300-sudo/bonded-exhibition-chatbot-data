"""에스컬레이션 판단 모듈.

사용자 질문이 사람 상담으로 넘겨야 하는 경우를 판단한다.
규칙은 초기화 시 1회 로드하여 캐싱한다.
"""

from src.utils import load_json, normalize_query

_escalation_rules_cache = None
_contacts_cache = None


def _load_rules() -> list[dict]:
    """에스컬레이션 규칙을 로드하고 캐싱한다."""
    global _escalation_rules_cache
    if _escalation_rules_cache is None:
        data = load_json("data/escalation_rules.json")
        _escalation_rules_cache = data.get("rules", [])
    return _escalation_rules_cache


def _load_contacts() -> dict:
    """연락처 정보를 로드하고 캐싱한다."""
    global _contacts_cache
    if _contacts_cache is None:
        config = load_json("config/chatbot_config.json")
        _contacts_cache = config.get("contacts", {})
    return _contacts_cache


def check_escalation(query: str) -> dict | None:
    """사용자 질문이 에스컬레이션 대상인지 확인한다."""
    if not query:
        return None

    rules = _load_rules()
    query_lower = normalize_query(query)

    matched_rules = []
    for rule in rules:
        keywords = rule.get("keywords", [])
        if not isinstance(keywords, list):
            continue
        match_count = sum(1 for kw in keywords if kw.lower() in query_lower)
        if match_count > 0:
            matched_rules.append((match_count, rule))

    if not matched_rules:
        return None

    matched_rules.sort(key=lambda x: x[0], reverse=True)
    return matched_rules[0][1]


def get_escalation_contact(rule: dict) -> dict:
    """에스컬레이션 규칙에 해당하는 연락처 정보를 반환한다."""
    contacts = _load_contacts()
    target = rule.get("target", "customer_support")
    return contacts.get(target, contacts.get("customer_support", {}))
