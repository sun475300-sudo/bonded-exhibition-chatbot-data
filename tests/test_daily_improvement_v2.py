import pytest
from src.chatbot import BondedExhibitionChatbot

def test_chatbot_initialization():
    """챗봇 초기화 테스트"""
    chatbot = BondedExhibitionChatbot()
    assert chatbot is not None
    assert hasattr(chatbot, 'faq_items')
    assert len(chatbot.faq_items) > 0

def test_query_normalization():
    """질문 정규화 테스트"""
    chatbot = BondedExhibitionChatbot()
    query = "  보세구역  설정  방법은?  "
    # normalize_query는 내부적으로 호출되거나 유틸리티에서 가져옴
    # 여기서는 process_query를 통해 간접적으로 확인
    response = chatbot.process_query(query)
    assert response is not None

def test_intent_classification_exists():
    """의도 분류기 존재 확인"""
    chatbot = BondedExhibitionChatbot()
    assert chatbot.intent_classifier is not None

def test_policy_engine_exists():
    """정책 엔진 존재 확인"""
    chatbot = BondedExhibitionChatbot()
    assert chatbot.policy_engine is not None

def test_persona_greeting():
    """페르소나 인사말 확인"""
    chatbot = BondedExhibitionChatbot()
    persona = chatbot.get_persona()
    assert isinstance(persona, str)
