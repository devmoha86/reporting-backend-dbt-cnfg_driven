"""
THE swap point. Identical to the pattern already established for this project:
DATA_LAYER=duckdb  -> local DuckDB file (Phase 1 / local dev)
DATA_LAYER=athena  -> pyathena against real AWS Athena (Phase 2 / production)

execute_query()'s signature and return shape never change. Every dashboard router
calls only this function - no dashboard-specific code touches the database directly.
"""
import os

_DATA_LAYER = os.environ.get("DATA_LAYER", "duckdb")
_conn = None


def get_connection():
    global _conn
    if _conn is not None:
        return _conn

    if _DATA_LAYER == "duckdb":
        import duckdb
        path = os.environ.get(
            "DUCKDB_PATH",
            os.path.join(os.path.dirname(__file__), "..", "..", "fnma.duckdb"),
        )
        _conn = duckdb.connect(path, read_only=False)

    elif _DATA_LAYER == "athena":
        import pyathena
        _conn = pyathena.connect(
            region_name=os.environ["AWS_REGION"],
            s3_staging_dir=os.environ["ATHENA_S3_STAGING"],
            schema_name=os.environ.get("ATHENA_SCHEMA", "reporting"),
        )
    else:
        raise RuntimeError(f"Unknown DATA_LAYER: {_DATA_LAYER!r} (expected 'duckdb' or 'athena')")

    return _conn


def execute_query(sql: str, params: list | None = None) -> list[dict]:
    """Executes parameterised SQL ($1, $2, ... positional). Returns list of row dicts."""
    conn = get_connection()
    if _DATA_LAYER == "duckdb":
        rel = conn.execute(sql, params or [])
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r)) for r in rel.fetchall()]
    else:
        cursor = conn.cursor()
        cursor.execute(sql, params or [])
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, r)) for r in cursor.fetchall()]
