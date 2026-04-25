"""PII Redactor 모듈 (Phase 62).

사용자의 입력에서 식별 가능한 민감 개인정보(PII)를 자동으로 감지하고,
시스템 로그 및 처리에 노출되기 전 안전하게 마스킹 처리합니다.
"""
from __future__ import annotations

import re


class PIIRedactor:
    """개인 식별 정보(PII) 난독화 엔진."""

    def __init__(self, enabled: bool = True):
        """초기화.

        Args:
            enabled: PII 모듈 활성화 여부
        """
        self.enabled = enabled

        # 우선순위가 높은 패턴부터 평가하기 위해 순서대로 정의.
        # 한글이 인접해도 매칭되도록 \b 대신 (?<!\d) / (?!\d) 사용.
        self.patterns = {
            "jumin": re.compile(r'(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])[- ]?[1-4]\d{6}(?!\d)'),
            "email": re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'),
            "phone": re.compile(
                r'(?<!\d)01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)'
                r'|(?<!\d)0[2-9]\d{0,1}[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)'
            ),
            "credit_card": re.compile(r'(?<!\d)(?:[0-9]{4}[-.\s]?){3}[0-9]{3,4}(?!\d)'),
        }

    def redact(self, text: str) -> str:
        """입력 문자열에서 개인정보를 스캔하고 치환한다.

        Args:
            text: 원본 문자열

        Returns:
            마스킹 처리된 안전한 문자열
        """
        if not self.enabled or not text or not isinstance(text, str):
            return text

        redacted_text = text
        for pii_type, pattern in self.patterns.items():
            replacement = f"[REDACTED_{pii_type.upper()}]"
            redacted_text = pattern.sub(replacement, redacted_text)

        return redacted_text
