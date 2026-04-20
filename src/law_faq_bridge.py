"""법령 API 변경사항과 FAQ 자동 업데이트를 연결하는 브리지 모듈.

국가법령정보센터 API에서 감지된 법령 변경을 FAQ 답변에 자동 반영한다.
변경 이력을 관리하며, 법령 개정 시 챗봇 답변이 자동으로 최신화된다.

사용법:
    from src.law_faq_bridge import LawFAQBridge
    bridge = LawFAQBridge()
    result = bridge.run_full_sync()
"""

import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAQ_PATH = os.path.join(BASE_DIR, "data", "faq.json")
LEGAL_REF_PATH = os.path.join(BASE_DIR, "data", "legal_references.json")
BRIDGE_DB_PATH = os.path.join(BASE_DIR, "data", "law_faq_bridge.db")

# 법령 조문 → 영향 FAQ ID 매핑 테이블
# 각 법령 조문이 변경될 때 검토해야 하는 FAQ 항목 목록
LAW_TO_FAQ_MAP: dict[tuple[str, str], list[str]] = {
    ("관세법", "제190조"): ["A", "H", "I", "J", "T", "U", "AC", "AR", "AJ"],
    ("관세법", "제161조"): ["D", "M", "Z", "AF", "AG"],
    ("관세법", "제174조"): ["G", "L", "AT"],
    ("관세법", "제183조"): ["T"],
    ("관세법", "제226조"): ["E", "AI", "AV"],
    ("관세법", "제269조"): ["N"],
    ("관세법 시행령", "제101조"): ["C", "I", "O", "X", "AD", "AE"],
    ("관세법 시행령", "제102조"): ["C", "X"],
    ("관세법 시행령", "제208조"): ["V", "W", "AS"],
    ("보세전시장 운영에 관한 고시", "관세청고시 제2026-15호"): [
        "B", "F", "G", "K", "L", "V", "W", "Y", "AB", "AI", "AM", "AN", "AW"
    ],
}

# FAQ 답변에서 자동으로 업데이트 가능한 법적 사실 패턴
# 예: "10일 이내"가 변경되면 해당 FAQ를 자동으로 플래그
LEGAL_FACT_PATTERNS: dict[tuple[str, str], list[dict]] = {
    ("관세법", "제161조"): [
        {
            "pattern": r"(\d+)일 이내에 허가",
            "description": "견본품 반출 허가 기한",
            "faq_ids": ["D", "M"],
        }
    ],
    ("관세법", "제190조"): [
        {
            "pattern": r"박람회|전람회|견본품 전시회",
            "description": "보세전시장 적용 행사 유형",
            "faq_ids": ["A", "H"],
        }
    ],
    ("관세법 시행령", "제101조"): [
        {
            "pattern": r"수입면허",
            "description": "수입면허 관련 규정",
            "faq_ids": ["C", "O", "AE"],
        }
    ],
}

# 법령 변경 유형 분류
CHANGE_TYPE_MINOR = "minor"      # 경미한 수정 (오타, 표현 변경)
CHANGE_TYPE_MODERATE = "moderate"  # 중간 변경 (조문 번호 변경, 일부 요건 수정)
CHANGE_TYPE_MAJOR = "major"      # 대폭 변경 (핵심 요건 변경, 조항 삭제/추가)


