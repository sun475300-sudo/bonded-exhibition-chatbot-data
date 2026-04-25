"""A/B testing system for chatbot answers.

Allows creating tests with multiple answer variants for FAQ items,
consistently assigning variants to sessions, and tracking metrics
to determine statistically significant winners.
"""

import hashlib
import json
import math
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "ab_tests.db")

VALID_METRICS = ["helpful_rate", "escalation_rate", "follow_up_rate"]


class ABTestManager:
    """Manages A/B tests for chatbot FAQ answer variants."""

    def __init__(self, db_path=None, faq_path=None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.faq_path = faq_path or os.path.join(BASE_DIR, "data", "faq.json")
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Create tables if they do not exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS ab_tests (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    faq_id TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    stopped_at TEXT
                );
                CREATE TABLE IF NOT EXISTS ab_variants (
                    id TEXT PRIMARY KEY,
                    test_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    FOREIGN KEY (test_id) REFERENCES ab_tests(id)
                );
                CREATE TABLE IF NOT EXISTS ab_impressions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_id TEXT NOT NULL,
                    variant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (test_id) REFERENCES ab_tests(id),
                    FOREIGN KEY (variant_id) REFERENCES ab_variants(id)
                );
                CREATE TABLE IF NOT EXISTS ab_conversions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_id TEXT NOT NULL,
                    variant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (test_id) REFERENCES ab_tests(id),
                    FOREIGN KEY (variant_id) REFERENCES ab_variants(id)
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create_test(self, name, faq_id, variants):
        """Create a new A/B test with multiple answer variants.

        Args:
            name: Human-readable test name
            faq_id: FAQ item ID being tested
            variants: List of dicts with 'name' and 'answer' keys

        Returns:
            Dict with test info including variant IDs.

        Raises:
            ValueError: If fewer than 2 variants provided or invalid input.
        """
        if not name or not name.strip():
            raise ValueError("Test name is required")
        if not faq_id or not str(faq_id).strip():
            raise ValueError("FAQ ID is required")
        if not variants or len(variants) < 2:
            raise ValueError("At least 2 variants are required")

        for v in variants:
            if not v.get("name") or not v.get("answer"):
                raise ValueError("Each variant must have 'name' and 'answer'")

        test_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        variant_records = []
        for v in variants:
            vid = str(uuid.uuid4())[:8]
            variant_records.append({
                "id": vid,
                "test_id": test_id,
                "name": v["name"],
                "answer": v["answer"],
            })

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO ab_tests (id, name, faq_id, active, created_at) VALUES (?, ?, ?, 1, ?)",
                    (test_id, name.strip(), str(faq_id).strip(), now),
                )
                for vr in variant_records:
                    conn.execute(
                        "INSERT INTO ab_variants (id, test_id, name, answer) VALUES (?, ?, ?, ?)",
                        (vr["id"], vr["test_id"], vr["name"], vr["answer"]),
                    )
                conn.commit()
            finally:
                conn.close()

        return {
            "id": test_id,
            "name": name.strip(),
            "faq_id": str(faq_id).strip(),
            "active": True,
            "created_at": now,
            "variants": variant_records,
        }

    def get_variant(self, test_id, session_id):
        """Consistently assign a variant to a session using hash-based assignment.

        Args:
            test_id: The A/B test ID
            session_id: The user session ID

        Returns:
            Dict with variant info, or None if test not found or inactive.
        """
        conn = self._connect()
        try:
            test = conn.execute(
                "SELECT * FROM ab_tests WHERE id = ? AND active = 1", (test_id,)
            ).fetchone()
            if not test:
                return None

            variants = conn.execute(
                "SELECT * FROM ab_variants WHERE test_id = ? ORDER BY id",
                (test_id,),
            ).fetchall()
            if not variants:
                return None

            # Hash-based consistent assignment
            hash_input = f"{test_id}:{session_id}"
            hash_val = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
            index = hash_val % len(variants)
            chosen = variants[index]

            return {
                "id": chosen["id"],
                "test_id": chosen["test_id"],
                "name": chosen["name"],
                "answer": chosen["answer"],
            }
        finally:
            conn.close()

    def record_impression(self, test_id, variant_id, session_id):
        """Record that a variant was shown to a session.

        Args:
            test_id: The A/B test ID
            variant_id: The variant ID shown
            session_id: The user session ID

        Returns:
            True if recorded, False if test not found.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            test = conn.execute(
                "SELECT id FROM ab_tests WHERE id = ?", (test_id,)
            ).fetchone()
            if not test:
                return False

            conn.execute(
                "INSERT INTO ab_impressions (test_id, variant_id, session_id, created_at) VALUES (?, ?, ?, ?)",
                (test_id, variant_id, session_id, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def record_conversion(self, test_id, variant_id, session_id, metric):
        """Record a conversion event for a variant.

        Args:
            test_id: The A/B test ID
            variant_id: The variant ID
            session_id: The user session ID
            metric: One of helpful_rate, escalation_rate, follow_up_rate

        Returns:
            True if recorded, False if test not found.

        Raises:
            ValueError: If metric is not valid.
        """
        if metric not in VALID_METRICS:
            raise ValueError(f"Invalid metric '{metric}'. Must be one of: {', '.join(VALID_METRICS)}")

        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            test = conn.execute(
                "SELECT id FROM ab_tests WHERE id = ?", (test_id,)
            ).fetchone()
            if not test:
                return False

            conn.execute(
                "INSERT INTO ab_conversions (test_id, variant_id, session_id, metric, created_at) VALUES (?, ?, ?, ?, ?)",
                (test_id, variant_id, session_id, metric, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def get_results(self, test_id):
        """Get detailed results for a test including per-variant stats.

        Returns:
            Dict with test info and per-variant impressions, conversions,
            conversion rate, and confidence indicator, or None if not found.
        """
        conn = self._connect()
        try:
            test = conn.execute(
                "SELECT * FROM ab_tests WHERE id = ?", (test_id,)
            ).fetchone()
            if not test:
                return None

            variants = conn.execute(
                "SELECT * FROM ab_variants WHERE test_id = ?", (test_id,)
            ).fetchall()

            variant_results = []
            for v in variants:
                impressions = conn.execute(
                    "SELECT COUNT(*) as cnt FROM ab_impressions WHERE test_id = ? AND variant_id = ?",
                    (test_id, v["id"]),
                ).fetchone()["cnt"]

                conversions = conn.execute(
                    "SELECT COUNT(*) as cnt FROM ab_conversions WHERE test_id = ? AND variant_id = ?",
                    (test_id, v["id"]),
                ).fetchone()["cnt"]

                # Per-metric breakdown
                metrics_breakdown = {}
                for m in VALID_METRICS:
                    m_count = conn.execute(
                        "SELECT COUNT(*) as cnt FROM ab_conversions WHERE test_id = ? AND variant_id = ? AND metric = ?",
                        (test_id, v["id"], m),
                    ).fetchone()["cnt"]
                    metrics_breakdown[m] = m_count

                rate = conversions / impressions if impressions > 0 else 0.0

                variant_results.append({
                    "id": v["id"],
                    "name": v["name"],
                    "answer": v["answer"],
                    "impressions": impressions,
                    "conversions": conversions,
                    "conversion_rate": round(rate, 4),
                    "metrics": metrics_breakdown,
                })

            # Compute statistical significance (chi-squared approximation)
            confidence = self._compute_confidence(variant_results)

            return {
                "test_id": test_id,
                "name": test["name"],
                "faq_id": test["faq_id"],
                "active": bool(test["active"]),
                "created_at": test["created_at"],
                "stopped_at": test["stopped_at"],
                "variants": variant_results,
                "significant": confidence >= 0.95,
                "confidence": round(confidence, 4),
            }
        finally:
            conn.close()

    def _compute_confidence(self, variant_results):
        """Compute statistical significance using chi-squared approximation.

        Uses a 2xN contingency table (converted vs not-converted for each variant)
        and approximates the chi-squared p-value.

        Returns a confidence level between 0 and 1.
        """
        total_impressions = sum(v["impressions"] for v in variant_results)
        total_conversions = sum(v["conversions"] for v in variant_results)

        if total_impressions == 0 or total_conversions == 0:
            return 0.0
        if total_conversions == total_impressions:
            return 0.0

        # Chi-squared test for independence
        k = len(variant_results)
        if k < 2:
            return 0.0

        chi_sq = 0.0
        total_non_conversions = total_impressions - total_conversions

        for v in variant_results:
            n_i = v["impressions"]
            if n_i == 0:
                continue
            # Expected values
            e_conv = (n_i * total_conversions) / total_impressions
            e_non = (n_i * total_non_conversions) / total_impressions

            if e_conv > 0:
                chi_sq += ((v["conversions"] - e_conv) ** 2) / e_conv
            if e_non > 0:
                non_conv = n_i - v["conversions"]
                chi_sq += ((non_conv - e_non) ** 2) / e_non

        # Degrees of freedom = k - 1
        df = k - 1

        # Approximate p-value using the survival function of chi-squared
        # Using the regularized incomplete gamma function approximation
        p_value = self._chi_sq_p_value(chi_sq, df)
        confidence = 1.0 - p_value

        return max(0.0, min(1.0, confidence))

    @staticmethod
    def _chi_sq_p_value(chi_sq, df):
        """Approximate p-value for chi-squared distribution.

        Uses a simple approximation based on the Wilson-Hilferty transformation.
        For df >= 1, transforms chi-squared to approximate normal and uses
        the complementary error function.
        """
        if chi_sq <= 0 or df <= 0:
            return 1.0

        # Wilson-Hilferty approximation: transform chi-sq to normal
        # Z = ((chi_sq/df)^(1/3) - (1 - 2/(9*df))) / sqrt(2/(9*df))
        try:
            ratio = chi_sq / df
            term = 2.0 / (9.0 * df)
            z = (ratio ** (1.0 / 3.0) - (1.0 - term)) / math.sqrt(term)
        except (ValueError, ZeroDivisionError, OverflowError):
            return 1.0

        # P(Z > z) using complementary error function
        # Phi(z) = 0.5 * erfc(-z / sqrt(2))
        # P-value = 1 - Phi(z) = 0.5 * erfc(z / sqrt(2))
        try:
            p_value = 0.5 * math.erfc(z / math.sqrt(2))
        except (ValueError, OverflowError):
            return 0.0

        return max(0.0, min(1.0, p_value))

    def get_winner(self, test_id):
        """Return the best performing variant for a test.

        Returns:
            Dict with winner variant info including stats, or None if
            test not found or no impressions recorded.
        """
        results = self.get_results(test_id)
        if not results:
            return None

        variants = results["variants"]
        if not variants:
            return None

        # Filter variants with at least 1 impression
        with_impressions = [v for v in variants if v["impressions"] > 0]
        if not with_impressions:
            return None

        best = max(with_impressions, key=lambda v: v["conversion_rate"])
        return {
            "variant": best,
            "significant": results["significant"],
            "confidence": results["confidence"],
        }

    def list_tests(self, active_only=True):
        """List all A/B tests.

        Args:
            active_only: If True, return only active tests.

        Returns:
            List of test dicts with variant counts.
        """
        conn = self._connect()
        try:
            if active_only:
                tests = conn.execute(
                    "SELECT * FROM ab_tests WHERE active = 1 ORDER BY created_at DESC"
                ).fetchall()
            else:
                tests = conn.execute(
                    "SELECT * FROM ab_tests ORDER BY created_at DESC"
                ).fetchall()

            result = []
            for t in tests:
                variant_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM ab_variants WHERE test_id = ?",
                    (t["id"],),
                ).fetchone()["cnt"]

                total_impressions = conn.execute(
                    "SELECT COUNT(*) as cnt FROM ab_impressions WHERE test_id = ?",
                    (t["id"],),
                ).fetchone()["cnt"]

                result.append({
                    "id": t["id"],
                    "name": t["name"],
                    "faq_id": t["faq_id"],
                    "active": bool(t["active"]),
                    "created_at": t["created_at"],
                    "stopped_at": t["stopped_at"],
                    "variant_count": variant_count,
                    "total_impressions": total_impressions,
                })

            return result
        finally:
            conn.close()

    def stop_test(self, test_id):
        """Deactivate a test.

        Args:
            test_id: The test ID to stop.

        Returns:
            True if stopped, False if not found.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE ab_tests SET active = 0, stopped_at = ? WHERE id = ? AND active = 1",
                (now, test_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def apply_winner(self, test_id):
        """Apply the winning variant's answer to the FAQ item.

        Stops the test and updates the FAQ JSON file with the winner's answer.

        Args:
            test_id: The test ID.

        Returns:
            Dict with applied variant info, or None if no winner.

        Raises:
            ValueError: If test not found or no clear winner.
        """
        winner_info = self.get_winner(test_id)
        if not winner_info:
            raise ValueError("No winner found for this test (no impressions or test not found)")

        winner_variant = winner_info["variant"]

        # Get test info
        conn = self._connect()
        try:
            test = conn.execute(
                "SELECT * FROM ab_tests WHERE id = ?", (test_id,)
            ).fetchone()
            if not test:
                raise ValueError(f"Test '{test_id}' not found")
            faq_id = test["faq_id"]
        finally:
            conn.close()

        # Update FAQ file
        with open(self.faq_path, "r", encoding="utf-8") as f:
            faq_data = json.load(f)

        updated = False
        for item in faq_data.get("items", []):
            if item.get("id") == faq_id:
                item["answer"] = winner_variant["answer"]
                updated = True
                break

        if not updated:
            raise ValueError(f"FAQ item '{faq_id}' not found in FAQ data")

        faq_data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with open(self.faq_path, "w", encoding="utf-8") as f:
            json.dump(faq_data, f, ensure_ascii=False, indent=2)
            f.write("\n")

        # Stop the test
        self.stop_test(test_id)

        return {
            "test_id": test_id,
            "faq_id": faq_id,
            "applied_variant": winner_variant,
            "significant": winner_info["significant"],
            "confidence": winner_info["confidence"],
        }
