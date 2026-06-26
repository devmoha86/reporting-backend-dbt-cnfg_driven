"""
End-to-end smoke test for the dlq (Delinquency Reported Data) report - mirrors
tests/test_framework_smoke.py's pattern for ldc, but additionally asserts:
  - the optional-endpoint behavior: dlq has api.summary_enabled: false, so this
    checks that no /summary route gets registered for it at all
  - the default-to-latest-period behavior: dlq's activity_period filter has
    default: latest, so a request that omits it should resolve to the most
    recent period in the mart, while an explicit empty value (?activity_period=)
    is the escape hatch that returns every period

Run with: python tests/test_dlq_smoke.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

tmp_db = tempfile.NamedTemporaryFile(suffix=".duckdb", delete=True)
tmp_db.close()
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
        CREATE TABLE mart_dlq_reported_data (
            fnm_loan_number VARCHAR, servicer_loan_identifier VARCHAR,
            master_servicer_number VARCHAR, master_servicer_name VARCHAR,
            acting_servicer_number VARCHAR, acting_servicer_name VARCHAR,
            activity_period VARCHAR, periods_delinquent INTEGER,
            servicer_action_type VARCHAR, servicer_action_type_received_dt DATE,
            conditional_data_attributes_action_type VARCHAR,
            delinquency_status_type_received_date DATE,
            delinquency_status_type_1 VARCHAR, delinquency_status_type_2 VARCHAR,
            delinquency_status_type_3 VARCHAR, delinquency_status_type_4 VARCHAR,
            delinquency_status_type_5 VARCHAR,
            delinquency_reason_type_received_date DATE,
            delinquency_reason_type_1 VARCHAR, delinquency_reason_type_2 VARCHAR,
            delinquency_reason_type_3 VARCHAR, delinquency_reason_type_4 VARCHAR,
            delinquency_reason_type_5 VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO mart_dlq_reported_data VALUES
        ('FNM000123456','SLI-001','MSN-1001','Master Servicer A','ASN-2001','Acting Servicer A',
         '06/2026',1,'Status Update','2026-06-04','Borrower Contact Required','2026-06-04',
         'Delinquent','30 Days Past Due',NULL,NULL,NULL,'2026-06-04','Payment Missed',NULL,NULL,NULL,NULL),
        ('FNM000999999','SLI-002','MSN-1002','Master Servicer B','ASN-2002','Acting Servicer B',
         '05/2026',2,'Status Update','2026-05-04','Borrower Contact Required','2026-05-04',
         'Delinquent','60 Days Past Due',NULL,NULL,NULL,'2026-05-04','Payment Missed',NULL,NULL,NULL,NULL)
    """)
    con.close()


def main():
    seed_mart(tmp_db.name)

    cfg = load_dashboard_config(BACKEND / "dashboards" / "dlq" / "reported-data" / "config.yaml")
    app = FastAPI()
    app.include_router(build_dashboard_router(cfg))
    client = TestClient(app)

    failures = []

    def check(label, cond):
        status = "PASS" if cond else "FAIL"
        print(f"[{status}] {label}")
        if not cond:
            failures.append(label)

    check("config loaded with summary disabled", cfg.api.summary_enabled is False)
    check("config loaded with no summary_model_name", cfg.mart.summary_model_name is None)

    r = client.get("/api/dlq/reported-data")
    check("reported-data endpoint returns 200", r.status_code == 200)
    body = r.json()
    check("no filter defaults to the latest activity_period (06/2026), 1 row",
          body.get("count") == 1 and len(body.get("items", [])) == 1)
    check("defaulted row is the 06/2026 loan", body["items"][0]["fnm_loan_number"] == "FNM000123456")

    r_all = client.get("/api/dlq/reported-data?activity_period=")
    check("explicit empty activity_period is the escape hatch - returns both periods",
          r_all.json().get("count") == 2)

    r2 = client.get("/api/dlq/reported-data?activity_period=06/2026")
    check("explicit activity_period=06/2026 narrows to 1 row", r2.json().get("count") == 1)
    check("filtered row is the 06/2026 loan", r2.json()["items"][0]["fnm_loan_number"] == "FNM000123456")

    r2b = client.get("/api/dlq/reported-data?activity_period=05/2026")
    check("explicit activity_period=05/2026 (not the default) still works", r2b.json().get("count") == 1)
    check("filtered row is the 05/2026 loan", r2b.json()["items"][0]["fnm_loan_number"] == "FNM000999999")

    r3 = client.get("/api/dlq/filters/activity_period")
    check("activity_period filter-lookup endpoint returns 200", r3.status_code == 200)
    check("filter lookup returns both distinct periods", set(r3.json()["items"]) == {"05/2026", "06/2026"})

    r4 = client.get("/api/dlq/reported-data/export?activity_period=06/2026")
    check("export endpoint returns 200 with CSV content-type", r4.status_code == 200 and "text/csv" in r4.headers["content-type"])
    check("export respects the activity_period filter (header + 1 data row)", len(r4.text.strip().splitlines()) == 2)

    r4b = client.get("/api/dlq/reported-data/export")
    check("export with no filter also defaults to the latest period (header + 1 data row)",
          len(r4b.text.strip().splitlines()) == 2)

    r5 = client.get("/api/dlq/summary")
    check("no summary route was registered for dlq (404, not 200)", r5.status_code == 404)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {failures}")
        sys.exit(1)
    print("All dlq smoke checks passed.")


if __name__ == "__main__":
    main()
