"""Prompt Injection Defender 모듈 (Phase 63).

사용자의 입력에서 SQL 인젝션, XSS(크로스 사이트 스크립팅), 
또는 LLM 시스템 프롬프트를 탈취하려는 시도를 감지하여 차단합니다.
"""
from __future__ import annotations

import re


class PromptDefender:
    """악의적인 사용자 입력을 감지하고 차단하는 방어 모듈."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

        # XSS, SQLi, 시스템 프롬프트 유출 시도 패턴
        self.blacklist_patterns = [
            re.compile(r'(?i)<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>'),  # XSS
            re.compile(r'(?i)\b(?:select|union)\b\s+[\w*,\s]+\bfrom\b'),  # SELECT/UNION ... FROM
            re.compile(r'(?i)\b(?:drop|truncate|alter|create)\s+(?:table|database|index|view|schema)\b'),  # DDL
            re.compile(r'(?i)\binsert\s+into\b|\bupdate\s+\w+\s+set\b|\bdelete\s+from\b|\bexec(?:ute)?\s*\('),  # DML/exec
            re.compile(r"(?i)('|\")\s*or\s+\1?\s*\d+\s*=\s*\d+|\b\d+\s*=\s*\d+\s*--|--\s*$|/\*.*?\*/|;\s*--"),  # tautology / 주석 인젝션
            re.compile(r'(?i)(ignore previous instructions|너의 지시사항|이전 프롬프트 무시|system prompt|jailbreak|\bDAN\b|개발자 모드)'),  # LLM Prompt Injection
            re.compile(r'(?i)<\s*(?:iframe|object|embed|applet|meta)[^>]*>'),  # HTML injection
        ]

    def is_malicious(self, text: str) -> bool:
        """입력 문자열이 악의적인 패턴을 포함하는지 확인한다.

        Args:
            text: 사용자 입력 문자열

        Returns:
            악의적이면 True, 안전하면 False
        """
        if not self.enabled or not text or not isinstance(text, str):
            return False

        for pattern in self.blacklist_patterns:
            if pattern.search(text):
                return True

        return False

    def sanitize(self, text: str) -> str:
        """기본적인 특수 기호 이스케이프 (만약 필터링만 하지 않고 원문을 변형할 경우)."""
        if not text:
            return ""
        # 챗봇 특성상 꺾쇠 등은 의도적으로 입력했을 수도 있으므로, 방어는 is_malicious로 거절 처리하는 것을 권장.
        return text.replace("<", "&lt;").replace(">", "&gt;")
