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

Every dashboard is a **fully self-contained folder** under `backend/dashboards/<dashboard_id>/` —
its config, its generated dbt SQL, and its schema tests all live together. A developer
building dashboard #2 works entirely inside their own folder and never touches `core/`,
`router_factory.py`, or any other dashboard's folder.

```
backend/
  main.py                    <- scans dashboards/ for folders (skips "_"-prefixed ones), registers each one's router. Never edited to add a dashboard.
  core/                       <- shared framework code; a dashboard dev never edits this
    config_schema.py         <- the contract every dashboard's config.yaml must satisfy
    config_loader.py         <- discovers + validates each dashboards/<id>/config.yaml
    db/connection.py          <- DATA_LAYER swap (duckdb / athena) - unchanged from the existing pattern
    cache/client.py            <- Redis fail-open get/set
    cache/keys.py               <- deterministic cache key builder
    filters.py                   <- generic WHERE-clause builder driven by config
    router_factory.py             <- turns one DashboardConfig into a live FastAPI router (queue + summary endpoints are dumb passthroughs of their dbt marts - no aggregation logic lives here)
  dbt_project/
    dbt_project.yml             <- the ONE shared dbt project file - model-paths/seed-paths point at ../dashboards, so every dashboard's SQL still lives in its own folder
    profiles.yml                  <- duckdb (local, default) + athena (CI/prod) targets, no secrets - everything env_var()-driven
  dashboards/
    _template/                 <- copy this whole folder to start dashboard #2 (leading "_" = skipped by the loader)
      config.yaml
    ldc/                        <- the first dashboard, fully configured and self-contained
      config.yaml               <- you write this
      dbt/
        staging/
          stg_ldc_case_requests.sql              <- generated
          sources_ldc.yml                          <- generated; resolves {{ source(...) }} to the local seed (duckdb) or real table (athena)
        marts/
          mart_ldc_case_queue.sql                  <- generated SHELL; TODO business logic goes here
          mart_ldc_status_summary.sql              <- generated DEFAULT; this dashboard's entire chart-aggregation logic goes here
          schema_ldc.yml                            <- generated
        seeds/
          seed_ldc_raw.csv                          <- generated header only; sample rows filled in by hand, loaded via `dbt seed` for local dev
  scripts/generate_dbt_models.py  <- config.yaml -> that dashboard's own dbt/staging + dbt/marts + dbt/seeds SQL/CSV + schema.yml + sources.yml
  cache_sync/sync.py                <- config-driven cache-sync job (replaces a hardcoded MARTS list); summary sync is also a dumb passthrough of the summary mart
deploy/
  Dockerfile, docker-compose.yml, task-definition.template.json, buildspec.yml,
  github-actions-deploy.yml        <- all dashboard-agnostic; never touched per dashboard
tests/
  test_framework_smoke.py            <- the test that was actually run to verify this works
```

**Where dashboard-specific business logic goes** — there are exactly two places, both inside
that dashboard's own folder, never in the shared Python code:
1. `dashboards/<id>/dbt/marts/mart_<id>_queue.sql`, inside the `TODO` lines stamped in for every
   `derived_columns` entry flagged `needs_clarification: true`.
2. `dashboards/<id>/dbt/marts/mart_<id>_summary.sql` — the API only does `SELECT * FROM` this
   model, so whatever shape that dashboard's summary chart needs (status counts, sums, a trend
   line, ...) is written here, in SQL. The generator gives you a working zero-filled status-count
   default; rewrite it freely for anything else.

A third, separate fill-in-by-hand spot — not business logic, just sample data —
is `dashboards/<id>/dbt/seeds/seed_<id>_raw.csv`. The generator only writes the
header row (same `source_col` names the staging model casts from); add a few
realistic sample rows by hand so `dbt seed && dbt run` actually produces data
locally, the same way the `TODO` lines wait for a human to fill in business
rules.

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

**3. Get mart data into a local DuckDB file — two ways**

