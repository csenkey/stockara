"""Commission-aware portfolio accounting primitives."""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from backend.src.backtesting.models import (
    BacktestHolding,
    BacktestPortfolio,
    BacktestSnapshot,
    BacktestTransaction,
    InstrumentType,
    TradeAction,
)

MONEY_QUANT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


class PortfolioSimulator:
    """Small accounting engine used by the future historical runner."""

    def __init__(self, commission_rate: Decimal = Decimal("0.01")) -> None:
        if commission_rate < 0:
            raise ValueError("commission_rate must be non-negative")
        self.commission_rate = commission_rate

    def buy(
        self,
        portfolio: BacktestPortfolio,
        *,
        ticker: str,
        trade_date: date,
        price: Decimal,
        max_gross_value: Decimal,
        instrument_type: InstrumentType = InstrumentType.STOCK,
        analysis_strategy_id: str | None = None,
        recommendation_id: str | None = None,
        reason: str | None = None,
    ) -> BacktestTransaction | None:
        if price <= 0:
            raise ValueError("price must be positive")
        affordable_gross = min(max_gross_value, portfolio.cash / (Decimal("1") + self.commission_rate))
        quantity = int(affordable_gross // price)
        if quantity <= 0:
            return None

        gross_value = money(price * Decimal(quantity))
        commission = money(gross_value * self.commission_rate)
        total_cost = gross_value + commission
        if total_cost > portfolio.cash:
            return None

        normalized_ticker = ticker.strip().upper()
        existing = portfolio.holdings.get(normalized_ticker)
        if existing:
            total_quantity = existing.quantity + quantity
            total_cost_basis = existing.average_cost * Decimal(existing.quantity) + gross_value
            existing.quantity = total_quantity
            existing.average_cost = money(total_cost_basis / Decimal(total_quantity))
        else:
            portfolio.holdings[normalized_ticker] = BacktestHolding(
                ticker=normalized_ticker,
                quantity=quantity,
                average_cost=price,
                opened_date=trade_date,
                instrument_type=instrument_type,
            )

        portfolio.cash = money(portfolio.cash - total_cost)
        transaction = BacktestTransaction(
            transaction_id=f"txn_{uuid4().hex}",
            portfolio_id=portfolio.portfolio_id,
            trade_date=trade_date,
            ticker=normalized_ticker,
            action=TradeAction.BUY,
            quantity=quantity,
            price=price,
            gross_value=gross_value,
            commission=commission,
            cash_after=portfolio.cash,
            analysis_strategy_id=analysis_strategy_id,
            recommendation_id=recommendation_id,
            reason=reason,
        )
        portfolio.transactions.append(transaction)
        return transaction

    def sell_all(
        self,
        portfolio: BacktestPortfolio,
        *,
        ticker: str,
        trade_date: date,
        price: Decimal,
        analysis_strategy_id: str | None = None,
        recommendation_id: str | None = None,
        reason: str | None = None,
    ) -> BacktestTransaction | None:
        if price <= 0:
            raise ValueError("price must be positive")
        normalized_ticker = ticker.strip().upper()
        holding = portfolio.holdings.get(normalized_ticker)
        if holding is None or holding.quantity <= 0:
            return None

        quantity = holding.quantity
        gross_value = money(price * Decimal(quantity))
        commission = money(gross_value * self.commission_rate)
        portfolio.cash = money(portfolio.cash + gross_value - commission)
        del portfolio.holdings[normalized_ticker]

        transaction = BacktestTransaction(
            transaction_id=f"txn_{uuid4().hex}",
            portfolio_id=portfolio.portfolio_id,
            trade_date=trade_date,
            ticker=normalized_ticker,
            action=TradeAction.SELL,
            quantity=quantity,
            price=price,
            gross_value=gross_value,
            commission=commission,
            cash_after=portfolio.cash,
            analysis_strategy_id=analysis_strategy_id,
            recommendation_id=recommendation_id,
            reason=reason,
        )
        portfolio.transactions.append(transaction)
        return transaction

    def snapshot(
        self,
        portfolio: BacktestPortfolio,
        *,
        snapshot_date: date,
        prices: dict[str, Decimal],
    ) -> BacktestSnapshot:
        holdings_value = Decimal("0")
        missing_prices: list[str] = []
        unrealized = Decimal("0")
        for ticker, holding in portfolio.holdings.items():
            price = prices.get(ticker)
            if price is None:
                missing_prices.append(ticker)
                continue
            current_value = price * Decimal(holding.quantity)
            holdings_value += current_value
            unrealized += (price - holding.average_cost) * Decimal(holding.quantity)

        commission_paid = sum((txn.commission for txn in portfolio.transactions), Decimal("0"))
        snapshot = BacktestSnapshot(
            portfolio_id=portfolio.portfolio_id,
            snapshot_date=snapshot_date,
            cash=portfolio.cash,
            holdings_value=money(holdings_value),
            total_value=money(portfolio.cash + holdings_value),
            unrealized_gain_loss=money(unrealized),
            commission_paid_to_date=money(commission_paid),
            data_quality="incomplete_prices" if missing_prices else "complete",
            missing_prices=missing_prices,
        )
        portfolio.snapshots.append(snapshot)
        return snapshot

