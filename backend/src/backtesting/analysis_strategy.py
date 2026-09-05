"""AnalysisStrategy manifest models and loaders."""

import math
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AnalysisStrategyHeader(BaseModel):
    id: str
    status: str
    parent: str | None = None
    git_commit: str
    created_at: date
    description: str
    owner: str | None = None

    @field_validator("id")
    @classmethod
    def _stable_id(cls, value: str) -> str:
        if not value.startswith("analysis_strategy_"):
            raise ValueError("analysis strategy id must start with analysis_strategy_")
        return value


class PreselectionConfig(BaseModel):
    flow_version: str
    predicates: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    scoring: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, int] = Field(default_factory=dict)


class EvidenceConfig(BaseModel):
    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    used_data_sources: dict[str, list[str]] = Field(default_factory=dict)
    excluded_data_sources: list[str] = Field(default_factory=list)
    missing_evidence_behavior: str


class AIConfig(BaseModel):
    enabled: bool = True
    model: str | None = None
    prompt_template: str | None = None
    prompt_inputs: list[str] = Field(default_factory=list)
    output_schema: str | None = None
    review_gate: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _enabled_requires_model(self) -> "AIConfig":
        """Keep an enabled AI stage from silently running without a real model.

        A disabled stage may omit both fields; a statistical strategy should not
        have to invent a model identifier that looks configured but is not.
        """
        if not self.enabled:
            return self
        if not self.model:
            raise ValueError("an enabled AI stage requires a model identifier")
        if not self.prompt_template:
            raise ValueError("an enabled AI stage requires a prompt template")
        return self


class EarningsPredictionScope(BaseModel):
    """Which upcoming earnings events a prediction strategy is allowed to score."""

    universe: str
    horizon_days: int = Field(..., gt=0, le=30)
    minimum_date_confidence: Literal["high", "medium", "low"]
    exclude_conflicting_dates: bool = True
    min_average_dollar_volume: float | None = Field(default=None, ge=0)


class EarningsPredictionTargets(BaseModel):
    """The separately modelled result-surprise and price-reaction targets."""

    surprise_targets: list[str] = Field(..., min_length=1)
    reaction_windows: list[
        Literal["[-5,-1]", "[-1,+1]", "[0,+1]", "[+1,+5]", "[+1,+20]"]
    ] = Field(..., min_length=1)
    reaction_basis: Literal["raw", "broad_market_adjusted", "sector_adjusted"]


class EarningsPredictionEvaluation(BaseModel):
    """Leakage-safe evaluation protocol this strategy must be scored under."""

    protocol: Literal["walk_forward", "expanding_window"]
    min_training_events: int = Field(..., ge=0)
    min_evaluation_events: int = Field(..., ge=0)
    min_market_regimes: int = Field(default=1, ge=1)
    required_metrics: list[str] = Field(..., min_length=1)


class EarningsPredictionCosts(BaseModel):
    """Round-trip cost assumptions applied before any expected-value claim."""

    commission_percent: float = Field(..., ge=0)
    spread_percent: float = Field(..., ge=0)
    slippage_percent: float = Field(..., ge=0)
    borrow_cost_percent: float | None = Field(default=None, ge=0)


class EarningsPromotionGates(BaseModel):
    """Thresholds a shadow run must clear before promotion is even considered."""

    status: Literal["proposed", "ratified"] = "proposed"
    min_scored_events: int = Field(..., ge=0)
    min_market_regimes: int = Field(default=1, ge=1)
    max_brier_score: float | None = Field(default=None, ge=0, le=1)
    min_directional_precision: float | None = Field(default=None, ge=0, le=1)
    min_net_expected_return_percent: float | None = None
    max_drawdown_percent: float | None = Field(default=None, ge=0)


class EarningsEventPredictionConfig(BaseModel):
    """Earnings-event prediction section of an analysis strategy manifest."""

    feature_schema_version: str
    shadow_mode: bool = True
    influences_production: bool = False
    scope: EarningsPredictionScope
    targets: EarningsPredictionTargets
    evaluation: EarningsPredictionEvaluation
    costs: EarningsPredictionCosts
    promotion_gates: EarningsPromotionGates

    @model_validator(mode="after")
    def _shadow_cannot_influence_production(self) -> "EarningsEventPredictionConfig":
        if self.shadow_mode and self.influences_production:
            raise ValueError(
                "a shadow-mode earnings strategy must not influence production consumers"
            )
        return self