**3a. Fastest loop: hand-seed the mart tables directly**, skipping dbt entirely:
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
con.execute('CREATE TABLE mart_ldc_status_summary (request_status VARCHAR, request_count INTEGER)')
con.execute(\"INSERT INTO mart_ldc_status_summary VALUES ('Draft',1),('Exception Review',0),('Pending Reclass',0),('Completed',0),('Canceled',0),('Rejected',0)\")
con.close()
"
```
The summary mart is seeded directly here, not derived from the queue mart — that's the
real contract: `router_factory.get_summary()` does a plain `SELECT * FROM mart_ldc_status_summary`
and returns the rows as-is, so whatever you seed (or whatever `dbt run` actually builds) is
exactly what the API returns.

(This is exactly what `tests/test_framework_smoke.py`'s `seed_mart()` does —
open that file if you want a ready-made version with more sample rows.)

**3b. Closer to CI/prod: actually run dbt**, against the bundled seed data —
see "Running dbt" below for the full explanation, but the short version:
```
cd backend/dbt_project
dbt seed --profiles-dir .
dbt run  --profiles-dir .
dbt test --profiles-dir .
```
This builds the same `mart_ldc_case_queue`/`mart_ldc_status_summary` tables a real
`dbt run` against Athena would, just sourced from `dbt/seeds/seed_ldc_raw.csv`
instead of the real (still-`TBD_*`) Iceberg table. Either 3a or 3b leaves you
with the same two tables in `backend/fnma.duckdb` — pick whichever fits what
you're testing.

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
`dashboards/ldc/config.yaml` shows up there automatically.

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

`dbt run`/`dbt test`/`dbt seed` now actually work locally — verified end-to-end
in this session, not just scaffolded. There's one shared dbt project at
`backend/dbt_project/` (`dbt_project.yml` + `profiles.yml`, no secrets, both
env_var()-driven), and `scripts/generate_dbt_models.py` produces every
dashboard-specific dbt file **inside that dashboard's own folder**:
`dbt/staging/<model>.sql`, `dbt/staging/sources_<id>.yml`, `dbt/marts/*.sql`,
`dbt/marts/schema_<id>.yml`, and `dbt/seeds/seed_<id>_raw.csv`.

```
cd backend/dbt_project
dbt seed --profiles-dir .
dbt run  --profiles-dir .
dbt test --profiles-dir .
```
`dbt_project.yml`'s `model-paths`/`seed-paths` both point at `../dashboards`, so
dbt discovers every dashboard's SQL/CSV without any of it living next to
`dbt_project.yml` itself. The default target is `duckdb`, writing to
`backend/fnma.duckdb` (override with `DUCKDB_PATH` if running from a different
working directory than the API) — matches `deploy/buildspec.yml`'s existing
`cd backend/dbt_project` assumption, so no CI changes were needed for the local
path to line up with it. `dbt run --target athena --profiles-dir .` is the
CI/prod path, same as `buildspec.yml` already runs; that target needs real AWS
credentials and the `dbt-athena-community` adapter (now uncommented in
`requirements.txt`).

<details><summary>Two things worth knowing about how this actually works</summary>

**The source resolves to different tables depending on target.** Each
dashboard's generated `sources_<id>.yml` defines one source name (the same
`source.database`/`source.table` from `config.yaml` — for `ldc`, still
`TBD_source_database`/`TBD_ldc_case_requests` pending the source data team) but
resolves it to two different physical tables via a target-conditional Jinja
expression: the `duckdb` target points at the seeded
`dbt/seeds/seed_<id>_raw.csv`, and the `athena` target points at the real
`source.database`/`source.table` once those stop being `TBD_*`. The generated
staging SQL itself never changes — only `sources.yml` needs updating when the
real names land.

**This package does NOT ship real source data — `dbt seed` does.** A dashboard's
`config.yaml` can have fully placeholder source names and `dbt seed && dbt run`
still works locally, because the seed CSV stands in for the real table. This is
why `generate_dbt_models.py` writes the seed CSV's header row but never its
data rows — only a human can supply realistic sample values.

</details>

A real bug was caught running this end-to-end for the first time: the
generated summary mart's zero-fill `VALUES (...)` clause relied on
`column1`/`column2` positional naming, which doesn't exist in current DuckDB
(it uses `col0`/`col1`). Fixed by explicitly aliasing the column via
`(VALUES (...)) AS t(<status_field>)`, which is also more portable to
Athena/Trino than relying on either engine's positional default. Re-run the
generator on any dashboard whose summary mart was generated before this fix.

### Adding a new dashboard

1. `cp -r backend/dashboards/_template backend/dashboards/<dashboard_id>` and fill in
   `config.yaml` (source table, staging columns, mart structure, filters, refresh tier).
2. `python backend/scripts/generate_dbt_models.py backend/dashboards/<dashboard_id>/config.yaml`
   — scaffolds that dashboard's staging model, `sources_<dashboard_id>.yml`, mart models,
   `schema_<dashboard_id>.yml`, and a header-only `seed_<dashboard_id>_raw.csv`, all written into
   `dashboards/<dashboard_id>/dbt/`. Any column you marked
   `needs_clarification: true` shows up as a `NULL AS <col> -- TODO (NEEDS CLARIFICATION): ...`
   so it's impossible to miss.
3. Add a few sample rows by hand to `dbt/seeds/seed_<dashboard_id>_raw.csv` (header only out of
   the generator — it can't invent business data). Fill in the TODO business logic in the
   generated queue mart SQL. Separately, rewrite `mart_<dashboard_id>_summary.sql` if this
   dashboard's chart needs anything other than the generated zero-filled status-count default
   (a sum, a trend, a multi-series rollup). Then, from `backend/dbt_project/`:
   `dbt seed --profiles-dir . && dbt run --profiles-dir . && dbt test --profiles-dir .`.
4. Restart the API. The new dashboard's `/queue`, `/summary`, `/filters/*`, and
   `/export` endpoints exist automatically — `main.py` and `router_factory.py` never change,
   regardless of what that dashboard's summary chart looks like.
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
- Status summary endpoint passes through all 6 seeded mart rows untouched, including zero-count ones — no aggregation happens in Python; the API just selects from the summary mart
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

- `source.database` / `source.table` in `dashboards/ldc/config.yaml` are still
  `TBD_*` pending the cross-account Iceberg table names from the source data team.
- `queue_age`, `cycle_time`, `sla_status` are generated as `NULL` with TODO
  comments — business rules for these are still open questions.
- `refresh_tier: near_real_time` for the LDC dashboard is the same flagged
  assumption as in the Excel task plan — confirm whether the live queue
  actually needs <15-min refresh before this ships.
