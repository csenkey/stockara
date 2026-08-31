"""Score-independent, on-demand review of capital already held in one ticker."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

import structlog
from openai import OpenAI
from pydantic import BaseModel, Field, field_validator

from src.db.connection import DatabasePool, DynamoStore, store
from src.models.schemas import Recommendation, RiskLevel, validate_ticker
from src.services.secrets import get_openai_api_key

logger = structlog.get_logger(__name__)

ANALYSIS_MODEL = os.environ.get(
    "OPENAI_ANALYSIS_MODEL", os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
)
REVIEW_MODEL = os.environ.get("OPENAI_REVIEW_MODEL", "gpt-5.6-terra")
MIN_HISTORY_ROWS = int(os.environ.get("HOLDING_REVIEW_MIN_HISTORY_ROWS", "20"))
MIN_HISTORY_DAYS = int(os.environ.get("HOLDING_REVIEW_MIN_HISTORY_DAYS", "30"))
MAX_PRICE_AGE_DAYS = int(os.environ.get("HOLDING_REVIEW_MAX_PRICE_AGE_DAYS", "3"))
PROMPT_VERSION = "holding_review_v1"
OUTPUT_SCHEMA_VERSION = "1.0"


class PortfolioObjective(str, Enum):
    INCOME = "income"
    BALANCED = "balanced"
    GROWTH = "growth"


class HoldingAction(str, Enum):
    KEEP = "KEEP"
    KEEP_INCOME = "KEEP_INCOME"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    REVIEW = "REVIEW"


class HoldingReviewStatus(str, Enum):
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    COMPLETED_DEGRADED = "COMPLETED_DEGRADED"
    COMPLETED = "COMPLETED"


class HoldingReviewRequest(BaseModel):
    ticker: str
    quantity: int = Field(..., gt=0)
    buying_price: Decimal = Field(..., gt=0)
    portfolio_total_value: Decimal | None = Field(default=None, gt=0)
    objective: PortfolioObjective = PortfolioObjective.BALANCED
    as_of: date = Field(default_factory=date.today)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return validate_ticker(value)


class HoldingReviewDecision(BaseModel):
    schema_version: Literal["1.0"] = OUTPUT_SCHEMA_VERSION
    security_recommendation: Recommendation
    risk_level: RiskLevel
    confidence_score: int = Field(..., ge=0, le=100)
    security_thesis: str = Field(..., min_length=1, max_length=1200)
    invalidation_criteria: str = Field(..., min_length=1, max_length=750)
    portfolio_action: HoldingAction
    holding_role: str = Field(..., min_length=1, max_length=120)
    capital_efficiency: Literal["STRONG", "ADEQUATE", "WEAK", "UNKNOWN"]
    opportunity_cost_assessment: str = Field(..., min_length=1, max_length=1000)
    dividend_sustainability: Literal["STRONG", "ADEQUATE", "WEAK", "UNKNOWN"]
    reasoning: str = Field(..., min_length=1, max_length=1500)
    next_review_trigger: str = Field(..., min_length=1, max_length=500)


class HoldingActionReview(BaseModel):
    schema_version: Literal["1.0"] = OUTPUT_SCHEMA_VERSION
    approved: bool
    rationale: str = Field(..., min_length=1, max_length=750)
    concerns: list[str] = Field(default_factory=list, max_length=8)


class HoldingEvidenceSnapshot(BaseModel):
    ticker: str
    company_name: str
    sector: str
    objective: PortfolioObjective
    evidence_as_of: date
    evidence_hash: str
    metrics: dict[str, Any]
    recent_prices: list[dict[str, Any]]
    market_signals: list[dict[str, Any]]
    recent_news: list[dict[str, Any]]
    earnings_events: list[dict[str, Any]]
    dividend_events: list[dict[str, Any]]
    missing_optional_evidence: list[str]


class HoldingReviewResult(BaseModel):
    status: HoldingReviewStatus
    ticker: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    evidence_as_of: date | None = None
    evidence_hash: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    missing_optional_evidence: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    error: str | None = None
    analysis_model: str | None = None
    review_model: str | None = None
    analysis: HoldingReviewDecision | None = None
    proposed_action: HoldingAction | None = None
    action_review: HoldingActionReview | None = None


class EvidenceBlocked(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__(", ".join(reasons))
        self.reasons = reasons


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _percent(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    return (numerator / denominator * Decimal("100")).quantize(Decimal("0.01"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


class HoldingEvidenceBuilder:
    """Build neutral evidence for one holding without candidate scoring."""

    def __init__(self, repository: DynamoStore):
        self.repository = repository

    def build(self, request: HoldingReviewRequest) -> HoldingEvidenceSnapshot:
        stock = self.repository.get_stock(request.ticker)
        if not stock or not bool(stock.get("is_active", True)):
            raise EvidenceBlocked(["ticker_not_active_in_watchlist"])

        rows = self.repository.get_stock_data(
            request.ticker,
            request.as_of - timedelta(days=75),
            request.as_of,
        )
        reasons = self._price_readiness_reasons(rows, request.as_of)
        if reasons:
            raise EvidenceBlocked(reasons)

        latest = rows[-1]
        first = rows[0]
        current_price = _decimal(latest["close_price"])
        first_price = _decimal(first["close_price"])
        closes = [_decimal(row["close_price"]) for row in rows]
        recent_20_closes = closes[-20:]
        sma_20 = sum(recent_20_closes, Decimal("0")) / len(recent_20_closes)
        recent_20_volumes = [
            Decimal(str(row.get("volume") or 0)) for row in rows[-20:]
        ]
        average_volume_20 = sum(recent_20_volumes, Decimal("0")) / len(
            recent_20_volumes
        )
        cost_basis = request.buying_price * request.quantity
        current_value = current_price * request.quantity
        unrealized = current_value - cost_basis

        signals = self.repository.market_signals_for_ticker(
            request.ticker, request.as_of - timedelta(days=30), request.as_of
        )
        news = self.repository.news_for_ticker(
            request.ticker, request.as_of - timedelta(days=7), request.as_of
        )
        earnings = self.repository.earnings_events_for_ticker(
            request.ticker,
            request.as_of - timedelta(days=90),
            request.as_of + timedelta(days=45),
        )
        dividends = self.repository.dividend_events_for_ticker(
            request.ticker,
            request.as_of - timedelta(days=365),
            request.as_of + timedelta(days=60),
        )

        trailing_dividends = [
            row
            for row in dividends
            if date.fromisoformat(str(row["ex_dividend_date"])[:10]) <= request.as_of
            and row.get("dividend_amount") is not None
        ]
        trailing_dividend_per_share = sum(
            (_decimal(row["dividend_amount"]) for row in trailing_dividends),
            Decimal("0"),
        )
        metrics: dict[str, Any] = {
            "quantity": request.quantity,
            "buying_price": request.buying_price,
            "current_price": current_price,
            "cost_basis": cost_basis,
            "current_value": current_value,
            "unrealized_gain_loss": unrealized,
            "unrealized_return_percent": _percent(unrealized, cost_basis),
            "observed_price_return_percent": _percent(current_price - first_price, first_price),
            "price_return_5_sessions_percent": _percent(
                current_price - closes[-6], closes[-6]
            ),
            "price_return_20_sessions_percent": _percent(
                current_price - closes[-20], closes[-20]
            ),
            "simple_moving_average_20": sma_20.quantize(Decimal("0.0001")),
            "price_vs_sma_20_percent": _percent(current_price - sma_20, sma_20),
            "latest_volume_vs_average_20": (
                (Decimal(str(latest.get("volume") or 0)) / average_volume_20).quantize(
                    Decimal("0.0001")
                )
                if average_volume_20 > 0
                else None
            ),
            "observed_price_start_date": str(first["trading_date"])[:10],
            "observed_price_end_date": str(latest["trading_date"])[:10],
            "trailing_dividend_per_share": trailing_dividend_per_share,
            "estimated_trailing_dividend_income": trailing_dividend_per_share * request.quantity,
            "estimated_current_dividend_yield_percent": _percent(
                trailing_dividend_per_share, current_price
            ),
            "estimated_yield_on_cost_percent": _percent(
                trailing_dividend_per_share, request.buying_price
            ),
            "position_weight_percent": (
                _percent(current_value, request.portfolio_total_value)
                if request.portfolio_total_value is not None
                else None
            ),
        }

        missing_optional: list[str] = []
        if not news:
            missing_optional.append("recent_news")
        if not earnings:
            missing_optional.append("earnings_context")
        if not dividends:
            missing_optional.append("dividend_context")
        if not signals:
            missing_optional.append("stored_market_signals")
        if request.portfolio_total_value is None:
            missing_optional.append("portfolio_total_value")
        # Replacement comparisons are intentionally a later production slice.
        missing_optional.append("replacement_comparison_set")

        recent_prices = [
            {
                "trading_date": str(row["trading_date"])[:10],
                "close_price": row["close_price"],
                "volume": row.get("volume"),
                "provider": row.get("data_provider"),
            }
            for row in rows[-30:]
        ]
        snapshot_material = {
            "ticker": request.ticker,
            "company_name": stock.get("company_name") or request.ticker,
            "sector": stock.get("sector") or "Unknown",
            "objective": request.objective.value,
            "evidence_as_of": str(latest["trading_date"])[:10],
            "metrics": metrics,
            "recent_prices": recent_prices,
            "market_signals": signals[-20:],
            "recent_news": news[:10],
            "earnings_events": earnings,
            "dividend_events": dividends,
            "missing_optional_evidence": sorted(missing_optional),
        }
        canonical = json.dumps(
            _jsonable(snapshot_material), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return HoldingEvidenceSnapshot(
            **snapshot_material,
            evidence_hash=hashlib.sha256(canonical).hexdigest(),
        )

    @staticmethod
    def _price_readiness_reasons(rows: list[dict[str, Any]], as_of: date) -> list[str]:
        if not rows:
            return ["missing_stock_history"]
        reasons: list[str] = []
        dates = [date.fromisoformat(str(row["trading_date"])[:10]) for row in rows]
        if len(rows) < MIN_HISTORY_ROWS:
            reasons.append("insufficient_stock_history_rows")
        if (dates[-1] - dates[0]).days < MIN_HISTORY_DAYS:
            reasons.append("insufficient_stock_history_span")
        if (as_of - dates[-1]).days > MAX_PRICE_AGE_DAYS:
            reasons.append("stale_stock_data")
        if _decimal(rows[-1].get("close_price", 0)) <= 0:
            reasons.append("invalid_latest_close_price")
        return reasons


class HoldingReviewEngine:
    def __init__(self, repository: DynamoStore, client: OpenAI | None):
        self.repository = repository
        self.client = client

    def review(self, request: HoldingReviewRequest) -> HoldingReviewResult:
        try:
            snapshot = HoldingEvidenceBuilder(self.repository).build(request)
        except EvidenceBlocked as exc:
            return HoldingReviewResult(
                status=HoldingReviewStatus.BLOCKED,
                ticker=request.ticker,
                blocked_reasons=exc.reasons,
            )

        if self.client is None:
            return self._failed(snapshot, "OpenAI analysis client is unavailable")

        try:
            decision = self._analyze(snapshot)
        except Exception as exc:
            logger.warning(
                "holding_review_ai_failed",
                ticker=request.ticker,
                error_type=type(exc).__name__,
            )
            return self._failed(
                snapshot,
                "AI holding analysis failed or returned an invalid response",
            )

        proposed_action = decision.portfolio_action
        action_review: HoldingActionReview | None = None
        if proposed_action in {HoldingAction.REDUCE, HoldingAction.EXIT}:
            action_review = self._review_action(snapshot, decision)
            if not action_review.approved:
                decision = decision.model_copy(
                    update={"portfolio_action": HoldingAction.REVIEW}
                )

        status = (
            HoldingReviewStatus.COMPLETED_DEGRADED
            if snapshot.missing_optional_evidence
            else HoldingReviewStatus.COMPLETED
        )
        return HoldingReviewResult(
            status=status,
            ticker=request.ticker,
            evidence_as_of=snapshot.evidence_as_of,
            evidence_hash=snapshot.evidence_hash,
            metrics=snapshot.metrics,
            missing_optional_evidence=snapshot.missing_optional_evidence,
            analysis_model=ANALYSIS_MODEL,
            review_model=REVIEW_MODEL if action_review else None,
            analysis=decision,
            proposed_action=proposed_action,
            action_review=action_review,
        )

    def _analyze(self, snapshot: HoldingEvidenceSnapshot) -> HoldingReviewDecision:
        response = self.client.chat.completions.create(
            model=ANALYSIS_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You review an existing portfolio holding, not a new-opportunity shortlist. "
                        "Always assess the supplied ticker from its evidence. Separate security "
                        "quality from whether this user's capital should remain allocated. A HOLD "
                        "security may still justify REDUCE or EXIT for capital efficiency. Treat "
                        "yield on cost as historical context, use current yield for allocation, "
                        "and never claim a quantified replacement advantage when comparison "
                        "evidence is missing. This is decision support, not financial advice."
                    ),
                },
                {"role": "user", "content": self._analysis_prompt(snapshot)},
            ],
            response_format=_response_format(
                "stockara_holding_review", HoldingReviewDecision
            ),
            **_completion_options(ANALYSIS_MODEL, 1200),
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        return HoldingReviewDecision.model_validate(parsed)

    def _review_action(
        self, snapshot: HoldingEvidenceSnapshot, decision: HoldingReviewDecision
    ) -> HoldingActionReview:
        try:
            response = self.client.chat.completions.create(
                model=REVIEW_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a skeptical portfolio-risk reviewer. Approve REDUCE or EXIT "
                            "only when the supplied evidence supports that action and limitations "
                            "are explicit. Reject unsupported replacement claims, tax assumptions, "
                            "or decisions driven only by purchase price."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "evidence_hash": snapshot.evidence_hash,
                                "missing_optional_evidence": snapshot.missing_optional_evidence,
                                "metrics": _jsonable(snapshot.metrics),
                                "decision": decision.model_dump(mode="json"),
                            },
                            sort_keys=True,
                        ),
                    },
                ],
                response_format=_response_format(
                    "stockara_holding_action_review", HoldingActionReview
                ),
                **_completion_options(REVIEW_MODEL, 600),
            )
            parsed = json.loads(response.choices[0].message.content or "{}")
            return HoldingActionReview.model_validate(parsed)
        except Exception as exc:
            logger.warning(
                "holding_action_review_failed",
                ticker=snapshot.ticker,
                error_type=type(exc).__name__,
            )
            return HoldingActionReview(
                approved=False,
                rationale="The stronger action review failed or returned an invalid response.",
                concerns=["action_review_unavailable"],
            )

    @staticmethod
    def _analysis_prompt(snapshot: HoldingEvidenceSnapshot) -> str:
        payload = snapshot.model_dump(mode="json", exclude={"evidence_hash"})
        return (
            f"Prompt version: {PROMPT_VERSION}. Review this held ticker. "
            "Return the strict requested JSON schema. KEEP_INCOME requires a defensible income "
            "role. REDUCE/EXIT must explain portfolio allocation rather than merely repeat a "
            "security HOLD/SELL label. Missing comparison data must be acknowledged.\n\n"
            + json.dumps(payload, sort_keys=True)
        )

    @staticmethod
    def _failed(snapshot: HoldingEvidenceSnapshot, error: str) -> HoldingReviewResult:
        return HoldingReviewResult(
            status=HoldingReviewStatus.FAILED,
            ticker=snapshot.ticker,
            evidence_as_of=snapshot.evidence_as_of,
            evidence_hash=snapshot.evidence_hash,
            metrics=snapshot.metrics,
            missing_optional_evidence=snapshot.missing_optional_evidence,
            error=error,
            analysis_model=ANALYSIS_MODEL,
        )


def _strict_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned = {
        key: _strict_schema(item)
        for key, item in value.items()
        if key != "default"
    }
    if cleaned.get("type") == "object" or "properties" in cleaned:
        cleaned["additionalProperties"] = False
        cleaned["required"] = list(cleaned.get("properties", {}).keys())
    return cleaned


def _response_format(name: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": _strict_schema(model.model_json_schema()),
        },
    }


def _completion_options(model: str, max_tokens: int) -> dict[str, Any]:
    if model.startswith("gpt-5"):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens, "temperature": 0}


def _build_client() -> OpenAI | None:
    api_key = get_openai_api_key()
    if not api_key:
        return None
    try:
        return OpenAI(api_key=api_key)
    except Exception:
        logger.warning("holding_review_openai_client_initialization_failed")
        return None


def run_holding_review(event: dict[str, Any]) -> dict[str, Any]:
    """Internal Lambda invocation; does not persist the supplied holding context."""
    DatabasePool.initialize()
    try:
        request_payload = {key: value for key, value in event.items() if key != "mode"}
        request = HoldingReviewRequest.model_validate(request_payload)
        result = HoldingReviewEngine(store, _build_client()).review(request)
        return {"statusCode": 200, "body": result.model_dump(mode="json")}
    except Exception as exc:
        logger.warning(
            "holding_review_request_invalid",
            error_type=type(exc).__name__,
        )
        return {
            "statusCode": 400,
            "body": {"status": "INVALID_REQUEST", "error": str(exc)[:500]},
        }
    finally:
        DatabasePool.close()
