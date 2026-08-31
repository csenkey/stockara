"""FastAPI application handler for public and authenticated APIs."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from src.api.health import router as health_router
from src.api.holding_reviews import router as holding_reviews_router

app = FastAPI(
    title="Stockara Phase 1",
    description="Public health and authenticated holding-analysis APIs",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("STOCKARA_SITE_URL", "http://localhost:5173")],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(health_router)
app.include_router(holding_reviews_router)

handler = Mangum(app, lifespan="off")
