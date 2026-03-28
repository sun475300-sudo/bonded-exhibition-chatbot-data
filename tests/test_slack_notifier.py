"""Tests for src/slack_notifier.py -- message formatting, dry-run mode."""

import json
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from src.slack_notifier import SlackNotifier, SEVERITY_COLORS, SEVERITY_EMOJI


# ------------------------------------------------------------------
# Dry-run mode tests
# ------------------------------------------------------------------

class TestDryRunMode:
    def test_dry_run_when_no_webhook(self):
        """When no webhook URL is provided, notifier should be in dry-run mode."""
        # Clear env var if set
        old = os.environ.pop("SLACK_WEBHOOK_URL", None)
        try:
            notifier = SlackNotifier(webhook_url="")
            assert notifier.dry_run is True
        finally:
            if old is not None:
                os.environ["SLACK_WEBHOOK_URL"] = old

    def test_dry_run_when_none(self):
        old = os.environ.pop("SLACK_WEBHOOK_URL", None)
        try:
            notifier = SlackNotifier(webhook_url=None)
            assert notifier.dry_run is True
        finally:
            if old is not None:
                os.environ["SLACK_WEBHOOK_URL"] = old

    def test_not_dry_run_with_webhook(self):
        notifier = SlackNotifier(webhook_url="https://hooks.slack.com/services/T00/B00/xxx")
        assert notifier.dry_run is False

    def test_send_alert_dry_run_returns_true(self, caplog):
        old = os.environ.pop("SLACK_WEBHOOK_URL", None)
        try:
            notifier = SlackNotifier(webhook_url="")
            with caplog.at_level(logging.INFO, logger="slack_notifier"):
                result = notifier.send_alert("Test Alert", "Something happened", severity="warning")
            assert result is True
            assert "dry-run" in caplog.text
        finally:
            if old is not None:
                os.environ["SLACK_WEBHOOK_URL"] = old

    def test_send_daily_report_dry_run_returns_true(self, caplog):
        old = os.environ.pop("SLACK_WEBHOOK_URL", None)
        try:
            notifier = SlackNotifier(webhook_url="")
            stats = {
                "total_queries": 150,
                "faq_match_rate": 87.5,
                "escalation_rate": 3.2,
                "avg_satisfaction": 0.82,
                "top_categories": [
                    {"category": "CUSTOMS", "count": 45},
                    {"category": "EXHIBITION", "count": 30},
                ],
            }
            with caplog.at_level(logging.INFO, logger="slack_notifier"):
                result = notifier.send_daily_report(stats)
            assert result is True
            assert "dry-run" in caplog.text
        finally:
            if old is not None:
                os.environ["SLACK_WEBHOOK_URL"] = old


# ------------------------------------------------------------------
# Message formatting tests
# ------------------------------------------------------------------

class TestMessageFormatting:
    def test_alert_severity_colors(self):
        """All severity levels should have a corresponding color."""
        for sev in ("info", "warning", "critical"):
            assert sev in SEVERITY_COLORS
            assert sev in SEVERITY_EMOJI

    def test_alert_unknown_severity_defaults_to_info(self, caplog):
        old = os.environ.pop("SLACK_WEBHOOK_URL", None)
        try:
            notifier = SlackNotifier(webhook_url="")
            with caplog.at_level(logging.INFO, logger="slack_notifier"):
                result = notifier.send_alert("Title", "Body", severity="unknown_level")
            assert result is True
            # Should have used "info" color in the payload
            assert "information_source" in caplog.text
        finally:
            if old is not None:
                os.environ["SLACK_WEBHOOK_URL"] = old

    def test_daily_report_format_includes_stats(self, caplog):
        old = os.environ.pop("SLACK_WEBHOOK_URL", None)
        try:
            notifier = SlackNotifier(webhook_url="")
            stats = {
                "total_queries": 200,
                "faq_match_rate": 92.0,
                "escalation_rate": 1.5,
                "avg_satisfaction": 0.91,
                "top_categories": [
                    {"category": "GENERAL", "count": 80},
                ],
            }
            with caplog.at_level(logging.INFO, logger="slack_notifier"):
                result = notifier.send_daily_report(stats)
            assert result is True
            # The dry-run log should contain key fields
            assert "200" in caplog.text  # total_queries
            assert "92.0" in caplog.text  # faq_match_rate
        finally:
            if old is not None:
                os.environ["SLACK_WEBHOOK_URL"] = old

    def test_daily_report_empty_categories(self, caplog):
        old = os.environ.pop("SLACK_WEBHOOK_URL", None)
        try:
            notifier = SlackNotifier(webhook_url="")
            stats = {"total_queries": 0, "faq_match_rate": 0, "escalation_rate": 0, "avg_satisfaction": 0}
            with caplog.at_level(logging.INFO, logger="slack_notifier"):
                result = notifier.send_daily_report(stats)
            assert result is True
        finally:
            if old is not None:
                os.environ["SLACK_WEBHOOK_URL"] = old

    def test_webhook_url_from_env(self):
        old = os.environ.get("SLACK_WEBHOOK_URL")
        try:
            os.environ["SLACK_WEBHOOK_URL"] = "https://hooks.slack.com/services/T00/B00/test"
            notifier = SlackNotifier()
            assert notifier.dry_run is False
            assert notifier.webhook_url == "https://hooks.slack.com/services/T00/B00/test"
        finally:
            if old is not None:
                os.environ["SLACK_WEBHOOK_URL"] = old
            else:
                os.environ.pop("SLACK_WEBHOOK_URL", None)
