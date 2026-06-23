"""
Same fail-open Redis pattern already established for this project: if Redis is
unreachable, cache_get returns None and cache_set/invalidate silently no-op, so
endpoints fall through to the database transparently. No dashboard ever has to
handle a Redis outage itself.
"""
import os
import json
import datetime
from decimal import Decimal

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_pool = None


def _json_default(obj):
    """DuckDB/Athena return datetime.date and Decimal for DATE/NUMERIC columns,
    neither of which json.dumps handles by default. Without this, cache_set's
    fail-open except-clause swallows the TypeError and silently never caches
    any row containing a date - exactly the columns most dashboards have."""
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


async def get_redis():
    global _pool
    if _pool is None:
        import redis.asyncio as aioredis
        _pool = aioredis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _pool


async def cache_get(key: str):
    try:
        r = await get_redis()
        raw = await r.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def cache_set(key: str, value, ttl: int = 3600):
    try:
        r = await get_redis()
        await r.set(key, json.dumps(value, default=_json_default), ex=ttl)
    except Exception:
        pass


async def cache_invalidate_prefix(prefix: str):
    try:
        r = await get_redis()
        async for key in r.scan_iter(f"{prefix}:*"):
            await r.delete(key)
    except Exception:
        pass
