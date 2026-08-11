"""In-memory LRU cache for preview responses."""

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock


@dataclass(slots=True)
class CachedPreview:
    """Cached preview with headers and body."""

    headers: dict[str, str]
    body: bytes


class PreviewCache:
    """Thread-safe LRU cache for preview responses."""

    def __init__(self, capacity: int = 500):
        self.capacity = capacity
        self._cache: OrderedDict[str, CachedPreview] = OrderedDict()
        self._lock = Lock()

    def get(self, key: str) -> CachedPreview | None:
        """Get cached preview, moving it to end (most recently used)."""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        return None

    def set(self, key: str, value: CachedPreview) -> None:
        """Cache preview, evicting oldest if at capacity."""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self.capacity:
                    self._cache.popitem(last=False)
                self._cache[key] = value

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)
