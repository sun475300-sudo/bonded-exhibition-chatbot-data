"""Prometheus-compatible metrics collector.

Pure Python implementation -- no external libraries required.
All operations are thread-safe.
"""

import threading
import time
import math
from collections import defaultdict


class MetricsCollector:
    """Collects counters, histograms, and gauges in Prometheus text format."""

    # Default histogram buckets (seconds)
    DEFAULT_BUCKETS = (
        0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
    )

    def __init__(self):
        self._lock = threading.Lock()

        # Counters: name -> {frozen_labels: count}
        self._counters: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

        # Histograms: name -> {frozen_labels: {"buckets": {...}, "sum": float, "count": int}}
        self._histograms: dict[str, dict[str, dict]] = defaultdict(dict)
        self._histogram_buckets: dict[str, tuple[float, ...]] = {}

        # Gauges: name -> {frozen_labels: value}
        self._gauges: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

        # Metric metadata: name -> {"type": ..., "help": ...}
        self._metadata: dict[str, dict[str, str]] = {}

        # Register default metrics
        self._register_defaults()

    def _register_defaults(self):
        """Register the standard chatbot metrics."""
        self.register_counter(
            "request_count",
            "Total number of HTTP requests",
        )
        self.register_histogram(
            "request_duration_seconds",
            "HTTP request duration in seconds",
        )
        self.register_gauge("active_sessions", "Number of active chat sessions")
        self.register_gauge("faq_count", "Number of FAQ items loaded")
        self.register_gauge("cache_hit_rate", "Cache hit rate (0.0 - 1.0)")

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------

    def register_counter(self, name: str, help_text: str = "") -> None:
        with self._lock:
            self._metadata[name] = {"type": "counter", "help": help_text}

    def register_histogram(
        self,
        name: str,
        help_text: str = "",
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        with self._lock:
            self._metadata[name] = {"type": "histogram", "help": help_text}
            self._histogram_buckets[name] = buckets or self.DEFAULT_BUCKETS

    def register_gauge(self, name: str, help_text: str = "") -> None:
        with self._lock:
            self._metadata[name] = {"type": "gauge", "help": help_text}

    # ------------------------------------------------------------------
    # Mutation methods
    # ------------------------------------------------------------------

    def increment(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        """Increment a counter by *value* (default 1)."""
        key = self._freeze_labels(labels)
        with self._lock:
            self._counters[name][key] += value

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Record an observation in a histogram."""
        key = self._freeze_labels(labels)
        with self._lock:
            buckets = self._histogram_buckets.get(name, self.DEFAULT_BUCKETS)
            if key not in self._histograms[name]:
                self._histograms[name][key] = {
                    "buckets": {b: 0 for b in buckets},
                    "sum": 0.0,
                    "count": 0,
                }
            entry = self._histograms[name][key]
            entry["sum"] += value
            entry["count"] += 1
            for b in buckets:
                if value <= b:
                    entry["buckets"][b] += 1

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge to an absolute value."""
        key = self._freeze_labels(labels)
        with self._lock:
            self._gauges[name][key] = value

    # ------------------------------------------------------------------
    # Collection / exposition
    # ------------------------------------------------------------------

    def collect(self) -> str:
        """Return all metrics in Prometheus text exposition format."""
        with self._lock:
            return self._render_unlocked()

    def _render_unlocked(self) -> str:
        """Render metrics (must be called while holding _lock)."""
        lines: list[str] = []

        # Render counters
        for name, label_map in sorted(self._counters.items()):
            meta = self._metadata.get(name, {})
            if meta.get("help"):
                lines.append(f"# HELP {name} {meta['help']}")
            lines.append(f"# TYPE {name} counter")
            for frozen_labels, val in sorted(label_map.items()):
                label_str = self._label_str(frozen_labels)
                lines.append(f"{name}{label_str} {self._fmt(val)}")

        # Render histograms
        for name, label_map in sorted(self._histograms.items()):
            meta = self._metadata.get(name, {})
            if meta.get("help"):
                lines.append(f"# HELP {name} {meta['help']}")
            lines.append(f"# TYPE {name} histogram")
            for frozen_labels, entry in sorted(label_map.items()):
                base_labels = dict(self._thaw_labels(frozen_labels))
                cumulative = 0
                for bound in sorted(entry["buckets"]):
                    cumulative += entry["buckets"][bound]
                    le_labels = {**base_labels, "le": self._fmt(bound)}
                    le_str = self._label_str(self._freeze_labels(le_labels))
                    lines.append(f"{name}_bucket{le_str} {cumulative}")
                # +Inf bucket
                inf_labels = {**base_labels, "le": "+Inf"}
                inf_str = self._label_str(self._freeze_labels(inf_labels))
                lines.append(f"{name}_bucket{inf_str} {entry['count']}")
                label_str = self._label_str(frozen_labels)
                lines.append(f"{name}_sum{label_str} {self._fmt(entry['sum'])}")
                lines.append(f"{name}_count{label_str} {entry['count']}")

        # Render gauges
        for name, label_map in sorted(self._gauges.items()):
            meta = self._metadata.get(name, {})
            if meta.get("help"):
                lines.append(f"# HELP {name} {meta['help']}")
            lines.append(f"# TYPE {name} gauge")
            for frozen_labels, val in sorted(label_map.items()):
                label_str = self._label_str(frozen_labels)
                lines.append(f"{name}{label_str} {self._fmt(val)}")

        lines.append("")  # trailing newline
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _freeze_labels(labels: dict[str, str] | None) -> tuple:
        if not labels:
            return ()
        return tuple(sorted(labels.items()))

    @staticmethod
    def _thaw_labels(frozen: tuple) -> list[tuple[str, str]]:
        return list(frozen)

    @staticmethod
    def _label_str(frozen_labels: tuple) -> str:
        if not frozen_labels:
            return ""
        pairs = [f'{k}="{v}"' for k, v in frozen_labels]
        return "{" + ",".join(pairs) + "}"

    @staticmethod
    def _fmt(value: float) -> str:
        """Format a numeric value for Prometheus exposition."""
        if value == float("inf"):
            return "+Inf"
        if value == float("-inf"):
            return "-Inf"
        if math.isnan(value):
            return "NaN"
        if value == int(value):
            return str(int(value))
        return f"{value:.6g}"


# Module-level singleton for convenience
metrics = MetricsCollector()
