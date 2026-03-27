"""질문 분석 엔진.

로그 및 피드백 데이터를 기반으로 트렌드, 품질 점수, 시간대별 분포 등
종합 분석 리포트를 생성한다.
"""

from datetime import datetime, timedelta


class QueryAnalytics:
    """질문 로그와 피드백을 분석하여 인텔리전스 리포트를 제공하는 클래스."""

    def __init__(self, logger_db, feedback_db):
        """ChatLogger와 FeedbackManager 인스턴스를 받아 초기화한다.

        Args:
            logger_db: ChatLogger 인스턴스
            feedback_db: FeedbackManager 인스턴스
        """
        self.logger_db = logger_db
        self.feedback_db = feedback_db

    def get_trend_report(self, days=7):
        """일별 질문 수, 카테고리별 추이, 에스컬레이션 추이를 반환한다.

        Args:
            days: 조회 기간 (일 단위, 기본 7일)

        Returns:
            dict: daily_counts, category_trends, escalation_trends
        """
        conn = self.logger_db._get_conn()
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        # 일별 질문 수
        rows = conn.execute(
            """SELECT DATE(timestamp) as date, COUNT(*) as count
               FROM chat_logs
               WHERE timestamp >= ?
               GROUP BY DATE(timestamp)
               ORDER BY date""",
            (start_date,),
        ).fetchall()
        daily_counts = [{"date": row["date"], "count": row["count"]} for row in rows]

        # 카테고리별 추이
        rows = conn.execute(
            """SELECT DATE(timestamp) as date, category, COUNT(*) as count
               FROM chat_logs
               WHERE timestamp >= ?
               GROUP BY DATE(timestamp), category
               ORDER BY date, category""",
            (start_date,),
        ).fetchall()
        category_trends = {}
        for row in rows:
            cat = row["category"] or "UNKNOWN"
            if cat not in category_trends:
                category_trends[cat] = []
            category_trends[cat].append({
                "date": row["date"],
                "count": row["count"],
            })

        # 에스컬레이션 추이
        rows = conn.execute(
            """SELECT DATE(timestamp) as date,
                      SUM(CASE WHEN is_escalation = 1 THEN 1 ELSE 0 END) as escalations,
                      COUNT(*) as total
               FROM chat_logs
               WHERE timestamp >= ?
               GROUP BY DATE(timestamp)
               ORDER BY date""",
            (start_date,),
        ).fetchall()
        escalation_trends = [
            {
                "date": row["date"],
                "escalations": row["escalations"],
                "total": row["total"],
                "rate": round(row["escalations"] / row["total"] * 100, 1)
                if row["total"] > 0 else 0,
            }
            for row in rows
        ]

        return {
            "days": days,
            "daily_counts": daily_counts,
            "category_trends": category_trends,
            "escalation_trends": escalation_trends,
        }

    def get_quality_score(self):
        """답변 품질 점수를 계산한다.

        피드백 기반 helpful_rate (가중치 0.5), FAQ 매칭률 (가중치 0.3),
        에스컬레이션 비율의 역수 (가중치 0.2)를 가중합한다.

        Returns:
            dict: overall_score, helpful_rate, faq_match_rate,
                  escalation_rate, breakdown
        """
        # 피드백 기반 helpful_rate
        feedback_stats = self.feedback_db.get_feedback_stats()
        helpful_rate = feedback_stats.get("helpful_rate", 0)

        # 로그 기반 통계
        log_stats = self.logger_db.get_stats()
        total = log_stats.get("total_queries", 0)
        unmatched_rate = log_stats.get("unmatched_rate", 0)
        faq_match_rate = 100.0 - unmatched_rate
        escalation_rate = log_stats.get("escalation_rate", 0)

        # 가중합 계산
        w_helpful = 0.5
        w_faq = 0.3
        w_escalation = 0.2

        score = (
            helpful_rate * w_helpful
            + faq_match_rate * w_faq
            + (100.0 - escalation_rate) * w_escalation
        )
        overall_score = round(score, 1)

        return {
            "overall_score": overall_score,
            "helpful_rate": helpful_rate,
            "faq_match_rate": round(faq_match_rate, 1),
            "escalation_rate": escalation_rate,
            "total_queries": total,
            "total_feedback": feedback_stats.get("total", 0),
            "breakdown": {
                "helpful_component": round(helpful_rate * w_helpful, 1),
                "faq_match_component": round(faq_match_rate * w_faq, 1),
                "escalation_component": round(
                    (100.0 - escalation_rate) * w_escalation, 1
                ),
            },
        }

    def get_peak_hours(self):
        """시간대별 질문 분포를 반환한다.

        Returns:
            dict: hours (0-23 시간대별 질문 수 리스트), peak_hour, peak_count
        """
        conn = self.logger_db._get_conn()

        rows = conn.execute(
            """SELECT CAST(SUBSTR(timestamp, 12, 2) AS INTEGER) as hour,
                      COUNT(*) as count
               FROM chat_logs
               WHERE timestamp LIKE '____-__-__ __:__:__'
               GROUP BY hour
               ORDER BY hour"""
        ).fetchall()

        # 0~23 시간대를 0으로 초기화
        hours = {h: 0 for h in range(24)}
        for row in rows:
            hour = row["hour"]
            if 0 <= hour <= 23:
                hours[hour] = row["count"]

        hours_list = [{"hour": h, "count": hours[h]} for h in range(24)]

        peak_hour = max(range(24), key=lambda h: hours[h])
        peak_count = hours[peak_hour]

        return {
            "hours": hours_list,
            "peak_hour": peak_hour,
            "peak_count": peak_count,
        }

    def get_weekly_report(self):
        """주간 종합 리포트를 생성한다.

        Returns:
            dict: trend, quality, peak_hours, unmatched_queries, recommendations
        """
        trend = self.get_trend_report(days=7)
        quality = self.get_quality_score()
        peak_hours = self.get_peak_hours()

        # 미매칭 질문 상위
        unmatched = self.logger_db.get_unmatched_queries(limit=10)

        return {
            "period": "weekly",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "trend": trend,
            "quality": quality,
            "peak_hours": peak_hours,
            "unmatched_queries": unmatched,
        }

    def generate_report_text(self):
        """사람이 읽을 수 있는 텍스트 리포트를 생성한다.

        Returns:
            str: 텍스트 형식의 주간 리포트
        """
        report = self.get_weekly_report()
        trend = report["trend"]
        quality = report["quality"]
        peak = report["peak_hours"]

        lines = []
        lines.append("=" * 50)
        lines.append("보세전시장 챗봇 주간 리포트")
        lines.append(f"생성 시각: {report['generated_at']}")
        lines.append("=" * 50)

        # 트렌드 요약
        lines.append("")
        lines.append("[트렌드 요약]")
        total_week = sum(d["count"] for d in trend["daily_counts"])
        lines.append(f"  지난 7일간 총 질문 수: {total_week}건")
        if trend["daily_counts"]:
            for day in trend["daily_counts"]:
                lines.append(f"    {day['date']}: {day['count']}건")

        # 품질 점수
        lines.append("")
        lines.append("[답변 품질 점수]")
        lines.append(f"  종합 점수: {quality['overall_score']}점 / 100점")
        lines.append(f"  사용자 만족도: {quality['helpful_rate']}%")
        lines.append(f"  FAQ 매칭률: {quality['faq_match_rate']}%")
        lines.append(f"  에스컬레이션 비율: {quality['escalation_rate']}%")

        # 피크 시간
        lines.append("")
        lines.append("[피크 시간대]")
        lines.append(f"  가장 바쁜 시간: {peak['peak_hour']}시 ({peak['peak_count']}건)")

        # 미매칭 질문
        lines.append("")
        lines.append("[미매칭 질문 (상위)]")
        unmatched = report["unmatched_queries"]
        if unmatched:
            for i, q in enumerate(unmatched[:5], 1):
                lines.append(f"  {i}. {q['query']}")
        else:
            lines.append("  미매칭 질문 없음")

        lines.append("")
        lines.append("=" * 50)

        return "\n".join(lines)
