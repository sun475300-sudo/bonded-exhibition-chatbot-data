import pytest
from src.pii_redactor import PIIRedactor
from src.prompt_defender import PromptDefender

def test_new_phone_format_redaction():
    redactor = PIIRedactor(enabled=True)
    # 붙어있는 010 번호 테스트
    assert "[REDACTED_PHONE]" in redactor.redact("문의사항은 01055554444로 주세요.")

def test_sql_injection_edge_case():
    defender = PromptDefender(enabled=True)
    # 명확한 SQL 인젝션 공격
    assert defender.is_malicious("SELECT * FROM users; DROP TABLE products;") is True
    # 자연어 질문 (오탐 방지 확인)
    assert defender.is_malicious("how to drop a database table safely?") is False

def test_llm_fallback_availability():
    from src.llm_fallback import is_llm_available
    # API 키가 없으므로 기본적으로 False여야 함
    assert is_llm_available() is False
