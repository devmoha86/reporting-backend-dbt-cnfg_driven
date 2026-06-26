# Reporting Dashboard Framework

Config-driven backend framework for the FNMA Reporting Dashboard MFE. The goal:
each of the 18 dashboards should mostly be a **YAML file**, not new Python code.

This package has been smoke-tested end-to-end (see "Verified" below) - it isn't
just sketched out, the queue/summary/filter/export endpoints, cache-aside Redis
flow, and the dbt model generator all actually run.

Two dashboards exist in this repo right now, deliberately shaped differently to
prove the framework scaffolds rather than assumes: `ldc` (queue + summary chart
+ filters + export) and `dlq` (queue + filters + export, **no summary chart at
all**). Same generator, same shared `core/` code, zero per-dashboard Python —
the only difference is what each one's `config.yaml` says it needs. See
"Adding a new dashboard" below for the config flags that make this possible.

## How it fits the existing architecture

Nothing here replaces what's already documented for this project - it's the
same `db/connection.py` DATA_LAYER swap, the same dbt staging→mart pattern, the
same cache-aside Redis flow, the same corporate Docker constraints. What's new
is that the FastAPI endpoints and the dbt model boilerplate are now **generated
from a config** instead of hand-written per dashboard.

**Every endpoint a dashboard exposes is opt-in, not assumed.** The schema used
to require every dashboard to have a summary chart and a status field. In
practice not every dashboard needs either — `dlq` only needs a queue/export
view, no chart. So `mart.summary_model_name` and `mart.status_field` are now
**optional** in `config.yaml`, and `api.queue_enabled` / `api.summary_enabled`
(both default `true`) let a dashboard's config explicitly turn either endpoint
off. `router_factory.py` only registers the routes a dashboard's config asks
for; a `model_validator` in `config_schema.py` blocks the one inconsistent
state (an endpoint enabled with no mart to back it) at config-load time,
before it can become a 500 at request time. Nothing else in `core/` changed —
this is additive, and the existing `ldc` dashboard (which still uses both
endpoints) needed zero changes to keep working.

