"""Automated Test Suite for Rate Limiter and Unstructured Storage.

Author: Suman Mondal (AU/2024/0000570)
Subject: Unstructured Database (UDB Assignment)
"""

import time
import unittest
from rate_limiter.storage import InMemoryKVStore
from rate_limiter.algorithms import (
    TokenBucketRateLimiter,
    SlidingWindowCounterRateLimiter,
    FixedWindowRateLimiter,
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


class TestRateLimitingAlgorithms(unittest.TestCase):
    """Test algorithmic behavior under normal and bursting conditions."""

    def setUp(self):
        self.store = InMemoryKVStore()

    def test_token_bucket_exhaustion_and_refill(self):
        # 3 tokens max, refills over 3 seconds (1 token / sec)
        limiter = TokenBucketRateLimiter(self.store, capacity=3, window_seconds=3)
        client = "client_alpha"

        # Request 1: Allowed (rem 2)
        r1 = limiter.is_allowed(client)
        self.assertTrue(r1.allowed)
        self.assertEqual(r1.remaining, 2)

        # Request 2: Allowed (rem 1)
        r2 = limiter.is_allowed(client)
        self.assertTrue(r2.allowed)
        self.assertEqual(r2.remaining, 1)

        # Request 3: Allowed (rem 0)
        r3 = limiter.is_allowed(client)
        self.assertTrue(r3.allowed)
        self.assertEqual(r3.remaining, 0)

        # Request 4: Blocked (rate limit exceeded)
        r4 = limiter.is_allowed(client)
        self.assertFalse(r4.allowed)
        self.assertGreaterEqual(r4.retry_after, 1)

        # Wait 1.1s for 1 token to refill
        time.sleep(1.1)
        r5 = limiter.is_allowed(client)
        self.assertTrue(r5.allowed)

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


class TestFlaskEndpoints(unittest.TestCase):
    """Integration tests for Flask HTTP API and headers."""

    def setUp(self):
        storage_instance.flush_all()
        self.client = app.test_client()

    def test_root_endpoint(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        json_data = res.get_json()
        self.assertEqual(json_data["student_name"], "Suman Mondal")
        self.assertEqual(json_data["registration_no"], "AU/2024/0000570")

    def test_public_endpoint_rate_limiting(self):
        # Limit is 5 per 10s
        for i in range(5):
            res = self.client.get("/api/public")
            self.assertEqual(res.status_code, 200, f"Request {i+1} should succeed")
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


if __name__ == "__main__":
    unittest.main()
