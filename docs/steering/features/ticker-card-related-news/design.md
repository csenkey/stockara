# Ticker Card Related News Design

## Data flow

Provider article URLs are normalized by the news collector, stored with the
canonical news item and ticker fanout rows, and copied into the daily static
publication as `related_news[].url`. The existing `summary` field remains the
single summary source for the dashboard.

Recent duplicate articles may predate URL preservation. When a provider returns
the same title/source hash with a URL, collection conditionally fills the URL on
the canonical row and its ticker fanout rows without replacing the article,
summary, classifications, or publication date.

## Frontend interaction

The shared `RelatedNews` component renders each item as a native disclosure:

- closed by default;
- summary visible while its title/disclosure is hovered;
- keyboard-operable through native focus and Enter/Space behavior;
- persistent after click/tap by using the native open state;
- explicit `Read original article` link inside the disclosed content.

Native disclosure semantics avoid custom React state and preserve usability on
touch and assistive technologies.

## Verification

- Collector tests cover URL normalization for all providers.
- Database tests cover conditional URL enrichment and ticker fanout updates.
- Pipeline tests continue to cover URL publication.
- TypeScript lint/build and manual responsive checks verify the disclosure.
