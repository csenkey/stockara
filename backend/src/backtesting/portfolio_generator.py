"""Deterministic starting portfolio generation."""

from dataclasses import dataclass
from decimal import Decimal
import random

from backend.src.backtesting.models import BacktestPortfolio


@dataclass(frozen=True)
class PortfolioSeedInstrument:
    ticker: str
    price: Decimal


class BacktestPortfolioGenerator:
    """Generate empty portfolio shells deterministically.

    Initial purchases are a later data-dependent task; this class currently
    establishes repeatable IDs, policies, and allocation method metadata.
    """

    def __init__(self, *, seed: int) -> None:
        self._random = random.Random(seed)

    def generate_shells(
        self,
        *,
        count: int,
        initial_capital: Decimal,
        portfolio_policy_ids: list[str],
    ) -> list[BacktestPortfolio]:
        if count <= 0:
            raise ValueError("count must be positive")
        if not portfolio_policy_ids:
            raise ValueError("portfolio_policy_ids must not be empty")

        policies = list(portfolio_policy_ids)
        portfolios: list[BacktestPortfolio] = []
        for index in range(count):
            policy = policies[index % len(policies)]
            concentration = self._random.choice([1, 3, 5, 10])
            portfolios.append(
                BacktestPortfolio(
                    portfolio_id=f"bt_portfolio_{index + 1:04d}",
                    portfolio_policy_id=policy,
                    initial_allocation_method=f"deterministic_{concentration}_ticker_bucket",
                    cash=initial_capital,
                    metadata={"target_concentration": str(concentration)},
                )
            )
        return portfolios

