"""네이버 톡톡 챗봇 웹훅 어댑터.

네이버 톡톡(Naver TalkTalk) 웹훅 요청을 받아
보세전시장 챗봇 API로 변환하는 어댑터.

사용법:
    web_server.py에서 엔드포인트를 등록하여 사용.
    네이버 톡톡 웹훅 URL: http://서버주소:8080/api/naver/webhook
"""

# 네이버 톡톡 API 상수
NAVER_API_VERSION = "v1"
NAVER_TEXT_LIMIT = 1000
NAVER_BUTTON_LABEL_LIMIT = 18
NAVER_BUTTON_MAX_COUNT = 10
NAVER_CAROUSEL_MAX_ITEMS = 10
NAVER_COMPOSITE_TITLE_LIMIT = 36
NAVER_COMPOSITE_DESCRIPTION_LIMIT = 128

# 이벤트 타입
EVENT_SEND = "send"
EVENT_OPEN = "open"
EVENT_LEAVE = "leave"
EVENT_FRIEND = "friend"

SUPPORTED_EVENTS = {EVENT_SEND, EVENT_OPEN, EVENT_LEAVE, EVENT_FRIEND}


class NaverTalkTalkAdapter:
    """네이버 톡톡 웹훅 어댑터.

    네이버 톡톡의 웹훅 페이로드를 파싱하고
    응답 메시지를 네이버 톡톡 API 형식으로 변환한다.
    """

    @staticmethod
    def parse_webhook(data: dict) -> dict:
        """네이버 톡톡 웹훅 페이로드를 표준 딕셔너리로 파싱한다.

        Args:
            data: 네이버 톡톡 웹훅 JSON

        Returns:
            파싱된 정보 dict:
            - event: 이벤트 타입 (send, open, leave, friend)
            - user_id: 사용자 식별자
            - text: 사용자 메시지 텍스트 (send 이벤트)
            - image_url: 이미지 URL (이미지 메시지인 경우)
            - options: 기타 옵션 정보
        """
        if not data or not isinstance(data, dict):
            return {
                "event": "",
                "user_id": "",
                "text": "",
                "image_url": None,
                "options": {},
            }

        event = data.get("event", "")
        user_id = data.get("user", "")

        text = ""
        image_url = None
        options = {}

        if event == EVENT_SEND:
            text_content = data.get("textContent", {})
            text = text_content.get("text", "").strip() if text_content else ""

            image_content = data.get("imageContent", {})
            if image_content:
                image_url = image_content.get("imageUrl")

            options = data.get("options", {})

        elif event == EVENT_FRIEND:
            options = {"set_on": data.get("options", {}).get("set", "")}

        return {
            "event": event,
            "user_id": user_id,
            "text": text,
            "image_url": image_url,
            "options": options,
        }

    @staticmethod
    def truncate_text(text: str, limit: int = NAVER_TEXT_LIMIT) -> str:
        """텍스트를 지정된 길이로 잘라낸다. 초과 시 말줄임표를 붙인다.

        Args:
            text: 원본 텍스트
            limit: 최대 글자 수 (기본 1000)

        Returns:
            잘라낸 텍스트
        """
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    @staticmethod
    def format_text_response(text: str) -> dict:
        """네이버 톡톡 텍스트 메시지 형식을 생성한다.

        Args:
            text: 응답 텍스트

        Returns:
            네이버 톡톡 텍스트 응답 dict
        """
        return {
            "event": "send",
            "textContent": {
                "text": NaverTalkTalkAdapter.truncate_text(text),
            },
        }

    @staticmethod
    def format_button_response(text: str, buttons: list[dict]) -> dict:
        """텍스트와 퀵리플라이 버튼이 포함된 응답을 생성한다.

        Args:
            text: 응답 텍스트
            buttons: 버튼 목록. 각 버튼은 {"label": "...", "value": "..."} 형식.
                     value가 URL이면 webLink, 아니면 text 타입으로 생성.

        Returns:
            네이버 톡톡 버튼 응답 dict
        """
        quick_buttons = []
        for btn in buttons[:NAVER_BUTTON_MAX_COUNT]:
            label = btn.get("label", "")[:NAVER_BUTTON_LABEL_LIMIT]
            value = btn.get("value", label)

            if isinstance(value, str) and value.startswith(("http://", "https://")):
                quick_buttons.append({
                    "type": "webLink",
                    "data": {
                        "label": label,
                        "url": value,
                    },
                })
            else:
                quick_buttons.append({
                    "type": "text",
                    "data": {
                        "label": label,
                        "code": value,
                    },
                })

        return {
            "event": "send",
            "textContent": {
                "text": NaverTalkTalkAdapter.truncate_text(text),
                "quickReply": {
                    "buttonList": quick_buttons,
                },
            },
        }

    @staticmethod
    def format_composite_response(
        title: str,
        description: str,
        buttons: list[dict],
        image_url: str | None = None,
    ) -> dict:
        """컴포지트 메시지를 생성한다.

        Args:
            title: 카드 제목
            description: 카드 설명
            buttons: 버튼 목록 [{"label": "...", "value": "..."}]
            image_url: 썸네일 이미지 URL (선택)

        Returns:
            네이버 톡톡 compositeContent 응답 dict
        """
        truncated_title = NaverTalkTalkAdapter.truncate_text(
            title, NAVER_COMPOSITE_TITLE_LIMIT
        )
        truncated_desc = NaverTalkTalkAdapter.truncate_text(
            description, NAVER_COMPOSITE_DESCRIPTION_LIMIT
        )

        button_list = []
        for btn in buttons[:NAVER_BUTTON_MAX_COUNT]:
            label = btn.get("label", "")[:NAVER_BUTTON_LABEL_LIMIT]
            value = btn.get("value", label)

            if isinstance(value, str) and value.startswith(("http://", "https://")):
                button_list.append({
                    "type": "webLink",
                    "data": {
                        "label": label,
                        "url": value,
                    },
                })
            else:
                button_list.append({
                    "type": "text",
                    "data": {
                        "label": label,
                        "code": value,
                    },
                })

        composite = {
            "title": truncated_title,
            "description": truncated_desc,
            "buttonList": button_list,
        }

        if image_url:
            composite["image"] = {
                "imageUrl": image_url,
            }

        return {
            "event": "send",
            "compositeContent": {
                "compositeList": [
                    {
                        "title": truncated_title,
                        "description": truncated_desc,
                        "buttonList": button_list,
                        "image": {"imageUrl": image_url} if image_url else None,
                    }
                ],
            },
        }

    @staticmethod
    def format_carousel(items: list[dict]) -> dict:
        """FAQ 항목 목록을 네이버 톡톡 캐러셀 형식으로 변환한다.

        Args:
            items: FAQ 항목 리스트. 각 항목에 question, answer, category(선택) 포함.

        Returns:
            네이버 톡톡 compositeContent 캐러셀 응답 dict
        """
        composite_list = []
        for item in items[:NAVER_CAROUSEL_MAX_ITEMS]:
            question = item.get("question", "")
            answer = item.get("answer", "")
            category = item.get("category", "")
            image_url = item.get("image_url")

            title = NaverTalkTalkAdapter.truncate_text(
                question, NAVER_COMPOSITE_TITLE_LIMIT
            )
            description = NaverTalkTalkAdapter.truncate_text(
                answer, NAVER_COMPOSITE_DESCRIPTION_LIMIT
            )

            button_list = [
                {
                    "type": "text",
                    "data": {
                        "label": "자세히 보기",
                        "code": question,
                    },
                }
            ]
            if category:
                button_list.append({
                    "type": "text",
                    "data": {
                        "label": category[:NAVER_BUTTON_LABEL_LIMIT],
                        "code": category,
                    },
                })

            card = {
                "title": title,
                "description": description,
                "buttonList": button_list,
            }
            if image_url:
                card["image"] = {"imageUrl": image_url}

            composite_list.append(card)

        return {
            "event": "send",
            "compositeContent": {
                "compositeList": composite_list,
            },
        }

    @staticmethod
    def build_response(event: str, response_data: dict) -> dict:
        """이벤트 타입에 따라 완성된 네이버 톡톡 응답을 조합한다.

        Args:
            event: 이벤트 타입 (send, open 등)
            response_data: 응답 데이터 dict. 키:
                - text: 텍스트 응답 (필수)
                - buttons: 버튼 목록 (선택)
                - user_id: 응답 대상 사용자 (필수)

        Returns:
            네이버 톡톡 전송용 응답 JSON dict
        """
        user_id = response_data.get("user_id", "")
        text = response_data.get("text", "")
        buttons = response_data.get("buttons")

        if buttons:
            message = NaverTalkTalkAdapter.format_button_response(text, buttons)
        else:
            message = NaverTalkTalkAdapter.format_text_response(text)

        message["user"] = user_id
        return message
