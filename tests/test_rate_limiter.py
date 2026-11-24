"""Automated Test Suite for Rate Limiter and Unstructured Storage Models.

Comprehensive tests covering:
1. Storage Primitives (Strings, Hashes, Sorted Sets, TTL Expiration)
2. All 5 Algorithmic Models (Token Bucket, Sliding Window Counter, Sliding Window Log, Fixed Window, Leaky Bucket)
3. Concurrency & Race Condition Verification (ThreadPoolExecutor stress testing)
4. Flask HTTP Endpoints, RFC Header Injection, and Error Payloads

Author: Suman Mondal (RagStarG)
Subject: Unstructured Database (UDB Assignment)
"""

import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from rate_limiter.storage import InMemoryKVStore
from rate_limiter.algorithms import (
    TokenBucketRateLimiter,
    SlidingWindowCounterRateLimiter,
    SlidingWindowLogRateLimiter,
    FixedWindowRateLimiter,
    LeakyBucketRateLimiter,
)
from rate_limiter.app import app, storage_instance


class TestInMemoryKVStore(unittest.TestCase):
    """Verify unstructured storage primitives and TTL semantics."""

    def setUp(self):
        self.store = InMemoryKVStore()

    def test_string_get_set(self):
        self.store.set("test_key", "hello", ttl_seconds=10)
        self.assertEqual(self.store.get("test_key"), "hello")

    def test_ttl_expiration(self):
        self.store.set("temp_key", "val", ttl_seconds=0.1)
        self.assertEqual(self.store.get("temp_key"), "val")
        time.sleep(0.15)
        self.assertIsNone(self.store.get("temp_key"))

    def test_incr_atomic(self):
        val1 = self.store.incr("counter", 1)
        val2 = self.store.incr("counter", 2)
        self.assertEqual(val1, 1)
        self.assertEqual(val2, 3)

    def test_hash_storage(self):
        self.store.hset("user:101", {"name": "Suman", "tokens": "10"})
        data = self.store.hgetall("user:101")
        self.assertEqual(data.get("name"), "Suman")
        self.assertEqual(data.get("tokens"), "10")

    def test_zset_storage(self):
        self.store.zadd("timestamps", {"req_1": 100.0, "req_2": 200.0, "req_3": 300.0})
        self.assertEqual(self.store.zcard("timestamps"), 3)
        removed = self.store.zremrangebyscore("timestamps", 0.0, 150.0)
        self.assertEqual(removed, 1)
        self.assertEqual(self.store.zcard("timestamps"), 2)


class TestAllRateLimitingAlgorithms(unittest.TestCase):
    """Test algorithmic behavior for all 5 implemented models."""

    def setUp(self):
        self.store = InMemoryKVStore()

    def test_token_bucket_exhaustion_and_refill(self):
        limiter = TokenBucketRateLimiter(self.store, capacity=3, window_seconds=3)
        client = "client_alpha"

        self.assertTrue(limiter.is_allowed(client).allowed)
        self.assertTrue(limiter.is_allowed(client).allowed)
        self.assertTrue(limiter.is_allowed(client).allowed)
        
        # 4th request must be blocked
        blocked = limiter.is_allowed(client)
        self.assertFalse(blocked.allowed)
        self.assertGreaterEqual(blocked.retry_after, 1)

        # Wait for 1 token refill
        time.sleep(1.05)
        self.assertTrue(limiter.is_allowed(client).allowed)

    def test_fixed_window_counter(self):
        limiter = FixedWindowRateLimiter(self.store, limit=2, window_seconds=2)
        c = "client_beta"

        self.assertTrue(limiter.is_allowed(c).allowed)
        self.assertTrue(limiter.is_allowed(c).allowed)
        self.assertFalse(limiter.is_allowed(c).allowed)

    def test_sliding_window_counter(self):
        limiter = SlidingWindowCounterRateLimiter(self.store, limit=2, window_seconds=2)
        c = "client_gamma"

        self.assertTrue(limiter.is_allowed(c).allowed)
        self.assertTrue(limiter.is_allowed(c).allowed)
        self.assertFalse(limiter.is_allowed(c).allowed)

    def test_sliding_window_log(self):
        limiter = SlidingWindowLogRateLimiter(self.store, limit=2, window_seconds=2)
        c = "client_delta"

        self.assertTrue(limiter.is_allowed(c).allowed)
        self.assertTrue(limiter.is_allowed(c).allowed)
        self.assertFalse(limiter.is_allowed(c).allowed)

    def test_leaky_bucket(self):
        limiter = LeakyBucketRateLimiter(self.store, capacity=2, window_seconds=2)
        c = "client_epsilon"

        self.assertTrue(limiter.is_allowed(c).allowed)
        self.assertTrue(limiter.is_allowed(c).allowed)
        self.assertFalse(limiter.is_allowed(c).allowed)


