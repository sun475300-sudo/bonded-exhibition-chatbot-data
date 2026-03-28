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


def pytest_runtest_teardown(item, nextitem):
    """Restore faq.json after every test to prevent pollution."""
    global _faq_backup_content
    if _faq_backup_content is not None:
        with open(FAQ_PATH, "r", encoding="utf-8") as f:
            current = f.read()
        if current != _faq_backup_content:
            with open(FAQ_PATH, "w", encoding="utf-8") as f:
                f.write(_faq_backup_content)
