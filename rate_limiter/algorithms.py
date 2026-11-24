"""Rate Limiting Algorithms using Unstructured Key-Value / Hash Data Models.

Implements:
1. Token Bucket Algorithm (with lazy refill and Hash storage)
2. Sliding Window Counter Algorithm (Weighted approximation)
3. Fixed Window Counter Algorithm (Baseline)

Author: Suman Mondal (AU/2024/0000570)
Subject: Unstructured Database (UDB Assignment)
"""

import time
import math
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import Optional
from .storage import StorageBackend


@dataclass
class RateLimitResult:
    """Standardized result returned by rate limiter checks."""
    allowed: bool
    limit: int
    remaining: int
    reset_epoch: int
    retry_after: int


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

        # Fetch current bucket state from unstructured hash
        state = self.storage.hgetall(key)
        
        if not state or "tokens" not in state or "last_refill" not in state:
            # First request: initialize full bucket minus 1 token
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
            # Consume 1 token
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
            )
        else:
            # Deficit: client exceeded quota
            time_needed = (1.0 - current_tokens) / self.refill_rate
            retry_after = max(1, int(math.ceil(time_needed)))
            
            # Persist the refilled tokens state
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
            )


class SlidingWindowCounterRateLimiter(BaseRateLimiter):
    """Sliding Window Counter using Key-Value Counters with TTL.
    
    Data Structure in Storage:
        Key format: 'rl:swc:<client_id>:<window_timestamp>'
        Type: String (Integer Counter)
        TTL: window_seconds * 2
        
    Mathematical Logic:
        Divides time into fixed discrete buckets of size `window_seconds`.
        Approximates sliding count using weighted formula:
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

        # Fetch counts
        prev_count_raw = self.storage.get(prev_key)
        prev_count = int(prev_count_raw) if prev_count_raw else 0

        curr_count_raw = self.storage.get(curr_key)
        curr_count = int(curr_count_raw) if curr_count_raw else 0

        # Calculate time passed within current window
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
            )
        else:
            retry_after = max(1, int(reset_epoch - now))
            return RateLimitResult(
                allowed=False,
                limit=self.limit,
                remaining=0,
                reset_epoch=reset_epoch,
                retry_after=retry_after,
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
            )
        else:
            retry_after = max(1, int(reset_epoch - now))
            return RateLimitResult(
                allowed=False,
                limit=self.limit,
                remaining=0,
                reset_epoch=reset_epoch,
                retry_after=retry_after,
            )