class TestConcurrencyStress(unittest.TestCase):
    """Stress test: verify zero race conditions under concurrent multi-threaded load."""

    def setUp(self):
        self.store = InMemoryKVStore()

    def test_concurrent_burst_token_bucket(self):
        # 5 capacity bucket, 25 concurrent threads
        limiter = TokenBucketRateLimiter(self.store, capacity=5, window_seconds=60)
        client = "concurrent_user"
        results = []

        def call():
            return limiter.is_allowed(client).allowed

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(call) for _ in range(25)]
            for f in as_completed(futures):
                results.append(f.result())

        # Exactly 5 should be True, exactly 20 should be False
        self.assertEqual(results.count(True), 5)
        self.assertEqual(results.count(False), 20)


class TestFlaskEndpoints(unittest.TestCase):
    """Integration tests for Flask HTTP API, templates, and RFC headers."""

    def setUp(self):
        storage_instance.flush_all()
        self.client = app.test_client()

    def test_root_json_endpoint(self):
        res = self.client.get("/?json=true")
        self.assertEqual(res.status_code, 200)
        json_data = res.get_json()
        self.assertEqual(json_data["author"], "Suman Mondal (RagStarG)")
        self.assertEqual(json_data["registration_no"], "AU/2024/0000570")

    def test_root_html_dashboard(self):
        res = self.client.get("/", headers={"Accept": "text/html"})
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Rate Limiter Simulator", res.data)
        self.assertIn(b"Suman Mondal", res.data)

    def test_public_endpoint_rate_limiting(self):
        # Limit is 5 per 10s
        for i in range(5):
            res = self.client.get("/api/public")
            self.assertEqual(res.status_code, 200)
            self.assertIn("X-RateLimit-Limit", res.headers)
            self.assertIn("X-RateLimit-Remaining", res.headers)

        # 6th request must be HTTP 429
        blocked = self.client.get("/api/public")
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)
        data = blocked.get_json()
        self.assertEqual(data["error"], "Too Many Requests")

    def test_tier_differentiation(self):
        # Free tier: 5 requests
        for _ in range(5):
            r = self.client.get("/api/tier", headers={"X-API-Key": "free_key_1"})
            self.assertEqual(r.status_code, 200)

        r_blocked = self.client.get("/api/tier", headers={"X-API-Key": "free_key_1"})
        self.assertEqual(r_blocked.status_code, 429)

        # Premium tier: up to 20 requests
        for _ in range(10):
            r_prem = self.client.get("/api/tier", headers={"X-API-Key": "prem_user_99"})
            self.assertEqual(r_prem.status_code, 200)

    def test_sliding_log_endpoint(self):
        for _ in range(5):
            res = self.client.get("/api/sliding-log")
            self.assertEqual(res.status_code, 200)
        res_blocked = self.client.get("/api/sliding-log")
        self.assertEqual(res_blocked.status_code, 429)

    def test_leaky_bucket_endpoint(self):
        for _ in range(6):
            res = self.client.get("/api/leaky")
            self.assertEqual(res.status_code, 200)
        res_blocked = self.client.get("/api/leaky")
        self.assertEqual(res_blocked.status_code, 429)


if __name__ == "__main__":
    unittest.main()
