"""Real-time monitoring for the chatbot admin dashboard."""

import time
import threading
from collections import deque, Counter


class RealtimeMonitor:
    """Tracks live chatbot events and provides real-time stats and alerts."""

    VALID_EVENT_TYPES = {"query", "error", "escalation", "unmatched", "feedback"}
    MAX_BUFFER_SIZE = 1000

    def __init__(self):
        self._buffer = deque(maxlen=self.MAX_BUFFER_SIZE)
        self._lock = threading.Lock()
        self._active_sessions: set[str] = set()

    def record_event(self, event_type: str, data: dict) -> None:
        """Record a monitoring event with timestamp.

        Args:
            event_type: One of "query", "error", "escalation", "unmatched", "feedback".
            data: Arbitrary event data. May include "session_id", "response_time_ms",
                  "category", etc.
        """
        if event_type not in self.VALID_EVENT_TYPES:
            raise ValueError(
                f"Invalid event_type '{event_type}'. "
                f"Must be one of {sorted(self.VALID_EVENT_TYPES)}"
            )

        event = {
            "event_type": event_type,
            "timestamp": time.time(),
            "data": data,
        }

        with self._lock:
            self._buffer.append(event)

            # Track active sessions
            session_id = data.get("session_id")
            if session_id:
                self._active_sessions.add(session_id)

    def get_recent_events(
        self, event_type: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Get recent events, optionally filtered by type.

        Args:
            event_type: If provided, only return events of this type.
            limit: Maximum number of events to return (default 50).

        Returns:
            List of event dicts, most recent first.
        """
        with self._lock:
            events = list(self._buffer)

        if event_type is not None:
            events = [e for e in events if e["event_type"] == event_type]

        # Most recent first
        events.reverse()
        return events[:limit]

    def get_live_stats(self) -> dict:
        """Return real-time statistics computed from the event buffer.

        Returns:
            Dict with keys: queries_last_minute, queries_last_hour, error_rate,
            avg_response_time_ms, active_sessions, top_categories, unmatched_rate.
        """
        now = time.time()
        one_minute_ago = now - 60
        one_hour_ago = now - 3600

        with self._lock:
            events = list(self._buffer)
            active_sessions = len(self._active_sessions)

        # Partition events by time window
        last_minute_queries = 0
        last_hour_queries = 0
        last_hour_errors = 0
        last_hour_unmatched = 0
        last_hour_total = 0
        response_times: list[float] = []
        category_counter: Counter[str] = Counter()

        for event in events:
            ts = event["timestamp"]
            etype = event["event_type"]

            if ts >= one_hour_ago:
                last_hour_total += 1

                if etype == "query":
                    last_hour_queries += 1
                    if ts >= one_minute_ago:
                        last_minute_queries += 1

                    # Track response times
                    rt = event["data"].get("response_time_ms")
                    if rt is not None:
                        response_times.append(float(rt))

                    # Track categories
                    cat = event["data"].get("category")
                    if cat:
                        category_counter[cat] += 1

                elif etype == "error":
                    last_hour_errors += 1

                elif etype == "unmatched":
                    last_hour_unmatched += 1

        # Compute rates
        query_and_unmatched = last_hour_queries + last_hour_unmatched
        error_rate = (
            last_hour_errors / last_hour_total if last_hour_total > 0 else 0.0
        )
        unmatched_rate = (
            last_hour_unmatched / query_and_unmatched
            if query_and_unmatched > 0
            else 0.0
        )
        avg_response_time = (
            sum(response_times) / len(response_times) if response_times else 0.0
        )

        # Top categories (up to 10)
        top_categories = [
            {"category": cat, "count": count}
            for cat, count in category_counter.most_common(10)
        ]

        return {
            "queries_last_minute": last_minute_queries,
            "queries_last_hour": last_hour_queries,
            "error_rate": round(error_rate, 4),
            "avg_response_time_ms": round(avg_response_time, 2),
            "active_sessions": active_sessions,
            "top_categories": top_categories,
            "unmatched_rate": round(unmatched_rate, 4),
        }

    def get_alerts(self) -> list[dict]:
        """Return active alerts based on threshold checks.

        Thresholds:
            - error_rate > 5%  -> "high_error_rate"
            - unmatched_rate > 30% -> "high_unmatched_rate"
            - queries_last_minute > 100 -> "high_traffic"

        Returns:
            List of alert dicts with keys: alert_type, message, value.
        """
        stats = self.get_live_stats()
        alerts: list[dict] = []

        if stats["error_rate"] > 0.05:
            alerts.append({
                "alert_type": "high_error_rate",
                "message": (
                    f"Error rate is {stats['error_rate'] * 100:.1f}% "
                    f"(threshold: 5%)"
                ),
                "value": stats["error_rate"],
            })

        if stats["unmatched_rate"] > 0.30:
            alerts.append({
                "alert_type": "high_unmatched_rate",
                "message": (
                    f"Unmatched rate is {stats['unmatched_rate'] * 100:.1f}% "
                    f"(threshold: 30%)"
                ),
                "value": stats["unmatched_rate"],
            })

        if stats["queries_last_minute"] > 100:
            alerts.append({
                "alert_type": "high_traffic",
                "message": (
                    f"{stats['queries_last_minute']} queries in the last minute "
                    f"(threshold: 100)"
                ),
                "value": stats["queries_last_minute"],
            })

        return alerts
