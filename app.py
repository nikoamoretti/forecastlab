"""Vercel FastAPI entrypoint. The local worker CLI stays available."""
from forecastlab_api.main import app

__all__ = ["app"]
