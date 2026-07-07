from datetime import date
from decimal import Decimal

from backend.src.backtesting.models import BacktestPortfolio, TradeAction
from backend.src.backtesting.simulator import PortfolioSimulator


def test_buy_uses_whole_shares_and_commission_without_negative_cash():
    portfolio = BacktestPortfolio(
        portfolio_id="p1",
        portfolio_policy_id="balanced",
        initial_allocation_method="fixture",
        cash=Decimal("1000.00"),
    )
    simulator = PortfolioSimulator(commission_rate=Decimal("0.01"))

    transaction = simulator.buy(
        portfolio,
        ticker="AAPL",
        trade_date=date(2022, 1, 3),
        price=Decimal("100.00"),
        max_gross_value=Decimal("1000.00"),
    )

    assert transaction is not None
    assert transaction.quantity == 9
    assert transaction.gross_value == Decimal("900.00")
    assert transaction.commission == Decimal("9.00")
    assert transaction.cash_after == Decimal("91.00")
    assert portfolio.cash == Decimal("91.00")
    assert portfolio.holdings["AAPL"].quantity == 9


def test_sell_all_liquidates_position_and_charges_commission():
    portfolio = BacktestPortfolio(
        portfolio_id="p1",
        portfolio_policy_id="balanced",
        initial_allocation_method="fixture",
        cash=Decimal("1000.00"),
    )
    simulator = PortfolioSimulator(commission_rate=Decimal("0.01"))
    simulator.buy(
        portfolio,
        ticker="MSFT",
        trade_date=date(2022, 1, 3),
        price=Decimal("100.00"),
        max_gross_value=Decimal("500.00"),
    )

    transaction = simulator.sell_all(
        portfolio,
        ticker="MSFT",
        trade_date=date(2022, 1, 4),
        price=Decimal("110.00"),
    )

    assert transaction is not None
    assert transaction.action == TradeAction.SELL
    assert transaction.quantity == 5
    assert transaction.gross_value == Decimal("550.00")
    assert transaction.commission == Decimal("5.50")
    assert "MSFT" not in portfolio.holdings
    assert portfolio.cash == Decimal("1039.50")


def test_snapshot_marks_missing_prices_incomplete():
    portfolio = BacktestPortfolio(
        portfolio_id="p1",
        portfolio_policy_id="balanced",
        initial_allocation_method="fixture",
        cash=Decimal("1000.00"),
    )
    simulator = PortfolioSimulator(commission_rate=Decimal("0.01"))
    simulator.buy(
        portfolio,
        ticker="NVDA",
        trade_date=date(2022, 1, 3),
        price=Decimal("100.00"),
        max_gross_value=Decimal("500.00"),
    )

    snapshot = simulator.snapshot(portfolio, snapshot_date=date(2022, 1, 4), prices={})

    assert snapshot.data_quality == "incomplete_prices"
    assert snapshot.missing_prices == ["NVDA"]
