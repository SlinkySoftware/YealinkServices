from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class CacheEntry(Generic[T]):
    value: T
    expires_at: float


class InMemoryCfaCache(Generic[T]):
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, CacheEntry[T]] = {}
        self._lock = RLock()

    def get(self, key: str) -> T | None:
        with self._lock:
            self._prune_locked()
            entry = self._entries.get(key)
            if entry is None or entry.expires_at <= monotonic():
                self._entries.pop(key, None)
                return None
            return entry.value

    def set(self, key: str, value: T) -> None:
        with self._lock:
            self._entries[key] = CacheEntry(
                value=value,
                expires_at=monotonic() + self._ttl_seconds,
            )

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _prune_locked(self) -> None:
        now = monotonic()
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)


def build_cache_key(mac: str, dn: str, route_partition: str) -> str:
    return "|".join([mac.upper(), dn, route_partition])
