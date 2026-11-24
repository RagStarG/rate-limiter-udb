# Production-Grade Distributed Rate Limiter Backend

[![Tests](https://img.shields.io/badge/tests-17%20passing-brightgreen)](#7-automated-test-suite)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.14-blue)](#quickstart-guide)
[![Architecture](https://img.shields.io/badge/architecture-unstructured%20key--value%20%2F%20hash-orange)](#2-unstructured-data-model-architecture)
[![Docker](https://img.shields.io/badge/docker-ready-blue)](#option-b-docker-compose-full-stack-with-redis)
[![Author](https://img.shields.io/badge/author-RagStarG-purple)](https://github.com/RagStarG)

---

## 1. Project Overview

A high-throughput, production-grade **Rate Limiter Backend** engineered around modern **Unstructured Database (NoSQL)** architecture. 

A rate limiter acts as a mission-critical traffic regulator situated at the ingress edge of an infrastructure. It protects downstream services from Denial-of-Service (DoS) attacks, brute-force credential stuffing, API quota exhaustion, and noisy neighbor resource starvation in multi-tenant SaaS environments.

### Core Highlights
- **Unstructured Data Architecture:** Powered by an in-memory Key-Value / Hash data model with sub-millisecond p99 latencies and native Time-To-Live (TTL) key expiration.
- **Dual Storage Engine with Auto-Fallback:**
  - **Remote Redis Engine (`RedisBackend`):** Executes **Atomic Redis Lua Scripts** directly within the Redis event loop, eliminating race conditions across distributed microservices.
  - **In-Memory Store (`InMemoryKVStore`):** Thread-safe in-memory store mimicking Redis Key-Value, Hash, and Sorted Set semantics with zero external dependencies.
- **All 5 Classic Rate Limiting Algorithms Implemented:**
  1. **Token Bucket** (Continuous lazy replenishment using $\Delta t$, burst tolerant)
  2. **Sliding Window Counter** (Weighted moving average approximation)
  3. **Sliding Window Log** (Exact timestamp tracking with Sorted Sets / `ZSET`)
  4. **Fixed Window Counter** (Discrete atomic counter with TTL)
  5. **Leaky Bucket** (FIFO queue traffic shaper with constant outflow)
- **Interactive Web Dashboard:** Modern, responsive UI with live visual Token Bucket meter, interactive burst buttons, RFC header inspection, and live unstructured database state inspector.
- **Multi-Threaded Benchmark Tool (`benchmark.py`):** Built-in concurrent load tester reporting p50, p95, and p99 latencies and verifying quota adherence under thread stress.
- **Full Docker & Docker Compose Support:** One-command deployment spinning up the Flask app and a live Redis 7 container with automated health checks.

---

## 2. Unstructured Data Model Architecture

### Why Unstructured In-Memory Databases Over Traditional RDBMS?

| Requirement | Traditional RDBMS (PostgreSQL, MySQL) | Unstructured In-Memory Store (Redis / KV Hash) |
|---|---|---|
| **Hot-Path Latency** | 10ms – 30ms (Disk B-Tree seek, WAL synchronization) | **< 0.5ms** (Operates entirely in RAM) |
| **Concurrency Contention** | Heavy row-level locks (`SELECT ... FOR UPDATE`), connection pool exhaustion | **Lock-free / Atomic** (Single-threaded event loop + Lua scripting) |
| **Key Garbage Collection** | Expensive batch `DELETE WHERE timestamp < ...` cron sweeps causing table bloat | **Native TTL** (Automatic probabilistic and lazy expiration) |
| **RAM Footprint (100k users)** | Hundreds of MBs (Table headers, foreign keys, indexes, WAL) | **~12.8 MB** (100,000 users $\times$ 128 bytes per Hash) |

### Physical Data Model (Token Bucket Hash)

```
Key Format : "rl:tb:<client_identifier>"
Data Type  : HASH
Fields     :
  - "tokens"      : Float64 (Current fractional token balance, e.g. "4.667")
  - "last_refill" : Float64 (Unix timestamp of last replenishment, e.g. "1732456789.124")
TTL        : 2 * window_seconds (Auto-evicts inactive clients with zero maintenance)
```

```
+-----------------------------------------------------------------------------------+
|                        INGRESS RATE LIMITER DECISION LIFECYCLE                    |
+-----------------------------------------------------------------------------------+
  [Inbound Request]
         |
         v
  [1. Resolve Client Identity] ---> IP Address / API Key / JWT Bearer Token
         |
         v
  [2. Query Unstructured Store] --> Atomic Redis Lua Script / In-Memory Lock
         |
         v
  [3. Algorithmic Condition] -----> Has quota exceeded capacity?
        /          \
    [YES]          [NO]
      |              |
      v              v
  [HTTP 429]    [HTTP 200 OK]
  - Retry-After - Deduct Token / Update TTL
  - Blocked     - Forward to API Endpoint
+-----------------------------------------------------------------------------------+
```

---

## 3. Interactive Web Dashboard

When running the service, open **`http://127.0.0.1:5000`** in your browser to access the live playground:

- **Animated Quota & Token Meter:** Real-time visual progress bar showing remaining tokens and dynamic color transitions (Green $\rightarrow$ Yellow $\rightarrow$ Red).
- **One-Click Stress Buttons:** Fire single requests, 5-request bursts, or 15-request floods with one click.
- **Countdown Backoff Banner:** Automatically displays live countdown timers when throttled with HTTP 429.
- **Live RFC Header Inspector:** Real-time visibility into `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, and `Retry-After`.
- **Database State Inspector:** Inspects raw Hash and String structures in the unstructured engine in real time.

---

## 4. Quickstart Guide

### Option A: Local Run (Zero Dependencies)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/RagStarG/rate-limiter-udb.git
   cd rate-limiter-udb
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Start the server:**
   ```bash
   python -m rate_limiter.app
   ```
   *Visit `http://127.0.0.1:5000` in your browser!*

---

### Option B: Docker Compose (Full Stack with Redis)

To run the complete production stack with live Redis 7:

```bash
docker compose up --build
```

The Flask app will automatically connect to the Redis container via `REDIS_URL=redis://redis:6379/0` and execute compiled atomic Lua scripts!

---

## 5. RESTful API Endpoints

All endpoints automatically inject standard RFC rate-limiting headers.

| Method | Endpoint | Algorithm | Rate Limit | Description |
|---|---|---|---|---|
| `GET` | `/` | — | — | Serves interactive UI (browser) or JSON metadata (`?json=true`) |
| `GET` | `/api/public` | Token Bucket | 5 req / 10s | Public endpoint rate-limited by Client IP |
| `GET` | `/api/sliding` | Sliding Window Counter | 8 req / 20s | Weighted moving average boundary smoothing |
| `GET` | `/api/sliding-log` | Sliding Window Log | 5 req / 15s | Exact microsecond timestamp tracking via Sorted Sets (`ZSET`) |
| `GET` | `/api/fixed` | Fixed Window Counter | 10 req / 30s | Discrete window counter with atomic TTL |
| `GET` | `/api/leaky` | Leaky Bucket | 6 req / 15s | FIFO queue traffic shaper with constant outflow |
| `GET` | `/api/tier` | Token Bucket | 5 req/min (Free) / 20 req/min (Prem) | Tier-based limit via `X-API-Key` header |
| `GET` | `/api/stats/<id>` | — | — | Inspects live unstructured database key contents |
| `POST` | `/api/reset` | — | — | Flushes the database storage for testing |

### Sample Response Headers
```http
HTTP/1.1 200 OK
Content-Type: application/json
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 4
X-RateLimit-Reset: 1732460120
X-RateLimit-Algorithm: token_bucket
```

### Rate Limited Error Response (HTTP 429)
```http
HTTP/1.1 429 TOO MANY REQUESTS
Content-Type: application/json
Retry-After: 2
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1732460122

{
  "status": "error",
  "error": "Too Many Requests",
  "message": "Rate limit exceeded. Maximum 5 requests per 10s.",
  "algorithm": "token_bucket",
  "limit": 5,
  "remaining": 0,
  "retry_after_seconds": 2
}
```

---

## 6. Concurrent Benchmark Utility

The repository includes a multi-threaded stress testing tool (`benchmark.py`) to benchmark throughput and latency percentiles:

```bash
python benchmark.py /api/public 50 5
```
*(Arguments: `<endpoint>` `<total_requests>` `<concurrent_threads>`)*

### Sample Benchmark Output:
```
========================================================================
           HIGH-CONCURRENCY RATE LIMITER BENCHMARK SUITE
========================================================================
Target Endpoint    : /api/public
Total Requests     : 50
Concurrent Workers : 5 threads
Storage Engine     : InMemoryKVStore (Thread-Safe)
------------------------------------------------------------------------
BENCHMARK EXECUTION RESULTS:
  Total Duration   : 21.76 ms
  Throughput       : 2298.1 requests/sec
  HTTP 200 (OK)    : 5 requests (Allowed)
  HTTP 429 (BLOCK) : 45 requests (Throttled)
------------------------------------------------------------------------
LATENCY DISTRIBUTION (Sub-millisecond Performance):
  Min Latency      : 0.162 ms
  Mean Latency     : 1.849 ms
  p50 (Median)     : 0.194 ms
  p95              : 18.457 ms
  p99              : 19.188 ms
  Max Latency      : 19.188 ms
========================================================================
```

---

## 7. Automated Test Suite

Run the full suite of 17 automated unit, integration, and concurrency stress tests:

```bash
python -m unittest tests/test_rate_limiter.py -v
```

```
test_fixed_window_counter (tests.test_rate_limiter.TestAllRateLimitingAlgorithms) ......... ok
test_leaky_bucket (tests.test_rate_limiter.TestAllRateLimitingAlgorithms) .................. ok
test_sliding_window_counter (tests.test_rate_limiter.TestAllRateLimitingAlgorithms) ....... ok
test_sliding_window_log (tests.test_rate_limiter.TestAllRateLimitingAlgorithms) ........... ok
test_token_bucket_exhaustion_and_refill (tests.test_rate_limiter.TestAllRateLimitingAlgs) . ok
test_concurrent_burst_token_bucket (tests.test_rate_limiter.TestConcurrencyStress) ........ ok
test_leaky_bucket_endpoint (tests.test_rate_limiter.TestFlaskEndpoints) .................... ok
test_public_endpoint_rate_limiting (tests.test_rate_limiter.TestFlaskEndpoints) ........... ok
test_root_html_dashboard (tests.test_rate_limiter.TestFlaskEndpoints) ..................... ok
test_root_json_endpoint (tests.test_rate_limiter.TestFlaskEndpoints) ...................... ok
test_sliding_log_endpoint (tests.test_rate_limiter.TestFlaskEndpoints) .................... ok
test_tier_differentiation (tests.test_rate_limiter.TestFlaskEndpoints) ..................... ok
test_hash_storage (tests.test_rate_limiter.TestInMemoryKVStore) ............................ ok
test_incr_atomic (tests.test_rate_limiter.TestInMemoryKVStore) ............................. ok
test_string_get_set (tests.test_rate_limiter.TestInMemoryKVStore) .......................... ok
test_ttl_expiration (tests.test_rate_limiter.TestInMemoryKVStore) .......................... ok
test_zset_storage (tests.test_rate_limiter.TestInMemoryKVStore) ............................ ok

Ran 17 tests in 1.380s | Status: OK (All 17 passing)
```

---

## 8. License

MIT License. Authored by [RagStarG](https://github.com/RagStarG).
