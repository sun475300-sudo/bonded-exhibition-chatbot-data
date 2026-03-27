"""답변 생성기 모듈.

템플릿 기반으로 구조화된 답변을 조립한다.
면책 문구는 이 모듈에서 단일 관리한다.
"""

DISCLAIMER_TEXTS = [
    "본 답변은 일반적인 안내용 설명이며, 구체적인 사실관계에 따라 달라질 수 있습니다.",
    "최종 처리는 관할 세관 또는 해당 소관기관 확인이 필요합니다.",
]


def build_response(
    topic: str,
    conclusion: str,
    explanation: list[str],
    legal_basis: list[str],
    confirmation_items: list[str] | None = None,
    is_escalation: bool = False,
    escalation_message: str = "",
) -> str:
    """구조화된 답변 문자열을 생성한다."""
    parts = []

    parts.append(f"문의하신 내용은 [{topic}]에 관한 사항입니다.")
    parts.append("")

    if conclusion:
        parts.append("결론:")
        parts.append(f"- {conclusion}")
        parts.append("")

    if explanation:
        parts.append("설명:")
        for i, item in enumerate(explanation, 1):
            parts.append(f"{i}. {item}")
        parts.append("")

    if confirmation_items:
        parts.append("민원인이 확인할 사항:")
        for item in confirmation_items:
            parts.append(f"- {item}")
        parts.append("")

    if legal_basis:
        parts.append("근거:")
        for basis in legal_basis:
            parts.append(f"- {basis}")
        parts.append("")

    if is_escalation and escalation_message:
        parts.append("추가 안내:")
        parts.append(f"- {escalation_message}")
        parts.append("")

    parts.append("안내:")
    for text in DISCLAIMER_TEXTS:
        parts.append(f"- {text}")

    return "\n".join(parts)


def build_unknown_response() -> str:
    """매칭되는 FAQ가 없을 때의 기본 답변을 생성한다."""
    parts = [
        "현재 확인한 공식 자료만으로는 단정하기 어렵습니다.",
        "",
        "구체적인 물품 성질, 반입 목적, 판매 여부, 행사 방식, "
        "신고 내용에 따라 달라질 수 있습니다.",
        "",
        "추가 안내:",
        "- 관세청 고객지원센터(125)로 문의하시거나,",
        "- 관할 세관에 직접 확인하시기 바랍니다.",
        "",
        "안내:",
    ]
    for text in DISCLAIMER_TEXTS:
        parts.append(f"- {text}")

    return "\n".join(parts)
