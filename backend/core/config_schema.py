"""
Defines the schema every dashboard config YAML must follow.
Adding a new report = writing a new YAML file that validates against this schema,
not writing new Python code. See backend/dashboards/_template/ to start one (a
service.yaml plus a _template_report/config.yaml, one directory per report).

Folder convention (enforced by core/config_loader.py, not by this file - this
file only validates the YAML content, not where it lives on disk):
    dashboards/<service_id>/service.yaml
    dashboards/<service_id>/<report_id>/config.yaml
A "service" (e.g. ldc, dlq) is a grouping of one or more "reports" that share a
business owner/source system; each report is exactly what used to be called a
"dashboard" - its own fully self-contained config + dbt models.
"""
from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, Field, model_validator

RefreshTier = Literal["near_real_time", "snapshot_eod", "snapshot_eom", "needs_clarification"]
FilterOp = Literal["eq", "ilike", "gte", "lte"]
FilterDefault = Literal["latest"]


class ServiceConfig(BaseModel):
    """Schema for dashboards/<service_id>/service.yaml - metadata for the
    folder grouping one or more reports. Cross-checked by config_loader.py
    against its own folder name; carries no routing/dbt behavior itself."""
    service_id: str
    display_name: str
    description: str = ""


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
    # Optional: not every dashboard has a summary chart. Required only if
    # api.summary_enabled is True (see DashboardConfig validator below).
    summary_model_name: Optional[str] = None
    primary_key: str
    # Optional: only needed if this dashboard has a canonical "status" column
    # to validate (accepted_values test) and/or roll up in a summary chart.
    status_field: Optional[str] = None
    status_values: list[str] = Field(default_factory=list)
    derived_columns: list[DerivedColumn] = Field(default_factory=list)


class FilterDef(BaseModel):
    field: str               # mart column name
    param: str                # query-param name exposed in the API
    op: FilterOp = "eq"
    # Optional: when set to "latest" and a request omits this filter's query
    # param entirely, router_factory.py substitutes the most recent value of
    # `field` present in the mart (computed at request time), instead of
    # returning unfiltered rows across every period. Has no effect if the
    # request explicitly passes the param (even as an empty string - that's
    # the escape hatch a caller uses to deliberately request "all", bypassing
    # the default). Most useful on period/date-like filters - see
    # dashboards/dlq/reported-data/config.yaml's activity_period filter.
    default: Optional[FilterDefault] = None
    # Optional: a strptime format string (e.g. "%m/%Y"), needed only when
    # `field`'s raw string values don't sort correctly as plain strings (e.g.
    # "06/2026" < "12/2025" lexically, which is backwards). When set,
    # router_factory.py orders by the parsed date instead of the raw string
    # to find the "latest" value. Leave unset for fields that already sort
    # correctly as strings (ISO dates, numbers stored as text, etc.).
    default_parse_format: Optional[str] = None


class SortDef(BaseModel):
    field: str
    direction: Literal["asc", "desc"] = "desc"


class ApiConfig(BaseModel):
    base_path: str                    # e.g. /api/ldc
    queue_endpoint: str = "queue"
    summary_endpoint: str = "summary"
    # Per-dashboard endpoint toggles - not every dashboard has every endpoint.
    # Default True for both so existing dashboards (e.g. ldc) keep working
    # unchanged without touching their config.yaml.
    queue_enabled: bool = True
    summary_enabled: bool = True
    filters: list[FilterDef] = Field(default_factory=list)
    default_page_size: int = 25
    default_sort: SortDef
    filter_lookup_fields: list[str] = Field(default_factory=list)
    export_enabled: bool = True


class DashboardConfig(BaseModel):
    dashboard_id: str                 # short slug, used in routes/cache keys/dbt model names. Must stay
                                        # globally unique across every report in every service - unrelated
                                        # to folder nesting, kept as its own field so existing cache
                                        # keys/route tags/dbt model names never had to change when the
                                        # service/report folder split was introduced.
    service_id: str                   # must match the parent folder name (dashboards/<service_id>/) -
                                        # checked by core/config_loader.py at load time, not here (this
                                        # validator only sees YAML content, not the file's path on disk).
    report_id: str                    # must match this folder's name (dashboards/<service_id>/<report_id>/) -
                                        # same cross-check as service_id, done in config_loader.py.
    display_name: str
    description: str = ""
    refresh_tier: RefreshTier
    cache_ttl_seconds: int = 3600
    source: SourceConfig
    staging: StagingConfig
    mart: MartConfig
    api: ApiConfig

    @model_validator(mode="after")
    def _validate_enabled_endpoints_have_models(self) -> "DashboardConfig":
        if self.api.summary_enabled and not self.mart.summary_model_name:
            raise ValueError(
                "api.summary_enabled is True but mart.summary_model_name is not set. "
                "Either set mart.summary_model_name or set api.summary_enabled: false."
            )
        if self.api.queue_enabled and not self.mart.queue_model_name:
            raise ValueError(
                "api.queue_enabled is True but mart.queue_model_name is not set."
            )
        return self
