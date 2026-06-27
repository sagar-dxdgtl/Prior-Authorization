"""HTTP API package. Re-export the FastAPI app so `uvicorn network_probe.api:app` works."""

from network_probe.api.app import app

__all__ = ["app"]
