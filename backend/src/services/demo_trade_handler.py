"""Demo Trade Executor Lambda handler.

Executes daily simulated trades for all 100 demo accounts based on
AI recommendations. Triggered by EventBridge daily at 22:30 UTC.

Requirements: 2.1
"""

import asyncio
from typing import Any

import structlog

from backend.src.services.demo_trade_executor import DemoTradeExecutor

logger = structlog.get_logger(__name__)


def handler(event: dict, context: Any) -> dict:
    """Lambda handler for demo trade execution.

    Triggered daily at 22:30 UTC via EventBridge, 30 minutes after
    AI analysis completes. Processes all 100 demo accounts in batches
    of 25 to avoid timeout.

    Args:
        event: EventBridge scheduled event payload.
        context: Lambda context object.

    Returns:
        dict with statusCode and execution summary.
    """
    logger.info("demo_trade_execution_started", event=event)

    try:
        executor = DemoTradeExecutor()
        summary = asyncio.run(executor.execute_daily_trades())

        logger.info(
            "demo_trade_execution_completed",
            accounts_processed=summary.accounts_processed,
            buys_executed=summary.buys_executed,
            sells_executed=summary.sells_executed,
            skipped_insufficient_cash=summary.skipped_insufficient_cash,
            skipped_no_price=summary.skipped_no_price,
        )

        return {
            "statusCode": 200,
            "body": {
                "message": "Demo trade execution completed successfully",
                "accounts_processed": summary.accounts_processed,
                "buys_executed": summary.buys_executed,
                "sells_executed": summary.sells_executed,
                "skipped_insufficient_cash": summary.skipped_insufficient_cash,
                "skipped_no_price": summary.skipped_no_price,
            },
        }
    except Exception as e:
        logger.error("demo_trade_execution_failed", error=str(e), exc_info=True)
        return {
            "statusCode": 500,
            "body": {
                "message": "Demo trade execution failed",
                "error": str(e),
            },
        }
