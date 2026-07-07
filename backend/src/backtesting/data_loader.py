"""Historical data loader interfaces for future S3-backed backtests."""

from datetime import date
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field

from backend.src.backtesting.models import InstrumentType


class PriceBar(BaseModel):
    ticker: str
    price_date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    adjusted_close_price: Decimal | None = None
    volume: int = Field(default=0, ge=0)
    instrument_type: InstrumentType = InstrumentType.STOCK
    provenance: dict[str, str] = Field(default_factory=dict)


class HistoricalMarketDataLoader(Protocol):
    def price_on_or_before(self, ticker: str, decision_date: date) -> PriceBar | None:
        """Return the latest point-in-time-safe price up to decision_date."""


class InMemoryMarketDataLoader:
    """Small fixture loader for unit tests and no-cost local development."""

    def __init__(self, price_bars: list[PriceBar]) -> None:
        self._bars = sorted(price_bars, key=lambda bar: (bar.ticker, bar.price_date))

    def price_on_or_before(self, ticker: str, decision_date: date) -> PriceBar | None:
        normalized = ticker.upper()
        candidates = [
            bar
            for bar in self._bars
            if bar.ticker.upper() == normalized and bar.price_date <= decision_date
        ]
        return candidates[-1] if candidates else None

