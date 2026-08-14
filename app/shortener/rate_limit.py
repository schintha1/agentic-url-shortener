import time
from collections import OrderedDict

DEFAULT_MAX_KEYS = 10_000


class SlidingWindowLimiter:
    """In-memory per-key sliding window limiter.

    Keys are evicted least-recently-used past `max_keys` so a long-lived process
    facing many distinct clients cannot grow without bound. Correct for a single
    process only; a fleet needs a shared counter.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: int = 60,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._hits: OrderedDict[str, list[float]] = OrderedDict()

    def reset(self) -> None:
        self._hits.clear()

    def tracked_keys(self) -> int:
        return len(self._hits)

    def _evict_expired(self, now: float) -> None:
        expired = [
            key
            for key, hits in self._hits.items()
            if not hits or now - hits[-1] >= self.window_seconds
        ]
        for key in expired:
            del self._hits[key]

    def _trim_to_cap(self, protected: str) -> None:
        """Drop least-recently-seen keys, never the one currently being served."""

        while len(self._hits) > self.max_keys:
            oldest, _ = next(iter(self._hits.items()))
            if oldest == protected:
                break
            del self._hits[oldest]

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        self._evict_expired(now)
        window = [t for t in self._hits.get(key, []) if now - t < self.window_seconds]
        allowed = len(window) < self.limit
        if allowed:
            window.append(now)
        self._hits[key] = window
        self._hits.move_to_end(key)
        self._trim_to_cap(key)
        return allowed
