"""Global test fixtures - ensures data files are restored after tests."""

import os
import shutil

import pytest

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
FAQ_PATH = os.path.join(DATA_DIR, "faq.json")
_faq_backup_content = None


def pytest_configure(config):
    """Backup faq.json at test session start."""
    global _faq_backup_content
    if os.path.exists(FAQ_PATH):
        with open(FAQ_PATH, "r", encoding="utf-8") as f:
            _faq_backup_content = f.read()


def _clear_rate_limiter():
    """Clear rate limiter state to prevent 429s across test modules."""
    try:
        from web_server import advanced_rate_limiter
        if hasattr(advanced_rate_limiter, 'reset'):
            advanced_rate_limiter.reset()
        else:
            for attr in ('_requests', '_endpoint_hits', '_user_hits', '_windows', '_quotas_used'):
                d = getattr(advanced_rate_limiter, attr, None)
                if d is not None and hasattr(d, 'clear'):
                    d.clear()
    except Exception:
        pass


def pytest_runtest_setup(item):
    """Reset rate limiter before tests that use the Flask client."""
    _clear_rate_limiter()


def pytest_runtest_teardown(item, nextitem):
    """Restore faq.json after every test to prevent pollution."""
    if _faq_backup_content is not None:
        with open(FAQ_PATH, "r", encoding="utf-8") as f:
            current = f.read()
        if current != _faq_backup_content:
            with open(FAQ_PATH, "w", encoding="utf-8") as f:
                f.write(_faq_backup_content)
