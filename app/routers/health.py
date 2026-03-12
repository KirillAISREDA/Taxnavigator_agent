"""Health check endpoint."""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    """Health check for monitoring and Portainer."""
    checks = {"api": "ok"}

    try:
        await request.app.state.redis.redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    try:
        info = request.app.state.qdrant.client.get_collections()
        checks["qdrant"] = "ok"
    except Exception:
        checks["qdrant"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "healthy" if all_ok else "degraded", "checks": checks}
