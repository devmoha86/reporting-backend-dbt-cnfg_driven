from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config_loader import load_all_dashboards
from core.router_factory import build_dashboard_router

BASE_DIR = Path(__file__).parent
DASHBOARDS_DIR = BASE_DIR / "dashboards"

app = FastAPI(title="FNMA Reporting Dashboard Framework")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_registered: list[str] = []
for cfg in load_all_dashboards(DASHBOARDS_DIR):
    app.include_router(build_dashboard_router(cfg))
    _registered.append(cfg.dashboard_id)


@app.get("/health")
async def health():
    return {"status": "ok", "dashboards_registered": _registered}


@app.get("/dashboards")
async def list_dashboards():
    """Lists every dashboard currently registered - useful while wiring up dashboard #2, #3, ..."""
    return {"dashboards": _registered}
