# Stooq S3 Backfill

Use this one-time process when Stooq historical `.txt` files have already been
downloaded locally and should seed Stockara's DynamoDB `stock_data` rows.

## Upload Files

The current uploaded source is:

```text
s3://stockmonitoringfrontend-sitebucket397a1860-q0p14kssoh4b/stooq/data.zip
```

Use the deployed `stockara-stooq-zip-extractor` Lambda to extract the zip inside
AWS. It streams `.txt` members from the zip back to S3 and can start the stock
collector backfill after extraction completes.

If local AWS credentials are unavailable, dispatch the GitHub Actions workflow
`Run Stooq Zip Extraction`. That workflow only invokes the Lambda; it does not
download or upload the zip.

```bash
aws lambda invoke \
  --function-name stockara-stooq-zip-extractor \
  --cli-binary-format raw-in-base64-out \
  --payload '{
    "bucket": "stockmonitoringfrontend-sitebucket397a1860-q0p14kssoh4b",
    "zip_key": "stooq/data.zip",
    "output_prefix": "stooq-extracted/",
    "max_entries": 1000,
    "continue_extraction": true,
    "start_backfill_on_complete": true
  }' \
  /tmp/stooq-extract-response.json
```

Equivalent GitHub control-plane invocation:

```bash
gh workflow run "Run Stooq Zip Extraction" \
  -f bucket="stockmonitoringfrontend-sitebucket397a1860-q0p14kssoh4b" \
  -f zip_key="stooq/data.zip" \
  -f output_prefix="stooq-extracted/" \
  -f max_entries="1000" \
  -f start_backfill_on_complete="true"
```

## Start Backfill

The extractor can start this automatically when
`start_backfill_on_complete=true`. To start the stock collector manually after
extraction completes, invoke:

```bash
aws lambda invoke \
  --function-name stockara-stock-collector \
  --cli-binary-format raw-in-base64-out \
  --payload '{
    "mode": "stooq_s3_backfill",
    "bucket": "stockmonitoringfrontend-sitebucket397a1860-q0p14kssoh4b",
    "s3_prefix": "stooq-extracted/",
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
