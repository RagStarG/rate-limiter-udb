# Distributed Rate Limiter Backend using Unstructured Database Data Model

**Student Name:** Suman Mondal  
**Registration No.:** AU/2024/0000570  
**Roll No.:** UG/SOET/30/24/148  
**Semester & Section:** 5th Semester, Section B  
**Subject:** Unstructured Database (UDB Assignment)  
**Repository / Github Link:** `https://github.com/RagStarG/rate-limiter-udb`

---

## 1. Project Overview

This repository contains an end-to-end, production-grade Rate Limiter backend designed and implemented for the **Unstructured Database (UDB)** assignment. 

A rate limiter regulates incoming request velocity across distributed systems, protecting upstream services against denial-of-service (DoS) bursts, scraping bots, and cascade failures, while guaranteeing fair usage across multi-tenant clients.

Instead of traditional disk-bound relational databases, this system leverages an **Unstructured Key-Value / Hash Data Model** (characteristic of in-memory NoSQL engines like Redis).

---

## 2. Unstructured Data Model Architecture

### Why an Unstructured Database?
Relational databases (RDBMS) suffer from serious architectural bottlenecks when applied to high-throughput rate limiting:
1. **Disk I/O and Row Locking:** Each evaluation requires transactional updates with row-level locks, causing severe connection pool exhaustion under load.
2. **Lack of Native Expiration:** RDBMS tables require periodic cron sweeps to delete stale window rows, creating expensive index table bloat.
3. **Latency Inefficiency:** Rate limiting sits in the critical hot path of every API request. A database latency of 15ms-50ms is unacceptable.

### The Chosen Unstructured Data Model
We utilize an **In-Memory Key-Value / Hash Data Model** providing $O(1)$ constant time complexity and native Time-To-Live (TTL) eviction.

```
+-----------------------------------------------------------------------------------+
| Unstructured Key-Value / Hash Store                                              |
+-----------------------------------------------------------------------------------+
| Key: "rl:tb:<client_id>"                                                          |
| Type: HASH                                                                        |
| Fields:                                                                           |
|   - "tokens":      <float>   (e.g., "4.333")                                      |
|   - "last_refill": <float>   (e.g., "1700001234.567")                             |
| TTL: 2 * window_seconds (Automatic lazy cleanup on inactivity)                     |
+-----------------------------------------------------------------------------------+
```

### Algorithmic Comparison

| Algorithm | Storage Model | Time Complexity | Space per Client | Burst Tolerance | Boundary Smoothing |
|---|---|---|---|---|---|
| **Token Bucket** *(Chosen)* | Key-Value Hash (`tokens`, `last_refill`) | $O(1)$ | Constant ($O(1)$) | Yes (up to bucket capacity) | Continuous lazy refill |
| **Sliding Window Counter** | Key-Value Integer Counters with TTL | $O(1)$ | 2 keys ($O(1)$) | Moderate | Weighted interpolation |
| **Fixed Window Counter** | Key-Value Integer Counter with TTL | $O(1)$ | 1 key ($O(1)$) | Poor (2x burst at boundary) | Reset at window edge |
| **Sliding Window Log** | Sorted Set (`ZSET` of timestamps) | $O(\log N + M)$ | Linear ($O(N)$) | Strict | Exact timestamp tracking |

---

## 3. Rate Limiter Backend Implementation Details

### Core Modules
- `rate_limiter/storage.py`: Clean storage abstraction layer (`StorageBackend`) and thread-safe, high-performance in-memory unstructured store (`InMemoryKVStore`) supporting Key-Value, Hash, and Sorted Set operations with millisecond TTL expiration.
- `rate_limiter/algorithms.py`: Implementations of Token Bucket, Sliding Window Counter, and Fixed Window algorithms.
- `rate_limiter/middleware.py`: Flask request interceptor and `@rate_limit` decorator supporting client resolution (API Key, Bearer Token, IP) and standard RFC HTTP headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `Retry-After`).
- `rate_limiter/app.py`: Flask REST API showcasing public, sliding-window, and tiered endpoints.
- `tests/test_rate_limiter.py`: Complete automated unit and integration test suite.

---

## 4. API Endpoints

### 1. Root Metadata
- **Endpoint:** `GET /`
- **Description:** Returns assignment metadata and student credentials.

### 2. Public Endpoint (Token Bucket)
- **Endpoint:** `GET /api/public`
- **Rate Limit:** 5 requests per 10 seconds.
- **Success Response (200 OK):**
  ```json
  {
    "status": "success",
    "message": "Public resource accessed successfully.",
    "client_id": "ip:127.0.0.1",
    "timestamp": 1700000000.123
  }
  ```
- **Rate Limit Response (429 Too Many Requests):**
  ```json
  {
    "status": "error",
    "error": "Too Many Requests",
    "message": "Rate limit exceeded. Maximum 5 requests per 10s.",
    "limit": 5,
    "remaining": 0,
    "retry_after_seconds": 2
  }
  ```

### 3. Tiered Endpoint (Free vs Premium)
- **Endpoint:** `GET /api/tier`
- **Headers:** `X-API-Key: prem_user_1` or `X-API-Key: free_user_1`
- **Rate Limit:**
  - Free Tier: 5 requests / minute
  - Premium Tier: 20 requests / minute

### 4. Database Inspection Endpoint
- **Endpoint:** `GET /api/stats/<client_id>`
- **Description:** Directly inspects the internal unstructured hash representation in real time.

---

## 5. How to Run and Test

### Prerequisites
- Python 3.9+
- Flask (`pip install Flask`)

### Running the API Server
```bash
python -m rate_limiter.app
```
The server will start on `http://127.0.0.1:5000`.

### Running the Test Suite
```bash
python -m unittest tests/test_rate_limiter.py -v
```

---

