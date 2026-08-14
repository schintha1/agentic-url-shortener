import time


class SlidingWindowLimiter:
    """In-memory per-key sliding window limiter."""

    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}

    def reset(self) -> None:
        self._hits.clear()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = [t for t in self._hits.get(key, []) if now - t < self.window_seconds]
        if len(window) >= self.limit:
            self._hits[key] = window
            return False
        window.append(now)
        self._hits[key] = window
        return True
