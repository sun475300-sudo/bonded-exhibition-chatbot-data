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

        # 우선순위가 높은 패턴부터 평가하기 위해 순서대로 정의
        self.patterns = {
            "jumin": re.compile(r'\b\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[1,2][0-9]|3[0,1])[- ]?[1-4]\d{6}\b'),
            "email": re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'),
            "phone": re.compile(r'\b01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}\b|\b0[2-9]\d{0,1}[-.\s]?\d{3,4}[-.\s]?\d{4}\b'),
            "credit_card": re.compile(r'\b(?:[0-9]{4}[-.\s]?){3}[0-9]{3,4}\b')
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

        # 전화번호 패턴 처리 (경계 조건 완화)
        phone_patterns = [
            re.compile(r'010\d{8}'),
            re.compile(r'01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}'),
            re.compile(r'0[2-9]\d{0,1}[-.\s]?\d{3,4}[-.\s]?\d{4}')
        ]

        # 주민번호
        redacted_text = self.patterns["jumin"].sub("[REDACTED_JUMIN]", redacted_text)
        # 이메일
        redacted_text = self.patterns["email"].sub("[REDACTED_EMAIL]", redacted_text)
        # 전화번호
        for p in phone_patterns:
            redacted_text = p.sub("[REDACTED_PHONE]", redacted_text)
        # 카드번호
        redacted_text = self.patterns["credit_card"].sub("[REDACTED_CREDIT_CARD]", redacted_text)

        return redacted_text
