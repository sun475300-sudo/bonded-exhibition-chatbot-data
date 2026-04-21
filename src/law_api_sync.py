"""국가법령정보센터 Open API 연동 모듈.

법령 원문을 자동으로 가져와 변경 여부를 감지하고,
변경 시 FAQ 및 legal_references.json을 자동 업데이트한다.

국가법령정보센터 Open API:
- 법령 검색: https://www.law.go.kr/DRF/lawSearch.do
- 법령 본문: https://www.law.go.kr/DRF/lawService.do
- XML 형식 응답

사용법:
    python -m src.law_api_sync          # 전체 동기화
    python -m src.law_api_sync --check  # 변경 확인만
"""

import json
import os
import hashlib
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import quote

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "law_sync.db")
LEGAL_REF_PATH = os.path.join(BASE_DIR, "data", "legal_references.json")
FAQ_PATH = os.path.join(BASE_DIR, "data", "faq.json")

LAW_API_BASE = "https://www.law.go.kr/DRF/lawService.do"
LAW_SEARCH_BASE = "https://www.law.go.kr/DRF/lawSearch.do"

MONITORED_LAWS = [
    {"law_name": "관세법", "articles": ["제190조", "제161조", "제269조", "제183조", "제174조", "제226조"]},
    {"law_name": "관세법 시행령", "articles": ["제101조", "제102조", "제208조"]},
]


class LawAPIClient:
    """국가법령정보센터 Open API 클라이언트."""

    def __init__(self, oc=None):
        self.oc = oc or os.environ.get("LAW_API_OC", "")

    def search_law(self, law_name):
        """법령명으로 검색하여 법령 ID를 반환한다."""
        params = f"OC={self.oc}&target=law&type=XML&query={quote(law_name)}"
        url = f"{LAW_SEARCH_BASE}?{params}"
        try:
            req = Request(url, headers={"User-Agent": "BondedExhibitionChatbot/1.0"})
            with urlopen(req, timeout=15) as resp:
                xml_data = resp.read().decode("utf-8")
            root = ET.fromstring(xml_data)
            results = []
            for item in root.findall(".//law") or root.findall(".//LawSearch"):
                law_id = self._get_text(item, "법령일련번호") or self._get_text(item, "lawId") or ""
                name = self._get_text(item, "법령명한글") or self._get_text(item, "lawNameKorean") or ""
                if name:
                    results.append({"law_id": law_id, "law_name": name})
            return results
        except (URLError, ET.ParseError) as e:
            return [{"error": str(e)}]

    def get_law_text(self, law_id=None, law_name=None):
        """법령 본문을 가져온다."""
        if law_id:
            params = f"OC={self.oc}&target=law&type=XML&ID={law_id}"
        elif law_name:
            params = f"OC={self.oc}&target=law&type=XML&query={quote(law_name)}"
        else:
            return None
        url = f"{LAW_API_BASE}?{params}"
        try:
            req = Request(url, headers={"User-Agent": "BondedExhibitionChatbot/1.0"})
            with urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except URLError as e:
            return None

    def get_article_text(self, xml_data, article_no):
        """XML에서 특정 조문 본문을 추출한다."""
        if not xml_data:
            return None
        try:
            root = ET.fromstring(xml_data)
            for article in root.iter():
                if article.tag in ("조문", "Article", "조문내용"):
                    no = self._get_text(article, "조문번호") or self._get_text(article, "조문제목") or ""
                    if article_no.replace("제", "").replace("조", "") in no.replace("제", "").replace("조", ""):
                        content = self._get_text(article, "조문내용") or ""
                        if not content:
                            content = "".join(article.itertext()).strip()
                        return content
            for elem in root.iter():
                text = elem.text or ""
                if article_no in text and len(text) > len(article_no) + 10:
                    return text.strip()
        except ET.ParseError:
            pass
        return None

    def _get_text(self, elem, tag):
        child = elem.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return None


