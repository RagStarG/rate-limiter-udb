"""Storage layer abstraction for rate limiting state.
Implements an Unstructured Key-Value / Hash / Sorted-Set data model.

Supports:
1. Live Redis Backend (with atomic Redis Lua scripts)
2. Thread-Safe In-Memory KV Store (for zero-dependency local runs and tests)

Author: Suman Mondal (RagStarG)
Subject: Unstructured Database (UDB Assignment)
"""

import os
import time
import math
import logging
import threading
from abc import ABC, abstractmethod
from typing import Dict, Optional, Any, List, Tuple

logger = logging.getLogger(__name__)

# Atomic Redis Lua script for Token Bucket evaluation
# Guarantees zero race conditions across distributed microservices
REDIS_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local state = redis.call('HMGET', key, 'tokens', 'last_refill')
local stored_tokens = tonumber(state[1])
local last_refill = tonumber(state[2])

local current_tokens
if not stored_tokens or not last_refill then
    current_tokens = capacity - 1.0
    last_refill = now
    redis.call('HMSET', key, 'tokens', tostring(current_tokens), 'last_refill', tostring(last_refill))
    redis.call('EXPIRE', key, ttl)
    return {1, tostring(current_tokens), tostring(0)}
end

local elapsed = math.max(0, now - last_refill)
current_tokens = math.min(capacity, stored_tokens + (elapsed * refill_rate))

if current_tokens >= 1.0 then
    local remaining = current_tokens - 1.0
    redis.call('HMSET', key, 'tokens', tostring(remaining), 'last_refill', tostring(now))
    redis.call('EXPIRE', key, ttl)
    return {1, tostring(remaining), tostring(0)}
else
    local needed = (1.0 - current_tokens) / refill_rate
    local retry_after = math.max(1, math.ceil(needed))
    redis.call('HMSET', key, 'tokens', tostring(current_tokens), 'last_refill', tostring(now))
    redis.call('EXPIRE', key, ttl)
    return {0, tostring(current_tokens), tostring(retry_after)}
end
"""


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

    @abstractmethod
    def get_backend_name(self) -> str:
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

    def get_backend_name(self) -> str:
        return "InMemoryKVStore (Thread-Safe)"

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


class RedisBackend(StorageBackend):
    """Production Redis Backend implementing Redis unstructured primitives and Lua scripting."""

    def __init__(self, redis_client):
        self.client = redis_client
        self._tb_lua = self.client.register_script(REDIS_TOKEN_BUCKET_LUA)

    def get_backend_name(self) -> str:
        return "Redis (Remote NoSQL Engine)"

    def eval_token_bucket(self, key: str, capacity: float, refill_rate: float, now: float, ttl: int):
        """Execute atomic Redis Lua script for Token Bucket."""
        res = self._tb_lua(keys=[key], args=[capacity, refill_rate, now, ttl])
        allowed = bool(int(res[0]))
        remaining = float(res[1])
        retry_after = int(res[2])
        return allowed, remaining, retry_after

    def get(self, key: str) -> Optional[str]:
        val = self.client.get(key)
        return val.decode("utf-8") if isinstance(val, bytes) else (str(val) if val is not None else None)

    def set(self, key: str, value: str, ttl_seconds: Optional[float] = None) -> bool:
        if ttl_seconds and ttl_seconds > 0:
            return bool(self.client.set(key, str(value), ex=int(math.ceil(ttl_seconds))))
        return bool(self.client.set(key, str(value)))

    def incr(self, key: str, amount: int = 1, ttl_seconds: Optional[float] = None) -> int:
        pipe = self.client.pipeline()
        pipe.incrby(key, amount)
        if ttl_seconds and ttl_seconds > 0:
            pipe.expire(key, int(math.ceil(ttl_seconds)))
        results = pipe.execute()
        return int(results[0])

    def expire(self, key: str, ttl_seconds: float) -> bool:
        return bool(self.client.expire(key, int(math.ceil(ttl_seconds))))

    def hgetall(self, key: str) -> Dict[str, str]:
        raw = self.client.hgetall(key)
        return {
            (k.decode() if isinstance(k, bytes) else str(k)): (v.decode() if isinstance(v, bytes) else str(v))
            for k, v in raw.items()
        }

    def hset(self, key: str, mapping: Dict[str, str], ttl_seconds: Optional[float] = None) -> bool:
        pipe = self.client.pipeline()
        pipe.hset(key, mapping=mapping)
        if ttl_seconds and ttl_seconds > 0:
            pipe.expire(key, int(math.ceil(ttl_seconds)))
        pipe.execute()
        return True

    def zadd(self, key: str, mapping: Dict[str, float], ttl_seconds: Optional[float] = None) -> int:
        pipe = self.client.pipeline()
        pipe.zadd(key, mapping)
        if ttl_seconds and ttl_seconds > 0:
            pipe.expire(key, int(math.ceil(ttl_seconds)))
        res = pipe.execute()
        return int(res[0])

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        return int(self.client.zremrangebyscore(key, min_score, max_score))

    def zcard(self, key: str) -> int:
        return int(self.client.zcard(key))

    def delete(self, key: str) -> bool:
        return bool(self.client.delete(key))

    def flush_all(self) -> None:
        self.client.flushdb()


def create_storage_backend(redis_url: Optional[str] = None) -> StorageBackend:
    """Factory creating RedisBackend if Redis is available, else falling back to InMemoryKVStore."""
    url = redis_url or os.environ.get("REDIS_URL")
    if url:
        try:
            import redis
            client = redis.from_url(url, decode_responses=False, socket_timeout=1.5)
            client.ping()
            logger.info("Connected to Redis backend at %s", url)
            return RedisBackend(client)
        except Exception as e:
            logger.warning("Redis connection failed (%s). Falling back to InMemoryKVStore.", e)
            return InMemoryKVStore()
    return InMemoryKVStore()
