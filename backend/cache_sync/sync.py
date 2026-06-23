"""
Generalizes the cache-sync pattern already established for this project: instead
of a hardcoded MARTS list, this iterates every dashboard config and syncs it
according to its declared refresh_tier. Run after `dbt run` in CodeBuild/CI -
near_real_time dashboards on an hourly EventBridge schedule, snapshot dashboards
on a daily/monthly one.

Dashboards with refresh_tier: needs_clarification are skipped on purpose - they
should not be silently scheduled until the cadence question is resolved.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config_loader import load_all_dashboards
from core.config_schema import DashboardConfig
from core.db.connection import execute_query
from core.cache.client import cache_set, cache_invalidate_prefix
from core.cache.keys import make_cache_key

DASHBOARDS_DIR = Path(__file__).parent.parent / "dashboards"


async def sync_dashboard(cfg: DashboardConfig):
    mart = cfg.mart.queue_model_name
    summary_mart = cfg.mart.summary_model_name
    await cache_invalidate_prefix(cfg.dashboard_id)

    sort_field = cfg.api.default_sort.field
    sort_dir = cfg.api.default_sort.direction.upper()
    rows = execute_query(f"SELECT * FROM {mart} ORDER BY {sort_field} {sort_dir}")

    page_size = cfg.api.default_page_size
    queue_key = make_cache_key(cfg.dashboard_id, cfg.api.queue_endpoint, {"page": 1, "page_size": page_size})
    await cache_set(
        queue_key,
        {"items": rows[:page_size], "count": len(rows), "page": 1, "page_size": page_size},
        ttl=cfg.cache_ttl_seconds,
    )

    # Dumb passthrough, same contract as router_factory.get_summary(): just
    # cache whatever the dbt-built summary mart already computed. No
    # aggregation is recomputed here - that logic lives only in
    # mart_<id>_summary.sql, once, per dashboard.
    summary_rows = execute_query(f"SELECT * FROM {summary_mart}")
    summary_key = make_cache_key(cfg.dashboard_id, cfg.api.summary_endpoint)
    await cache_set(
        summary_key,
        {
            "title": f"{cfg.display_name} — Summary",
            "items": summary_rows,
            "count": len(summary_rows),
        },
        ttl=cfg.cache_ttl_seconds,
    )

    print(f"[cache-sync] {cfg.dashboard_id}: {len(rows)} rows cached (tier={cfg.refresh_tier})")


async def sync_all():
    for cfg in load_all_dashboards(DASHBOARDS_DIR):
        if cfg.refresh_tier in ("near_real_time", "snapshot_eod", "snapshot_eom"):
            await sync_dashboard(cfg)
        else:
            print(f"[cache-sync] SKIPPED {cfg.dashboard_id} — refresh_tier={cfg.refresh_tier}, needs clarification before scheduling")


if __name__ == "__main__":
    asyncio.run(sync_all())