class LawSyncManager:
    """법령 변경 감지 및 자동 동기화 매니저."""

    def __init__(self, api_client=None, db_path=None):
        self.client = api_client or LawAPIClient()
        self.db_path = db_path or DB_PATH
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS law_sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    law_name TEXT NOT NULL,
                    article TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    content_preview TEXT,
                    previous_hash TEXT,
                    changed INTEGER DEFAULT 0,
                    checked_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS law_content_cache (
                    law_name TEXT NOT NULL,
                    article TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (law_name, article)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def check_all(self):
        """모니터링 대상 모든 법령 조문의 변경을 확인한다."""
        results = {
            "checked_at": datetime.now().isoformat(),
            "total_checked": 0,
            "changes_detected": 0,
            "errors": 0,
            "details": [],
        }

        for law in MONITORED_LAWS:
            law_name = law["law_name"]
            xml_data = self.client.get_law_text(law_name=law_name)

            for article in law["articles"]:
                results["total_checked"] += 1
                detail = {"law_name": law_name, "article": article}

                if xml_data:
                    content = self.client.get_article_text(xml_data, article)
                    if content:
                        changed = self._record_check(law_name, article, content)
                        detail["status"] = "changed" if changed else "unchanged"
                        detail["content_preview"] = content[:100]
                        if changed:
                            results["changes_detected"] += 1
                    else:
                        detail["status"] = "article_not_found"
                        results["errors"] += 1
                else:
                    detail["status"] = "api_error"
                    results["errors"] += 1

                results["details"].append(detail)

        return results

    def check_single(self, law_name, article):
        """단일 조문의 변경을 확인한다."""
        xml_data = self.client.get_law_text(law_name=law_name)
        if not xml_data:
            return {"status": "api_error", "law_name": law_name, "article": article}

        content = self.client.get_article_text(xml_data, article)
        if not content:
            return {"status": "article_not_found", "law_name": law_name, "article": article}

        changed = self._record_check(law_name, article, content)
        return {
            "status": "changed" if changed else "unchanged",
            "law_name": law_name,
            "article": article,
            "content_preview": content[:200],
        }

    def _record_check(self, law_name, article, content):
        """조문 내용을 기록하고 변경 여부를 반환한다."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT content_hash FROM law_content_cache WHERE law_name = ? AND article = ?",
                (law_name, article),
            )
            row = cursor.fetchone()
            previous_hash = row[0] if row else None
            changed = previous_hash is not None and previous_hash != content_hash

            now = datetime.now().isoformat()
            conn.execute(
                """INSERT OR REPLACE INTO law_content_cache
                   (law_name, article, content, content_hash, fetched_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (law_name, article, content, content_hash, now),
            )
            conn.execute(
                """INSERT INTO law_sync_log
                   (law_name, article, content_hash, content_preview, previous_hash, changed, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (law_name, article, content_hash, content[:100], previous_hash, int(changed), now),
            )
            conn.commit()
            return changed
        finally:
            conn.close()

    def get_sync_history(self, limit=50):
        """동기화 이력을 반환한다."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """SELECT law_name, article, content_hash, content_preview,
                          previous_hash, changed, checked_at
                   FROM law_sync_log ORDER BY id DESC LIMIT ?""",
                (limit,),
            )
            return [
                {
                    "law_name": r[0], "article": r[1], "content_hash": r[2],
                    "content_preview": r[3], "previous_hash": r[4],
                    "changed": bool(r[5]), "checked_at": r[6],
                }
                for r in cursor.fetchall()
            ]
        finally:
            conn.close()

    def get_cached_content(self, law_name, article):
        """캐시된 조문 내용을 반환한다."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT content, content_hash, fetched_at FROM law_content_cache WHERE law_name = ? AND article = ?",
                (law_name, article),
            )
            row = cursor.fetchone()
            if row:
                return {"content": row[0], "content_hash": row[1], "fetched_at": row[2]}
            return None
        finally:
            conn.close()

    def update_legal_references(self):
        """캐시된 최신 조문으로 legal_references.json을 업데이트한다.

        Returns:
            {
                "updated": int,         # 요약이 변경된 항목 수
                "total": int,           # 전체 항목 수
                "changed_refs": list,   # 변경된 (law_name, article) 목록
            }
        """
        if not os.path.exists(LEGAL_REF_PATH):
            return {"updated": 0, "total": 0, "changed_refs": []}

        with open(LEGAL_REF_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        updated = 0
        changed_refs = []
        for ref in data.get("references", []):
            law_name = ref.get("law_name", "")
            article = ref.get("article", "")
            cached = self.get_cached_content(law_name, article)
            if cached and cached["content"]:
                new_summary = cached["content"][:200].strip()
                if new_summary and new_summary != ref.get("summary", ""):
                    ref["summary"] = new_summary
                    ref["last_synced"] = cached["fetched_at"]
                    updated += 1
                    changed_refs.append({"law_name": law_name, "article": article})

        if updated > 0:
            with open(LEGAL_REF_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        return {
            "updated": updated,
            "total": len(data.get("references", [])),
            "changed_refs": changed_refs,
        }

    def sync_and_propagate(self, faq_path=None, legal_ref_path=None, notifier=None):
        """전체 파이프라인: API → legal_references.json → faq.json → 알림.

        1) 모니터링 대상 법령의 변경을 확인한다 (check_all).
        2) 변경이 감지되면 legal_references.json의 요약을 갱신한다.
        3) 갱신된 조문이 포함된 FAQ의 last_synced와 law_snapshot을 업데이트한다.
        4) 변경된 조문별로 FAQUpdateNotifier 알림을 생성한다(옵셔널).

        Args:
            faq_path: 대체 FAQ 경로 (테스트용)
            legal_ref_path: 대체 legal_references 경로 (테스트용)
            notifier: 주입된 FAQUpdateNotifier. None이면 알림 단계 스킵.

        Returns:
            {
                "check": {...},           # check_all 결과
                "legal_refs": {...},      # update_legal_references 결과
                "faq": {...},             # FAQAutoUpdater.propagate 결과
                "notifications": int,     # 생성된 알림 수 (notifier 주입 시)
            }
        """
        check_result = self.check_all()
        legal_result = self.update_legal_references()

        faq_result = {"updated_items": 0, "changed_items": []}
        notifications_created = 0
        if legal_result.get("changed_refs"):
            updater = FAQAutoUpdater(
                faq_path=faq_path,
                legal_ref_path=legal_ref_path,
                sync_manager=self,
            )
            faq_result = updater.propagate(legal_result["changed_refs"])

            if notifier is not None:
                for ref in legal_result["changed_refs"]:
                    try:
                        notifs = notifier.create_notifications(
                            ref["law_name"], ref["article"]
                        )
                        notifications_created += len(notifs)
                    except Exception:
                        # 알림 실패가 전체 파이프라인을 중단시키지 않도록 방어
                        pass

        return {
            "check": check_result,
            "legal_refs": legal_result,
            "faq": faq_result,
            "notifications": notifications_created,
        }

    def get_monitored_laws(self):
        """모니터링 대상 법령 목록을 반환한다."""
        return MONITORED_LAWS


class FAQAutoUpdater:
    """법령 변경을 FAQ에 자동 반영하는 업데이터.

    FAQ 항목의 `legal_basis`에 포함된 법령명/조항이 변경된 경우,
    해당 항목에 `law_snapshot`과 `last_synced` 메타데이터를 기록하고,
    FAQ 파일의 `last_updated`를 갱신한다.

    FAQ `answer` 본문은 자동 덮어쓰지 않는다. 법령 원문의 요약은
    참고용으로 `law_snapshot`에 보존되어 관리자가 답변을 확인할 수
    있도록 한다. (본문 자동 편집은 오역 위험이 있어 수동 검수를
    유지한다.)
    """

    def __init__(self, faq_path=None, legal_ref_path=None, sync_manager=None):
        self.faq_path = faq_path or FAQ_PATH
        self.legal_ref_path = legal_ref_path or LEGAL_REF_PATH
        self.sync_manager = sync_manager or LawSyncManager()

    @staticmethod
    def _basis_matches(basis: str, law_name: str, article: str) -> bool:
        """`legal_basis` 문자열이 특정 법령/조항과 매칭되는지 확인한다."""
        if not basis:
            return False
        return law_name in basis and article in basis

    def find_affected_faq_ids(self, changed_refs):
        """변경된 법령 참조에 영향을 받는 FAQ id 목록을 반환한다."""
        if not os.path.exists(self.faq_path):
            return []

        with open(self.faq_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        affected = []
        for item in data.get("items", []):
            bases = item.get("legal_basis", []) or []
            for ref in changed_refs:
                law_name = ref.get("law_name", "")
                article = ref.get("article", "")
                if any(self._basis_matches(b, law_name, article) for b in bases):
                    affected.append({
                        "faq_id": item.get("id", ""),
                        "law_name": law_name,
                        "article": article,
                    })
                    break
        return affected

    def propagate(self, changed_refs):
        """변경된 법령을 참조하는 FAQ에 메타데이터를 기록한다.

        Args:
            changed_refs: [{"law_name": "...", "article": "..."}, ...]

        Returns:
            {
                "updated_items": int,
                "changed_items": [{"faq_id": "...", "law_name": "...", "article": "..."}],
                "faq_last_updated": str,
            }
        """
        if not os.path.exists(self.faq_path) or not changed_refs:
            return {"updated_items": 0, "changed_items": [], "faq_last_updated": ""}

        with open(self.faq_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        now_iso = datetime.now().isoformat()
        today = now_iso[:10]
        updated_items = 0
        changed_items = []

        for item in data.get("items", []):
            bases = item.get("legal_basis", []) or []
            matched_refs = []
            for ref in changed_refs:
                law_name = ref.get("law_name", "")
                article = ref.get("article", "")
                if any(self._basis_matches(b, law_name, article) for b in bases):
                    matched_refs.append({"law_name": law_name, "article": article})

            if not matched_refs:
                continue

            snapshots = []
            for ref in matched_refs:
                cached = self.sync_manager.get_cached_content(
                    ref["law_name"], ref["article"]
                )
                snapshot = {
                    "law_name": ref["law_name"],
                    "article": ref["article"],
                    "fetched_at": cached["fetched_at"] if cached else now_iso,
                    "summary": (cached["content"][:300] if cached and cached.get("content") else ""),
                }
                snapshots.append(snapshot)

            item["law_snapshot"] = snapshots
            item["last_synced"] = now_iso
            updated_items += 1
            changed_items.append({
                "faq_id": item.get("id", ""),
                "refs": matched_refs,
            })

        if updated_items > 0:
            data["last_updated"] = today
            tmp_path = self.faq_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.faq_path)

        return {
            "updated_items": updated_items,
            "changed_items": changed_items,
            "faq_last_updated": data.get("last_updated", ""),
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="국가법령정보센터 동기화")
    parser.add_argument("--check", action="store_true", help="변경 확인만")
    parser.add_argument("--sync", action="store_true", help="변경 확인 + legal_references 업데이트")
    parser.add_argument("--propagate", action="store_true", help="전체 파이프라인 (API → legal_refs → FAQ)")
    parser.add_argument("--history", action="store_true", help="동기화 이력 조회")
    args = parser.parse_args()

    manager = LawSyncManager()

    if args.history:
        history = manager.get_sync_history(limit=20)
        for h in history:
            mark = "변경" if h["changed"] else "동일"
            print(f"[{mark}] {h['law_name']} {h['article']} - {h['checked_at']}")
    elif args.propagate:
        print("전체 파이프라인 실행 중...")
        result = manager.sync_and_propagate()
        check = result["check"]
        legal = result["legal_refs"]
        faq = result["faq"]
        print(f"확인: {check['total_checked']}개, 변경: {check['changes_detected']}개")
        print(f"legal_references 업데이트: {legal['updated']}/{legal['total']}개")
        print(f"FAQ 메타 업데이트: {faq['updated_items']}개")
    elif args.sync:
        print("법령 변경 확인 중...")
        result = manager.check_all()
        print(f"확인: {result['total_checked']}개, 변경: {result['changes_detected']}개, 오류: {result['errors']}개")
        if result["changes_detected"] > 0:
            print("legal_references.json 업데이트 중...")
            update_result = manager.update_legal_references()
            print(f"업데이트: {update_result['updated']}/{update_result['total']}개")
    else:
        print("법령 변경 확인 중...")
        result = manager.check_all()
        print(f"확인: {result['total_checked']}개, 변경: {result['changes_detected']}개")
        for d in result["details"]:
            print(f"  {d['law_name']} {d['article']}: {d['status']}")
