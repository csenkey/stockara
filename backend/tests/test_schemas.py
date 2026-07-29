"""Tests for Phase 1 Pydantic models."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from src.models.schemas import (
    CandidateAnalysis,
    CandidateSignal,
    CollectionCoverageGate,
    CollectionManifest,
    CollectionManifestSummary,
    CollectionOutputCounts,
    CollectionProviderAttempt,
    CollectionTask,
    CollectionTaskStatus,
    CollectionTaskType,
    CollectionTickerHealth,
    CompanySize,
    Recommendation,
    RepairMode,
    RepairModeRequest,
    RiskLevel,
    SignalDirection,
    SignalSource,
    SignalType,
    Stock,
    StockData,
    collection_manifest_s3_key,
)


def test_stock_validates_sector_and_ticker():
    stock = Stock(
        ticker="aapl",
        company_name="Apple Inc.",
        sector="Technology",
        company_size=CompanySize.BLUE_CHIP,
        provider_symbols={"stooq": "aapl.us", "alpha_vantage": "AAPL"},
    )
    assert stock.ticker == "AAPL"
    assert stock.provider_symbols["stooq"] == "aapl.us"


def test_stock_accepts_static_business_metadata():
    stock = Stock(
        ticker="AAPL",
        company_name="Apple Inc.",
        sector="Technology",
        industry="Consumer Electronics",
        company_size=CompanySize.BLUE_CHIP,
        metadata_source="company_profile",
        metadata_source_url="https://www.apple.com/investor-relations/",
        metadata_as_of=date(2026, 6, 17),
        business_description="Designs and sells consumer electronics and services.",
        flagship_products=["iPhone", "Mac", "Services"],
        revenue_segments=["Products", "Services"],
        primary_customers=["Consumers", "Businesses"],
        geographic_exposure=["Americas", "Europe", "Greater China"],
        competitive_position="Global premium consumer technology ecosystem.",
        key_static_risks=["Supply chain concentration", "Regulatory pressure"],
        exchange="NASDAQ",
        currency="USD",
        country="United States",
        website="https://www.apple.com",
        founded_year=1976,
        headquarters="Cupertino, California",
        logo_url="https://cdn.example.com/logos/AAPL/logo.svg",
        logo_icon_url="https://cdn.example.com/logos/AAPL/icon.png",
        logo_source="polygon_ticker_details",
        logo_source_url="https://api.polygon.io/v3/reference/tickers/AAPL",
        logo_checked_at=datetime(2026, 7, 6, 8, 0, 0),
    )
    assert stock.industry == "Consumer Electronics"
    assert stock.flagship_products == ["iPhone", "Mac", "Services"]
    assert stock.logo_icon_url == "https://cdn.example.com/logos/AAPL/icon.png"


def test_stock_rejects_invalid_sector():
    with pytest.raises(ValueError, match="Sector must be one of"):
        Stock(
            ticker="AAPL",
            company_name="Apple Inc.",
            sector="Invalid",
            company_size=CompanySize.BLUE_CHIP,
        )


def test_stock_data_accepts_ohlcv_record():
    record = StockData(
        ticker="MSFT",
        trading_date=date(2026, 6, 15),
        open_price=Decimal("420.10"),
        high_price=Decimal("425.20"),
        low_price=Decimal("419.00"),
        close_price=Decimal("424.55"),
        volume=1000,
        data_provider="yfinance",
        provider_priority="primary",
        price_adjustment="unadjusted",
        adjustment_context="raw_ohlcv_with_adjusted_close",
        split_dividend_adjustment="adjusted_close_available",
        exchange="NASDAQ",
        currency="USD",
        fetch_period="5y",
        fetch_window_start=date(2021, 6, 15),
        fetch_window_end=date(2026, 6, 15),
    )
    assert record.ticker == "MSFT"
    assert record.data_provider == "yfinance"
    assert record.fetch_window_start == date(2021, 6, 15)


def test_candidate_signal_bounds_score():
    with pytest.raises(ValueError):
        CandidateSignal(
            ticker="NVDA",
            signal_type=SignalType.PRICE_MOVE,
            direction=SignalDirection.POSITIVE,
            score=101,
            title="Too high",
            summary="Invalid signal score",
            source=SignalSource(provider="test", observed_at=datetime.utcnow()),
        )


def test_candidate_analysis_models_shortlisted_ai_result():
    analysis = CandidateAnalysis(
        ticker="NVDA",
        analysis_date=date(2026, 6, 15),
        recommendation=Recommendation.BUY,
        risk_level=RiskLevel.MEDIUM,
        confidence_score=82,
        catalyst="Unusual volume",
        expected_timeframe="1-30 days",
        reasoning="Momentum is strong after a catalyst cluster.",
        invalidation_criteria="Volume fades and price loses support.",
        opportunity_score=77,
        negative_score=10,
    )
    assert analysis.recommendation == Recommendation.BUY


def test_collection_manifest_s3_key_uses_daily_partition():
    assert (
        collection_manifest_s3_key(date(2026, 6, 20))
        == "collection_manifest/2026-06-20.json"
    )


def test_repair_mode_request_normalizes_shared_operator_payload():
    request = RepairModeRequest(
        mode=RepairMode.REPAIR_NEWS,
        run_date="2026-07-29",
        tickers=["aapl", "msft"],
        max_tickers=25,
        provider_budget={"NewsAPI": 20, "FINNHUB": "5"},
        dry_run=True,
    )

    assert request.mode == RepairMode.REPAIR_NEWS
    assert request.run_date == date(2026, 7, 29)
    assert request.tickers == ["AAPL", "MSFT"]
    assert request.provider_budget == {"newsapi": 20, "finnhub": 5}
    assert request.dry_run is True


def test_repair_mode_request_rejects_invalid_budget_and_ticker():
    with pytest.raises(ValueError, match="Ticker must contain only"):
        RepairModeRequest(mode="repair_news", tickers=["BAD!"])

    with pytest.raises(ValueError, match="Provider budget values must be non-negative"):
        RepairModeRequest(
            mode="repair_news",
            provider_budget={"newsapi": -1},
        )


def test_collection_task_normalizes_tickers_and_tracks_attempt_output():
    now = datetime(2026, 6, 20, 8, 0, 0)

    task = CollectionTask(
        task_id="price-AAPL-MSFT",
        task_type=CollectionTaskType.PRICE,
        status=CollectionTaskStatus.RETRY_WAIT,
        tickers=["aapl", "msft"],
        ticker_range_start="aapl",
        ticker_range_end="msft",
        provider="alpha_vantage",
        provider_attempts=[
            CollectionProviderAttempt(
                provider="yfinance",
                provider_symbol="AAPL",
                status=CollectionTaskStatus.FAILED,
                health=CollectionTickerHealth.RATE_LIMITED,
                attempted_at=now,
                completed_at=now,
                failure_reason="rate_limited",
            )
        ],
        ticker_health={"aapl": CollectionTickerHealth.RATE_LIMITED},
        attempts=1,
        max_attempts=3,
        next_retry_at=datetime(2026, 6, 20, 9, 0, 0),
        created_at=now,
        updated_at=now,
        failure_reason="rate_limited",
        output_counts=CollectionOutputCounts(
            records_fetched=2,
            records_written=1,
            duplicate_records=1,
            successful_tickers=1,
            failed_tickers=1,
        ),
    )

    assert task.tickers == ["AAPL", "MSFT"]
    assert task.ticker_range_start == "AAPL"
    assert task.output_counts.records_written == 1
    assert task.ticker_health["aapl"] == CollectionTickerHealth.RATE_LIMITED
    assert task.provider_attempts[0].health == CollectionTickerHealth.RATE_LIMITED
    assert task.provider_attempts[0].failure_reason == "rate_limited"


def test_collection_manifest_serializes_json_contract():
    generated_at = datetime(2026, 6, 20, 7, 30, 0)
    manifest = CollectionManifest(
        manifest_date=date(2026, 6, 20),
        generated_at=generated_at,
        updated_at=generated_at,
        analysis_not_before=datetime(2026, 6, 20, 22, 0, 0),
        active_ticker_count=1000,
        task_types=[
            CollectionTaskType.PRICE,
            CollectionTaskType.NEWS,
            CollectionTaskType.EARNINGS,
            CollectionTaskType.DIVIDEND,
        ],
        tasks=[
            CollectionTask(
                task_id="news-general-0001",
                task_type=CollectionTaskType.NEWS,
                created_at=generated_at,
                updated_at=generated_at,
            )
        ],
        summary=CollectionManifestSummary(
            total_tasks=1,
            pending_tasks=1,
            total_tickers=1000,
            successful_tickers=0,
            coverage_ratio=Decimal("0"),
            coverage_gates=[
                CollectionCoverageGate(
                    name="price_freshness",
                    passed=False,
                    observed_value=Decimal("0.0"),
                    required_value=Decimal("0.9"),
                    unit="ratio",
                    message="At least 90% of active tickers need fresh prices.",
                )
            ],
        ),
    )

    payload = manifest.model_dump(mode="json")

    assert manifest.s3_key == "collection_manifest/2026-06-20.json"
    assert payload["manifest_date"] == "2026-06-20"
    assert payload["task_types"] == ["price", "news", "earnings", "dividend"]
    assert payload["tasks"][0]["status"] == "pending"
    assert payload["summary"]["coverage_gates"][0]["required_value"] == "0.9"


def test_collection_manifest_rejects_invalid_ticker():
    with pytest.raises(ValueError, match="Ticker must contain only"):
        CollectionTask(
            task_id="bad",
            task_type=CollectionTaskType.PRICE,
            tickers=["AAPL!"],
            created_at=datetime(2026, 6, 20, 7, 30, 0),
            updated_at=datetime(2026, 6, 20, 7, 30, 0),
        )
