"""Lambda entry point for the Phase 1 daily top-picks pipeline."""

from typing import Any

from src.analysis.phase1_pipeline import run_phase1_pipeline


def handler(event: dict, context: Any) -> dict:
    return run_phase1_pipeline(event)
