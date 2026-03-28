"""Advanced Rate Limiting module.

Provides per-endpoint rate limits with sliding window algorithm,
per-user daily quotas, and usage statistics.
"""

import fnmatch
import logging
import threading
import time
from collections import defaultdict

logger = logging.getLogger("chatbot.rate_limiter_v2")


class AdvancedRateLimiter:
    """Advanced rate limiter with per-endpoint limits and per-user quotas.

    Features:
        - Per-endpoint sliding window rate limits
        - Per-user daily quotas keyed by API key
        - Usage statistics and top-user tracking
        - Thread-safe in-memory storage with periodic cleanup
    """

    DEFAULT_ENDPOINT_LIMITS = {
        "/api/chat": 30,
        "/api/faq": 60,
        "/api/admin/*": 20,
        "/api/autocomplete": 120,
    }

    DEFAULT_DAILY_QUOTA = 1000
    WINDOW_SECONDS = 60
    CLEANUP_INTERVAL = 300  # 5 minutes

    def __init__(self, endpoint_limits=None, default_daily_quota=None):
        self._endpoint_limits = dict(self.DEFAULT_ENDPOINT_LIMITS)
        if endpoint_limits:
            self._endpoint_limits.update(endpoint_limits)

        self._default_daily_quota = (
            default_daily_quota
            if default_daily_quota is not None
            else self.DEFAULT_DAILY_QUOTA
        )

        # Sliding window data: {(ip, endpoint_pattern): [timestamps]}
        self._requests = defaultdict(list)

        # Per-user quota data: {api_key: {"timestamps": [ts], "daily_limit": int}}
        self._quotas = defaultdict(lambda: {
            "timestamps": [],
            "daily_limit": self._default_daily_quota,
        })

        # Usage stats tracking
        self._endpoint_hits = defaultdict(int)
        self._user_hits = defaultdict(int)

        self._lock = threading.Lock()
        self._last_cleanup = time.time()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _match_endpoint(self, endpoint):
        """Find the best matching endpoint pattern for the given path.

        Exact matches are preferred over glob patterns. Among glob patterns,
        the longest pattern wins.
        """
        # Exact match first
        if endpoint in self._endpoint_limits:
            return endpoint

        # Glob match – longest pattern wins
        best = None
        for pattern in self._endpoint_limits:
            if fnmatch.fnmatch(endpoint, pattern):
                if best is None or len(pattern) > len(best):
                    best = pattern
        return best

    def _cleanup(self):
        """Periodically remove expired entries."""
        now = time.time()
        if now - self._last_cleanup < self.CLEANUP_INTERVAL:
            return

        cutoff_window = now - self.WINDOW_SECONDS
        cutoff_day = now - 86400

        keys_to_delete = []
        for key, timestamps in self._requests.items():
            self._requests[key] = [t for t in timestamps if t > cutoff_window]
            if not self._requests[key]:
                keys_to_delete.append(key)
        for key in keys_to_delete:
            del self._requests[key]

        keys_to_delete = []
        for api_key, data in self._quotas.items():
            data["timestamps"] = [t for t in data["timestamps"] if t > cutoff_day]
            if not data["timestamps"]:
                keys_to_delete.append(api_key)
        for key in keys_to_delete:
            del self._quotas[key]

        self._last_cleanup = now

    def _get_window_timestamps(self, key, now):
        """Return timestamps within the current sliding window."""
        cutoff = now - self.WINDOW_SECONDS
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]
        return self._requests[key]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_rate_limit(self, ip, endpoint):
        """Check if a request from *ip* to *endpoint* is allowed.

        Returns:
            (allowed, remaining, reset_time) where reset_time is a Unix
            timestamp indicating when the window resets.
        """
        now = time.time()

        with self._lock:
            self._cleanup()

            pattern = self._match_endpoint(endpoint)
            if pattern is None:
                # No limit configured for this endpoint – allow
                return (True, -1, 0)

            limit = self._endpoint_limits[pattern]
            key = (ip, pattern)

            timestamps = self._get_window_timestamps(key, now)
            remaining = max(0, limit - len(timestamps))
            reset_time = int(now + self.WINDOW_SECONDS)

            if len(timestamps) >= limit:
                return (False, 0, reset_time)

            # Record the request
            self._requests[key].append(now)
            remaining = max(0, limit - len(self._requests[key]))

            # Track stats
            self._endpoint_hits[endpoint] += 1

            return (True, remaining, reset_time)

    def check_quota(self, api_key):
        """Check whether *api_key* still has daily quota remaining.

        Returns:
            (allowed, used, limit, reset_time) where reset_time is the Unix
            timestamp when the daily window resets (24h from first request).
        """
        if not api_key:
            return (True, 0, self._default_daily_quota, 0)

        now = time.time()
        cutoff = now - 86400

        with self._lock:
            data = self._quotas[api_key]
            daily_limit = data["daily_limit"]

            # Keep only last 24h
            data["timestamps"] = [t for t in data["timestamps"] if t > cutoff]
            used = len(data["timestamps"])

            if data["timestamps"]:
                reset_time = int(data["timestamps"][0] + 86400)
            else:
                reset_time = int(now + 86400)

            if used >= daily_limit:
                return (False, used, daily_limit, reset_time)

            data["timestamps"].append(now)
            used += 1
            self._user_hits[api_key] += 1

            return (True, used, daily_limit, reset_time)

    def get_usage_stats(self, api_key=None):
        """Return usage statistics.

        If *api_key* is provided, returns stats for that user only.
        Otherwise, returns aggregate stats.
        """
        now = time.time()
        cutoff_day = now - 86400

        with self._lock:
            if api_key:
                data = self._quotas.get(api_key)
                if data is None:
                    return {
                        "api_key": api_key,
                        "used_today": 0,
                        "daily_limit": self._default_daily_quota,
                        "total_hits": 0,
                    }
                recent = [t for t in data["timestamps"] if t > cutoff_day]
                return {
                    "api_key": api_key,
                    "used_today": len(recent),
                    "daily_limit": data["daily_limit"],
                    "total_hits": self._user_hits.get(api_key, 0),
                }

            # Aggregate stats
            total_requests = sum(self._endpoint_hits.values())
            endpoint_stats = dict(self._endpoint_hits)
            active_users = len([
                k for k, d in self._quotas.items()
                if any(t > cutoff_day for t in d["timestamps"])
            ])

            return {
                "total_requests": total_requests,
                "endpoint_stats": endpoint_stats,
                "active_users": active_users,
                "endpoint_limits": dict(self._endpoint_limits),
                "default_daily_quota": self._default_daily_quota,
            }

    def get_top_users(self, limit=10):
        """Return the most active users sorted by total hits (descending)."""
        with self._lock:
            sorted_users = sorted(
                self._user_hits.items(), key=lambda x: x[1], reverse=True
            )
            return [
                {"api_key": api_key, "total_hits": hits}
                for api_key, hits in sorted_users[:limit]
            ]

    def set_endpoint_limit(self, endpoint_pattern, requests_per_minute):
        """Configure or update the rate limit for an endpoint pattern."""
        with self._lock:
            self._endpoint_limits[endpoint_pattern] = requests_per_minute
        logger.info(
            f"Endpoint limit set: {endpoint_pattern} = {requests_per_minute}/min"
        )

    def set_user_quota(self, api_key, daily_limit):
        """Configure a per-user daily quota."""
        with self._lock:
            self._quotas[api_key]["daily_limit"] = daily_limit
        logger.info(f"User quota set: {api_key} = {daily_limit}/day")

    def reset(self, ip=None):
        """Clear rate-limit data. If *ip* given, only clear that IP's data."""
        with self._lock:
            if ip:
                keys_to_delete = [k for k in self._requests if k[0] == ip]
                for key in keys_to_delete:
                    del self._requests[key]
            else:
                self._requests.clear()
                self._quotas.clear()
                self._endpoint_hits.clear()
                self._user_hits.clear()
