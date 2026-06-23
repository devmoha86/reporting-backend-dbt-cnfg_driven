"""
End-to-end smoke test for the framework: builds a synthetic DuckDB mart matching
the LDC config's shape (standing in for what `dbt run` would produce), points
core.db.connection at it, loads the real ldc_case_management.yaml config through
the real router_factory, and exercises every endpoint via FastAPI's TestClient.

Run with: python tests/test_framework_smoke.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# Must be set before core.db.connection is imported (module-level env read)
tmp_db = tempfile.NamedTemporaryFile(suffix=".duckdb", delete=True)
tmp_db.close()  # delete=True removes it on close; DuckDB will create a fresh file at this path
os.environ["DATA_LAYER"] = "duckdb"
os.environ["DUCKDB_PATH"] = tmp_db.name

import duckdb  # noqa: E402

from core.config_loader import load_dashboard_config  # noqa: E402
from core.router_factory import build_dashboard_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def seed_mart(db_path: str):
    con = duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE mart_ldc_case_queue (
            request_no VARCHAR, request_status VARCHAR, sub_status VARCHAR,
            submitter_name VARCHAR, submitter_group VARCHAR,
            seller_number VARCHAR, seller_name VARCHAR,
            servicer_number VARCHAR, servicer_name VARCHAR,
            reviewer_group VARCHAR, reviewer_name VARCHAR,
            fm_loan_number VARCHAR, submission_date DATE, completion_date DATE,
            loan_count INTEGER, attribute_count INTEGER,
            queue_age VARCHAR, cycle_time VARCHAR, sla_status VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO mart_ldc_case_queue VALUES
        ('100101','Draft','Draft','Jennifer Lee','External','99887','PennyMac',
         '7721',NULL,'Auto-Decision','System',NULL,'2026-05-26',NULL,44,0,NULL,NULL,NULL),
        ('100401','Completed','Request Completed','David Smith','Internal','99887','PennyMac',
         '7721',NULL,'LDC','David Smith',NULL,'2026-05-25','2026-05-25',110,20,NULL,NULL,'Within SLA'),
        ('100501','Canceled','Canceled','Michael Brown','Internal','55210','Mr. Cooper',
         '2210',NULL,'LDC','Michael Brown',NULL,'2026-05-26',NULL,19,3,NULL,NULL,'SLA At Risk')
    """)
    # Seeded directly, the way dbt's mart_ldc_status_summary.sql would build it -
    # zero-filled across all 6 configured statuses, not derived from the queue
    # mart at request time. The router/cache-sync layer no longer computes this;
    # it only does SELECT * FROM this table and passes the rows through.
    con.execute("""
        CREATE TABLE mart_ldc_status_summary (request_status VARCHAR, request_count INTEGER)
    """)
    con.execute("""
        INSERT INTO mart_ldc_status_summary VALUES
        ('Draft', 1), ('Exception Review', 0), ('Pending Reclass', 0),
        ('Completed', 1), ('Canceled', 1), ('Rejected', 0)
    """)
    con.close()


def main():
    seed_mart(tmp_db.name)

    cfg = load_dashboard_config(BACKEND / "dashboards" / "ldc" / "config.yaml")
    app = FastAPI()
    app.include_router(build_dashboard_router(cfg))
    client = TestClient(app)

    failures = []

    def check(label, cond):
        status = "PASS" if cond else "FAIL"
        print(f"[{status}] {label}")
        if not cond:
            failures.append(label)

    r = client.get("/api/ldc/case-queue")
    check("queue endpoint returns 200", r.status_code == 200)
    body = r.json()
    check("queue returns all 3 seeded rows", body.get("count") == 3 and len(body.get("items", [])) == 3)

    r2 = client.get("/api/ldc/case-queue?servicer_number=7721")
    check("servicer_number filter narrows to 2 rows", r2.json().get("count") == 2)

    r3 = client.get("/api/ldc/case-queue?submitter_name=david")
    check("ilike filter on submitter_name matches case-insensitively", r3.json().get("count") == 1)

    r4 = client.get("/api/ldc/case-queue")
    check("second identical call hits cache path without error", r4.status_code == 200)
    check("second call's body matches first call's body (served from Redis)", r4.json() == body)

    import asyncio

    async def _check_redis_keys():
        import redis.asyncio as aioredis
        r = aioredis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
        await r.ping()
        return await r.keys(f"{cfg.dashboard_id}:*")

    try:
        keys = asyncio.run(_check_redis_keys())
        check("Redis holds cache keys for this dashboard after the calls above", len(keys) > 0)
        check("Redis specifically holds the case-queue key (the one with DATE columns)",
              any(k.startswith(f"{cfg.dashboard_id}:{cfg.api.queue_endpoint}") for k in keys))
    except Exception as e:
        print(f"[SKIP] Redis cache-hit verification - Redis not reachable ({e})")

    r5 = client.get("/api/ldc/status-summary")
    check("status-summary returns 200", r5.status_code == 200)
    summary = r5.json()
    # Dumb passthrough contract: the router did SELECT * FROM mart_ldc_status_summary
    # and returned the rows as-is - this is asserting on the seeded mart's rows,
    # not on any aggregation computed by the API.
    summary_rows = {row["request_status"]: row["request_count"] for row in summary["items"]}
    check("status-summary passes through all 6 seeded mart rows untouched",
          summary_rows == {"Draft": 1, "Exception Review": 0, "Pending Reclass": 0,
                            "Completed": 1, "Canceled": 1, "Rejected": 0})

    r6 = client.get("/api/ldc/filters/sub_status")
    check("filter lookup endpoint returns 200", r6.status_code == 200)
    check("filter lookup returns distinct sub_status values", set(r6.json()["items"]) == {"Draft", "Request Completed", "Canceled"})

    r7 = client.get("/api/ldc/case-queue/export")
    check("export endpoint returns 200 with CSV content-type", r7.status_code == 200 and "text/csv" in r7.headers["content-type"])
    check("export CSV has a header row plus 3 data rows", len(r7.text.strip().splitlines()) == 4)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {failures}")
        sys.exit(1)
    print("All framework smoke checks passed.")


if __name__ == "__main__":
    main()
