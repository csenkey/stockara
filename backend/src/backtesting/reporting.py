"""Backtest artifact path and summary helpers."""

from typing import Any

from backend.src.backtesting.config import BacktestConfig


def run_artifact_key(config: BacktestConfig, artifact_name: str) -> str:
    artifact = artifact_name.strip().lstrip("/")
    if not artifact:
        raise ValueError("artifact_name must not be empty")
    return f"{config.s3_prefix}/runs/{config.run_id}/{artifact}"


def run_manifest(config: BacktestConfig) -> dict[str, Any]:
    return {
        "run_id": config.run_id,
        "config": config.model_dump(mode="json"),
        "artifacts": {
            "config": run_artifact_key(config, "config.json"),
            "portfolios": run_artifact_key(config, "portfolios.json"),
            "transactions": run_artifact_key(config, "transactions.csv"),
            "snapshots": run_artifact_key(config, "snapshots.csv"),
            "shadows": run_artifact_key(config, "shadows.csv"),
            "metrics": run_artifact_key(config, "metrics.json"),
            "limitations": run_artifact_key(config, "limitations.json"),
        },
        "limitations": {
            "evidence_mode": config.evidence_mode,
            "live_ai_replay": "disabled",
            "recommendation_cache_policy": config.recommendation_cache_policy,
        },
    }

