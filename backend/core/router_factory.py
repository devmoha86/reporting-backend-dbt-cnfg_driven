"""
This is the piece that removes the per-dashboard coding: given a validated
DashboardConfig, it returns a fully wired FastAPI router (queue table, status
summary, filter-lookup dropdowns, CSV export) using the cache-aside pattern
already established in this project. Adding dashboard #2 means writing a new
YAML config, not a new router module.

Summary endpoint is intentionally dumb: it does SELECT * FROM the dbt-built
summary mart and returns the rows as-is - no GROUP BY, no aggregation, no
chart-shaping logic lives here. Every dashboard's summary chart can be a
completely different aggregation (status counts, sums by category, a trend
line, ...) because that logic is written once in that dashboard's own
mart_<id>_summary.sql, not in this shared Python file. See
dashboards/<id>/dbt/marts/mart_<id>_summary.sql.
"""
import csv
import io

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from .config_schema import DashboardConfig
from .db.connection import execute_query
from .cache.client import cache_get, cache_set
from .cache.keys import make_cache_key
from .filters import build_filter_clause


def build_dashboard_router(config: DashboardConfig) -> APIRouter:
    router = APIRouter(prefix=config.api.base_path, tags=[config.dashboard_id])
    mart = config.mart.queue_model_name
    summary_mart = config.mart.summary_model_name
    sort_field = config.api.default_sort.field
    sort_dir = config.api.default_sort.direction.upper()

    def _collect_params(request: Request) -> dict:
        return {fd.param: request.query_params.get(fd.param) for fd in config.api.filters}

    # ---- Queue table -------------------------------------------------
    @router.get(f"/{config.api.queue_endpoint}", summary=f"{config.display_name} — queue")
    async def get_queue(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(config.api.default_page_size, ge=1, le=500),
    ):
        params_in = _collect_params(request)
        cache_key = make_cache_key(
            config.dashboard_id, config.api.queue_endpoint,
            {**params_in, "page": page, "page_size": page_size},
        )
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached

        where, params = build_filter_clause(config.api.filters, params_in)
        offset = (page - 1) * page_size
        sql = (
            f"SELECT * FROM {mart} {where} "
            f"ORDER BY {sort_field} {sort_dir} "
            f"LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}"
        )
        rows = execute_query(sql, params + [page_size, offset])

        count_sql = f"SELECT COUNT(*) AS n FROM {mart} {where}"
        total = execute_query(count_sql, params)[0]["n"]

        result = {"items": rows, "count": total, "page": page, "page_size": page_size}
        await cache_set(cache_key, result, ttl=config.cache_ttl_seconds)
        return result

    # ---- Summary chart (dumb passthrough) ------------------------------
    # No GROUP BY, no aggregation, no chart-shaping here. The mart_<id>_summary
    # model already contains whatever rollup that dashboard's chart needs -
    # this just selects it and hands the rows back untouched, exactly the way
    # get_queue() above hands back the queue mart's rows untouched. If a new
    # dashboard's chart is a sum-by-category, a trend line, or anything else,
    # that logic is written once in that dashboard's mart_<id>_summary.sql -
    # this function never has to change.
    @router.get(f"/{config.api.summary_endpoint}", summary=f"{config.display_name} — summary chart")
    async def get_summary():
        cache_key = make_cache_key(config.dashboard_id, config.api.summary_endpoint)
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached

        rows = execute_query(f"SELECT * FROM {summary_mart}")
        result = {
            "title": f"{config.display_name} — Summary",
            "items": rows,
            "count": len(rows),
        }
        await cache_set(cache_key, result, ttl=config.cache_ttl_seconds)
        return result

    # ---- Filter / dropdown lookups -------------------------------------
    for field in config.api.filter_lookup_fields:
        def _make_lookup(field_name: str):
            async def lookup():
                cache_key = make_cache_key(config.dashboard_id, f"filters:{field_name}")
                cached = await cache_get(cache_key)
                if cached is not None:
                    return cached
                sql = (
                    f"SELECT DISTINCT {field_name} AS value FROM {mart} "
                    f"WHERE {field_name} IS NOT NULL ORDER BY {field_name}"
                )
                rows = execute_query(sql)
                result = {"items": [r["value"] for r in rows], "count": len(rows)}
                await cache_set(cache_key, result, ttl=max(config.cache_ttl_seconds, 21600))
                return result
            return lookup

        router.add_api_route(
            f"/filters/{field}", _make_lookup(field), methods=["GET"],
            summary=f"{config.display_name} — distinct values for {field}",
        )

    # ---- CSV export (same columns as the queue view) --------------------
    if config.api.export_enabled:
        @router.get(f"/{config.api.queue_endpoint}/export", summary=f"{config.display_name} — export CSV")
        async def export_csv(request: Request):
            params_in = _collect_params(request)
            where, params = build_filter_clause(config.api.filters, params_in)
            sql = f"SELECT * FROM {mart} {where} ORDER BY {sort_field} {sort_dir}"
            rows = execute_query(sql, params)

            buf = io.StringIO()
            if rows:
                writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            buf.seek(0)
            return StreamingResponse(
                iter([buf.getvalue()]), media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={config.dashboard_id}_export.csv"},
            )

    return router
