"""Flask Middleware and Route Decorators for Rate Limiting.

Author: Suman Mondal (RagStarG)
Subject: Unstructured Database (UDB Assignment)
"""

from functools import wraps
from typing import Callable, Optional
from flask import request, jsonify, make_response
from .storage import StorageBackend, create_storage_backend
from .algorithms import (
    BaseRateLimiter,
    TokenBucketRateLimiter,
    SlidingWindowCounterRateLimiter,
    SlidingWindowLogRateLimiter,
    FixedWindowRateLimiter,
    LeakyBucketRateLimiter,
    RateLimitResult,
)

# Global default store (auto-detects Redis or falls back to in-memory)
_default_storage = create_storage_backend()


def get_client_identifier() -> str:
    """Extract a consistent client identifier from the incoming request.
    
    Priority:
    1. 'X-API-Key' header (for API subscribers/tiers)
    2. 'Authorization' header bearer token / user ID
    3. 'X-Forwarded-For' proxy header
    4. Remote IP address (request.remote_addr)
    """
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"apikey:{api_key.strip()}"

    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        return f"auth:{token[:16]}"

    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
        return f"ip:{client_ip}"

    return f"ip:{request.remote_addr or '127.0.0.1'}"


class RateLimitMiddleware:
    """Manages rate limiter instances mapped to endpoints."""

    def __init__(self, storage: Optional[StorageBackend] = None):
        self.storage = storage or _default_storage
        self._limiters = {}

    def get_limiter(
        self,
        endpoint_name: str,
        limit: int,
        window_seconds: int,
        algorithm: str = "token_bucket",
    ) -> BaseRateLimiter:
        key = f"{endpoint_name}:{algorithm}:{limit}:{window_seconds}"
        if key not in self._limiters:
            if algorithm in ("token_bucket", "tb"):
                limiter = TokenBucketRateLimiter(self.storage, limit, window_seconds)
            elif algorithm in ("sliding_window", "sliding_window_counter", "swc"):
                limiter = SlidingWindowCounterRateLimiter(self.storage, limit, window_seconds)
            elif algorithm in ("sliding_window_log", "swl"):
                limiter = SlidingWindowLogRateLimiter(self.storage, limit, window_seconds)
            elif algorithm in ("fixed_window", "fwc"):
                limiter = FixedWindowRateLimiter(self.storage, limit, window_seconds)
            elif algorithm in ("leaky_bucket", "lb"):
                limiter = LeakyBucketRateLimiter(self.storage, limit, window_seconds)
            else:
                raise ValueError(f"Unknown rate limiter algorithm: {algorithm}")
            self._limiters[key] = limiter
        return self._limiters[key]


# Singleton middleware instance
default_middleware = RateLimitMiddleware()


def rate_limit(
    limit: int = 10,
    window_seconds: int = 60,
    algorithm: str = "token_bucket",
    identifier_func: Optional[Callable[[], str]] = None,
    middleware: Optional[RateLimitMiddleware] = None,
):
    """Flask decorator to apply rate limiting on specific route endpoints."""
    mgr = middleware or default_middleware
    ident_func = identifier_func or get_client_identifier

    def decorator(fn):
        endpoint = fn.__name__
        limiter = mgr.get_limiter(endpoint, limit, window_seconds, algorithm)

        @wraps(fn)
        def wrapper(*args, **kwargs):
            client_id = ident_func()
            namespaced_id = f"{endpoint}:{client_id}"
            
            result: RateLimitResult = limiter.is_allowed(namespaced_id)

            if not result.allowed:
                response = make_response(
                    jsonify(
                        {
                            "status": "error",
                            "error": "Too Many Requests",
                            "message": f"Rate limit exceeded. Maximum {result.limit} requests per {window_seconds}s.",
                            "algorithm": result.algorithm,
                            "limit": result.limit,
                            "remaining": 0,
                            "retry_after_seconds": result.retry_after,
                        }
                    ),
                    429,
                )
                response.headers["X-RateLimit-Limit"] = str(result.limit)
                response.headers["X-RateLimit-Remaining"] = "0"
                response.headers["X-RateLimit-Reset"] = str(result.reset_epoch)
                response.headers["Retry-After"] = str(result.retry_after)
                response.headers["Content-Type"] = "application/json"
                return response

            # Execute view function
            res = fn(*args, **kwargs)
            response = make_response(res)
            response.headers["X-RateLimit-Limit"] = str(result.limit)
            response.headers["X-RateLimit-Remaining"] = str(result.remaining)
            response.headers["X-RateLimit-Reset"] = str(result.reset_epoch)
            response.headers["X-RateLimit-Algorithm"] = result.algorithm
            return response

        return wrapper

    return decorator
