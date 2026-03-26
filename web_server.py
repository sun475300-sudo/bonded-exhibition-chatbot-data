"""보세전시장 민원응대 챗봇 웹 서버.

Flask 기반 REST API + 웹 UI를 제공한다.

사용법:
    python web_server.py              # 기본 포트 5000
    python web_server.py --port 8080  # 포트 지정
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, send_from_directory
from src.chatbot import BondedExhibitionChatbot
from src.classifier import classify_query
from src.escalation import check_escalation

app = Flask(__name__, static_folder="web", static_url_path="/static")

chatbot = BondedExhibitionChatbot()


@app.route("/")
def index():
    """웹 챗봇 UI 페이지를 반환한다."""
    return send_from_directory("web", "index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    """사용자 질문을 처리하여 답변을 반환한다.

    Request JSON:
        {"query": "질문 문자열"}

    Response JSON:
        {
            "answer": "답변 문자열",
            "category": "분류된 카테고리",
            "categories": ["매칭된 카테고리 목록"],
            "is_escalation": bool,
            "escalation_target": "에스컬레이션 대상" or null
        }
    """
    data = request.get_json()
    if not data or "query" not in data:
        return jsonify({"error": "query 필드가 필요합니다."}), 400

    query = data["query"].strip()
    if not query:
        return jsonify({"error": "질문을 입력해 주세요."}), 400

    categories = classify_query(query)
    escalation = check_escalation(query)
    answer = chatbot.process_query(query)

    response = {
        "answer": answer,
        "category": categories[0],
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
            "id": item["id"],
            "category": item["category"],
            "question": item["question"],
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
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    print(f"보세전시장 챗봇 웹 서버 시작: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
