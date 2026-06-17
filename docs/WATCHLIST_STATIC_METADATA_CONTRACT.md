# Watchlist Static Metadata Contract

`data/watchlist_seed.csv` is the canonical Phase 1 static metadata source for the monitored stock universe. It must contain source-backed company metadata, not placeholder labels. The seed process should reject missing required fields rather than guessing.

## Required Columns

- `ticker`: Exchange ticker symbol used by the market data providers.
- `company_name`: Legal or commonly listed company name.
- `sector`: One valid Stockara sector.
- `industry`: More specific business classification within the sector.
- `company_size`: One of `blue_chip`, `mid_cap`, or `startup`.
- `source`: Watchlist inclusion source, such as `sp500`, `nasdaq100`, or `manual_research`.
- `metadata_source`: Source used for static company metadata, such as `sec_companyfacts`, `nasdaq_profile`, `yfinance_profile`, or `manual_research`.
- `metadata_source_url`: URL or stable source identifier for the metadata.
- `metadata_as_of`: ISO date when the metadata was last verified.

## AI Context Columns

These fields help the analysis pipeline produce better business reasoning. They should be populated wherever source-backed data is available.

- `business_description`: Short description of what the company does and its historical/business context.
- `flagship_products`: Pipe-delimited active products, services, or platforms that materially define the business.
- `revenue_segments`: Pipe-delimited major revenue or operating segments.
- `primary_customers`: Pipe-delimited main customer groups or end markets.
- `geographic_exposure`: Pipe-delimited main geographic markets when known.
- `competitive_position`: Short source-backed summary of moat, market position, or primary differentiation.
- `key_static_risks`: Pipe-delimited durable business risks, excluding transient news.
- `exchange`: Primary listing exchange when known.
- `currency`: Trading/reporting currency when known.
- `country`: Headquarters or primary domicile country.
- `website`: Official company website.
- `founded_year`: Year founded when known.
- `headquarters`: Headquarters location when known.
- `ipo_year`: IPO year when known.
- `market_cap`: Market capitalization from the metadata provider when known.

## Rules

- Do not default missing sectors to `Technology` or any other broad placeholder.
- Do not invent company descriptions, products, segments, or risks.
- Prefer stable, source-backed metadata from exchange profiles, SEC/company filings, company investor relations pages, or another explicit provider.
- Use empty optional fields only when the source does not provide the data yet; required fields must be complete before seeding.
- When metadata changes, update `metadata_as_of` and preserve the source fields.
