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
from src.conversation_export import ConversationExporter
from src.escalation import check_escalation
from src.analytics import QueryAnalytics
from src.auto_faq_pipeline import AutoFAQPipeline
from src.faq_quality_checker import FAQQualityChecker
from src.faq_recommender import FAQRecommender
from src.feedback import FeedbackManager
from src.logger_db import ChatLogger
from src.realtime_monitor import RealtimeMonitor
from src.satisfaction_tracker import SatisfactionTracker
from src.security import APIKeyAuth, RateLimiter, sanitize_input
from src.translator import SimpleTranslator
from src.utils import load_json

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
query_analytics = QueryAnalytics(chat_logger, feedback_manager)
auto_faq_pipeline = AutoFAQPipeline(
    faq_recommender, faq_path=os.path.join(BASE_DIR, "data", "faq.json")
)

# 보안 미들웨어 초기화
api_key_auth = APIKeyAuth(app)
rate_limit_value = int(os.environ.get("CHATBOT_RATE_LIMIT", "60"))
rate_limiter = RateLimiter(max_requests=rate_limit_value)

# Phase 13-18 모듈 초기화
realtime_monitor = RealtimeMonitor()
conversation_exporter = ConversationExporter()
legal_refs = load_json("data/legal_references.json")
faq_quality_checker = FAQQualityChecker(chatbot.faq_items, legal_refs)
satisfaction_tracker = SatisfactionTracker()


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
    # Rate Limiting 적용
    client_ip = request.remote_addr or "unknown"
    if not rate_limiter.is_allowed(client_ip):
        return jsonify({"error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."}), 429

    data = request.get_json(silent=True)
    if not data or "query" not in data:
        return jsonify({"error": "query 필드가 필요합니다."}), 400

    raw_query = data["query"]
    if not isinstance(raw_query, str):
        return jsonify({"error": "query는 문자열이어야 합니다."}), 400

    # 입력 살균 적용
    query = sanitize_input(raw_query, max_length=MAX_QUERY_LENGTH)
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

    # 로그 저장 + 모니터링 이벤트
    try:
        chat_logger.log_query(
            query=query,
            category=primary_category,
            faq_id=faq_id,
            is_escalation=is_escalation,
            response_preview=answer,
        )
        event_type = "escalation" if is_escalation else ("query" if faq_match else "unmatched")
        realtime_monitor.record_event(event_type, {"query": query, "category": primary_category})
    except Exception as e:
        logger.error(f"로그 저장 실패: {e}")

    # 다국어 지원: lang 파라미터에 따라 답변 헤더 번역
    lang = data.get("lang", "ko")
    if lang and lang != "ko" and translator.is_supported(lang):
        translated_answer = translator.translate_response(answer, lang)
    else:
        translated_answer = answer
        lang = "ko"

    # 관련 질문 추천
    related = []
    if faq_id:
        try:
            related = chatbot.related_faq_finder.find_related(faq_id, top_k=3)
        except Exception:
            pass

    response = {
        "answer": translated_answer,
        "category": primary_category,
        "categories": categories,
        "is_escalation": is_escalation,
        "escalation_target": escalation.get("target") if escalation else None,
        "lang": lang,
        "related_questions": [{"id": r["id"], "question": r["question"]} for r in related],
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


@app.route("/api/autocomplete", methods=["GET"])
def autocomplete():
    """검색 자동완성: FAQ 질문 중 쿼리 문자열을 포함하는 상위 5개를 반환한다."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"suggestions": []})

    q_lower = q.lower()
    suggestions = []
    for item in chatbot.faq_items:
        question = item.get("question", "")
        if q_lower in question.lower():
            suggestions.append({
                "id": item.get("id", ""),
                "question": question,
                "category": item.get("category", ""),
            })
            if len(suggestions) >= 5:
                break

    return jsonify({"suggestions": suggestions})


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


@app.route("/api/admin/analytics", methods=["GET"])
def admin_analytics():
    """분석 리포트를 반환한다."""
    try:
        days = request.args.get("days", 7, type=int)
        trend = query_analytics.get_trend_report(days=days)
        quality = query_analytics.get_quality_score()
        peak_hours = query_analytics.get_peak_hours()
        return jsonify({
            "trend": trend,
            "quality": quality,
            "peak_hours": peak_hours,
        })
    except Exception as e:
        logger.error(f"분석 리포트 생성 실패: {e}")
        return jsonify({"error": "분석 리포트 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/report", methods=["GET"])
def admin_report():
    """주간 리포트 텍스트를 반환한다."""
    try:
        report_text = query_analytics.generate_report_text()
        return jsonify({"report": report_text})
    except Exception as e:
        logger.error(f"주간 리포트 생성 실패: {e}")
        return jsonify({"error": "주간 리포트 생성 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/faq-pipeline", methods=["GET"])
def admin_faq_pipeline():
    """FAQ 후보 목록을 반환한다."""
    try:
        min_freq = request.args.get("min_frequency", 3, type=int)
        candidates = auto_faq_pipeline.get_pending_candidates(min_frequency=min_freq)
        return jsonify({"candidates": candidates, "count": len(candidates)})
    except Exception as e:
        logger.error(f"FAQ 파이프라인 조회 실패: {e}")
        return jsonify({"error": "FAQ 파이프라인 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/faq-pipeline/approve", methods=["POST"])
def admin_faq_approve():
    """FAQ 후보를 승인한다."""
    data = request.get_json(silent=True)
    if not data or "candidate_id" not in data:
        return jsonify({"error": "candidate_id 필드가 필요합니다."}), 400

    try:
        result = auto_faq_pipeline.approve_candidate(data["candidate_id"])
        return jsonify({"success": True, "candidate": result})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"FAQ 후보 승인 실패: {e}")
        return jsonify({"error": "FAQ 후보 승인 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/faq-pipeline/reject", methods=["POST"])
def admin_faq_reject():
    """FAQ 후보를 거부한다."""
    data = request.get_json(silent=True)
    if not data or "candidate_id" not in data:
        return jsonify({"error": "candidate_id 필드가 필요합니다."}), 400

    try:
        result = auto_faq_pipeline.reject_candidate(data["candidate_id"])
        return jsonify({"success": True, "candidate": result})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"FAQ 후보 거부 실패: {e}")
        return jsonify({"error": "FAQ 후보 거부 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/monitor", methods=["GET"])
def admin_monitor():
    """실시간 모니터링 데이터를 반환한다."""
    try:
        stats = realtime_monitor.get_live_stats()
        alerts = realtime_monitor.get_alerts()
        return jsonify({"stats": stats, "alerts": alerts})
    except Exception as e:
        logger.error(f"모니터링 조회 실패: {e}")
        return jsonify({"error": "모니터링 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/quality", methods=["GET"])
def admin_quality():
    """FAQ 품질 검사 결과를 반환한다."""
    try:
        result = faq_quality_checker.check_all()
        return jsonify(result)
    except Exception as e:
        logger.error(f"품질 검사 실패: {e}")
        return jsonify({"error": "품질 검사 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/realtime", methods=["GET"])
def admin_realtime():
    """실시간 모니터링 라이브 통계를 반환한다."""
    try:
        stats = realtime_monitor.get_live_stats()
        alerts = realtime_monitor.get_alerts()

        # Build hourly query counts for the last 24 hours
        import time as _time
        now = _time.time()
        hourly_counts: list[dict] = []
        with realtime_monitor._lock:
            events = list(realtime_monitor._buffer)
        for h in range(23, -1, -1):
            start = now - (h + 1) * 3600
            end = now - h * 3600
            count = sum(
                1 for e in events
                if e["event_type"] in ("query", "unmatched")
                and start <= e["timestamp"] < end
            )
            hour_label = _time.strftime("%H", _time.localtime(end))
            hourly_counts.append({"hour": hour_label, "count": count})

        return jsonify({
            "queries_per_minute": stats["queries_last_minute"],
            "avg_response_time_ms": stats["avg_response_time_ms"],
            "active_sessions": stats["active_sessions"],
            "error_rate": stats["error_rate"],
            "unmatched_rate": stats["unmatched_rate"],
            "queries_last_hour": stats["queries_last_hour"],
            "top_categories": stats["top_categories"],
            "alerts": alerts,
            "hourly_counts": hourly_counts,
        })
    except Exception as e:
        logger.error(f"실시간 모니터링 조회 실패: {e}")
        return jsonify({"error": "실시간 모니터링 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/faq-quality", methods=["GET"])
def admin_faq_quality():
    """FAQ 품질 대시보드 데이터를 반환한다."""
    try:
        result = faq_quality_checker.check_all()
        # Enrich each issue with severity level
        for issue in result.get("issues", []):
            check_type = issue.get("check", "")
            count = issue.get("count", 0)
            if check_type == "duplicates" or (check_type == "keyword_coverage" and count > 5):
                issue["severity"] = "critical"
            elif count > 2:
                issue["severity"] = "warning"
            else:
                issue["severity"] = "good"
        return jsonify(result)
    except Exception as e:
        logger.error(f"FAQ 품질 대시보드 조회 실패: {e}")
        return jsonify({"error": "FAQ 품질 대시보드 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/admin/satisfaction", methods=["GET"])
def admin_satisfaction():
    """만족도 트렌드 데이터를 반환한다."""
    try:
        stats = satisfaction_tracker.get_satisfaction_stats()
        low_queries = satisfaction_tracker.get_low_satisfaction_queries(limit=10)

        # Determine trend direction based on recent vs overall score
        avg_score = stats.get("avg_satisfaction_score", 0.0)
        if avg_score >= 0.7:
            trend = "up"
        elif avg_score >= 0.4:
            trend = "stable"
        else:
            trend = "down"

        return jsonify({
            "overall_score": avg_score,
            "trend": trend,
            "total_queries": stats.get("total_queries", 0),
            "re_ask_rate": stats.get("re_ask_rate", 0.0),
            "response_type_distribution": stats.get("response_type_distribution", {}),
            "lowest_rated": low_queries,
        })
    except Exception as e:
        logger.error(f"만족도 트렌드 조회 실패: {e}")
        return jsonify({"error": "만족도 트렌드 조회 중 오류가 발생했습니다."}), 500


@app.route("/api/export", methods=["POST"])
def export_conversation():
    """대화 내역을 파일로 내보낸다.

    요청 본문: {"session_id": "...", "format": "text|json|csv|html"}
    """
    data = request.get_json(silent=True)
    if not data or "session_id" not in data:
        return jsonify({"error": "session_id 필드가 필요합니다."}), 400

    session_id = data["session_id"]
    fmt = data.get("format", "text")
    if fmt not in ("text", "json", "csv", "html"):
        return jsonify({"error": "format은 text, json, csv, html 중 하나여야 합니다."}), 400

    session = chatbot.session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "세션을 찾을 수 없거나 만료되었습니다."}), 404

    history = session.history

    ext_map = {"text": "txt", "json": "json", "csv": "csv", "html": "html"}
    mime_map = {
        "text": "text/plain; charset=utf-8",
        "json": "application/json",
        "csv": "text/csv; charset=utf-8",
        "html": "text/html; charset=utf-8",
    }
    filename = f"conversation_export.{ext_map[fmt]}"

    if fmt == "json":
        content = conversation_exporter.export_json(history, session_id)
    elif fmt == "csv":
        content = conversation_exporter.export_csv(history, session_id)
    elif fmt == "html":
        content = conversation_exporter.export_html(history, session_id)
    else:
        content = conversation_exporter.export_text(history, session_id)

    resp = app.response_class(content, mimetype=mime_map[fmt])
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@app.route("/api/session/<session_id>/export", methods=["GET"])
def session_export(session_id):
    """세션 대화를 내보낸다."""
    session = chatbot.session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "세션을 찾을 수 없습니다."}), 404

    fmt = request.args.get("format", "text")
    history = session.history

    if fmt == "json":
        content = conversation_exporter.export_json(history, session_id)
        return app.response_class(content, mimetype="application/json")
    elif fmt == "csv":
        content = conversation_exporter.export_csv(history, session_id)
        return app.response_class(content, mimetype="text/csv")
    elif fmt == "html":
        content = conversation_exporter.export_html(history, session_id)
        return app.response_class(content, mimetype="text/html")
    else:
        content = conversation_exporter.export_text(history, session_id)
        return app.response_class(content, mimetype="text/plain; charset=utf-8")


@app.route("/api/related/<faq_id>", methods=["GET"])
def related_faq(faq_id):
    """관련 FAQ를 반환한다."""
    top_k = request.args.get("top_k", 3, type=int)
    try:
        related = chatbot.related_faq_finder.find_related(faq_id, top_k=top_k)
        return jsonify({"related": related, "count": len(related)})
    except Exception as e:
        logger.error(f"관련 FAQ 조회 실패: {e}")
        return jsonify({"error": "관련 FAQ 조회 중 오류가 발생했습니다."}), 500


def main():
    parser = argparse.ArgumentParser(description="보세전시장 챗봇 웹 서버")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    logger.info(f"보세전시장 챗봇 웹 서버 시작: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