class AnalysisStrategyManifest(BaseModel):
    analysis_strategy: AnalysisStrategyHeader
    preselection: PreselectionConfig
    evidence: EvidenceConfig
    recommendation_ai: AIConfig
    review_ai: AIConfig
    publication: dict[str, Any] = Field(default_factory=dict)
    fallbacks: dict[str, Any] = Field(default_factory=dict)
    cost_limits: dict[str, Any] = Field(default_factory=dict)
    earnings_event_prediction: EarningsEventPredictionConfig | None = None

    @property
    def strategy_id(self) -> str:
        return self.analysis_strategy.id

    @model_validator(mode="after")
    def _requires_core_evidence(self) -> "AnalysisStrategyManifest":
        if "ohlcv_30d" not in self.evidence.required:
            raise ValueError("analysis strategies must require ohlcv_30d")
        return self

    @model_validator(mode="after")
    def _unpromoted_strategies_stay_out_of_production(
        self,
    ) -> "AnalysisStrategyManifest":
        """Only a promoted strategy may declare production influence.

        Earnings-event predictions must stay research-only until a recorded
        promotion decision, so they cannot reach top picks, holding reviews,
        demo trading, or other automated consumers by manifest alone.
        """
        prediction = self.earnings_event_prediction
        if prediction is None or not prediction.influences_production:
            return self
        if self.analysis_strategy.status != "promoted":
            raise ValueError(
                "only a promoted analysis strategy may influence production consumers"
            )
        return self


def load_analysis_strategy_manifest(path: str | Path) -> AnalysisStrategyManifest:
    """Load a manifest from JSON or Stockara's limited YAML subset."""
    manifest_path = Path(path)
    text = manifest_path.read_text(encoding="utf-8")
    if manifest_path.suffix.lower() == ".json":
        return AnalysisStrategyManifest.model_validate_json(text)
    if manifest_path.suffix.lower() in {".yaml", ".yml"}:
        return AnalysisStrategyManifest.model_validate(_parse_simple_yaml(text))
    raise ValueError(f"Unsupported manifest type: {manifest_path.suffix}")


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "[]":
        return []
    if value == "{}":
        return {}
    if value in {"null", "~"}:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    number = _parse_number(value)
    if number is not None:
        return number
    return value.strip('"').strip("'")


def _parse_number(value: str) -> int | float | None:
    """Return the numeric value of an unquoted scalar, or None when it is text.

    Quoted scalars stay text. Without this, thresholds such as `0.35` or `-2`
    would load as strings and compare incorrectly against numeric results.
    """
    if not value or value[0] in {'"', "'"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        parsed = float(value)
    except ValueError:
        return None
    # Keep nan/inf as text so a manifest cannot carry a threshold that silently
    # compares false against every measured value.
    return parsed if math.isfinite(parsed) else None


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the simple YAML shape used by strategy manifests.

    This avoids adding a runtime dependency before backtesting needs richer YAML
    features. It supports nested mappings and scalar lists.
    """
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any] | list[Any]]] = [(-1, root)]
    pending_key: dict[int, str] = {}

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if line.startswith("- "):
            item = _parse_scalar(line[2:])
            if not isinstance(parent, list):
                raise ValueError("YAML list item without list parent")
            parent.append(item)
            continue

        key, separator, remainder = line.partition(":")
        if not separator:
            raise ValueError(f"Invalid YAML line: {raw_line}")
        key = key.strip()
        remainder = remainder.strip()

        if remainder:
            if not isinstance(parent, dict):
                raise ValueError("YAML mapping under list is unsupported")
            parent[key] = _parse_scalar(remainder)
            continue

        if not isinstance(parent, dict):
            raise ValueError("YAML nested mapping under list is unsupported")
        container: dict[str, Any] | list[Any]
        pending_key[indent] = key
        next_lines = text.splitlines()
        # Infer list containers when the following significant line at a
        # deeper indent starts with "- ".
        current_index = next_lines.index(raw_line)
        container = {}
        for candidate in next_lines[current_index + 1 :]:
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            candidate_indent = len(candidate) - len(candidate.lstrip(" "))
            if candidate_indent <= indent:
                break
            if candidate.strip().startswith("- "):
                container = []
            break
        parent[key] = container
        stack.append((indent, container))

    return root
