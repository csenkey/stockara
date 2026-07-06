"""Tests for watchlist logo enrichment and caching helpers."""

from backend.src.scripts.enrich_watchlist_logos import (
    DownloadedLogo,
    cache_downloaded_logos,
    cache_key,
    canonical_fieldnames,
    company_domain,
    logo_dev_candidate,
    metadata_key,
    polygon_logo_candidates,
    public_url,
)


def test_canonical_fieldnames_adds_optional_logo_fields():
    fields = canonical_fieldnames(["ticker", "company_name"])

    assert fields[:2] == ["ticker", "company_name"]
    assert "logo_url" in fields
    assert "logo_icon_url" in fields
    assert "logo_checked_at" in fields


def test_company_domain_normalizes_website():
    assert company_domain("https://www.apple.com/investor-relations/") == "apple.com"
    assert company_domain("nvidia.com/en-us/") == "nvidia.com"
    assert company_domain("") == ""


def test_polygon_logo_candidates_prefers_branding_assets():
    candidates = polygon_logo_candidates(
        {
            "branding": {
                "logo_url": "https://api.polygon.io/logo.svg",
                "icon_url": "https://api.polygon.io/icon.png",
            }
        }
    )

    assert [candidate.kind for candidate in candidates] == ["logo", "icon"]
    assert {candidate.source for candidate in candidates} == {"polygon_ticker_details"}


def test_logo_dev_candidate_requires_website_domain():
    candidate = logo_dev_candidate({"website": "https://www.apple.com"}, token="secret")

    assert candidate is not None
    assert candidate.kind == "logo"
    assert candidate.source == "logo_dev"
    assert candidate.source_url == "https://img.logo.dev/apple.com"
    assert candidate.request_url == "https://img.logo.dev/apple.com?token=secret"
    assert logo_dev_candidate({"website": ""}) is None


def test_cache_keys_are_stable_by_ticker_kind_and_content_type():
    assert cache_key("aapl", "logo", "image/svg+xml") == "logos/AAPL/logo.svg"
    assert cache_key("aapl", "icon", "image/png") == "logos/AAPL/icon.png"
    assert metadata_key("aapl") == "logos/AAPL/metadata.json"
    assert public_url("https://cdn.example.com/", "logos/AAPL/logo.svg") == (
        "https://cdn.example.com/logos/AAPL/logo.svg"
    )


def test_cache_downloaded_logos_uploads_assets_and_metadata():
    s3 = _FakeS3()
    fields = cache_downloaded_logos(
        s3,
        "artifact-bucket",
        "AAPL",
        [
            DownloadedLogo(
                kind="logo",
                source="polygon_ticker_details",
                source_url="https://provider/logo.svg",
                content=b"<svg />",
                content_type="image/svg+xml",
            ),
            DownloadedLogo(
                kind="icon",
                source="polygon_ticker_details",
                source_url="https://provider/icon.png",
                content=b"png",
                content_type="image/png",
            ),
        ],
        "https://cdn.example.com",
        "2026-07-06T08:00:00+00:00",
    )

    assert fields["logo_url"] == "https://cdn.example.com/logos/AAPL/logo.svg"
    assert fields["logo_icon_url"] == "https://cdn.example.com/logos/AAPL/icon.png"
    assert fields["logo_source"] == "polygon_ticker_details"
    assert fields["logo_checked_at"] == "2026-07-06T08:00:00+00:00"
    assert {put["Key"] for put in s3.puts} == {
        "logos/AAPL/logo.svg",
        "logos/AAPL/icon.png",
        "logos/AAPL/metadata.json",
    }


class _FakeS3:
    def __init__(self):
        self.puts = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
