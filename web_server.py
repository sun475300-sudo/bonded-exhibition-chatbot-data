"""보세전시장 민원응대 챗봇 웹 서버.

Flask 기반 REST API + 웹 UI를 제공한다.

사용법:
    python web_server.py              # 기본 포트 5000
    python web_server.py --port 8080  # 포트 지정
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, send_from_directory
from src.chatbot import BondedExhibitionChatbot
from src.classifier import classify_query
from src.escalation import check_escalation

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_QUERY_LENGTH = 2000

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "web"), static_url_path="/static")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("chatbot")

chatbot = BondedExhibitionChatbot()


@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "잘못된 요청입니다."}), 400


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "요청한 리소스를 찾을 수 없습니다."}), 404


@app.errorhandler(500)
def internal_error(e):
    logger.error(f"내부 서버 오류: {e}")
    return jsonify({"error": "내부 서버 오류가 발생했습니다."}), 500


@app.route("/")
def index():
    """웹 챗봇 UI 페이지를 반환한다."""
    return send_from_directory(os.path.join(BASE_DIR, "web"), "index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    """사용자 질문을 처리하여 답변을 반환한다."""
    data = request.get_json(silent=True)
    if not data or "query" not in data:
        return jsonify({"error": "query 필드가 필요합니다."}), 400

    raw_query = data["query"]
    if not isinstance(raw_query, str):
        return jsonify({"error": "query는 문자열이어야 합니다."}), 400

    query = raw_query.strip()
    if not query:
        return jsonify({"error": "질문을 입력해 주세요."}), 400

    if len(query) > MAX_QUERY_LENGTH:
        return jsonify({"error": f"질문은 {MAX_QUERY_LENGTH}자 이내로 입력해 주세요."}), 400

    categories = classify_query(query)
    escalation = check_escalation(query)
    answer = chatbot.process_query(query)

    logger.info(f"질문: {query[:50]}... | 분류: {categories[0]} | 에스컬레이션: {escalation is not None}")

    response = {
        "answer": answer,
        "category": categories[0] if categories else "GENERAL",
        "categories": categories,
        "is_escalation": escalation is not None,
        "escalation_target": escalation.get("target") if escalation else None,
    }

    return jsonify(response)


@app.route("/api/faq", methods=["GET"])
def faq_list():
    """FAQ 목록을 반환한다."""
    items = []
    for item in chatbot.faq_items:
        items.append({
            "id": item.get("id", ""),
            "category": item.get("category", ""),
            "question": item.get("question", ""),
        })
    return jsonify({"items": items, "count": len(items)})


@app.route("/api/config", methods=["GET"])
def config():
    """챗봇 설정 정보를 반환한다."""
    return jsonify({
        "persona": chatbot.get_persona(),
        "categories": chatbot.config.get("categories", []),
        "contacts": chatbot.config.get("contacts", {}),
    })


@app.route("/api/health", methods=["GET"])
def health():
    """헬스 체크 엔드포인트."""
    return jsonify({"status": "ok", "faq_count": len(chatbot.faq_items)})


def main():
    parser = argparse.ArgumentParser(description="보세전시장 챗봇 웹 서버")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    logger.info(f"보세전시장 챗봇 웹 서버 시작: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
