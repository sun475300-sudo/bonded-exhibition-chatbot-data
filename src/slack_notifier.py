"""Slack webhook notifier for alerts and daily reports.

Sends messages to a Slack channel via an incoming webhook URL.
When no webhook is configured, operates in dry-run mode (logs only).
"""

import json
import logging
import os
import time
import urllib.request
import urllib.error

logger = logging.getLogger("slack_notifier")

SEVERITY_COLORS = {
    "info": "#36a64f",       # green
    "warning": "#ff9900",    # orange
    "critical": "#ff0000",   # red
}

SEVERITY_EMOJI = {
    "info": "information_source",
    "warning": "warning",
    "critical": "rotating_light",
}


class SlackNotifier:
    """Send alerts and reports to Slack via incoming webhook."""

    MAX_RETRIES = 3
    INITIAL_BACKOFF = 1.0  # seconds

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL", "")
        self.dry_run = not bool(self.webhook_url)
        if self.dry_run:
            logger.info("SlackNotifier: no webhook URL configured -- running in dry-run mode")

    def send_alert(
        self,
        title: str,
        message: str,
        severity: str = "info",
    ) -> bool:
        """Send an alert message to Slack.

        Args:
            title: Alert title.
            message: Alert body text.
            severity: One of "info", "warning", "critical".

        Returns:
            True if sent (or dry-run logged) successfully.
        """
        if severity not in SEVERITY_COLORS:
            severity = "info"

        emoji = SEVERITY_EMOJI.get(severity, "information_source")
        color = SEVERITY_COLORS[severity]

        payload = {
            "attachments": [
                {
                    "color": color,
                    "title": f":{emoji}: {title}",
                    "text": message,
                    "footer": "Bonded Exhibition Chatbot",
                    "ts": int(time.time()),
                }
            ]
        }

        return self._send(payload)

    def send_daily_report(self, stats: dict) -> bool:
        """Send a formatted daily stats report to Slack.

        Args:
            stats: Dictionary with keys like total_queries, faq_match_rate,
                   escalation_rate, avg_satisfaction, top_categories, etc.

        Returns:
            True if sent successfully.
        """
        total = stats.get("total_queries", 0)
        match_rate = stats.get("faq_match_rate", 0)
        escalation_rate = stats.get("escalation_rate", 0)
        avg_satisfaction = stats.get("avg_satisfaction", 0)
        top_categories = stats.get("top_categories", [])

        cat_lines = ""
        if top_categories:
            cat_lines = "\n".join(
                f"  - {c.get('category', 'N/A')}: {c.get('count', 0)} queries"
                for c in top_categories[:5]
            )

        text_parts = [
            f"*Total Queries:* {total}",
            f"*FAQ Match Rate:* {match_rate:.1f}%",
            f"*Escalation Rate:* {escalation_rate:.1f}%",
            f"*Avg Satisfaction:* {avg_satisfaction:.2f}",
        ]
        if cat_lines:
            text_parts.append(f"*Top Categories:*\n{cat_lines}")

        payload = {
            "attachments": [
                {
                    "color": "#2196F3",
                    "title": ":bar_chart: Daily Chatbot Report",
                    "text": "\n".join(text_parts),
                    "footer": "Bonded Exhibition Chatbot",
                    "ts": int(time.time()),
                }
            ]
        }

        return self._send(payload)

    def _send(self, payload: dict) -> bool:
        """Send a payload to the Slack webhook with retry logic.

        Returns True on success or dry-run, False on failure.
        """
        payload_json = json.dumps(payload)

        if self.dry_run:
            logger.info(f"SlackNotifier dry-run: {payload_json}")
            return True

        backoff = self.INITIAL_BACKOFF
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    self.webhook_url,
                    data=payload_json.encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        return True
                    logger.warning(
                        f"Slack webhook returned status {resp.status} "
                        f"(attempt {attempt}/{self.MAX_RETRIES})"
                    )
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                logger.warning(
                    f"Slack webhook error: {exc} (attempt {attempt}/{self.MAX_RETRIES})"
                )

            if attempt < self.MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2

        logger.error("SlackNotifier: all retry attempts exhausted")
        return False
