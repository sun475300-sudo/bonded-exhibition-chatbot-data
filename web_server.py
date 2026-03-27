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
from src.faq_recommender import FAQRecommender
from src.feedback import FeedbackManager
from src.logger_db import ChatLogger
from src.translator import SimpleTranslator

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_QUERY_LENGTH = 2000

# logs 디렉토리 자동 생성
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "web"), static_url_path="/static")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("chatbot")

chatbot = BondedExhibitionChatbot()
chat_logger = ChatLogger(db_path=os.path.join(BASE_DIR, "logs", "chat_logs.db"))
feedback_manager = FeedbackManager(db_path=os.path.join(BASE_DIR, "logs", "feedback.db"))
translator = SimpleTranslator()
faq_recommender = FAQRecommender(chat_logger)


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


@app.route("/manifest.json")
def manifest():
    """PWA 매니페스트 파일을 반환한다."""
    return send_from_directory(os.path.join(BASE_DIR, "web"), "manifest.json")


@app.route("/sw.js")
def service_worker():
    """Service Worker 파일을 반환한다."""
    response = send_from_directory(os.path.join(BASE_DIR, "web"), "sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


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
    # 세션 ID 처리 (선택적)
    session_id = data.get("session_id")
    answer = chatbot.process_query(query, session_id=session_id)

    logger.info(f"질문: {query[:50]}... | 분류: {categories[0]} | 에스컬레이션: {escalation is not None}")

    primary_category = categories[0] if categories else "GENERAL"
    is_escalation = escalation is not None

    # FAQ 매칭 결과에서 faq_id 추출
    faq_match = chatbot.find_matching_faq(query, primary_category)
    faq_id = faq_match.get("id") if faq_match else None

    # 로그 저장
    try:
        chat_logger.log_query(
            query=query,
            category=primary_category,
            faq_id=faq_id,
            is_escalation=is_escalation,
            response_preview=answer,
        )
    except Exception as e:
        logger.error(f"로그 저장 실패: {e}")

    # 다국어 지원: lang 파라미터에 따라 답변 헤더 번역
    lang = data.get("lang", "ko")
    if lang and lang != "ko" and translator.is_supported(lang):
        translated_answer = translator.translate_response(answer, lang)
    else:
        translated_answer = answer
        lang = "ko"

    response = {
        "answer": translated_answer,
        "category": primary_category,
        "categories": categories,
        "is_escalation": is_escalation,
        "escalation_target": escalation.get("target") if escalation else None,
        "lang": lang,
    }

    if session_id:
        response["session_id"] = session_id

    return jsonify(response)


@app.route("/api/session/new", methods=["POST"])
def session_new():
    """새 세션을 생성한다."""
    session = chatbot.session_manager.create_session()
    return jsonify({
        "session_id": session.session_id,
        "created_at": session.created_at,
    }), 201


@app.route("/api/session/<session_id>", methods=["GET"])
def session_status(session_id):
    """세션 상태를 조회한다."""
    session = chatbot.session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "세션을 찾을 수 없거나 만료되었습니다."}), 404
    return jsonify(session.to_dict())


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


@app.route("/admin")
def admin():
    """관리자 대시보드 페이지를 반환한다."""
    return send_from_directory(os.path.join(BASE_DIR, "web"), "admin.html")


@app.route("/api/admin/stats", methods=["GET"])
def admin_stats():
    """통계 JSON을 반환한다."""
    return jsonify(chat_logger.get_stats())


@app.route("/api/admin/logs", methods=["GET"])
def admin_logs():
    """최근 로그 JSON을 반환한다."""
    limit = request.args.get("limit", 50, type=int)
    return jsonify({"logs": chat_logger.get_recent_logs(limit=limit)})


@app.route("/api/admin/unmatched", methods=["GET"])
def admin_unmatched():
    """미매칭 질문 JSON을 반환한다."""
    limit = request.args.get("limit", 20, type=int)
    return jsonify({"queries": chat_logger.get_unmatched_queries(limit=limit)})


@app.route("/api/admin/recommendations", methods=["GET"])
def admin_recommendations():
    """미매칭 질문 기반 FAQ 추가 후보 추천 목록을 반환한다."""
    top_k = request.args.get("top_k", 10, type=int)
    try:
        recommendations = faq_recommender.get_recommendations(top_k=top_k)
        return jsonify({"recommendations": recommendations, "count": len(recommendations)})
    except Exception as e:
        logger.error(f"FAQ 추천 생성 실패: {e}")
        return jsonify({"error": "추천 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/feedback", methods=["POST"])
def feedback():
    """사용자 피드백을 저장한다."""
    data = request.get_json(silent=True)
    if not data or "query_id" not in data or "rating" not in data:
        return jsonify({"error": "query_id와 rating 필드가 필요합니다."}), 400

    query_id = data["query_id"]
    rating = data["rating"]
    comment = data.get("comment", "")

    if rating not in ("helpful", "unhelpful"):
        return jsonify({"error": "rating은 'helpful' 또는 'unhelpful'이어야 합니다."}), 400

    try:
        feedback_id = feedback_manager.save_feedback(
            query_id=query_id, rating=rating, comment=comment
        )
        return jsonify({"success": True, "feedback_id": feedback_id}), 201
    except Exception as e:
        logger.error(f"피드백 저장 실패: {e}")
        return jsonify({"error": "피드백 저장에 실패했습니다."}), 500


@app.route("/api/admin/feedback", methods=["GET"])
def admin_feedback():
    """피드백 통계를 반환한다."""
    stats = feedback_manager.get_feedback_stats()
    low_rated = feedback_manager.get_low_rated_queries(limit=20)
    return jsonify({"stats": stats, "low_rated_queries": low_rated})


def main():
    parser = argparse.ArgumentParser(description="보세전시장 챗봇 웹 서버")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    logger.info(f"보세전시장 챗봇 웹 서버 시작: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
