"""
Defines the schema every dashboard config YAML must follow.
Adding a new dashboard = writing a new YAML file that validates against this schema,
not writing new Python code. See backend/dashboards/_template/config.yaml to start one.
"""
from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, Field

RefreshTier = Literal["near_real_time", "snapshot_eod", "snapshot_eom", "needs_clarification"]
FilterOp = Literal["eq", "ilike", "gte", "lte"]


class SourceConfig(BaseModel):
    database: str          # Glue/Iceberg database in the source data team's account (placeholder until confirmed)
    table: str              # Iceberg table backing the case/request grain
    loan_attributes_table: Optional[str] = None  # Iceberg table backing loan-level rollups, if any


class StagingColumn(BaseModel):
    name: str               # column name after staging (snake_case)
    type: str                # DuckDB/Athena-compatible type, e.g. varchar, date, integer
    source_col: str          # raw column name in the source table


class StagingConfig(BaseModel):
    model_name: str
    columns: list[StagingColumn]


class DerivedColumn(BaseModel):
    """A mart column whose business logic isn't known yet (e.g. SLA thresholds)."""
    name: str
    needs_clarification: bool = False
    note: Optional[str] = None


class MartConfig(BaseModel):
    queue_model_name: str
    summary_model_name: str
    primary_key: str
    status_field: str
    status_values: list[str] = Field(default_factory=list)
    derived_columns: list[DerivedColumn] = Field(default_factory=list)


class FilterDef(BaseModel):
    field: str               # mart column name
    param: str                # query-param name exposed in the API
    op: FilterOp = "eq"


class SortDef(BaseModel):
    field: str
    direction: Literal["asc", "desc"] = "desc"


class ApiConfig(BaseModel):
    base_path: str                    # e.g. /api/ldc
    queue_endpoint: str = "queue"
    summary_endpoint: str = "summary"
    filters: list[FilterDef] = Field(default_factory=list)
    default_page_size: int = 25
    default_sort: SortDef
    filter_lookup_fields: list[str] = Field(default_factory=list)
    export_enabled: bool = True


class DashboardConfig(BaseModel):
    dashboard_id: str                 # short slug, used in routes/cache keys/dbt model names
    display_name: str
    description: str = ""
    refresh_tier: RefreshTier
    cache_ttl_seconds: int = 3600
    source: SourceConfig
    staging: StagingConfig
    mart: MartConfig
    api: ApiConfig
