"""Decision shadow helpers."""

from datetime import date

from backend.src.backtesting.models import BacktestPortfolio, DecisionShadow, TradeAction


def fork_decision_shadow(
    portfolio: BacktestPortfolio,
    *,
    shadow_id: str,
    forked_at: date,
    triggering_transaction_id: str | None = None,
    ignored_action: TradeAction | None = None,
    evaluation_windows_days: list[int] | None = None,
) -> tuple[BacktestPortfolio, DecisionShadow]:
    shadow_portfolio_id = f"{portfolio.portfolio_id}__shadow__{shadow_id}"
    shadow_portfolio = portfolio.clone_for_shadow(shadow_portfolio_id)
    shadow = DecisionShadow(
        shadow_id=shadow_id,
        parent_portfolio_id=portfolio.portfolio_id,
        shadow_portfolio_id=shadow_portfolio_id,
        forked_at=forked_at,
        triggering_transaction_id=triggering_transaction_id,
        ignored_action=ignored_action,
        evaluation_windows_days=evaluation_windows_days or [7, 30, 90, 180, 365],
        recursive_shadows_enabled=False,
    )
    return shadow_portfolio, shadow