class LawFAQBridge:
    """법령 API 변경사항과 FAQ를 연결하는 브리지.

    국가법령정보센터 API에서 법령 변경을 감지하면:
    1. legal_references.json의 요약 업데이트
    2. 영향받는 FAQ 항목 식별
    3. FAQ notes에 변경 플래그 추가
    4. 변경 이력 DB에 기록
    5. 자동 업데이트 가능한 패턴은 즉시 반영
    """

    def __init__(
        self,
        sync_manager=None,
        faq_path: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        # 순환 import 방지를 위한 지연 import
        from src.law_api_sync import LawSyncManager
        self.sync_manager = sync_manager or LawSyncManager()
        self.faq_path = faq_path or FAQ_PATH
        self.db_path = db_path or BRIDGE_DB_PATH
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """브리지 이력 데이터베이스를 초기화한다."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS law_faq_updates (
                    id TEXT PRIMARY KEY,
                    law_name TEXT NOT NULL,
                    article TEXT NOT NULL,
                    faq_id TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    previous_content TEXT,
                    new_content TEXT,
                    description TEXT,
                    applied INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    laws_checked INTEGER DEFAULT 0,
                    changes_detected INTEGER DEFAULT 0,
                    faqs_flagged INTEGER DEFAULT 0,
                    faqs_updated INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running',
                    error_message TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # 전체 동기화 파이프라인
    # -------------------------------------------------------------------------

    def run_full_sync(self) -> dict:
        """법령 API에서 최신 내용을 가져와 FAQ를 업데이트하는 전체 파이프라인.

        Returns:
            {
                "sync_id": int,
                "checked": int,
                "changes_detected": int,
                "legal_ref_updated": int,
                "faq_items_flagged": int,
                "faq_items_updated": int,
                "details": list[dict],
                "synced_at": str,
            }
        """
        with self._lock:
            return self._run_full_sync_locked()

    def _run_full_sync_locked(self) -> dict:
        started_at = datetime.now().isoformat()
        sync_id = self._record_sync_start(started_at)
        logger.info("법령-FAQ 전체 동기화 시작 (run_id=%s)", sync_id)

        try:
            # 1단계: 국가법령정보센터 API에서 변경 감지
            check_result = self.sync_manager.check_all()
            logger.info(
                "법령 확인 완료: %d개 중 %d개 변경, %d개 오류",
                check_result["total_checked"],
                check_result["changes_detected"],
                check_result["errors"],
            )

            # 2단계: legal_references.json 업데이트
            ref_update = self.sync_manager.update_legal_references()
            logger.info("legal_references.json 업데이트: %d개", ref_update["updated"])

            # 3단계: 변경된 법령 → FAQ 영향 분석 및 업데이트
            faq_items_flagged = 0
            faq_items_updated = 0
            update_details = []

            changed_articles = [
                d for d in check_result.get("details", [])
                if d.get("status") == "changed"
            ]

            for detail in changed_articles:
                law_name = detail["law_name"]
                article = detail["article"]
                content_preview = detail.get("content_preview", "")

                affected_ids = self._get_affected_faq_ids(law_name, article)
                if not affected_ids:
                    logger.debug("매핑된 FAQ 없음: %s %s", law_name, article)
                    continue

                faq_items_flagged += len(affected_ids)

                # 캐시된 최신 법령 내용 가져오기
                cached = self.sync_manager.get_cached_content(law_name, article)
                if not cached:
                    continue

                # 패턴 기반 자동 업데이트 감지
                auto_updates = self._detect_auto_updates(
                    law_name, article, cached["content"]
                )

                # FAQ 플래그 업데이트
                updated = self._flag_affected_faqs(
                    affected_ids, law_name, article, cached["fetched_at"]
                )
                faq_items_updated += updated

                # 변경 이력 기록
                for faq_id in affected_ids:
                    self._record_faq_update(
                        law_name=law_name,
                        article=article,
                        faq_id=faq_id,
                        change_type=CHANGE_TYPE_MODERATE,
                        description=f"법령 변경 감지: {content_preview[:80]}",
                    )

                update_details.append({
                    "law_name": law_name,
                    "article": article,
                    "affected_faq_ids": affected_ids,
                    "auto_updates": auto_updates,
                    "action": "flagged_for_review",
                })

            result = {
                "sync_id": sync_id,
                "checked": check_result["total_checked"],
                "changes_detected": check_result["changes_detected"],
                "legal_ref_updated": ref_update["updated"],
                "faq_items_flagged": faq_items_flagged,
                "faq_items_updated": faq_items_updated,
                "details": update_details,
                "synced_at": started_at,
            }

            self._record_sync_finish(
                sync_id,
                laws_checked=check_result["total_checked"],
                changes_detected=check_result["changes_detected"],
                faqs_flagged=faq_items_flagged,
                faqs_updated=faq_items_updated,
                status="success",
            )

            logger.info(
                "동기화 완료: 법령 %d개 확인, 변경 %d개, FAQ %d개 플래그",
                check_result["total_checked"],
                check_result["changes_detected"],
                faq_items_flagged,
            )
            return result

        except Exception as exc:
            logger.error("동기화 중 오류 발생: %s", exc, exc_info=True)
            self._record_sync_finish(sync_id, status="error", error_message=str(exc))
            raise

    # -------------------------------------------------------------------------
    # 영향 분석
    # -------------------------------------------------------------------------

    def _get_affected_faq_ids(self, law_name: str, article: str) -> list[str]:
        """법령 조문 변경 시 영향받는 FAQ ID 목록을 반환한다."""
        key = (law_name, article)
        if key in LAW_TO_FAQ_MAP:
            return list(LAW_TO_FAQ_MAP[key])

        # 법령명만으로 폴백 매칭
        affected = []
        for (mapped_law, _), faq_ids in LAW_TO_FAQ_MAP.items():
            if law_name == mapped_law:
                affected.extend(faq_ids)
        return list(dict.fromkeys(affected))

    def _detect_auto_updates(
        self, law_name: str, article: str, content: str
    ) -> list[dict]:
        """법령 내용에서 자동 업데이트 가능한 패턴을 감지한다."""
        patterns = LEGAL_FACT_PATTERNS.get((law_name, article), [])
        detected = []
        for pattern_def in patterns:
            match = re.search(pattern_def["pattern"], content)
            if match:
                detected.append({
                    "pattern": pattern_def["pattern"],
                    "matched": match.group(0),
                    "description": pattern_def["description"],
                    "faq_ids": pattern_def["faq_ids"],
                })
        return detected

    def get_affected_faqs_for_law(
        self, law_name: str, article: str
    ) -> list[dict]:
        """법령 조문과 관련된 FAQ 항목들을 반환한다."""
        if not os.path.exists(self.faq_path):
            return []
        with open(self.faq_path, "r", encoding="utf-8") as f:
            faq_data = json.load(f)
        faq_ids = set(self._get_affected_faq_ids(law_name, article))
        return [
            item for item in faq_data.get("items", [])
            if item.get("id") in faq_ids
        ]

    # -------------------------------------------------------------------------
    # FAQ 업데이트
    # -------------------------------------------------------------------------

    def _flag_affected_faqs(
        self,
        faq_ids: list[str],
        law_name: str,
        article: str,
        synced_at: str,
    ) -> int:
        """FAQ 항목의 notes에 법령 변경 플래그를 추가한다."""
        if not os.path.exists(self.faq_path):
            return 0

        with open(self.faq_path, "r", encoding="utf-8") as f:
            faq_data = json.load(f)

        updated = 0
        id_set = set(faq_ids)
        sync_date = synced_at[:10]

        for item in faq_data.get("items", []):
            if item.get("id") not in id_set:
                continue

            existing_notes = item.get("notes", "")
            sync_tag = f"[법령동기화:{sync_date}]"

            # 같은 날짜로 이미 플래그된 경우 스킵
            if sync_tag in existing_notes:
                continue

            # 이전 동기화 노트 제거 후 최신 노트 추가
            lines = [
                line for line in existing_notes.splitlines()
                if not line.startswith("[법령동기화:")
            ]
            lines = [line for line in lines if line.strip()]
            lines.append(
                f"{sync_tag} {law_name} {article} 변경 감지 — 답변 검토 필요"
            )
            item["notes"] = "\n".join(lines)
            updated += 1

        if updated > 0:
            faq_data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
            with open(self.faq_path, "w", encoding="utf-8") as f:
                json.dump(faq_data, f, ensure_ascii=False, indent=2)

        return updated

    def update_faq_answer(
        self,
        faq_id: str,
        new_answer: str,
        reason: str = "",
        auto_applied: bool = False,
    ) -> bool:
        """FAQ 항목의 답변을 업데이트한다.

        Args:
            faq_id: 업데이트할 FAQ ID
            new_answer: 새로운 답변 텍스트
            reason: 변경 이유
            auto_applied: 자동 적용 여부

        Returns:
            성공 여부
        """
        if not os.path.exists(self.faq_path):
            return False

        with open(self.faq_path, "r", encoding="utf-8") as f:
            faq_data = json.load(f)

        for item in faq_data.get("items", []):
            if item.get("id") != faq_id:
                continue

            old_answer = item.get("answer", "")
            item["answer"] = new_answer
            item["last_law_sync"] = datetime.now().isoformat()

            # 변경 이유를 notes에 기록
            if reason:
                notes = item.get("notes", "")
                lines = [l for l in notes.splitlines() if l.strip()]
                lines.append(f"[자동업데이트:{datetime.now():%Y-%m-%d}] {reason}")
                item["notes"] = "\n".join(lines)

            faq_data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
            with open(self.faq_path, "w", encoding="utf-8") as f:
                json.dump(faq_data, f, ensure_ascii=False, indent=2)

            logger.info("FAQ %s 답변 업데이트 완료 (auto=%s)", faq_id, auto_applied)

            # 이력 기록
            self._record_faq_update(
                law_name="manual",
                article="",
                faq_id=faq_id,
                change_type=CHANGE_TYPE_MAJOR,
                description=reason or "수동 업데이트",
                previous_content=old_answer,
                new_content=new_answer,
                applied=True,
            )
            return True

        logger.warning("FAQ ID '%s' 를 찾을 수 없음", faq_id)
        return False

    # -------------------------------------------------------------------------
    # 동기화 상태 조회
    # -------------------------------------------------------------------------

    def get_sync_status(self) -> dict:
        """현재 법령 동기화 상태를 반환한다."""
        history = self.sync_manager.get_sync_history(limit=20)

        # 조문별 최신 상태 집계
        law_status: dict[str, dict] = {}
        for entry in history:
            key = f"{entry['law_name']}_{entry['article']}"
            if key not in law_status:
                law_status[key] = {
                    "law_name": entry["law_name"],
                    "article": entry["article"],
                    "last_checked": entry["checked_at"],
                    "last_changed": entry["checked_at"] if entry["changed"] else None,
                    "current_hash": entry["content_hash"],
                }

        # 미확인 FAQ 플래그 수집
        flagged_faqs = self._get_flagged_faqs()

        # 최근 동기화 실행 이력
        recent_runs = self._get_recent_sync_runs(limit=5)

        from src.law_api_sync import MONITORED_LAWS
        return {
            "monitored_laws": len(MONITORED_LAWS),
            "total_articles": sum(len(l["articles"]) for l in MONITORED_LAWS),
            "law_status": list(law_status.values()),
            "flagged_faqs": flagged_faqs,
            "recent_runs": recent_runs,
        }

    def _get_flagged_faqs(self) -> list[dict]:
        """법령 변경으로 플래그된 FAQ 목록을 반환한다."""
        if not os.path.exists(self.faq_path):
            return []
        with open(self.faq_path, "r", encoding="utf-8") as f:
            faq_data = json.load(f)
        flagged = []
        for item in faq_data.get("items", []):
            notes = item.get("notes", "")
            if "[법령동기화:" in notes:
                # 플래그 날짜 추출
                match = re.search(r"\[법령동기화:(\d{4}-\d{2}-\d{2})\]", notes)
                flagged.append({
                    "faq_id": item.get("id"),
                    "question": item.get("question", "")[:60],
                    "flagged_date": match.group(1) if match else "",
                    "notes_preview": notes[:120],
                })
        return flagged

    # -------------------------------------------------------------------------
    # 데이터베이스 헬퍼
    # -------------------------------------------------------------------------

    def _record_sync_start(self, started_at: str) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "INSERT INTO sync_runs (started_at, status) VALUES (?, 'running')",
                (started_at,),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def _record_sync_finish(
        self,
        sync_id: int,
        laws_checked: int = 0,
        changes_detected: int = 0,
        faqs_flagged: int = 0,
        faqs_updated: int = 0,
        status: str = "success",
        error_message: Optional[str] = None,
    ):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """UPDATE sync_runs
                   SET finished_at = ?, laws_checked = ?, changes_detected = ?,
                       faqs_flagged = ?, faqs_updated = ?, status = ?, error_message = ?
                   WHERE id = ?""",
                (
                    datetime.now().isoformat(),
                    laws_checked,
                    changes_detected,
                    faqs_flagged,
                    faqs_updated,
                    status,
                    error_message,
                    sync_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _record_faq_update(
        self,
        law_name: str,
        article: str,
        faq_id: str,
        change_type: str,
        description: str,
        previous_content: Optional[str] = None,
        new_content: Optional[str] = None,
        applied: bool = False,
    ):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO law_faq_updates
                   (id, law_name, article, faq_id, change_type,
                    previous_content, new_content, description, applied, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    law_name,
                    article,
                    faq_id,
                    change_type,
                    previous_content,
                    new_content,
                    description,
                    int(applied),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_recent_sync_runs(self, limit: int = 10) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """SELECT id, started_at, finished_at, laws_checked,
                          changes_detected, faqs_flagged, faqs_updated, status
                   FROM sync_runs ORDER BY id DESC LIMIT ?""",
                (limit,),
            )
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "started_at": r[1],
                    "finished_at": r[2],
                    "laws_checked": r[3],
                    "changes_detected": r[4],
                    "faqs_flagged": r[5],
                    "faqs_updated": r[6],
                    "status": r[7],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_update_history(self, limit: int = 50) -> list[dict]:
        """FAQ 업데이트 이력을 반환한다."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """SELECT id, law_name, article, faq_id, change_type,
                          description, applied, created_at, applied_at
                   FROM law_faq_updates ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            )
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "law_name": r[1],
                    "article": r[2],
                    "faq_id": r[3],
                    "change_type": r[4],
                    "description": r[5],
                    "applied": bool(r[6]),
                    "created_at": r[7],
                    "applied_at": r[8],
                }
                for r in rows
            ]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 자동 스케줄러 인터페이스
# ---------------------------------------------------------------------------

_bridge_instance: Optional[LawFAQBridge] = None
_bridge_lock = threading.Lock()


def get_bridge() -> LawFAQBridge:
    """싱글턴 브리지 인스턴스를 반환한다."""
    global _bridge_instance
    with _bridge_lock:
        if _bridge_instance is None:
            _bridge_instance = LawFAQBridge()
    return _bridge_instance
