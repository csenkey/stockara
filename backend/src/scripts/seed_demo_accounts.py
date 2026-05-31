"""Seed script for initial demo account setup.

Creates 100 demo trading accounts with random initial allocations.
Each account starts with $10,000 split between cash and stock purchases.

Usage:
    python -m backend.src.scripts.seed_demo_accounts
"""

import asyncio
import logging
import sys

import structlog

from backend.src.db.connection import DatabasePool
from backend.src.services.demo_account_manager import DemoAccountManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stdout,
)
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger(__name__)


async def seed_demo_accounts(count: int = 100) -> None:
    """Create demo trading accounts with initial allocations.

    Args:
        count: Number of accounts to create.
    """
    logger.info("Starting demo account seeding", target_count=count)

    try:
        DatabasePool.initialize()

        manager = DemoAccountManager()
        accounts = await manager.create_accounts(count)

        logger.info(
            "Demo account seeding completed successfully",
            accounts_created=len(accounts),
            account_names=[a.account_name for a in accounts[:5]],
            total_initial_bankroll=f"${count * 10000:,.2f}",
        )

    except ValueError as e:
        logger.error(
            "Failed to seed demo accounts: insufficient active stocks",
            error=str(e),
        )
        sys.exit(1)

    except Exception as e:
        logger.error(
            "Failed to seed demo accounts",
            error=str(e),
            error_type=type(e).__name__,
        )
        sys.exit(1)

    finally:
        DatabasePool.close()


if __name__ == "__main__":
    asyncio.run(seed_demo_accounts())
