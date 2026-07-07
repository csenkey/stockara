from datetime import date
from decimal import Decimal

from backend.src.backtesting.config import BacktestConfig
from backend.src.backtesting.data_loader import InMemoryMarketDataLoader, PriceBar
from backend.src.backtesting.models import BacktestPortfolio, TradeAction
from backend.src.backtesting.reporting import run_artifact_key, run_manifest
from backend.src.backtesting.shadows import fork_decision_shadow


def test_decision_shadow_forks_pre_trade_state_without_recursive_shadows():
    portfolio = BacktestPortfolio(
        portfolio_id="p1",
        portfolio_policy_id="balanced",
        initial_allocation_method="fixture",
        cash=Decimal("1000.00"),
    )

    shadow_portfolio, shadow = fork_decision_shadow(
        portfolio,
        shadow_id="s1",
        forked_at=date(2022, 2, 1),
        triggering_transaction_id="txn_1",
        ignored_action=TradeAction.BUY,
    )

    assert shadow.parent_portfolio_id == "p1"
    assert shadow.shadow_portfolio_id == shadow_portfolio.portfolio_id
    assert shadow.recursive_shadows_enabled is False
    assert shadow_portfolio.cash == portfolio.cash
    assert shadow_portfolio.transactions == []


def test_run_manifest_uses_s3_artifact_layout_and_labels_ai_disabled():
    config = BacktestConfig(run_id="bt_fixture")

    assert run_artifact_key(config, "metrics.json") == "backtests/runs/bt_fixture/metrics.json"
    manifest = run_manifest(config)

    assert manifest["artifacts"]["transactions"] == "backtests/runs/bt_fixture/transactions.csv"
    assert manifest["limitations"]["live_ai_replay"] == "disabled"
    assert manifest["limitations"]["evidence_mode"] == "reduced_evidence"


def test_in_memory_loader_excludes_future_prices():
    loader = InMemoryMarketDataLoader(
        [
            PriceBar(
                ticker="AAPL",
                price_date=date(2022, 1, 3),
                open_price=Decimal("100"),
                high_price=Decimal("101"),
                low_price=Decimal("99"),
                close_price=Decimal("100"),
            ),
            PriceBar(
                ticker="AAPL",
                price_date=date(2022, 1, 5),
                open_price=Decimal("120"),
                high_price=Decimal("121"),
                low_price=Decimal("119"),
                close_price=Decimal("120"),
            ),
        ]
    )

    assert loader.price_on_or_before("AAPL", date(2022, 1, 4)).close_price == Decimal("100")
