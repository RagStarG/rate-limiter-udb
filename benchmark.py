"""High-Concurrency Rate Limiter Benchmark & Stress Testing Utility.

Simulates concurrent client traffic bursts, measuring throughput, latency distribution,
and strict adherence to rate limit quotas across multiple worker threads.

Author: Suman Mondal (RagStarG)
Subject: Unstructured Database (UDB Assignment)
"""

import sys
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from rate_limiter.app import app, storage_instance


def run_benchmark(
    endpoint: str = "/api/public",
    total_requests: int = 50,
    concurrency: int = 5,
    headers: dict = None,
):
    """Run an in-process benchmark using Flask test client with multi-threading."""
    print("=" * 72)
    print("           HIGH-CONCURRENCY RATE LIMITER BENCHMARK SUITE")
    print("=" * 72)
    print(f"Target Endpoint    : {endpoint}")
    print(f"Total Requests     : {total_requests}")
    print(f"Concurrent Workers : {concurrency} threads")
    print(f"Storage Engine     : {storage_instance.get_backend_name()}")
    print("-" * 72)

    # Flush storage before benchmark
    storage_instance.flush_all()

    client = app.test_client()
    latencies = []
    status_counts = {}
    remaining_values = []

    def make_request(req_idx):
        start = time.perf_counter()
        resp = client.get(endpoint, headers=headers or {})
        duration = (time.perf_counter() - start) * 1000.0  # ms
        return resp.status_code, duration, resp.headers.get("X-RateLimit-Remaining")

    benchmark_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(make_request, i) for i in range(total_requests)]
        for f in as_completed(futures):
            status, duration, rem = f.result()
            latencies.append(duration)
            status_counts[status] = status_counts.get(status, 0) + 1
            if rem is not None:
                try:
                    remaining_values.append(int(rem))
                except ValueError:
                    pass

    total_time = time.perf_counter() - benchmark_start
    throughput = total_requests / total_time if total_time > 0 else 0

    # Statistical calculations
    latencies.sort()
    p50 = statistics.median(latencies)
    p95 = latencies[int(len(latencies) * 0.95)] if len(latencies) >= 20 else latencies[-1]
    p99 = latencies[int(len(latencies) * 0.99)] if len(latencies) >= 100 else latencies[-1]

    print("BENCHMARK EXECUTION RESULTS:")
    print(f"  Total Duration   : {total_time * 1000.0:.2f} ms")
    print(f"  Throughput       : {throughput:.1f} requests/sec")
    print(f"  HTTP 200 (OK)    : {status_counts.get(200, 0)} requests (Allowed)")
    print(f"  HTTP 429 (BLOCK) : {status_counts.get(429, 0)} requests (Throttled)")
    print("-" * 72)
    print("LATENCY DISTRIBUTION (Sub-millisecond Performance):")
    print(f"  Min Latency      : {min(latencies):.3f} ms")
    print(f"  Mean Latency     : {statistics.mean(latencies):.3f} ms")
    print(f"  p50 (Median)     : {p50:.3f} ms")
    print(f"  p95              : {p95:.3f} ms")
    print(f"  p99              : {p99:.3f} ms")
    print(f"  Max Latency      : {max(latencies):.3f} ms")
    print("=" * 72)

    return {
        "status_counts": status_counts,
        "throughput": throughput,
        "p50": p50,
        "p95": p95,
    }


if __name__ == "__main__":
    ep = sys.argv[1] if len(sys.argv) > 1 else "/api/public"
    reqs = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    run_benchmark(endpoint=ep, total_requests=reqs, concurrency=workers)
