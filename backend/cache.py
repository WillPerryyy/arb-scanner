import time
from typing import Any, Optional


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str, ttl: float) -> Optional[Any]:
        entry = self._store.get(key)
        if not entry:
            return None
        value, stored_at = entry
        if time.time() - stored_at > ttl:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, time.time())

    def clear(self) -> None:
        self._store.clear()


cache = TTLCache()
