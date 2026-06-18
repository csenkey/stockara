# Stooq S3 Backfill

Use this one-time process when Stooq historical `.txt` files have already been
downloaded locally and should seed Stockara's DynamoDB `stock_data` rows.

## Upload Files

The current uploaded source is:

```text
s3://stockmonitoringfrontend-sitebucket397a1860-q0p14kssoh4b/stooq/
```

The loader scans recursively under the prefix, so files in Stooq's nested
subfolders are included.

```bash
aws s3 sync "/Users/istvancsenkey-sinko/Downloads/data/daily/us" \
  "s3://stockmonitoringfrontend-sitebucket397a1860-q0p14kssoh4b/stooq/" \
  --exclude "*" \
  --include "*.txt"
```

## Start Backfill

Invoke the deployed stock collector Lambda with `mode=stooq_s3_backfill`.

```bash
aws lambda invoke \
  --function-name stockara-stock-collector \
  --cli-binary-format raw-in-base64-out \
  --payload '{
    "mode": "stooq_s3_backfill",
    "bucket": "stockmonitoringfrontend-sitebucket397a1860-q0p14kssoh4b",
    "s3_prefix": "stooq/",
    "max_files": 1,
    "continue_backfill": true
  }' \
  /tmp/stooq-backfill-response.json
```

The default is one Stooq file per invocation because a single file can contain
thousands of daily rows. Set `max_files` higher only after observing runtime and
DynamoDB write behavior.

Progress is written to:

```text
stock-history/_backfill/stooq-upload-latest.json
```

## Data Notes

The downloaded Stooq files provide daily ticker, date, open, high, low, close,
and volume. That is sufficient for Stockara's required OHLCV history.

The files do not include sector, company size, company metadata, currency,
exchange, news, corporate action events, or a separate raw close versus adjusted
close. Stockara fills currency and exchange from existing watchlist metadata.
The observed historical rows use adjusted-looking prices and fractional early
volumes, so the loader records them as provider-adjusted OHLCV and rounds volume
to the nearest whole share before storing.