Dashboards are organized two levels deep: a **service** (the business owner / source
system — `ldc`, `dlq`) groups one or more **reports** (what used to just be called "a
dashboard" — `case-management`, `reported-data`), and each report is the fully
self-contained folder: its config, its generated dbt SQL, and its schema tests all live
together under `backend/dashboards/<service_id>/<report_id>/`. A developer building
report #3 works entirely inside their own report folder and never touches `core/`,
`router_factory.py`, the service's `service.yaml`, or any other report's folder.

Each service folder also carries one `service.yaml` — service-level metadata
(`service_id`/`display_name`/`description`) for the grouping, validated by a new
`ServiceConfig` model and cross-checked by `config_loader.py` against the folder name,
the same way each report's `config.yaml` carries `service_id`/`report_id` fields
cross-checked against its own two-level folder path. Today `ldc` and `dlq` each have
exactly one report, but the structure supports adding a second report under an
existing service (new folder, existing `service.yaml`) without touching anything else.
**Route paths and dbt model/file names are still keyed off `dashboard_id` alone** (e.g.
`/api/ldc/case-queue`, `mart_ldc_case_queue`) and are unaffected by this folder split —
`dashboard_id` was already the framework's per-report unique identifier before
service/report folders existed, so nothing generated had to be renamed.

```
backend/
  main.py                    <- scans dashboards/<service_id>/<report_id>/ two levels deep (skips "_"-prefixed folders at both levels), registers each report's router. Never edited to add a dashboard.
  core/                       <- shared framework code; a dashboard dev never edits this
    config_schema.py         <- the contract every service.yaml and report config.yaml must satisfy (ServiceConfig + DashboardConfig)
    config_loader.py         <- discovers + validates each dashboards/<service_id>/service.yaml and dashboards/<service_id>/<report_id>/config.yaml
    db/connection.py          <- DATA_LAYER swap (duckdb / athena) - unchanged from the existing pattern
    cache/client.py            <- Redis fail-open get/set
    cache/keys.py               <- deterministic cache key builder
    filters.py                   <- generic WHERE-clause builder driven by config
    router_factory.py             <- turns one DashboardConfig into a live FastAPI router (queue + summary endpoints are dumb passthroughs of their dbt marts - no aggregation logic lives here)
  dbt_project/
    dbt_project.yml             <- the ONE shared dbt project file - model-paths/seed-paths point at ../dashboards, recursively discovering every nested service/report folder with no changes needed
    profiles.yml                  <- duckdb (local, default) + athena (CI/prod) targets, no secrets - everything env_var()-driven
  dashboards/
    _template/                 <- copy this whole tree to start a new SERVICE; copy just _template_report/ to start a new REPORT under an existing service (leading "_" = skipped by the loader at both levels)
      service.yaml
      _template_report/
        config.yaml
    ldc/                        <- "ldc" service
      service.yaml               <- service-level metadata, validated against this folder name
      case-management/             <- first report: queue + summary chart + filters + export
        config.yaml                 <- you write this
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
    dlq/                        <- "dlq" service
      service.yaml               <- service-level metadata, validated against this folder name
      reported-data/               <- first report: queue + filters + export, NO summary chart -
        config.yaml                   the worked example of api.summary_enabled: false / mart.summary_model_name: null,
                                       and of a filter with default: latest (activity_period)
        dbt/
          staging/
            stg_dlq_reported_data.sql                <- generated
            sources_dlq.yml                            <- generated, same duckdb/athena target-conditional pattern as ldc
          marts/
            mart_dlq_reported_data.sql                 <- generated SHELL, hand-edited to add the trailing-24-month
                                                           activity_period window (see "Adding a new dashboard" below)
            schema_dlq.yml                             <- generated, no summary model block since none is configured
          seeds/
            seed_dlq_raw.csv                            <- 100 sample rows per activity_period (26 periods, 2,600 rows
                                                           total) - enough to exercise the 24-month window filter
  scripts/generate_dbt_models.py  <- config.yaml -> that report's own dbt/staging + dbt/marts + dbt/seeds SQL/CSV + schema.yml + sources.yml; skips the summary mart entirely when summary_model_name isn't set
  cache_sync/sync.py                <- config-driven cache-sync job (replaces a hardcoded MARTS list); summary sync is also a dumb passthrough of the summary mart
deploy/
  Dockerfile, docker-compose.yml, task-definition.template.json, buildspec.yml,
  github-actions-deploy.yml        <- all dashboard-agnostic; never touched per dashboard
tests/
  test_framework_smoke.py            <- the test that was actually run to verify this works (ldc, both endpoints)
  test_dlq_smoke.py                  <- same pattern for dlq; also asserts /summary is genuinely absent (404), not just empty, and exercises the default: latest activity_period behavior
```

**Where dashboard-specific business logic goes** — there are exactly two places, both inside
that report's own folder, never in the shared Python code (note: generated filenames are
still keyed by `dashboard_id`, not `report_id` — see the module docstring in
`scripts/generate_dbt_models.py`):
1. `dashboards/<service_id>/<report_id>/dbt/marts/mart_<id>_queue.sql`, inside the `TODO` lines
   stamped in for every `derived_columns` entry flagged `needs_clarification: true`. This is also
   where any other row-level business logic goes — `dlq`'s trailing-24-month `activity_period`
   window (see "Adding a new dashboard" below) is a second example of the same pattern: generated
   SHELL, hand-edited once, documented in a comment block at the top of the file.
2. `dashboards/<service_id>/<report_id>/dbt/marts/mart_<id>_summary.sql` — the API only does
   `SELECT * FROM` this model, so whatever shape that dashboard's summary chart needs (status
   counts, sums, a trend line, ...) is written here, in SQL. The generator gives you a working
   zero-filled status-count default; rewrite it freely for anything else. **Only generated at all
   if `mart.summary_model_name` is set in `config.yaml`** — leave it unset (and
   `api.summary_enabled: false`) for a dashboard like `dlq` that has no chart, and this file, its
   route, and its cache-sync entry simply don't exist.

A third, separate fill-in-by-hand spot — not business logic, just sample data —
is `dashboards/<service_id>/<report_id>/dbt/seeds/seed_<id>_raw.csv`. The generator only writes the
header row (same `source_col` names the staging model casts from); add a few
realistic sample rows by hand so `dbt seed && dbt run` actually produces data
locally, the same way the `TODO` lines wait for a human to fill in business
rules. `dlq`'s seed file has 100 rows per `activity_period` (2,600 rows across
26 months) specifically so the 24-month window filter has real data to include
*and* exclude — a couple of hand-typed rows is enough to prove an endpoint
responds, but proving a date-window filter actually filters needs enough
periods on both sides of the boundary.

