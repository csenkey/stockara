from argparse import Namespace
from pathlib import Path

from scripts.download_tiingo_ohlcv import (
    DownloadTarget,
    build_targets,
    download_targets,
    load_watchlist_tickers,
    tiingo_symbol,
)


class FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


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
        "start_date": "2021-12-01",
        "end_date": "2023-01-31",
        "tickers": None,
        "etfs": "SPY,VOO",
        "max_tickers": 0,
        "offset": 0,
        "max_symbols_per_run": 500,
        "dot_symbol_style": "dash",
    }
    values.update(overrides)
    return Namespace(**values)


def test_load_watchlist_tickers_deduplicates_and_sorts(tmp_path):
    watchlist = tmp_path / "watchlist.csv"
    watchlist.write_text("ticker,name\naapl,Apple\nMSFT,Microsoft\nAAPL,Apple\n", encoding="utf-8")

    assert load_watchlist_tickers(watchlist) == ["AAPL", "MSFT"]


def test_tiingo_symbol_uses_dash_for_dot_tickers_by_default():
    assert tiingo_symbol("BRK.B") == "BRK-B"
    assert tiingo_symbol("BRK.B", dot_symbol_style="dot") == "BRK.B"


def test_build_targets_uses_watchlist_chunk_and_etfs(tmp_path):
    watchlist = tmp_path / "watchlist.csv"
    watchlist.write_text("ticker\nAAPL\nMSFT\nNVDA\n", encoding="utf-8")

    targets = build_targets(_args(tmp_path, watchlist, offset=1, max_tickers=1, etfs="SPY"))

    assert [(target.ticker, target.instrument_type) for target in targets] == [
        ("MSFT", "stock"),
        ("SPY", "etf"),
    ]
    assert targets[0].output_path == tmp_path / "prices/stocks/MSFT.csv"
    assert targets[1].output_path == tmp_path / "prices/etfs/SPY.csv"


def test_download_targets_writes_csv_and_manifest(tmp_path):
    target = DownloadTarget(
        ticker="AAPL",
        provider_symbol="AAPL",
        instrument_type="stock",
        output_path=tmp_path / "prices/stocks/AAPL.csv",
    )
    session = FakeSession(
        [FakeResponse(200, "date,close,high,low,open,volume,adjClose,adjHigh,adjLow,adjOpen,adjVolume,divCash,splitFactor\n2022-01-03,1,1,1,1,100,1,1,1,1,100,0,1")]
    )

    manifest = download_targets(
        [target],
        token="token",
        start_date="2021-12-01",
        end_date="2023-01-31",
        output_dir=tmp_path,
        sleep_seconds=0,
        retries=0,
        force=False,
        dry_run=False,
        session=session,
    )

    assert target.output_path.exists()
    assert (tmp_path / "manifest.json").exists()
    assert manifest["downloaded"][0]["ticker"] == "AAPL"
    assert manifest["downloaded"][0]["row_count"] == 1
    assert session.calls[0][1]["startDate"] == "2021-12-01"
    assert session.calls[0][1]["endDate"] == "2023-01-31"


def test_download_targets_skips_existing_without_force(tmp_path):
    target = DownloadTarget(
        ticker="AAPL",
        provider_symbol="AAPL",
        instrument_type="stock",
        output_path=tmp_path / "prices/stocks/AAPL.csv",
    )
    target.output_path.parent.mkdir(parents=True)
    target.output_path.write_text("date,close\n2022-01-03,1\n", encoding="utf-8")
    session = FakeSession([])

    manifest = download_targets(
        [target],
        token="token",
        start_date="2021-12-01",
        end_date="2023-01-31",
        output_dir=tmp_path,
        sleep_seconds=0,
        retries=0,
        force=False,
        dry_run=False,
        session=session,
    )

    assert manifest["skipped_existing"][0]["ticker"] == "AAPL"
    assert session.calls == []
