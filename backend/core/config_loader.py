from pathlib import Path
import yaml
from .config_schema import DashboardConfig, ServiceConfig


def load_dashboard_config(path: Path) -> DashboardConfig:
    """Loads one report's config.yaml and cross-checks its service_id/report_id
    against the folder it actually lives in: dashboards/<service_id>/<report_id>/
    config.yaml. This is what makes service_id/report_id "config-driven" rather
    than just inferred from the folder - the YAML is the explicit source of
    truth, and this check exists purely to catch drift (a renamed folder that
    nobody updated the YAML for, or vice versa) at load time instead of letting
    it surface later as a confusing mismatch."""
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    cfg = DashboardConfig(**data)

    report_dir = path.parent
    service_dir = report_dir.parent
    if cfg.report_id != report_dir.name:
        raise ValueError(
            f"{path}: report_id ('{cfg.report_id}') does not match its folder name "
            f"('{report_dir.name}'). Rename the folder or fix report_id so they match."
        )
    if cfg.service_id != service_dir.name:
        raise ValueError(
            f"{path}: service_id ('{cfg.service_id}') does not match its parent folder "
            f"name ('{service_dir.name}'). Rename the folder or fix service_id so they match."
        )
    return cfg


def load_service_config(path: Path) -> ServiceConfig:
    """Loads a service.yaml and cross-checks its service_id against the folder
    it lives in: dashboards/<service_id>/service.yaml."""
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    svc = ServiceConfig(**data)
    service_dir = path.parent
    if svc.service_id != service_dir.name:
        raise ValueError(
            f"{path}: service_id ('{svc.service_id}') does not match its folder name "
            f"('{service_dir.name}'). Rename the folder or fix service_id so they match."
        )
    return svc


def load_all_dashboards(dashboards_dir: Path) -> list[DashboardConfig]:
    """Walks dashboards/<service_id>/<report_id>/config.yaml two levels deep.
    Every service folder must have a service.yaml (even if it has only one
    report) - see dashboards/_template/service.yaml. Folders starting with
    "_" (e.g. _template) are skipped at both levels."""
    configs = []
    for service_dir in sorted(Path(dashboards_dir).iterdir()):
        if not service_dir.is_dir() or service_dir.name.startswith("_"):
            continue

        service_file = service_dir / "service.yaml"
        if not service_file.exists():
            raise FileNotFoundError(
                f"{service_dir} has no service.yaml - every service folder under dashboards/ "
                f"needs one (service_id/display_name/description), even if it has only one report. "
                f"See dashboards/_template/service.yaml."
            )
        load_service_config(service_file)

        for report_dir in sorted(service_dir.iterdir()):
            if not report_dir.is_dir() or report_dir.name.startswith("_"):
                continue
            config_file = report_dir / "config.yaml"
            if config_file.exists():
                configs.append(load_dashboard_config(config_file))
    return configs
