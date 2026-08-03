# Ticker Card Related News Requirements

## Goal

Make related news on every recommendation ticker card compact, readable, and
traceable to the original reporting.

## Requirements

### RN-1 Article list

- Each ticker card lists the title, source, and publication date for the
  existing card display limit of related articles.
- The empty state remains explicit when no related articles are available.

### RN-2 Summary disclosure

- Article summaries are collapsed by default to reduce card height.
- Hovering the article title reveals the stored summary on pointer devices.
- Keyboard focus and click/tap provide equivalent access without requiring
  hover.
- The UI uses the existing stored summary, whether produced by the configured
  AI summarizer or its provider/title fallback.

### RN-3 Original source

- When an article URL is available, the disclosure includes an explicit link
  to the original article that opens in a new tab.
- A missing provider URL must not render a broken or placeholder link.
- NewsAPI, Finnhub, and Alpha Vantage collection preserve provider article URLs
  when supplied.

### RN-4 Compatibility

- The interaction applies consistently to published picks, sell alerts,
  fallback previews, and withheld candidate cards.
- Existing slate and red visual tones remain supported.
