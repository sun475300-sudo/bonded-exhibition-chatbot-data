"""국가법령정보센터 API → FAQ 자동 전파 오케스트레이터.

LawSyncManager(API → legal_references.json)와
FAQUpdateNotifier(legal_references → FAQ 영향 분석)를 묶어
다음을 한 번에 수행한다.

  1) law.go.kr에서 최신 조문 본문을 가져온다.
  2) 변경 감지 시 legal_references.json의 summary·last_synced를 갱신한다.
  3) 영향을 받는 FAQ 항목을 찾아 last_law_synced·law_summary 메타데이터를
     faq.json에 기록한다 (답변 텍스트는 운영자 검토 후 반영하도록 유지).
  4) 실행 결과를 sync 이력 DB에 남긴다.

bonded 챗봇 인스턴스가 주어지면 reload를 호출하여 실행 중인 봇도
최신 FAQ·법령 텍스트로 바로 답변하도록 갱신할 수 있다.

사용법::

    from src.law_auto_sync import LawAutoSyncOrchestrator
    orch = LawAutoSyncOrchestrator()
    result = orch.run_full_sync()

CLI::

    python -m src.law_auto_sync           # 한 번 실행
    python -m src.law_auto_sync --watch   # 24시간 주기로 반복 실행
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any

from src.law_api_sync import LawSyncManager
from src.law_updater import FAQUpdateNotifier, LawVersionTracker

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAQ_PATH = os.path.join(BASE_DIR, "data", "faq.json")
LEGAL_REF_PATH = os.path.join(BASE_DIR, "data", "legal_references.json")


class LawAutoSyncOrchestrator:
    """법령 API → legal_references.json → FAQ 메타데이터 전파 오케스트레이터."""

    def __init__(
        self,
        sync_manager: LawSyncManager | None = None,
        notifier: FAQUpdateNotifier | None = None,
        version_tracker: LawVersionTracker | None = None,
        faq_path: str | None = None,
        legal_ref_path: str | None = None,
    ):
        self.sync_manager = sync_manager or LawSyncManager()
        self.notifier = notifier or FAQUpdateNotifier()
        self.version_tracker = version_tracker or LawVersionTracker()
        self.faq_path = faq_path or FAQ_PATH
        self.legal_ref_path = legal_ref_path or LEGAL_REF_PATH
        self._timer: threading.Timer | None = None
        self._running = False

    # ------------------------------------------------------------------
    # 메인 파이프라인
    # ------------------------------------------------------------------
    def run_full_sync(self, chatbot=None) -> dict[str, Any]:
        """전체 동기화 파이프라인을 실행한다.

        Args:
            chatbot: 선택적 챗봇 인스턴스. 전달되면 reload_data()를 호출한다.

        Returns:
            실행 결과 요약 dict.
        """
        started_at = datetime.now().isoformat()
        result: dict[str, Any] = {
            "started_at": started_at,
            "api_check": None,
            "legal_refs_updated": 0,
            "faq_items_touched": 0,
            "notifications_created": 0,
            "bot_reloaded": False,
            "errors": [],
        }

        # 1) 국가법령정보센터 API에서 전체 모니터링 조문 확인
        try:
            check = self.sync_manager.check_all()
            result["api_check"] = {
                "checked": check["total_checked"],
                "changes": check["changes_detected"],
                "errors": check["errors"],
            }
        except Exception as e:
            logger.error(f"API check 실패: {e}", exc_info=True)
            result["errors"].append(f"api_check: {e}")
            return result

        # 2) legal_references.json 요약을 최신 조문으로 갱신
        try:
            update = self.sync_manager.update_legal_references()
            result["legal_refs_updated"] = update.get("updated", 0)
        except Exception as e:
            logger.error(f"legal_references 갱신 실패: {e}", exc_info=True)
            result["errors"].append(f"legal_refs_update: {e}")

        # 3) 변경된 조문마다 영향 FAQ를 식별하고 메타데이터 전파
        try:
            changed_articles = [
                d for d in (check.get("details") or [])
                if d.get("status") == "changed"
            ]
            touched, notifs = self._propagate_to_faq(changed_articles)
            result["faq_items_touched"] = touched
            result["notifications_created"] = notifs
        except Exception as e:
            logger.error(f"FAQ 전파 실패: {e}", exc_info=True)
            result["errors"].append(f"faq_propagation: {e}")

        # 4) 실행 중 챗봇이 있으면 reload
        if chatbot is not None:
            try:
                reloaded = self._reload_chatbot(chatbot)
                result["bot_reloaded"] = reloaded
            except Exception as e:
                logger.error(f"챗봇 reload 실패: {e}", exc_info=True)
                result["errors"].append(f"bot_reload: {e}")

        result["finished_at"] = datetime.now().isoformat()
        return result

    # ------------------------------------------------------------------
    # FAQ 메타데이터 전파
    # ------------------------------------------------------------------
    def _propagate_to_faq(
        self, changed_articles: list[dict]
    ) -> tuple[int, int]:
        """변경 감지된 조문에 대해 FAQ 메타데이터를 갱신하고 알림을 만든다.

        법조문 본문의 의미 변경은 사람 검토가 필요하므로 answer 텍스트는
        건드리지 않고 다음 필드만 갱신한다.
          - last_law_synced: ISO 타임스탬프
          - law_summary[{law_name}_{article}]: 최신 조문 미리보기

        Returns:
            (touched_faq_count, notifications_created_count)
        """
        if not changed_articles:
            return (0, 0)

        if not os.path.exists(self.faq_path):
            return (0, 0)

        with open(self.faq_path, "r", encoding="utf-8") as f:
            faq_data = json.load(f)

        items = faq_data.get("items", [])
        now = datetime.now().isoformat()
        touched_ids: set[str] = set()
        notifications_total = 0

        for changed in changed_articles:
            law_name = changed.get("law_name", "")
            article = changed.get("article", "")
            preview = changed.get("content_preview", "")

            if not law_name or not article:
                continue

            # 4a) 조문 버전 이력 기록
            try:
                self.version_tracker.record_version(law_name, article, preview)
            except Exception as e:
                logger.warning(f"version_tracker 기록 실패: {e}")

            # 4b) 영향 FAQ 알림 생성 (기존 로직 재사용)
            try:
                notifs = self.notifier.create_notifications(law_name, article)
                notifications_total += len(notifs)
            except Exception as e:
                logger.warning(f"알림 생성 실패: {e}")
                notifs = []

            # 4c) FAQ items에 최신 조문 스니펫 기록
            for item in items:
                legal_basis = item.get("legal_basis", [])
                if any(
                    law_name in basis and article in basis
                    for basis in legal_basis
                ):
                    item["last_law_synced"] = now
                    law_summary = item.setdefault("law_summary", {})
                    law_summary[f"{law_name} {article}"] = preview
                    touched_ids.add(item.get("id", ""))

        # 4d) FAQ 저장 (변경이 있을 때만)
        if touched_ids:
            faq_data["last_updated"] = now.split("T")[0]
            with open(self.faq_path, "w", encoding="utf-8") as f:
                json.dump(faq_data, f, ensure_ascii=False, indent=2)

        return (len(touched_ids), notifications_total)

    # ------------------------------------------------------------------
    # 챗봇 hot-reload
    # ------------------------------------------------------------------
    def _reload_chatbot(self, chatbot) -> bool:
        """실행 중인 챗봇 인스턴스가 FAQ·법령 데이터를 다시 읽도록 한다."""
        from src.utils import load_json

        reload_method = getattr(chatbot, "reload_data", None)
        if callable(reload_method):
            reload_method()
            return True

        # fallback: 주요 필드를 직접 재로드
        try:
            chatbot.faq_data = load_json("data/faq.json")
            chatbot.legal_refs = load_json("data/legal_references.json").get(
                "references", []
            )
            chatbot.faq_items = chatbot._normalize_faq_items(
                chatbot.faq_data.get("items", [])
            )
            # TF-IDF·벡터 인덱스도 재구축
            from src.similarity import TFIDFMatcher
            chatbot.tfidf_matcher = TFIDFMatcher(chatbot.faq_items)
            if getattr(chatbot, "vector_search_enabled", False):
                try:
                    from src.vector_search import VectorSearchEngine
                    chatbot.vector_search = VectorSearchEngine(chatbot.faq_items)
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.error(f"fallback reload 실패: {e}")
            return False

    # ------------------------------------------------------------------
    # 주기 실행 스케줄러
    # ------------------------------------------------------------------
    def start_periodic_sync(
        self, interval_hours: float = 24, chatbot=None
    ):
        """주기적으로 run_full_sync를 호출하는 Timer 스케줄러를 시작한다."""
        self._running = True
        self._interval_hours = interval_hours
        self._chatbot = chatbot
        self._schedule_next()

    def _schedule_next(self):
        if not self._running:
            return
        self._timer = threading.Timer(
            self._interval_hours * 3600, self._tick
        )
        self._timer.daemon = True
        self._timer.start()

    def _tick(self):
        try:
            self.run_full_sync(chatbot=self._chatbot)
        finally:
            self._schedule_next()

    def stop(self):
        """스케줄러를 중지한다."""
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import time

    parser = argparse.ArgumentParser(description="법령 API → FAQ 자동 동기화")
    parser.add_argument("--watch", action="store_true", help="24시간 주기 반복")
    parser.add_argument(
        "--interval", type=float, default=24, help="반복 간격(시간)"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    orch = LawAutoSyncOrchestrator()

    if args.watch:
        orch.start_periodic_sync(interval_hours=args.interval)
        print(f"주기 동기화 시작 (매 {args.interval}시간)")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            orch.stop()
            print("중지됨")
    else:
        result = orch.run_full_sync()
        print(json.dumps(result, ensure_ascii=False, indent=2))
