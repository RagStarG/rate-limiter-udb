"""Storage layer abstraction for rate limiting state.
Implements an Unstructured Key-Value / Hash / Sorted-Set data model.

Author: Suman Mondal (AU/2024/0000570)
Subject: Unstructured Database (UDB Assignment)
"""

import time
import threading
from abc import ABC, abstractmethod
from typing import Dict, Optional, Any, List, Tuple


class StorageBackend(ABC):
    """Abstract interface defining required atomic primitives for rate limiting."""

    @abstractmethod
    def get(self, key: str) -> Optional[str]:
        pass

    @abstractmethod
    def set(self, key: str, value: str, ttl_seconds: Optional[float] = None) -> bool:
        pass

    @abstractmethod
    def incr(self, key: str, amount: int = 1, ttl_seconds: Optional[float] = None) -> int:
        pass

    @abstractmethod
    def expire(self, key: str, ttl_seconds: float) -> bool:
        pass

    @abstractmethod
    def hgetall(self, key: str) -> Dict[str, str]:
        pass

    @abstractmethod
    def hset(self, key: str, mapping: Dict[str, str], ttl_seconds: Optional[float] = None) -> bool:
        pass

    @abstractmethod
    def zadd(self, key: str, mapping: Dict[str, float], ttl_seconds: Optional[float] = None) -> int:
        pass

    @abstractmethod
    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        pass

    @abstractmethod
    def zcard(self, key: str) -> int:
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        pass

    @abstractmethod
    def flush_all(self) -> None:
        pass


class InMemoryKVStore(StorageBackend):
    """Thread-safe, high-performance in-memory unstructured store.
    
    Mimics Redis key-value, hash, and sorted set semantics with TTL support.
    Ideal for local development, unit tests, and stand-alone deployments.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {}
        self._types: Dict[str, str] = {}  # 'string', 'hash', 'zset'
        self._expires: Dict[str, float] = {}

    def _purge_if_expired(self, key: str) -> bool:
        """Internal helper to remove key if TTL has elapsed (lazy eviction)."""
        if key in self._expires:
            if time.time() >= self._expires[key]:
                self._data.pop(key, None)
                self._types.pop(key, None)
                self._expires.pop(key, None)
                return True
        return False

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if self._purge_if_expired(key):
                return None
            val = self._data.get(key)
            return str(val) if val is not None else None

    def set(self, key: str, value: str, ttl_seconds: Optional[float] = None) -> bool:
        with self._lock:
            self._data[key] = str(value)
            self._types[key] = "string"
            if ttl_seconds is not None and ttl_seconds > 0:
                self._expires[key] = time.time() + ttl_seconds
            else:
                self._expires.pop(key, None)
            return True

    def incr(self, key: str, amount: int = 1, ttl_seconds: Optional[float] = None) -> int:
        with self._lock:
            self._purge_if_expired(key)
            current = int(self._data.get(key, 0))
            new_val = current + amount
            self._data[key] = new_val
            self._types[key] = "string"
            if key not in self._expires and ttl_seconds is not None and ttl_seconds > 0:
                self._expires[key] = time.time() + ttl_seconds
            return new_val

    def expire(self, key: str, ttl_seconds: float) -> bool:
        with self._lock:
            if self._purge_if_expired(key) or key not in self._data:
                return False
            self._expires[key] = time.time() + ttl_seconds
            return True

    def hgetall(self, key: str) -> Dict[str, str]:
        with self._lock:
            if self._purge_if_expired(key):
                return {}
            h = self._data.get(key, {})
            if not isinstance(h, dict):
                return {}
            return dict(h)

    def hset(self, key: str, mapping: Dict[str, str], ttl_seconds: Optional[float] = None) -> bool:
        with self._lock:
            self._purge_if_expired(key)
            if key not in self._data or not isinstance(self._data[key], dict):
                self._data[key] = {}
                self._types[key] = "hash"
            for f, v in mapping.items():
                self._data[key][str(f)] = str(v)
            if ttl_seconds is not None and ttl_seconds > 0:
                self._expires[key] = time.time() + ttl_seconds
            return True

    def zadd(self, key: str, mapping: Dict[str, float], ttl_seconds: Optional[float] = None) -> int:
        with self._lock:
            self._purge_if_expired(key)
            if key not in self._data or not isinstance(self._data[key], list):
                self._data[key] = []  # list of (score, member) tuples
                self._types[key] = "zset"
            
            added_count = 0
            existing = {m: s for s, m in self._data[key]}
            for member, score in mapping.items():
                if member not in existing:
                    added_count += 1
                existing[member] = float(score)
            
            # Sort by score ascending
            self._data[key] = sorted([(s, m) for m, s in existing.items()], key=lambda x: x[0])
            if ttl_seconds is not None and ttl_seconds > 0:
                self._expires[key] = time.time() + ttl_seconds
            return added_count

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        with self._lock:
            if self._purge_if_expired(key) or key not in self._data:
                return 0
            entries = self._data[key]
            retained = [item for item in entries if not (min_score <= item[0] <= max_score)]
            removed = len(entries) - len(retained)
            self._data[key] = retained
            return removed

    def zcard(self, key: str) -> int:
        with self._lock:
            if self._purge_if_expired(key) or key not in self._data:
                return 0
            return len(self._data[key])

    def delete(self, key: str) -> bool:
        with self._lock:
            existed = key in self._data
            self._data.pop(key, None)
            self._types.pop(key, None)
            self._expires.pop(key, None)
            return existed

    def flush_all(self) -> None:
        with self._lock:
            self._data.clear()
            self._types.clear()
            self._expires.clear()
