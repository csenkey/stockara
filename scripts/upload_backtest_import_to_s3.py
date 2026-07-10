"""Upload locally downloaded backtest import files to Stockara's S3 artifact bucket.

GitHub Actions cannot read files that exist only on an operator's laptop or
external drive. This script stages those raw provider files in S3 so a workflow
can validate/import them into DynamoDB and leave a canonical S3 snapshot for
backtests.
"""

from __future__ import annotations

import argparse
import mimetypes
from pathlib import Path

import boto3


DEFAULT_PREFIX_BY_PROVIDER = {
    "tiingo": "backtests/data/raw/tiingo",
    "alpha_vantage": "backtests/data/raw/alpha_vantage",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload local Stockara backtest import files to S3."
    )
    parser.add_argument(
        "--local-dir",
        type=Path,
        required=True,
        help="Local provider output directory, for example data/backtest-import/raw/tiingo.",
    )
    parser.add_argument("--bucket", required=True, help="Destination S3 bucket.")
    parser.add_argument(
        "--provider",
        choices=sorted(DEFAULT_PREFIX_BY_PROVIDER),
        required=True,
        help="Provider layout to stage.",
    )
    parser.add_argument(
        "--s3-prefix",
        help="Destination S3 prefix. Defaults to the provider's raw backtest prefix.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned uploads without writing to S3.",
    )
    return parser.parse_args()


def _iter_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def upload_directory(
    *,
    local_dir: Path,
    bucket: str,
    s3_prefix: str,
    dry_run: bool,
) -> int:
    if not local_dir.exists() or not local_dir.is_dir():
        raise ValueError(f"{local_dir} is not a directory")

    s3 = boto3.client("s3")
    uploaded = 0
    for path in _iter_files(local_dir):
        relative = path.relative_to(local_dir).as_posix()
        key = f"{s3_prefix.strip('/')}/{relative}"
        print(f"{'would upload' if dry_run else 'upload'} {path} -> s3://{bucket}/{key}")
        if not dry_run:
            s3.upload_file(
                str(path),
                bucket,
                key,
                ExtraArgs={"ContentType": _content_type(path)},
            )
        uploaded += 1
    return uploaded


def main() -> None:
    args = _parse_args()
    s3_prefix = args.s3_prefix or DEFAULT_PREFIX_BY_PROVIDER[args.provider]
    count = upload_directory(
        local_dir=args.local_dir,
        bucket=args.bucket,
        s3_prefix=s3_prefix,
        dry_run=args.dry_run,
    )
    print(f"{'Planned' if args.dry_run else 'Uploaded'} {count} file(s).")


if __name__ == "__main__":
    main()
