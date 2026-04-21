"""법령 업데이트 감지 모듈.

국가법령정보센터의 보세전시장 관련 법령 변경을 감지하는 유틸리티.
수동 또는 cron으로 실행하여 법령 변경 여부를 확인한다.

사용법:
    python -m src.law_updater          # 변경 감지 실행
    python -m src.law_updater --check  # 현재 상태만 확인
"""

import json
import hashlib
import os
import sqlite3
import threading
import uuid
from datetime import datetime

from src.utils import load_json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "data", "law_update_state.json")
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "law_versions.db")


def compute_data_hash() -> dict:
    """현재 데이터 파일들의 해시를 계산한다."""
    files_to_check = [
        "data/faq.json",
        "data/legal_references.json",
        "data/escalation_rules.json",
    ]
    hashes = {}
    for filepath in files_to_check:
        full_path = os.path.join(BASE_DIR, filepath)
        if os.path.exists(full_path):
            with open(full_path, "rb") as f:
                hashes[filepath] = hashlib.md5(f.read()).hexdigest()
    return hashes


def load_state() -> dict:
    """이전 상태를 로드한다."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    """현재 상태를 저장한다."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def check_for_updates() -> dict:
    """법령 데이터 변경 여부를 확인한다.

    Returns:
        {
            "has_changes": bool,
            "changed_files": list[str],
            "last_checked": str (ISO timestamp),
            "current_hashes": dict
        }
    """
    previous_state = load_state()
    previous_hashes = previous_state.get("hashes", {})
    current_hashes = compute_data_hash()

    changed_files = []
    for filepath, current_hash in current_hashes.items():
        prev_hash = previous_hashes.get(filepath)
        if prev_hash and prev_hash != current_hash:
            changed_files.append(filepath)
        elif not prev_hash:
            changed_files.append(filepath)

    result = {
        "has_changes": len(changed_files) > 0,
        "changed_files": changed_files,
        "last_checked": datetime.now().isoformat(),
        "current_hashes": current_hashes,
    }

    # 상태 저장
    save_state({
        "hashes": current_hashes,
        "last_checked": result["last_checked"],
    })

    return result


def get_legal_references_summary() -> list[dict]:
    """현재 법령 근거 요약을 반환한다."""
    data = load_json("data/legal_references.json")
    refs = data.get("references", [])
    return [
        {
            "law_name": ref.get("law_name", ""),
            "article": ref.get("article", ""),
            "title": ref.get("title", ""),
            "has_url": bool(ref.get("url", "")),
        }
        for ref in refs
    ]


# ---------------------------------------------------------------------------
# LawVersionTracker: SQLite-based version history
# ---------------------------------------------------------------------------

