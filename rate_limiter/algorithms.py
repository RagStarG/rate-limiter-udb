"""Rate Limiting Algorithms using Unstructured Key-Value / Hash / Sorted-Set Data Models.

Implements all 5 classic industry rate limiting algorithms:
1. Token Bucket (with atomic Lua scripts and continuous lazy refill)
2. Sliding Window Counter (weighted moving average approximation)
3. Sliding Window Log (exact timestamp tracking via Sorted Sets)
4. Fixed Window Counter (discrete window with atomic increment)
5. Leaky Bucket (FIFO traffic shaper with constant outflow)

Author: Suman Mondal (RagStarG)
Subject: Unstructured Database (UDB Assignment)
"""

import time
import math
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import Optional
from .storage import StorageBackend, RedisBackend


@dataclass
class RateLimitResult:
    """Standardized result returned by rate limiter checks."""
    allowed: bool
    limit: int
    remaining: int
    reset_epoch: int
    retry_after: int
    algorithm: str = "token_bucket"


class BaseRateLimiter(ABC):
    """Base class for rate limiter algorithms."""

    def __init__(self, storage: StorageBackend, limit: int, window_seconds: int):
        self.storage = storage
        self.limit = int(limit)
        self.window_seconds = int(window_seconds)

    @abstractmethod
    def is_allowed(self, client_id: str) -> RateLimitResult:
        """Evaluate whether a request from client_id should be permitted."""
        pass


class TokenBucketRateLimiter(BaseRateLimiter):
    """Token Bucket Rate Limiter using an Unstructured Hash Data Model.
    
    Data Structure in Storage:
        Key: 'rl:tb:<client_id>'
        Type: Hash (dictionary)
        Fields:
            - 'tokens': Current token balance (float)
            - 'last_refill': Unix timestamp in seconds (float)
        TTL: 2 * window_seconds (auto-evicts inactive clients)
        
    Mathematical Logic:
        Refill Rate r = capacity / window_seconds (tokens/second)
        Elapsed Time dt = now - last_refill
        Tokens = min(capacity, current_tokens + (dt * r))
        If Tokens >= 1:
            Tokens -= 1
            ALLOW
        Else:
            REJECT (retry_after = ceil((1.0 - Tokens) / r))
    """

    def __init__(self, storage: StorageBackend, capacity: int, window_seconds: int):
        super().__init__(storage, limit=capacity, window_seconds=window_seconds)
        self.capacity = float(capacity)
        self.refill_rate = self.capacity / float(window_seconds)

    def is_allowed(self, client_id: str) -> RateLimitResult:
        now = time.time()
        key = f"rl:tb:{client_id}"
        ttl = self.window_seconds * 2

        # Fast path: If running against real Redis, use compiled atomic Lua script
        if isinstance(self.storage, RedisBackend):
            allowed, rem, retry_after = self.storage.eval_token_bucket(
                key=key,
                capacity=self.capacity,
                refill_rate=self.refill_rate,
                now=now,
                ttl=ttl,
            )
            reset_epoch = int(now + (retry_after if not allowed else ((self.capacity - rem) / self.refill_rate)))
            return RateLimitResult(
                allowed=allowed,
                limit=int(self.capacity),
                remaining=int(math.floor(max(0.0, rem))),
                reset_epoch=reset_epoch,
                retry_after=retry_after,
                algorithm="token_bucket",
            )

        # In-Memory path: Thread-safe atomic evaluation
        state = self.storage.hgetall(key)
        
        if not state or "tokens" not in state or "last_refill" not in state:
            current_tokens = self.capacity - 1.0
            last_refill = now
            self.storage.hset(
                key,
                {"tokens": str(current_tokens), "last_refill": str(last_refill)},
                ttl_seconds=ttl,
            )
            return RateLimitResult(
                allowed=True,
                limit=int(self.capacity),
                remaining=int(math.floor(current_tokens)),
                reset_epoch=int(now + (1.0 / self.refill_rate)),
                retry_after=0,
                algorithm="token_bucket",
            )

        try:
            stored_tokens = float(state["tokens"])
            last_refill = float(state["last_refill"])
        except (ValueError, TypeError):
            stored_tokens = self.capacity
            last_refill = now

        # Compute continuous lazy refill
        elapsed = max(0.0, now - last_refill)
        refilled_tokens = stored_tokens + (elapsed * self.refill_rate)
        current_tokens = min(self.capacity, refilled_tokens)

        if current_tokens >= 1.0:
            remaining_tokens = current_tokens - 1.0
            self.storage.hset(
                key,
                {"tokens": str(remaining_tokens), "last_refill": str(now)},
                ttl_seconds=ttl,
            )
            return RateLimitResult(
                allowed=True,
                limit=int(self.capacity),
                remaining=int(math.floor(remaining_tokens)),
                reset_epoch=int(now + ((self.capacity - remaining_tokens) / self.refill_rate)),
                retry_after=0,
                algorithm="token_bucket",
            )
        else:
            time_needed = (1.0 - current_tokens) / self.refill_rate
            retry_after = max(1, int(math.ceil(time_needed)))
            self.storage.hset(
                key,
                {"tokens": str(current_tokens), "last_refill": str(now)},
                ttl_seconds=ttl,
            )
            return RateLimitResult(
                allowed=False,
                limit=int(self.capacity),
                remaining=0,
                reset_epoch=int(now + retry_after),
                retry_after=retry_after,
                algorithm="token_bucket",
            )


