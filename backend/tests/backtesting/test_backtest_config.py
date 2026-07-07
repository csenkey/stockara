from datetime import date
from decimal import Decimal

import pytest

from backend.src.backtesting.config import (
    BacktestConfig,
    EvidenceMode,
    RecommendationCachePolicy,
)


def test_backtest_config_defaults_are_cost_controlled_and_offline():
    config = BacktestConfig(run_id="bt_test")

    assert config.start_date == date(2022, 1, 1)
    assert config.end_date == date(2022, 12, 31)
    assert config.portfolio_count == 20
    assert config.initial_capital == Decimal("10000.00")
    assert config.commission_rate == Decimal("0.01")
    assert config.recommendation_cache_policy == RecommendationCachePolicy.FIXTURE_ONLY
    assert config.evidence_mode == EvidenceMode.REDUCED_EVIDENCE


def test_backtest_config_rejects_invalid_date_range():
    with pytest.raises(ValueError, match="end_date"):
        BacktestConfig(start_date=date(2023, 1, 1), end_date=date(2022, 1, 1))


def test_backtest_config_normalizes_s3_prefix_and_windows():
    config = BacktestConfig(run_id="bt_test", s3_prefix="/backtests/", shadow_windows_days=[30, 7, 7])

    assert config.s3_prefix == "backtests"
    assert config.shadow_windows_days == [7, 30]
