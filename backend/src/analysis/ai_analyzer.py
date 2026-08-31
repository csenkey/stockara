"""Lambda entry point for the Phase 1 daily top-picks pipeline."""

from typing import Any

from src.analysis.holding_review import run_holding_review
from src.analysis.phase1_pipeline import run_phase1_pipeline


def handler(event: dict, context: Any) -> dict:
    if str((event or {}).get("mode")) == "holding_review":
        return run_holding_review(event)
    return run_phase1_pipeline(event)
