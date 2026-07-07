"""AnalysisStrategy manifest models and loaders."""

from datetime import date
from pathlib import Path
from typing import Any

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
    model: str
    prompt_template: str
    prompt_inputs: list[str] = Field(default_factory=list)
    output_schema: str | None = None
    review_gate: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)


class AnalysisStrategyManifest(BaseModel):
    analysis_strategy: AnalysisStrategyHeader
    preselection: PreselectionConfig
    evidence: EvidenceConfig
    recommendation_ai: AIConfig
    review_ai: AIConfig
    publication: dict[str, Any] = Field(default_factory=dict)
    fallbacks: dict[str, Any] = Field(default_factory=dict)
    cost_limits: dict[str, Any] = Field(default_factory=dict)

    @property
    def strategy_id(self) -> str:
        return self.analysis_strategy.id

    @model_validator(mode="after")
    def _requires_core_evidence(self) -> "AnalysisStrategyManifest":
        if "ohlcv_30d" not in self.evidence.required:
            raise ValueError("analysis strategies must require ohlcv_30d")
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
    if value.isdigit():
        return int(value)
    return value.strip('"').strip("'")


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