class SlidingWindowCounterRateLimiter(BaseRateLimiter):
    """Sliding Window Counter using Key-Value Counters with TTL.
    
    Data Structure in Storage:
        Key format: 'rl:swc:<client_id>:<window_timestamp>'
        Type: String (Integer Counter)
        TTL: window_seconds * 2
        
    Mathematical Logic:
        Divides time into fixed discrete buckets of size `window_seconds`.
        overlap_weight = 1.0 - ((now - current_window_start) / window_seconds)
        estimated_requests = (previous_window_count * overlap_weight) + current_window_count
    """

    def is_allowed(self, client_id: str) -> RateLimitResult:
        now = time.time()
        w_size = self.window_seconds
        
        current_window_start = int(now // w_size) * w_size
        prev_window_start = current_window_start - w_size
        
        curr_key = f"rl:swc:{client_id}:{current_window_start}"
        prev_key = f"rl:swc:{client_id}:{prev_window_start}"

        prev_count_raw = self.storage.get(prev_key)
        prev_count = int(prev_count_raw) if prev_count_raw else 0

        curr_count_raw = self.storage.get(curr_key)
        curr_count = int(curr_count_raw) if curr_count_raw else 0

        time_into_curr_window = now - current_window_start
        weight_prev = max(0.0, 1.0 - (time_into_curr_window / float(w_size)))
        estimated_count = (prev_count * weight_prev) + curr_count

        reset_epoch = current_window_start + w_size

        if estimated_count < self.limit:
            new_curr = self.storage.incr(curr_key, 1, ttl_seconds=w_size * 2)
            new_estimated = (prev_count * weight_prev) + new_curr
            remaining = max(0, self.limit - int(math.ceil(new_estimated)))
            return RateLimitResult(
                allowed=True,
                limit=self.limit,
                remaining=remaining,
                reset_epoch=reset_epoch,
                retry_after=0,
                algorithm="sliding_window_counter",
            )
        else:
            retry_after = max(1, int(reset_epoch - now))
            return RateLimitResult(
                allowed=False,
                limit=self.limit,
                remaining=0,
                reset_epoch=reset_epoch,
                retry_after=retry_after,
                algorithm="sliding_window_counter",
            )


class SlidingWindowLogRateLimiter(BaseRateLimiter):
    """Sliding Window Log using Sorted Sets (ZSET).
    
    Stores every individual request timestamp.
    Provides 100% precision with zero boundary spikes.
    """

    def is_allowed(self, client_id: str) -> RateLimitResult:
        now = time.time()
        key = f"rl:swl:{client_id}"
        window_start = now - self.window_seconds
        ttl = self.window_seconds * 2

        # Remove timestamps older than current sliding window
        self.storage.zremrangebyscore(key, 0.0, window_start)

        # Count remaining requests in current window
        current_count = self.storage.zcard(key)

        if current_count < self.limit:
            # Add current request with microsecond precision
            req_id = f"{now:.6f}"
            self.storage.zadd(key, {req_id: now}, ttl_seconds=ttl)
            remaining = self.limit - (current_count + 1)
            return RateLimitResult(
                allowed=True,
                limit=self.limit,
                remaining=remaining,
                reset_epoch=int(now + self.window_seconds),
                retry_after=0,
                algorithm="sliding_window_log",
            )
        else:
            retry_after = max(1, int(math.ceil(self.window_seconds - (now - window_start))))
            return RateLimitResult(
                allowed=False,
                limit=self.limit,
                remaining=0,
                reset_epoch=int(now + retry_after),
                retry_after=retry_after,
                algorithm="sliding_window_log",
            )


class FixedWindowRateLimiter(BaseRateLimiter):
    """Fixed Window Counter Rate Limiter.
    
    Data Structure in Storage:
        Key format: 'rl:fwc:<client_id>:<window_timestamp>'
        Type: String (Integer Counter)
        TTL: window_seconds
    """

    def is_allowed(self, client_id: str) -> RateLimitResult:
        now = time.time()
        w_size = self.window_seconds
        current_window_start = int(now // w_size) * w_size
        reset_epoch = current_window_start + w_size
        key = f"rl:fwc:{client_id}:{current_window_start}"

        current_val = self.storage.incr(key, 1, ttl_seconds=w_size)

        if current_val <= self.limit:
            remaining = self.limit - current_val
            return RateLimitResult(
                allowed=True,
                limit=self.limit,
                remaining=remaining,
                reset_epoch=reset_epoch,
                retry_after=0,
                algorithm="fixed_window",
            )
        else:
            retry_after = max(1, int(reset_epoch - now))
            return RateLimitResult(
                allowed=False,
                limit=self.limit,
                remaining=0,
                reset_epoch=reset_epoch,
                retry_after=retry_after,
                algorithm="fixed_window",
            )


class LeakyBucketRateLimiter(BaseRateLimiter):
    """Leaky Bucket Traffic Shaper using an Unstructured Hash Data Model.
    
    Water (requests) enters a bucket and leaks out at a constant rate.
    If the water level reaches bucket capacity, incoming requests are dropped.
    """

    def __init__(self, storage: StorageBackend, capacity: int, window_seconds: int):
        super().__init__(storage, limit=capacity, window_seconds=window_seconds)
        self.capacity = float(capacity)
        self.leak_rate = self.capacity / float(window_seconds)

    def is_allowed(self, client_id: str) -> RateLimitResult:
        now = time.time()
        key = f"rl:lb:{client_id}"
        ttl = self.window_seconds * 2

        state = self.storage.hgetall(key)
        if not state:
            water = 1.0
            self.storage.hset(key, {"water": "1.0", "last_leak": str(now)}, ttl_seconds=ttl)
            remaining = int(self.capacity - 1.0)
            return RateLimitResult(
                allowed=True,
                limit=int(self.capacity),
                remaining=remaining,
                reset_epoch=int(now + (1.0 / self.leak_rate)),
                retry_after=0,
                algorithm="leaky_bucket",
            )

        try:
            stored_water = float(state.get("water", 0.0))
            last_leak = float(state.get("last_leak", now))
        except (ValueError, TypeError):
            stored_water = 0.0
            last_leak = now

        elapsed = max(0.0, now - last_leak)
        leaked_water = max(0.0, stored_water - (elapsed * self.leak_rate))

        if leaked_water + 1.0 <= self.capacity:
            new_water = leaked_water + 1.0
            self.storage.hset(key, {"water": str(new_water), "last_leak": str(now)}, ttl_seconds=ttl)
            remaining = int(math.floor(self.capacity - new_water))
            return RateLimitResult(
                allowed=True,
                limit=int(self.capacity),
                remaining=remaining,
                reset_epoch=int(now + (new_water / self.leak_rate)),
                retry_after=0,
                algorithm="leaky_bucket",
            )
        else:
            time_to_leak = (leaked_water + 1.0 - self.capacity) / self.leak_rate
            retry_after = max(1, int(math.ceil(time_to_leak)))
            self.storage.hset(key, {"water": str(leaked_water), "last_leak": str(now)}, ttl_seconds=ttl)
            return RateLimitResult(
                allowed=False,
                limit=int(self.capacity),
                remaining=0,
                reset_epoch=int(now + retry_after),
                retry_after=retry_after,
                algorithm="leaky_bucket",
            )
