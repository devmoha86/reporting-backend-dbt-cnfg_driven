# Reporting Dashboard Framework

Config-driven backend framework for the FNMA Reporting Dashboard MFE. The goal:
each of the 18 dashboards should mostly be a **YAML file**, not new Python code.

This package has been smoke-tested end-to-end (see "Verified" below) - it isn't
just sketched out, the queue/summary/filter/export endpoints, cache-aside Redis
flow, and the dbt model generator all actually run.

## How it fits the existing architecture

Nothing here replaces what's already documented for this project - it's the
same `db/connection.py` DATA_LAYER swap, the same dbt staging→mart pattern, the
same cache-aside Redis flow, the same corporate Docker constraints. What's new
is that the FastAPI endpoints and the dbt model boilerplate are now **generated
from a config** instead of hand-written per dashboard.

```
backend/
  main.py                    <- scans dashboards/*.yaml, registers each one's router. Never edited to add a dashboard.
  core/
    config_schema.py         <- the contract every dashboard YAML must satisfy
    config_loader.py         <- loads + validates dashboards/*.yaml
    db/connection.py          <- DATA_LAYER swap (duckdb / athena) - unchanged from the existing pattern
    cache/client.py            <- Redis fail-open get/set
    cache/keys.py               <- deterministic cache key builder
    filters.py                   <- generic WHERE-clause builder driven by config
    router_factory.py             <- turns one DashboardConfig into a live FastAPI router
  dashboards/
    _template.yaml             <- copy this to start dashboard #2
    ldc_case_management.yaml    <- the first dashboard, fully configured
  dbt_project/
    models/staging/             <- generated
    models/marts/                <- generated
  scripts/generate_dbt_models.py  <- config -> dbt SQL + schema.yml scaffolding
  cache_sync/sync.py                <- config-driven cache-sync job (replaces a hardcoded MARTS list)
deploy/
  Dockerfile, docker-compose.yml, task-definition.template.json, buildspec.yml,
  github-actions-deploy.yml        <- all dashboard-agnostic; never touched per dashboard
tests/
  test_framework_smoke.py            <- the test that was actually run to verify this works
```

## Quickstart — Running This Locally

Two paths. Path A is fastest for iterating on a config; Path B matches the full
Docker stack from the deployment guide. Both assume you're in the unzipped
`reporting-dashboard-framework/` folder.

### Path A — Bare Python venv (fastest)

**1. Create the venv and install dependencies**
```
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```
If pip needs to resolve through the Nexus mirror on the corporate network,
confirm `pip config list` shows it before this step.

**2. Start Redis** (optional — the cache fails open, so the API still works
without it, just uncached every call)
```
redis-server --daemonize yes --port 6379
```

**3. Seed a local DuckDB file with mart data**

No real dbt project ships in this package yet (see "Running dbt" below) — for
the fastest possible loop, hand-seed the mart tables directly:
```
python3 -c "
import duckdb
con = duckdb.connect('fnma.duckdb')
con.execute('''CREATE TABLE mart_ldc_case_queue (
    request_no VARCHAR, request_status VARCHAR, sub_status VARCHAR,
    submitter_name VARCHAR, submitter_group VARCHAR, seller_number VARCHAR, seller_name VARCHAR,
    servicer_number VARCHAR, servicer_name VARCHAR, reviewer_group VARCHAR, reviewer_name VARCHAR,
    fm_loan_number VARCHAR, submission_date DATE, completion_date DATE,
    loan_count INTEGER, attribute_count INTEGER, queue_age VARCHAR, cycle_time VARCHAR, sla_status VARCHAR)''')
con.execute(\"INSERT INTO mart_ldc_case_queue VALUES ('100101','Draft','Draft','Jennifer Lee','External','99887','PennyMac','7721',NULL,'Auto-Decision','System',NULL,'2026-05-26',NULL,44,0,NULL,NULL,NULL)\")
con.execute('CREATE TABLE mart_ldc_status_summary AS SELECT request_status, COUNT(*) AS request_count FROM mart_ldc_case_queue GROUP BY request_status')
con.close()
"
```
(This is exactly what `tests/test_framework_smoke.py`'s `seed_mart()` does —
open that file if you want a ready-made version with more sample rows.)

**4. Set environment variables and start the API**
```
export DATA_LAYER=duckdb
export DUCKDB_PATH=./fnma.duckdb
export REDIS_URL=redis://localhost:6379/0
uvicorn main:app --reload --port 8000
```
PowerShell equivalents: `$env:DATA_LAYER="duckdb"`, etc.