**Filters can also default to the latest value, config-only.** A `FilterDef` in
`api.filters` may set `default: latest` (and, if the field's raw text doesn't sort
correctly as a string — e.g. `dlq`'s `activity_period` is `"MM/YYYY"`, where `"06/2026" <
"12/2025"` lexically — `default_parse_format: "%m/%Y"` too). When a request omits that
filter's query param entirely, `router_factory.py` resolves it at request time to the most
recent value of that field present in the mart, instead of returning every row across every
period. An explicit empty value (`?activity_period=`) is the escape hatch — it reuses
`build_filter_clause`'s existing "empty string means no filter" convention to ask for every
period on purpose. `dlq`'s `reported-data` config is the worked example; nothing else in
`core/` needed to change to support this for any other filter on any other report.

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
curl http://localhost:8000/api/dlq/reported-data                              # no activity_period -> defaults to the latest period in the mart
curl "http://localhost:8000/api/dlq/reported-data?activity_period=06/2026"    # explicit period -> narrows to just that one
curl "http://localhost:8000/api/dlq/reported-data?activity_period="           # explicit EMPTY value -> escape hatch, returns every period
curl http://localhost:8000/api/dlq/summary       # 404 by design - dlq has no summary endpoint
```
Swagger UI: http://localhost:8000/docs — every endpoint generated from
`dashboards/ldc/case-management/config.yaml` and `dashboards/dlq/reported-data/config.yaml`
shows up there automatically (only the endpoints each one's config actually enables).

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

`dbt seed` / `dbt run` / `dbt test` work in three modes: local DuckDB,
Dockerized DuckDB, and Athena (CI/prod). One shared dbt project lives at
`backend/dbt_project/` (`dbt_project.yml` + `profiles.yml`, both env-driven).
Each report keeps its own dbt files under `backend/dashboards/<service_id>/<report_id>/dbt/`
— `model-paths`/`seed-paths` point at `../dashboards` and walk it recursively, so
the two-level service/report nesting needed zero changes to `dbt_project.yml`.

#### 1) Local DuckDB flow (recommended while iterating)

From `backend/dbt_project/`:

```
dbt deps --profiles-dir .
dbt seed --profiles-dir . --target duckdb --full-refresh
dbt run  --profiles-dir . --target duckdb --full-refresh
dbt test --profiles-dir . --target duckdb
```

What each step does:

- `dbt seed`: loads CSVs from `backend/dashboards/*/dbt/seeds/` into DuckDB tables.
- `dbt run`: builds staging + mart models in dependency order.
- `dbt test`: executes tests from `schema_<id>.yml` against built model tables.

For the LDC dashboard specifically:

- seed file: `backend/dashboards/ldc/case-management/dbt/seeds/seed_ldc_raw.csv`
- staging model: `backend/dashboards/ldc/case-management/dbt/staging/stg_ldc_case_requests.sql`
- mart under test: `backend/dashboards/ldc/case-management/dbt/marts/mart_ldc_case_queue.sql`
- tests config: `backend/dashboards/ldc/case-management/dbt/marts/schema_ldc.yml`

#### 2) Local DuckDB flow (LDC only, faster loop)

From `backend/dbt_project/`:

```
dbt seed --profiles-dir . --target duckdb --full-refresh --select seed_ldc_raw
dbt run  --profiles-dir . --target duckdb --full-refresh --select stg_ldc_case_requests mart_ldc_case_queue mart_ldc_status_summary
dbt test --profiles-dir . --target duckdb --select mart_ldc_case_queue mart_ldc_status_summary
```

This avoids rebuilding unrelated dashboards.

#### 2b) Local DuckDB flow (dlq only, faster loop)

Same pattern, no summary model to select since `dlq` doesn't have one:

```
dbt seed --profiles-dir . --target duckdb --full-refresh --select seed_dlq_raw
dbt run  --profiles-dir . --target duckdb --full-refresh --select stg_dlq_reported_data mart_dlq_reported_data
dbt test --profiles-dir . --target duckdb --select mart_dlq_reported_data
```

`seed_dlq_raw.csv` has 100 rows for each of 26 monthly `activity_period`s
(2,600 rows total), so this is also the quickest way to confirm the
trailing-24-month window in `mart_dlq_reported_data.sql` is doing its job —
after `dbt run`, `select count(*) from mart_dlq_reported_data` should return
2,400 (24 periods × 100), not 2,600, since the 2 oldest seeded periods fall
outside the window on purpose.

#### 3) Docker flow (same dbt commands inside container)

Because `deploy/docker-compose.yml` does not mount the repo as a bind volume,
container files are baked at image-build time. If you changed seed CSV/SQL on
host, rebuild before running dbt in Docker.

From repo root:

```
docker compose -f deploy/docker-compose.yml up --build -d
docker compose -f deploy/docker-compose.yml exec backend sh -lc "cd /app/dbt_project && dbt seed --profiles-dir . --target duckdb --full-refresh && dbt run --profiles-dir . --target duckdb --full-refresh && dbt test --profiles-dir . --target duckdb"
```

#### 4) Athena flow (CI/prod)

From `backend/dbt_project/`:

```
dbt run  --profiles-dir . --target athena
dbt test --profiles-dir . --target athena
```

Requirements:

- real AWS credentials
- `ATHENA_S3_STAGING`, `AWS_REGION` (optional default exists), and `ATHENA_SCHEMA`
- `dbt-athena-community` installed

#### Troubleshooting dbt (common failures and false positives)

1. `dbt test` passes even after changing a seed row value

- Cause: tests run on built marts, not directly on CSV.
- Fix: rerun `seed` and `run` before `test`, ideally with `--full-refresh`.
- Verify bad value reached mart:

```
dbt run-operation run_query --args '{sql: "select request_status, count(*) from mart_ldc_case_queue group by 1 order by 1"}' --profiles-dir . --target duckdb
```

2. `dbt test --select ldc` returns success too quickly / tests not actually selected

- Cause: selector does not match model/test names.
- Fix: select by model names or path:

```
dbt ls --resource-type test --profiles-dir . --target duckdb
dbt test --profiles-dir . --target duckdb --select mart_ldc_case_queue
```

3. Docker run does not reflect edited CSV/model files

- Cause: image is stale; compose file has no bind mount.
- Fix: `docker compose ... up --build -d` (or rebuild image) before dbt commands.

4. `accepted_values` does not fail for a typo like `Canceleds`

- Check that test is defined under the correct column in
  `backend/dashboards/ldc/case-management/dbt/marts/schema_ldc.yml`.
- Ensure typo value exists in `mart_ldc_case_queue` (not only in CSV).
- Re-run in order: `seed -> run -> test`.

5. Build stage errors in Docker (`no build stage in current context`)

- Cause: missing `FROM` in `deploy/Dockerfile`.
- Fix: use a valid base image (already fixed in this repo).

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

There are two different starting points, depending on whether this is a brand-new
service (a new source system / business owner) or another report under one that
already exists:

**Adding a second report under an existing service** (e.g. another `dlq` report
alongside `reported-data`):

1. `cp -r backend/dashboards/dlq/reported-data backend/dashboards/dlq/<report_id>`
   (copy any existing report folder under that service as a starting point — its
   `service.yaml` one level up is reused unchanged) and fill in `config.yaml`,
   updating `dashboard_id`/`report_id` and everything else (source table, staging
   columns, mart structure, filters, refresh tier). `service_id` stays the same.
   **Decide which endpoints this report actually needs.** `api.queue_enabled` and
   `api.summary_enabled` each default to `true`; set either to `false` if this report
   doesn't need that endpoint. If you disable `summary_enabled`, leave
   `mart.summary_model_name` unset (`null`) — the schema's `model_validator` will reject
   the opposite combination (an endpoint enabled with no mart configured to back it) the
   moment you try to load the config, before it ever reaches a router. `mart.status_field`
   is independently optional too (only meaningful if you do have a summary/status-count
   mart). `dlq/reported-data/config.yaml` is the worked example: `api.summary_enabled: false`,
   `mart.summary_model_name: null`, `mart.status_field: null` — no summary chart, no
   `/summary` route, nothing extra generated. It's also the worked example of a
   `default: latest` filter (`activity_period`) — see the "Filters can also default to the
   latest value" callout above if this report has a period/date-like filter that should
   behave the same way.
2. Continue with steps 2–5 below.

**Adding a brand-new service** (a new source system / business owner, with its first report):

1. `cp -r backend/dashboards/_template backend/dashboards/<service_id>`, then
   `mv backend/dashboards/<service_id>/_template_report backend/dashboards/<service_id>/<report_id>`.
   Fill in `backend/dashboards/<service_id>/service.yaml` — `service_id` must equal the
   folder name you just created. Fill in
   `backend/dashboards/<service_id>/<report_id>/config.yaml` the same way as step 1 above
   (`service_id` must equal `<service_id>`, `report_id` must equal `<report_id>`).
2. `python backend/scripts/generate_dbt_models.py backend/dashboards/<service_id>/<report_id>/config.yaml`
   — scaffolds that report's staging model, `sources_<dashboard_id>.yml`, mart models,
   `schema_<dashboard_id>.yml`, and a header-only `seed_<dashboard_id>_raw.csv` (generated
   filenames stay keyed by `dashboard_id`, not `report_id`), all written into
   `dashboards/<service_id>/<report_id>/dbt/`. Any column you marked
   `needs_clarification: true` shows up as a `NULL AS <col> -- TODO (NEEDS CLARIFICATION): ...`
   so it's impossible to miss. If `mart.summary_model_name` is unset, the generator prints a line
   saying it skipped the summary mart and moves on — no empty/dummy summary file gets written.
3. Add sample rows by hand to `dbt/seeds/seed_<dashboard_id>_raw.csv` (header only out of
   the generator — it can't invent business data). A handful of rows is enough to prove an
   endpoint responds; if the report has date-window or trailing-period filtering logic, seed
   enough periods to have rows on *both* sides of the boundary so the filter is actually exercised
   (see `dlq`'s seed: 100 rows × 26 monthly `activity_period`s, 2 of which fall outside its
   24-month window on purpose). Fill in the TODO business logic in the generated queue mart SQL —
   `dlq`'s trailing-24-month window in `mart_dlq_reported_data.sql` is a worked example of a
   hand-edit beyond the `derived_columns` TODO pattern, documented in a comment block explaining
   *why* (anchored to the latest `activity_period` in the data, not wall-clock `today()`, because
   the source is `snapshot_eom` batch data) and warning that re-running the generator will
   silently drop the edit back to a plain `SELECT *`. If this report does have a summary chart,
   separately rewrite `mart_<dashboard_id>_summary.sql` if it needs anything other than the
   generated zero-filled status-count default (a sum, a trend, a multi-series rollup). Then, from
   `backend/dbt_project/`: `dbt seed --profiles-dir . && dbt run --profiles-dir . && dbt test --profiles-dir .`.
4. Restart the API. The new report's enabled endpoints — `/queue` and/or `/summary` (whichever
   `config.yaml` turned on), plus `/filters/*` and `/export` — exist automatically. `main.py` and
   `router_factory.py` never change, regardless of what endpoints that report turns on, what its
   summary chart looks like (if it has one), or how deep its folder nests under `dashboards/`.
5. `cache_sync/sync.py` already covers it too, since it iterates every config via
   `load_all_dashboards()` — and skips the summary-sync step for any report with
   `summary_enabled: false`.

Either way, `config_loader.py` validates the folder/config consistency for you: it will
refuse to load a `config.yaml` whose `service_id`/`report_id` fields don't match the
folder it actually lives in, and it will refuse to load a service folder with no
`service.yaml` at all — both fail loudly at startup, not as a confusing runtime mismatch
later.

Roughly 80% of the boilerplate (column casting, route wiring, cache keys,
pagination, dbt test stubs) is generated. The 20% that stays manual is exactly
the dashboard-specific business logic (SLA thresholds, cycle-time rules, status
enums, date-window filters) — which is the only part that genuinely differs
dashboard to dashboard. `dlq` next to `ldc` is the proof: same generator, same
`core/`, two dashboards with different endpoint shapes and different mart-level
business logic, zero new Python either time.

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

**The `dlq` dashboard and the optional-endpoint framework change were verified
separately**, with `python tests/test_dlq_smoke.py`:

- Config loads with `api.summary_enabled: False` and `mart.summary_model_name: None`
  without the schema rejecting it (the whole point of making these optional)
- `/api/dlq/reported-data` returns seeded rows; `?activity_period=06/2026` narrows correctly
- **Omitting `activity_period` entirely defaults to the latest period present in the mart**
  (the `default: latest` / `default_parse_format: "%m/%Y"` feature) — confirmed it resolves to
  exactly one row, the most recent of the two seeded periods
- **Explicit empty `?activity_period=` is the escape hatch** — confirmed it returns every
  seeded period, not just the default
- An explicit non-default period (`?activity_period=05/2026`) still narrows correctly, proving
  the default only kicks in when the param is omitted, not whenever it's set to anything
- `/api/dlq/filters/activity_period` (dropdown lookup) returns the right distinct values
- `/api/dlq/reported-data/export?activity_period=` respects the same filter as the queue endpoint,
  and export with no filter also defaults to the latest period — the default-resolution logic in
  `router_factory.py` is shared between the queue and export routes, not duplicated
- **`/api/dlq/summary` returns 404** — not an empty 200 — proving `router_factory.py` genuinely
  never registers a route for a disabled endpoint, rather than registering it with nothing behind it
- `tests/test_framework_smoke.py` (the `ldc` test, which still uses both endpoints) was re-run after
  every framework change and passed throughout — the optional-endpoint work is additive, not a
  rewrite of existing behavior

**The service/report folder restructuring was verified separately**: a clean `dbt seed && dbt run`
against the new nested layout (`dashboards/ldc/case-management/dbt/...`,
`dashboards/dlq/reported-data/dbt/...`) built all 5 models with zero path errors, confirming
`dbt_project.yml`'s existing `model-paths`/`seed-paths: ["../dashboards"]` discovers the two-level
nesting recursively with no config changes. Both python smoke tests pass in full against the
restructured layout: 13/13 checks for `ldc` (framework smoke), 16/16 for `dlq` (including every
default-latest-period check above).

Separately, the full real pipeline was run end-to-end against `dlq`'s 2,600-row seed (not the
synthetic stand-in above): `dbt seed && dbt run && dbt test` against local DuckDB built
`stg_dlq_reported_data` (2,600 rows) and `mart_dlq_reported_data` (2,400 rows — confirming the
trailing-24-month window correctly dropped the 2 oldest of the 26 seeded `activity_period`s and
kept the other 24), and the `not_null` test on `fnm_loan_number` passed.

## Known placeholders carried over from the project plan

- `source.database` / `source.table` in `dashboards/ldc/case-management/config.yaml` are still
  `TBD_*` pending the cross-account Iceberg table names from the source data team.
- `queue_age`, `cycle_time`, `sla_status` are generated as `NULL` with TODO
  comments — business rules for these are still open questions.
- `refresh_tier: near_real_time` for the LDC dashboard is the same flagged
  assumption as in the Excel task plan — confirm whether the live queue
  actually needs <15-min refresh before this ships.
- `source.database` / `source.table` in `dashboards/dlq/reported-data/config.yaml` are likewise still
  `TBD_*` (`TBD_source_database` / `TBD_dlq_reported_data`) pending the real table names.
  `refresh_tier: snapshot_eom` reflects the "Last Updated" batch pattern on the dlq mockup,
  not a confirmed SLA — confirm with the source data team before this ships.
- `mart.primary_key: fnm_loan_number` for `dlq` is not a true unique key — the real grain is
  `(fnm_loan_number, activity_period)`. No `dbt_utils` package is installed in this project, so
  the generated `schema_dlq.yml` only carries a `not_null` test on `fnm_loan_number`, not
  `unique`; add a `dbt_utils.unique_combination_of_columns` test (and the package) if/when
  composite-key uniqueness needs to be enforced in CI.
- `dashboards/ldc/case-management/config.yaml`'s `mart.status_values` has a typo —
  `"Rejec"` should almost certainly be `"Rejected"`. Pre-existing (not introduced by the
  service/report restructuring), and it's why `dbt test --select mart_ldc_case_queue` currently
  fails one `accepted_values` test. Fix the string in that one config and re-run the generator's
  downstream `dbt test` to confirm.
