"""Flask Web Application showcasing Rate Limiting with Unstructured Database Models.

Author: Suman Mondal (AU/2024/0000570)
Subject: Unstructured Database (UDB Assignment)
"""

import time
from flask import Flask, jsonify, request
from .storage import InMemoryKVStore
from .middleware import RateLimitMiddleware, rate_limit, get_client_identifier

# Initialize Flask application
app = Flask(__name__)

# Shared storage instance for inspection and resetting
storage_instance = InMemoryKVStore()
middleware_instance = RateLimitMiddleware(storage=storage_instance)


@app.route("/", methods=["GET"])
def index():
    """Root metadata endpoint."""
    return jsonify(
        {
            "assignment": "Unstructured Database (UDB) - Rate Limiter Backend",
            "student_name": "Suman Mondal",
            "registration_no": "AU/2024/0000570",
            "roll_no": "UG/SOET/30/24/148",
            "semester_sec": "5th Sem, Sec: B",
            "status": "online",
            "endpoints": {
                "/api/public": "Public endpoint (Token Bucket: 5 req / 10s)",
                "/api/sliding": "Sliding Window Counter endpoint (8 req / 20s)",
                "/api/tier": "Tier-based endpoint (Free: 5 req/min, Premium: 20 req/min)",
                "/api/stats/<client_id>": "Inspect unstructured database state for a client",
                "/api/reset": "POST: Reset internal database state",
            },
        }
    )


@app.route("/api/public", methods=["GET"])
@rate_limit(
    limit=5,
    window_seconds=10,
    algorithm="token_bucket",
    middleware=middleware_instance,
)
def public_endpoint():
    """Demonstrates Token Bucket rate limiting."""
    client_id = get_client_identifier()
    return jsonify(
        {
            "status": "success",
            "message": "Public resource accessed successfully.",
            "client_id": client_id,
            "timestamp": time.time(),
        }
    )


@app.route("/api/sliding", methods=["GET"])
@rate_limit(
    limit=8,
    window_seconds=20,
    algorithm="sliding_window",
    middleware=middleware_instance,
)
def sliding_window_endpoint():
    """Demonstrates Sliding Window Counter rate limiting."""
    client_id = get_client_identifier()
    return jsonify(
        {
            "status": "success",
            "message": "Sliding window resource accessed successfully.",
            "client_id": client_id,
            "timestamp": time.time(),
        }
    )


@app.route("/api/tier", methods=["GET"])
def tier_endpoint():
    """Tiered endpoint: Free vs Premium based on API Key."""
    api_key = request.headers.get("X-API-Key") or request.args.get("api_key", "guest")
    is_premium = api_key.startswith("prem_") or api_key == "gold"
    
    limit = 20 if is_premium else 5
    tier_name = "Premium" if is_premium else "Free"

    limiter = middleware_instance.get_limiter(
        endpoint_name=f"tier_{tier_name.lower()}",
        limit=limit,
        window_seconds=60,
        algorithm="token_bucket",
    )

    client_id = f"tier:{api_key}"
    result = limiter.is_allowed(client_id)

    if not result.allowed:
        resp = jsonify(
            {
                "status": "error",
                "tier": tier_name,
                "error": "Too Many Requests",
                "message": f"{tier_name} tier quota of {limit} requests/minute exceeded.",
                "retry_after_seconds": result.retry_after,
            }
        )
        resp.status_code = 429
        resp.headers["X-RateLimit-Limit"] = str(result.limit)
        resp.headers["X-RateLimit-Remaining"] = "0"
        resp.headers["X-RateLimit-Reset"] = str(result.reset_epoch)
        resp.headers["Retry-After"] = str(result.retry_after)
        return resp

    resp = jsonify(
        {
            "status": "success",
            "tier": tier_name,
            "message": f"Hello {tier_name} subscriber! You have {result.remaining} requests remaining.",
            "timestamp": time.time(),
        }
    )
    resp.headers["X-RateLimit-Limit"] = str(result.limit)
    resp.headers["X-RateLimit-Remaining"] = str(result.remaining)
    resp.headers["X-RateLimit-Reset"] = str(result.reset_epoch)
    return resp


@app.route("/api/stats/<path:client_id>", methods=["GET"])
def inspect_stats(client_id: str):
    """Directly inspect unstructured storage keys for a client."""
    tb_key = f"rl:tb:{client_id}"
    tb_data = storage_instance.hgetall(tb_key)
    
    return jsonify(
        {
            "query_client_id": client_id,
            "token_bucket_hash": tb_data,
            "all_internal_keys": list(storage_instance._data.keys()),
        }
    )


@app.route("/api/reset", methods=["POST"])
def reset_db():
    """Reset storage state (useful for automated testing)."""
    storage_instance.flush_all()
    return jsonify({"status": "success", "message": "Rate limiter database flushed."})


if __name__ == "__main__":
    print("Starting UDB Rate Limiter Backend on http://127.0.0.1:5000 ...")
    app.run(host="127.0.0.1", port=5000, debug=True)