**5. Verify**
```
curl http://localhost:8000/health
curl http://localhost:8000/dashboards
curl http://localhost:8000/api/ldc/case-queue
curl http://localhost:8000/api/ldc/status-summary
```
Swagger UI: http://localhost:8000/docs — every endpoint generated from
`ldc_case_management.yaml` shows up there automatically.

**6. Or just run the automated check instead of doing steps 3–5 by hand**
```
pip install httpx
python ../tests/test_framework_smoke.py
```
This seeds DuckDB, boots the app in-process, and exercises every endpoint —
the same checks listed under "Verified" below.

### Path B — Full Docker stack

Matches Step 0–1 of the deployment guide delivered earlier in this conversation.
```
$env:DOCKER_BUILDKIT=0                                  # corporate network requirement
docker compose -f deploy/docker-compose.yml up --build -d
docker compose -f deploy/docker-compose.yml ps
```
Then run the same `curl` checks from step 5 above against `localhost:8000`.
This stack also brings up Redis and a LocalStack S3 mock per that guide's
Step 1/2 design (LocalStack is S3-only — DuckDB remains the local Athena
substitute, not LocalStack).

### Running dbt

`scripts/generate_dbt_models.py` produces the staging/mart SQL and
`schema.yml` (already generated for `ldc_case_management.yaml` under
`backend/dbt_project/models/`). What this package does **not** include is
`dbt_project.yml`, `profiles.yml`, or a `sources.yml` pointing at real
tables/seeds — those are project-level files this project's backend README
already documents a working pattern for, and duplicating them here risked
drifting out of sync with whatever's actually checked in. To make the
generated models runnable with `dbt run`, carry over that existing
`dbt_project.yml`/`profiles.yml` pair and add a `sources.yml` entry for
whichever table backs `source.database` / `source.table` in each dashboard's
config. Happy to generate that scaffolding too if useful — say the word.



1. `cp backend/dashboards/_template.yaml backend/dashboards/<dashboard_id>.yaml` and fill it in
   (source table, staging columns, mart structure, filters, refresh tier).
2. `python backend/scripts/generate_dbt_models.py backend/dashboards/<dashboard_id>.yaml`
   — scaffolds the staging model, mart models, and `schema.yml`. Any column you marked
   `needs_clarification: true` shows up as a `NULL AS <col> -- TODO (NEEDS CLARIFICATION): ...`
   so it's impossible to miss.
3. Fill in the TODO business logic in the generated mart SQL, then `dbt run`.
4. Restart the API. The new dashboard's `/queue`, `/summary`, `/filters/*`, and
   `/export` endpoints exist automatically — `main.py` never changes.
5. `cache_sync/sync.py` already covers it too, since it iterates every config.

Roughly 80% of the boilerplate (column casting, route wiring, cache keys,
pagination, dbt test stubs) is generated. The 20% that stays manual is exactly
the dashboard-specific business logic (SLA thresholds, cycle-time rules, status
enums) — which is the only part that genuinely differs dashboard to dashboard.

## Verified (this session)

Run yourself with `python tests/test_framework_smoke.py` (needs `pip install
fastapi uvicorn pydantic pyyaml duckdb redis[hiredis] httpx` and a local Redis).
What it actually checked, against a synthetic DuckDB mart standing in for what
`dbt run` would produce:

- Queue endpoint returns paginated, filtered rows matching the config's filter list
- `ilike` filters match case-insensitively; `eq` filters narrow correctly
- Status summary endpoint returns all 6 configured statuses, including zero-count ones
- Filter-lookup (dropdown) endpoints return correct distinct values
- CSV export returns the right content-type and row count
- **Redis actually caches the queue response, including DATE columns**

That last point caught a real bug: `json.dumps` can't serialize
`datetime.date`/`Decimal`, and the cache's fail-open design (by design - a
Redis outage shouldn't break the API) was silently swallowing that error,
meaning the queue endpoint - the one with `submission_date`/`completion_date`
- was never actually being cached. Fixed in `core/cache/client.py` with a
custom JSON encoder; covered by an explicit assertion in the smoke test so it
can't regress silently again.

## Known placeholders carried over from the project plan

- `source.database` / `source.table` in `ldc_case_management.yaml` are still
  `TBD_*` pending the cross-account Iceberg table names from the source data team.
- `queue_age`, `cycle_time`, `sla_status` are generated as `NULL` with TODO
  comments — business rules for these are still open questions.
- `refresh_tier: near_real_time` for the LDC dashboard is the same flagged
  assumption as in the Excel task plan — confirm whether the live queue
  actually needs <15-min refresh before this ships.
