"""FastAPI application handler for AWS Lambda via Mangum.

This is the main entry point for the API Lambda function. It creates
the FastAPI application, registers all routers, and exposes a Mangum
handler for API Gateway integration.
"""

from fastapi import FastAPI
from mangum import Mangum

from backend.src.api.auth import router as auth_router
from backend.src.api.demo import router as demo_router
from backend.src.api.health import router as health_router
from backend.src.api.portfolio import router as portfolio_router
from backend.src.api.preferences import router as preferences_router
from backend.src.api.stocks import router as stocks_router
from backend.src.api.suggestions import router as suggestions_router
from backend.src.api.top_pick import router as top_pick_router

app = FastAPI(
    title="Stock Monitoring and Analysis System",
    description="REST API for stock monitoring, portfolio management, and demo trading accounts",
    version="1.0.0",
)

# Public routers (no auth)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(demo_router)
app.include_router(top_pick_router)

# Protected routers (auth handled per-router via dependencies)
app.include_router(stocks_router)
app.include_router(portfolio_router)
app.include_router(preferences_router)
app.include_router(suggestions_router)

# Mangum adapter for AWS Lambda + API Gateway
handler = Mangum(app, lifespan="off")
