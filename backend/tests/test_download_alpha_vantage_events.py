from argparse import Namespace
from pathlib import Path

from scripts.download_alpha_vantage_events import (
    ApiKeyRef,
    DownloadTarget,
    build_targets,
    download_targets,
    load_watchlist_tickers,
    pending_targets,
    selected_event_types,
)


class FakeResponse:
    def __init__(self, status_code: int, text: str, payload=None) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, params: dict[str, str], timeout: int) -> FakeResponse:
        self.calls.append((url, params))
        return self.responses.pop(0)


def _args(tmp_path: Path, watchlist: Path, **overrides) -> Namespace:
    values = {
        "watchlist": watchlist,
        "output_dir": tmp_path,
        "include": "earnings,dividends,listing-status",
        "tickers": None,
        "max_tickers": 0,
        "offset": 0,
        "listing_dates": "2022-01-01",
        "listing_states": "active,delisted",
    }
    values.update(overrides)
    return Namespace(**values)


def test_load_watchlist_tickers_deduplicates_and_sorts(tmp_path):
    watchlist = tmp_path / "watchlist.csv"
    watchlist.write_text("ticker\nmsft\nAAPL\nMSFT\n", encoding="utf-8")

    assert load_watchlist_tickers(watchlist) == ["AAPL", "MSFT"]


def test_selected_event_types_rejects_unknown_values():
    assert selected_event_types("earnings,dividends") == {"earnings", "dividends"}


def test_build_targets_chunks_tickers_and_adds_listing_status(tmp_path):
    watchlist = tmp_path / "watchlist.csv"
    watchlist.write_text("ticker\nAAPL\nMSFT\nNVDA\n", encoding="utf-8")

    targets = build_targets(
        _args(
            tmp_path,
            watchlist,
            include="earnings,listing-status",
            offset=1,
            max_tickers=1,
            listing_dates="2022-01-01",
            listing_states="active",
        )
    )

    assert [(target.function, target.ticker) for target in targets] == [
        ("EARNINGS", "MSFT"),
        ("LISTING_STATUS", None),
    ]
    assert targets[0].output_path == tmp_path / "events/earnings/MSFT.json"
    assert targets[1].output_path == tmp_path / "instruments/listing_status/2022-01-01_active.csv"


def test_download_targets_writes_json_csv_and_manifest(tmp_path):
    targets = [
        DownloadTarget(
            ticker="AAPL",
            function="EARNINGS",
            output_path=tmp_path / "events/earnings/AAPL.json",
            params={"function": "EARNINGS", "symbol": "AAPL"},
        ),
        DownloadTarget(
            ticker=None,
            function="LISTING_STATUS",
            output_path=tmp_path / "instruments/listing_status/2022-01-01_active.csv",
            params={"function": "LISTING_STATUS", "date": "2022-01-01", "state": "active"},
        ),
    ]
    session = FakeSession(
        [
            FakeResponse(200, "{}", {"symbol": "AAPL", "quarterlyEarnings": []}),
            FakeResponse(200, "symbol,name,exchange,assetType,ipoDate,delistingDate,status\nAAPL,Apple,NASDAQ,Stock,1980-12-12,null,Active\n"),
        ]
    )

    manifest = download_targets(
        targets,
        api_keys=[ApiKeyRef("ALPHA_VANTAGE_API_KEY", "token")],
        output_dir=tmp_path,
        sleep_seconds=0,
        retries=0,
        force=False,
        dry_run=False,
        session=session,
    )

    assert targets[0].output_path.exists()
    assert targets[1].output_path.exists()
    assert (tmp_path / "manifest.json").exists()
    assert manifest["downloaded"][0]["function"] == "EARNINGS"
    assert session.calls[0][1]["apikey"] == "token"


def test_download_targets_skips_existing_without_calling_provider(tmp_path):
    target = DownloadTarget(
        ticker="AAPL",
        function="DIVIDENDS",
        output_path=tmp_path / "events/dividends/AAPL.json",
        params={"function": "DIVIDENDS", "symbol": "AAPL"},
    )
    target.output_path.parent.mkdir(parents=True)
    target.output_path.write_text("{}\n", encoding="utf-8")
    session = FakeSession([])

    manifest = download_targets(
        [target],
        api_keys=[ApiKeyRef("ALPHA_VANTAGE_API_KEY", "token")],
        output_dir=tmp_path,
        sleep_seconds=0,
        retries=0,
        force=False,
        dry_run=False,
        session=session,
    )

    assert manifest["skipped_existing"][0]["function"] == "DIVIDENDS"
    assert session.calls == []


def test_pending_targets_excludes_existing_files_without_offset_math(tmp_path):
    existing = DownloadTarget(
        ticker="AAPL",
        function="EARNINGS",
        output_path=tmp_path / "events/earnings/AAPL.json",
        params={"function": "EARNINGS", "symbol": "AAPL"},
    )
    missing = DownloadTarget(
        ticker="MSFT",
        function="EARNINGS",
        output_path=tmp_path / "events/earnings/MSFT.json",
        params={"function": "EARNINGS", "symbol": "MSFT"},
    )
    existing.output_path.parent.mkdir(parents=True)
    existing.output_path.write_text("{}\n", encoding="utf-8")

    assert pending_targets([existing, missing], force=False) == [missing]
    assert pending_targets([existing, missing], force=True) == [existing, missing]
