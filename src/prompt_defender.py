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

        # XSS, SQLi, 시스템 프롬프트 유출 시도 패턴.
        # 주의: 이전 버전은 alternation 메타문자 `|` 를 `\|` 로 잘못 이스케이프하여
        # 핵심 SQLi 패턴이 전혀 매칭되지 않았다. 또한 자연어 질의("how to drop a
        # database table?") 가 오탐되지 않도록 SQL 구문 구조(키워드 사이 토큰을
        # 식별자 한두 개로 제한)를 활용한다.
        self.blacklist_patterns = [
            re.compile(r'(?i)<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>'),  # XSS
            # SELECT ... FROM <ident>: 사이에 식별자/문장부호만 허용
            re.compile(r'(?i)\bselect\b[\s\w*,.`"\'()]+\bfrom\b\s+[`"\']?\w'),
            # DROP TABLE <ident> / DROP DATABASE <ident> (관사 "a/an/the" 직접 인접 시 제외)
            re.compile(
                r'(?i)\bdrop\s+(?:table|database|schema|view|index)\s+(?!a\b|an\b|the\b)[`"\']?\w'
            ),
            re.compile(r'(?i)\btruncate\s+table\s+\w'),
            re.compile(r'(?i)\binsert\s+into\s+\w'),
            re.compile(r'(?i)\bdelete\s+from\s+\w'),
            re.compile(r'(?i)\bupdate\s+\w+\s+set\b'),
            re.compile(r'(?i)\bunion\s+(?:all\s+)?select\b'),
            # SQL 주석 (라인 끝 또는 공백 연속): "WHERE 1=1 --"
            re.compile(r'--\s*(?:$|\r|\n|\s{2,})'),
            re.compile(  # LLM 프롬프트 인젝션
                r'(?i)(ignore previous instructions|너의 지시사항|이전 프롬프트 무시'
                r'|system prompt|jailbreak|DAN\b|개발자 모드)'
            ),
            re.compile(r'(?i)(<\s*(?:iframe|object|embed|applet|meta)[^>]*>)'),  # HTML injection
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
