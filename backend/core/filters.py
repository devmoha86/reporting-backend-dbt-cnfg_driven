"""
Generic version of the _filter_clause() pattern already used in this project's
backend, except the field list comes from each dashboard's config instead of
being hand-written per endpoint. Always positional ($1, $2, ...) - never f-string
user input into SQL.
"""
from .config_schema import FilterDef

_OP_SQL = {
    "eq": "=",
    "ilike": "ILIKE",
    "gte": ">=",
    "lte": "<=",
}


def build_filter_clause(filter_defs: list[FilterDef], query_params: dict) -> tuple[str, list]:
    conditions: list[str] = []
    params: list = []
    for fd in filter_defs:
        value = query_params.get(fd.param)
        if value is None or value == "":
            continue
        params.append(f"%{value}%" if fd.op == "ilike" else value)
        conditions.append(f"{fd.field} {_OP_SQL[fd.op]} ${len(params)}")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params
