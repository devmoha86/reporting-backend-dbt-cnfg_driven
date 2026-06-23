def make_cache_key(dashboard_id: str, endpoint: str, params: dict | None = None) -> str:
    """
    Deterministic cache key, same convention used elsewhere in this project:
    sorted params so identical filter sets always map to the same key.
    Example: make_cache_key("ldc", "case-queue", {"servicer_number": "SVC-001", "page": 1})
             -> "ldc:case-queue:page=1:servicer_number=SVC-001"
    """
    parts = [dashboard_id, endpoint]
    if params:
        for k in sorted(params):
            v = params[k]
            if v is None or v == "":
                continue
            parts.append(f"{k}={v}")
    return ":".join(parts)
