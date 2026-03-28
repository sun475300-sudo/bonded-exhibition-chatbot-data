"""카카오톡 챗봇 스킬서버 어댑터.

카카오 i 오픈빌더의 스킬(Skill) 요청을 받아
보세전시장 챗봇 API로 변환하는 어댑터.

사용법:
    web_server.py에 블루프린트 등록하여 사용.
    카카오 오픈빌더 스킬 URL: http://서버주소:8080/kakao/chat
"""

from flask import Blueprint, request, jsonify

kakao_bp = Blueprint("kakao", __name__, url_prefix="/kakao")

# 카카오 오픈빌더 텍스트 제한
SIMPLE_TEXT_LIMIT = 1000
CARD_DESCRIPTION_LIMIT = 400


def _truncate(text: str, limit: int) -> str:
    """텍스트를 지정된 길이로 잘라낸다. 초과 시 말줄임표를 붙인다."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


# 카카오 오픈빌더 응답 포맷
def _build_kakao_response(text: str, quick_replies: list[dict] | None = None) -> dict:
    """카카오 i 오픈빌더 응답 JSON을 생성한다."""
    response = {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": _truncate(text, SIMPLE_TEXT_LIMIT)
                    }
                }
            ]
        }
    }

    if quick_replies:
        response["template"]["quickReplies"] = quick_replies

    return response


def _build_quick_replies(faq_suggestions: list[str]) -> list[dict]:
    """바로가기 버튼 목록을 생성한다."""
    return [
        {"messageText": q, "action": "message", "label": q[:20]}
        for q in faq_suggestions[:5]
    ]


def format_simple_text(answer: str) -> dict:
    """카카오 simpleText 블록을 생성한다.

    Args:
        answer: 응답 텍스트

    Returns:
        카카오 simpleText 출력 블록
    """
    return {
        "simpleText": {
            "text": _truncate(answer, SIMPLE_TEXT_LIMIT)
        }
    }


def format_quick_replies(categories: list[str]) -> list[dict]:
    """FAQ 카테고리 목록에서 카카오 quickReply 버튼을 생성한다.

    Args:
        categories: 카테고리 이름 목록

    Returns:
        카카오 quickReply 버튼 리스트 (최대 5개)
    """
    return [
        {
            "messageText": cat,
            "action": "message",
            "label": cat[:14],
        }
        for cat in categories[:5]
    ]


def format_carousel(faq_items: list[dict]) -> dict:
    """FAQ 항목 목록을 카카오 캐러셀 카드 형식으로 변환한다.

    Args:
        faq_items: FAQ 항목 리스트 (각 항목에 question, answer, category 포함)

    Returns:
        카카오 carousel 출력 블록
    """
    cards = []
    for item in faq_items:
        question = item.get("question", "")
        answer = item.get("answer", "")
        category = item.get("category", "")

        card = {
            "title": _truncate(question, 40),
            "description": _truncate(answer, CARD_DESCRIPTION_LIMIT),
            "buttons": [
                {
                    "action": "message",
                    "label": "자세히 보기",
                    "messageText": question,
                }
            ],
        }
        if category:
            card["buttons"].append({
                "action": "message",
                "label": category[:14],
                "messageText": category,
            })
        cards.append(card)

    return {
        "carousel": {
            "type": "basicCard",
            "items": cards,
        }
    }


def format_escalation_card(escalation_info: dict) -> dict:
    """에스컬레이션 정보를 카카오 카드(전화/링크 버튼 포함)로 변환한다.

    Args:
        escalation_info: 에스컬레이션 연락처 정보
            - name: 담당부서 이름
            - phone: 전화번호
            - url: 웹 링크 (선택)
            - description: 안내 문구 (선택)

    Returns:
        카카오 basicCard 출력 블록
    """
    name = escalation_info.get("name", "고객센터")
    phone = escalation_info.get("phone", "")
    url = escalation_info.get("url", "")
    description = escalation_info.get("description", "담당 부서로 연결해 드리겠습니다.")

    buttons = []
    if phone:
        buttons.append({
            "action": "phone",
            "label": f"전화 연결",
            "phoneNumber": phone,
        })
    if url:
        buttons.append({
            "action": "webLink",
            "label": "웹사이트 바로가기",
            "webLinkUrl": url,
        })

    card = {
        "basicCard": {
            "title": name,
            "description": _truncate(description, CARD_DESCRIPTION_LIMIT),
            "buttons": buttons,
        }
    }
    return card


def parse_kakao_request(data: dict) -> dict:
    """카카오 i 오픈빌더 요청을 파싱하여 표준 포맷으로 변환한다.

    Args:
        data: 카카오 요청 JSON

    Returns:
        파싱된 요청 정보 dict:
        - utterance: 사용자 발화
        - user_id: 사용자 ID
        - bot_id: 봇 ID
        - action_name: 액션 이름
    """
    user_request = data.get("userRequest", {})
    utterance = user_request.get("utterance", "").strip()
    user_id = user_request.get("user", {}).get("id", "")
    bot = data.get("bot", {})
    bot_id = bot.get("id", "")
    action = data.get("action", {})
    action_name = action.get("name", "")

    return {
        "utterance": utterance,
        "user_id": user_id,
        "bot_id": bot_id,
        "action_name": action_name,
    }


def build_skill_response(outputs: list[dict], quick_replies: list[dict] | None = None) -> dict:
    """카카오 i 오픈빌더 스킬 응답을 조합한다.

    Args:
        outputs: 출력 블록 리스트 (simpleText, basicCard, carousel 등)
        quick_replies: 바로가기 버튼 리스트 (선택)

    Returns:
        카카오 스킬 응답 JSON
    """
    response = {
        "version": "2.0",
        "template": {
            "outputs": outputs,
        }
    }
    if quick_replies:
        response["template"]["quickReplies"] = quick_replies
    return response


def init_kakao_routes(chatbot, chat_logger=None):
    """카카오톡 라우트를 초기화한다.

    Args:
        chatbot: BondedExhibitionChatbot 인스턴스
        chat_logger: ChatLogger 인스턴스 (선택)
    """

    @kakao_bp.route("/chat", methods=["POST"])
    def kakao_chat():
        """카카오 오픈빌더 스킬 요청을 처리한다.

        카카오 요청 형식:
        {
            "userRequest": {
                "utterance": "사용자 질문",
                "user": {"id": "..."}
            },
            "bot": {"id": "..."},
            "action": {"name": "..."}
        }
        """
        data = request.get_json(silent=True)
        if not data:
            return jsonify(_build_kakao_response("요청을 처리할 수 없습니다.")), 200

        parsed = parse_kakao_request(data)
        utterance = parsed["utterance"]

        if not utterance:
            return jsonify(_build_kakao_response(
                "질문을 입력해 주세요.\n\n"
                "예: 보세전시장이 무엇인가요?"
            )), 200

        # 챗봇 응답 생성
        answer = chatbot.process_query(utterance)

        # 로깅
        if chat_logger:
            try:
                from src.classifier import classify_query
                categories = classify_query(utterance)
                faq_match = chatbot.find_matching_faq(utterance, categories[0] if categories else "GENERAL")
                chat_logger.log_query(
                    query=utterance,
                    category=categories[0] if categories else "GENERAL",
                    faq_id=faq_match.get("id") if faq_match else None,
                    is_escalation=False,
                    response_preview=answer[:200],
                )
            except Exception:
                pass

        # 바로가기 버튼 추가
        quick_replies = _build_quick_replies([
            "보세전시장이란?",
            "물품 반입 절차",
            "현장 판매 가능?",
            "문의처 안내",
        ])

        return jsonify(_build_kakao_response(answer, quick_replies)), 200

    @kakao_bp.route("/welcome", methods=["POST"])
    def kakao_welcome():
        """카카오 오픈빌더 웰컴 블록 응답."""
        persona = chatbot.get_persona()
        quick_replies = _build_quick_replies([
            "보세전시장이란?",
            "물품 반입/반출",
            "현장 판매 가능?",
            "견본품 반출",
            "문의처 안내",
        ])
        return jsonify(_build_kakao_response(persona, quick_replies)), 200

    return kakao_bp
