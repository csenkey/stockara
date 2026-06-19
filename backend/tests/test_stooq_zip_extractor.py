"""Tests for the one-time Stooq zip extractor Lambda."""

import shutil
import zipfile
from unittest.mock import MagicMock

from src.scripts import stooq_zip_extractor as extractor


def _build_zip(path, names):
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(
                name,
                "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>\n"
                "AAPL.US,D,20260617,000000,1,2,1,2,100,0\n",
            )


def test_extractor_streams_txt_members_to_s3_and_queues_continuation(
    tmp_path, monkeypatch
):
    source_zip = tmp_path / "data.zip"
    local_zip = tmp_path / "lambda.zip"
    _build_zip(
        source_zip,
        [
            "data/daily/us/nasdaq stocks/1/aapl.us.txt",
            "data/daily/us/nasdaq stocks/1/msft.us.txt",
            "__MACOSX/data/daily/us/nasdaq stocks/1/._aapl.us.txt",
            "data/daily/us/nasdaq stocks/1/readme.csv",
        ],
    )

    s3 = MagicMock()

    def download_file(bucket, key, destination):
        shutil.copyfile(source_zip, destination)

    s3.download_file.side_effect = download_file
    monkeypatch.setattr(extractor, "LOCAL_ZIP_PATH", str(local_zip))
    monkeypatch.setattr(extractor.boto3, "client", MagicMock(return_value=s3))
    monkeypatch.setattr(extractor, "_invoke_self", MagicMock(return_value=True))

    response = extractor.handler(
        {
            "bucket": "bucket",
            "zip_key": "stooq/data.zip",
            "output_prefix": "stooq-extracted/",
            "max_entries": 1,
        },
        None,
    )

    assert response["statusCode"] == 200
    body = response["body"]
    assert body["extracted_count"] == 1
    assert body["complete"] is False
    assert body["continuation_queued"] is True
    uploaded_key = s3.upload_fileobj.call_args.args[2]
    assert uploaded_key == "stooq-extracted/data/daily/us/nasdaq stocks/1/aapl.us.txt"
    extractor._invoke_self.assert_called_once()
    assert extractor._invoke_self.call_args.args[0]["start_after"].endswith(
        "aapl.us.txt"
    )


def test_extractor_can_start_stock_backfill_after_complete(tmp_path, monkeypatch):
    source_zip = tmp_path / "data.zip"
    local_zip = tmp_path / "lambda.zip"
    _build_zip(source_zip, ["data/daily/us/nyse stocks/2/zws.us.txt"])

    s3 = MagicMock()

    def download_file(bucket, key, destination):
        shutil.copyfile(source_zip, destination)

    s3.download_file.side_effect = download_file
    monkeypatch.setattr(extractor, "LOCAL_ZIP_PATH", str(local_zip))
    monkeypatch.setattr(extractor.boto3, "client", MagicMock(return_value=s3))
    monkeypatch.setattr(extractor, "_invoke_stock_backfill", MagicMock(return_value=True))

    response = extractor.handler(
        {
            "bucket": "bucket",
            "zip_key": "stooq/data.zip",
            "output_prefix": "stooq-extracted/",
            "max_entries": 10,
            "start_backfill_on_complete": True,
        },
        None,
    )

    assert response["statusCode"] == 200
    body = response["body"]
    assert body["extracted_count"] == 1
    assert body["complete"] is True
    assert body["backfill_queued"] is True
    extractor._invoke_stock_backfill.assert_called_once_with(
        {
            "mode": "stooq_s3_backfill",
            "bucket": "bucket",
            "s3_prefix": "stooq-extracted/",
            "max_files": 5,
            "continue_backfill": True,
        }
    )
