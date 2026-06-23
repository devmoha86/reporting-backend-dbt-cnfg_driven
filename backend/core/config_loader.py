from pathlib import Path
import yaml
from .config_schema import DashboardConfig


def load_dashboard_config(path: Path) -> DashboardConfig:
    data = yaml.safe_load(Path(path).read_text())
    return DashboardConfig(**data)


def load_all_dashboards(dashboards_dir: Path) -> list[DashboardConfig]:
    """Loads dashboards/<dashboard_id>/config.yaml for every dashboard folder,
    except folders prefixed with '_' (templates/examples)."""
    configs = []
    for d in sorted(Path(dashboards_dir).iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        config_file = d / "config.yaml"
        if config_file.exists():
            configs.append(load_dashboard_config(config_file))
    return configs