class LawVersionTracker:
    """법령 조항별 버전 이력을 SQLite에 저장하고 변경을 추적한다."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._init_db()

    def _init_db(self):
        """데이터베이스 테이블을 초기화한다."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS law_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    law_name TEXT NOT NULL,
                    article TEXT NOT NULL,
                    version_date TEXT NOT NULL,
                    previous_text TEXT,
                    current_text TEXT NOT NULL,
                    detected_at TEXT NOT NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def record_version(self, law_name: str, article: str, text: str) -> bool:
        """새로운 버전을 기록한다. 텍스트가 변경된 경우에만 저장.

        Returns:
            True if a new version was recorded (change detected), False otherwise.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """SELECT current_text FROM law_versions
                   WHERE law_name = ? AND article = ?
                   ORDER BY id DESC LIMIT 1""",
                (law_name, article),
            )
            row = cursor.fetchone()
            previous_text = row[0] if row else None

            # 변경이 없으면 기록하지 않음
            if previous_text is not None and previous_text == text:
                return False

            now = datetime.now().isoformat()
            conn.execute(
                """INSERT INTO law_versions
                   (law_name, article, version_date, previous_text, current_text, detected_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (law_name, article, now, previous_text, text, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def get_changes_since(self, date: str) -> list[dict]:
        """지정 날짜 이후의 변경 이력을 반환한다.

        Args:
            date: ISO format date string (e.g. '2026-01-01' or '2026-01-01T00:00:00')
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """SELECT id, law_name, article, version_date,
                          previous_text, current_text, detected_at
                   FROM law_versions
                   WHERE detected_at >= ?
                   ORDER BY detected_at DESC""",
                (date,),
            )
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "law_name": r[1],
                    "article": r[2],
                    "version_date": r[3],
                    "previous_text": r[4],
                    "current_text": r[5],
                    "detected_at": r[6],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_all_versions(self) -> list[dict]:
        """모든 버전 이력을 반환한다."""
        return self.get_changes_since("1970-01-01")


# ---------------------------------------------------------------------------
# FAQUpdateNotifier: FAQ 영향 분석 및 알림
# ---------------------------------------------------------------------------

class FAQUpdateNotifier:
    """법령 변경 시 영향을 받는 FAQ 항목을 식별하고 알림을 관리한다."""

    def __init__(self, faq_path: str | None = None, db_path: str | None = None):
        self.faq_path = faq_path or os.path.join(BASE_DIR, "data", "faq.json")
        self.db_path = db_path or DEFAULT_DB_PATH
        self._init_db()

    def _init_db(self):
        """알림 테이블을 초기화한다."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS update_notifications (
                    id TEXT PRIMARY KEY,
                    faq_id TEXT NOT NULL,
                    affected_field TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    law_name TEXT,
                    article TEXT,
                    created_at TEXT NOT NULL,
                    acknowledged INTEGER DEFAULT 0,
                    acknowledged_at TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _load_faq_items(self) -> list[dict]:
        """FAQ 항목을 로드한다."""
        if os.path.exists(self.faq_path):
            with open(self.faq_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("items", [])
        return []

    def analyze_impact(self, law_name: str, article: str) -> list[dict]:
        """법령 변경이 영향을 미치는 FAQ 항목을 식별한다.

        Returns:
            list of {"faq_id": "...", "affected_field": "legal_basis", "reason": "법령 변경 감지"}
        """
        faq_items = self._load_faq_items()
        affected = []

        for item in faq_items:
            legal_basis_list = item.get("legal_basis", [])
            for basis in legal_basis_list:
                # 법령명과 조항 번호가 모두 포함되어 있으면 매칭
                if law_name in basis and article in basis:
                    affected.append({
                        "faq_id": item.get("id", ""),
                        "affected_field": "legal_basis",
                        "reason": "법령 변경 감지",
                    })
                    break

        return affected

    def create_notifications(self, law_name: str, article: str) -> list[dict]:
        """법령 변경에 대한 알림을 생성하여 DB에 저장한다.

        Returns:
            생성된 알림 목록
        """
        affected = self.analyze_impact(law_name, article)
        notifications = []

        conn = sqlite3.connect(self.db_path)
        try:
            now = datetime.now().isoformat()
            for item in affected:
                notification_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO update_notifications
                       (id, faq_id, affected_field, reason, law_name, article, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        notification_id,
                        item["faq_id"],
                        item["affected_field"],
                        item["reason"],
                        law_name,
                        article,
                        now,
                    ),
                )
                notifications.append({
                    "id": notification_id,
                    "faq_id": item["faq_id"],
                    "affected_field": item["affected_field"],
                    "reason": item["reason"],
                    "law_name": law_name,
                    "article": article,
                    "created_at": now,
                    "acknowledged": False,
                })
            conn.commit()
        finally:
            conn.close()

        return notifications

    def get_pending_notifications(self) -> list[dict]:
        """미확인 알림 목록을 반환한다."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """SELECT id, faq_id, affected_field, reason, law_name, article,
                          created_at, acknowledged
                   FROM update_notifications
                   WHERE acknowledged = 0
                   ORDER BY created_at DESC"""
            )
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "faq_id": r[1],
                    "affected_field": r[2],
                    "reason": r[3],
                    "law_name": r[4],
                    "article": r[5],
                    "created_at": r[6],
                    "acknowledged": bool(r[7]),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def acknowledge(self, notification_id: str) -> bool:
        """알림을 확인 처리한다.

        Returns:
            True if notification was found and acknowledged, False otherwise.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            now = datetime.now().isoformat()
            cursor = conn.execute(
                """UPDATE update_notifications
                   SET acknowledged = 1, acknowledged_at = ?
                   WHERE id = ? AND acknowledged = 0""",
                (now, notification_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# LawUpdateScheduler: 주기적 업데이트 확인 스케줄러
# ---------------------------------------------------------------------------

class LawUpdateScheduler:
    """법령 업데이트를 주기적으로 확인하는 스케줄러."""

    def __init__(
        self,
        version_tracker: LawVersionTracker | None = None,
        notifier: FAQUpdateNotifier | None = None,
        sync_manager=None,
        on_propagate=None,
    ):
        """
        Args:
            version_tracker: 버전 추적기
            notifier: FAQ 알림 관리자
            sync_manager: 국가법령정보센터 API 클라이언트 (있으면 스케줄 실행 시
                          원문 fetch → legal_references 업데이트 → FAQ 전파까지 수행).
            on_propagate: FAQ 전파 직후 호출될 콜백(예: chatbot.reload_faq).
        """
        self.version_tracker = version_tracker or LawVersionTracker()
        self.notifier = notifier or FAQUpdateNotifier()
        self.sync_manager = sync_manager
        self.on_propagate = on_propagate
        self._timer: threading.Timer | None = None
        self._running = False
        self._interval_hours: float = 24
        self._update_history: list[dict] = []

    def schedule_check(self, interval_hours: float = 24):
        """주기적 업데이트 확인을 설정한다.

        Args:
            interval_hours: 확인 주기 (시간 단위, 기본 24시간)
        """
        self._interval_hours = interval_hours
        self._running = True
        self._schedule_next()

    def _schedule_next(self):
        """다음 확인을 예약한다."""
        if not self._running:
            return
        interval_seconds = self._interval_hours * 3600
        self._timer = threading.Timer(interval_seconds, self._run_check)
        self._timer.daemon = True
        self._timer.start()

    def _run_check(self):
        """예약된 확인을 실행한다."""
        try:
            if self.sync_manager is not None:
                self.run_full_sync()
            else:
                self.check_for_updates()
        finally:
            self._schedule_next()

    def run_full_sync(self) -> dict:
        """국가법령정보센터 API에서 원문을 가져와 FAQ까지 전파하는 풀 사이클을 실행한다.

        `sync_manager`가 주입되어 있을 때만 호출 가능하다.

        Returns:
            {
                "checked_at": str,
                "changes_detected": int,
                "propagated": int,
                "propagated_items": list[str],
                "details": list[dict],
            }
        """
        if self.sync_manager is None:
            raise RuntimeError("sync_manager is not configured")

        sync_result = self.sync_manager.sync_and_propagate()
        check = sync_result.get("check", {})
        propagation = sync_result.get("faq_propagation", {})

        # 변경 이력을 scheduler 기록에도 반영
        for detail in check.get("details", []):
            if detail.get("status") == "changed":
                law_name = detail.get("law_name", "")
                article = detail.get("article", "")
                content_preview = detail.get("content_preview", "")
                if law_name and article:
                    self.version_tracker.record_version(law_name, article, content_preview)
                    self.notifier.create_notifications(law_name, article)

        # 콜백: chatbot.reload_faq 등
        if propagation.get("propagated", 0) > 0 and self.on_propagate is not None:
            try:
                self.on_propagate()
            except Exception:  # noqa: BLE001 (callback errors should not break sync)
                pass

        result = {
            "checked_at": datetime.now().isoformat(),
            "changes_detected": check.get("changes_detected", 0),
            "propagated": propagation.get("propagated", 0),
            "propagated_items": propagation.get("items", []),
            "details": check.get("details", []),
        }
        self._update_history.append(result)
        return result

    def stop(self):
        """스케줄러를 중지한다."""
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def check_for_updates(self) -> dict:
        """현재 legal_references.json을 읽어 버전 변경을 확인한다.

        Returns:
            {
                "checked_at": str,
                "changes_detected": int,
                "notifications_created": int,
                "details": list[dict]
            }
        """
        data = load_json("data/legal_references.json")
        refs = data.get("references", [])

        changes_detected = 0
        notifications_created = 0
        details = []

        for ref in refs:
            law_name = ref.get("law_name", "")
            article = ref.get("article", "")
            summary = ref.get("summary", "")

            if not law_name or not article:
                continue

            changed = self.version_tracker.record_version(law_name, article, summary)
            if changed:
                changes_detected += 1
                # 알림 생성
                notifs = self.notifier.create_notifications(law_name, article)
                notifications_created += len(notifs)
                details.append({
                    "law_name": law_name,
                    "article": article,
                    "notifications": len(notifs),
                })

        result = {
            "checked_at": datetime.now().isoformat(),
            "changes_detected": changes_detected,
            "notifications_created": notifications_created,
            "details": details,
        }
        self._update_history.append(result)
        return result

    def get_update_history(self) -> list[dict]:
        """업데이트 확인 이력을 반환한다."""
        return list(self._update_history)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="법령 업데이트 감지")
    parser.add_argument("--check", action="store_true", help="현재 상태만 확인")
    args = parser.parse_args()

    if args.check:
        state = load_state()
        print(f"마지막 확인: {state.get('last_checked', '없음')}")
        refs = get_legal_references_summary()
        print(f"등록된 법령: {len(refs)}개")
        for ref in refs:
            url_mark = "O" if ref["has_url"] else "X"
            print(f"  [{url_mark}] {ref['law_name']} {ref['article']} - {ref['title']}")
    else:
        result = check_for_updates()
        if result["has_changes"]:
            print(f"변경 감지: {result['changed_files']}")
        else:
            print("변경 없음")
        print(f"확인 시각: {result['last_checked']}")
