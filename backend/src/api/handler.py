"""FastAPI application handler for the small Phase 1 public API."""

from fastapi import FastAPI
from mangum import Mangum

from src.api.health import router as health_router

app = FastAPI(
    title="Stockara Phase 1",
    description="Public health API for the daily top picks and risk alerts pipeline",
    version="1.0.0",
)

app.include_router(health_router)

handler = Mangum(app, lifespan="off")
