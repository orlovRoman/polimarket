from unittest.mock import AsyncMock
"""
Unit-тесты для agents/shared/utils/resolution_extractor.py
"""

import json
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

try:
    from agents.shared.utils.resolution_extractor import (
        _apex_domain,
        extract_resolution_source_regex,
        extract_resolution_source_llm,
        get_resolution_source,
        check_rss_for_keywords,
        _extract_keywords,
        _build_resolution_block,
        ResolutionSource,
        KNOWN_RSS_MAP,
        ORACLE_PATTERNS,
    )
    MODULE_AVAILABLE = True
except ImportError:
    MODULE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not MODULE_AVAILABLE,
    reason="resolution_extractor.py not yet created",
)

def run(coro):
    import asyncio
    return asyncio.run(coro)

def _feed_entry(title: str, published: str = "Thu, 01 Jan 2026 12:00:00 +0000", link: str = "https://example.com/article", summary: str = "") -> MagicMock:
    entry = MagicMock()
    entry.get = lambda k, default="": {
        "title": title,
        "published": published,
        "link": link,
        "summary": summary,
    }.get(k, default)
    return entry

def _make_feed(entries: list) -> MagicMock:
    feed = MagicMock()
    feed.entries = entries
    return feed

def _llm_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {
        "candidates": [{
            "content": {
                "parts": [{"text": json.dumps(payload)}]
            }
        }]
    }
    return resp

def _mock_async_client(return_value=None, side_effect=None):
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if side_effect:
        mock_client.post = AsyncMock(side_effect=side_effect)
    else:
        mock_client.post = AsyncMock(return_value=return_value)
    return mock_client

class TestApexDomain:
    def test_simple_domain(self):
        assert _apex_domain("https://apnews.com/article/test") == "apnews.com"

    def test_www_stripped(self):
        assert _apex_domain("https://www.reuters.com/world/") == "reuters.com"

    def test_co_uk_preserved(self):
        assert _apex_domain("https://www.bbc.co.uk/news") == "bbc.co.uk"

    def test_gov_domain(self):
        assert _apex_domain("https://www.fda.gov/news-events") == "fda.gov"

    def test_deep_subdomain(self):
        result = _apex_domain("https://feeds.bbci.co.uk/news/rss.xml")
        assert "co.uk" in result

    def test_no_scheme_does_not_crash(self):
        result = _apex_domain("apnews.com")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_malformed_url_does_not_crash(self):
        result = _apex_domain("not_a_url_at_all")
        assert isinstance(result, str)

class TestExtractResolutionSourceRegex:
    def test_uma_oracle_detected(self):
        desc = "This market resolves via UMA oracle based on the final score."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.resolution_type == "oracle"
        assert result.extraction_method == "regex"

    def test_uma_oracle_case_insensitive(self):
        desc = "Resolves USING UMA ORACLE after the event."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.resolution_type == "oracle"

    def test_admin_resolution_detected(self):
        desc = "Admin resolution will be used if the official announcement is unclear."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.resolution_type == "oracle"

    def test_polymarket_council_detected(self):
        desc = "Polymarket Resolution Council will determine the outcome."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.resolution_type == "oracle"

    def test_markdown_url_apnews(self):
        desc = "This market will resolve YES according to [AP News](https://apnews.com/article/election-2026) reporting."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.domain == "apnews.com"
        assert result.rss_url is not None
        assert result.resolution_type == "rss_monitorable"
        assert result.extraction_method == "regex"
        assert result.confidence >= 0.8

    def test_markdown_url_reuters(self):
        desc = "Resolves based on [Reuters](https://reuters.com/world/us/) coverage."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.domain == "reuters.com"
        assert result.rss_url == KNOWN_RSS_MAP["reuters.com"]

    def test_markdown_url_unknown_domain(self):
        desc = "Resolves per [Niche Sports](https://nichesite.example.io/results) data."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.resolution_type == "api_only"
        assert result.rss_url is None

    def test_plain_url_in_resolve_sentence(self):
        desc = "This market resolves based on https://espn.com/nba/standings results."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.domain == "espn.com"

    def test_plain_url_anywhere(self):
        desc = "Check https://coindesk.com/price/bitcoin for the closing price."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.domain == "coindesk.com"

    def test_url_trailing_punctuation_stripped(self):
        desc = "Resolves based on https://apnews.com/article/test."
        result = extract_resolution_source_regex(desc)
        if result and result.raw_url:
            assert not result.raw_url.endswith(".")
            assert not result.raw_url.endswith(",")

    def test_domain_mention_reuters(self):
        desc = "Resolution will be determined according to Reuters reporting."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.domain == "reuters.com"
        assert result.raw_url is None
        assert result.confidence < 0.8

    def test_domain_mention_espn(self):
        desc = "Outcome determined by ESPN official standings."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.domain == "espn.com"

    def test_domain_mention_apnews(self):
        desc = "According to apnews reporting, the winner will be verified."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.domain == "apnews.com"

    def test_empty_string_returns_none(self):
        assert extract_resolution_source_regex("") is None

    def test_none_returns_none(self):
        assert extract_resolution_source_regex(None) is None

    def test_no_source_returns_none(self):
        desc = "This is a general discussion about the market with no specific source."
        result = extract_resolution_source_regex(desc)
        assert result is None

    def test_whitespace_only_returns_none(self):
        assert extract_resolution_source_regex("   \t\n  ") is None

    def test_oracle_wins_over_url(self):
        desc = "Resolves via UMA oracle. See https://apnews.com/article/test for context."
        result = extract_resolution_source_regex(desc)
        assert result is not None
        assert result.resolution_type == "oracle"

class TestExtractKeywords:
    def test_basic_extraction(self):
        title = "Will Bitcoin reach $100,000 by end of 2026?"
        kws = _extract_keywords(title)
        lower_kws = [k.lower() for k in kws]
        assert "bitcoin" in lower_kws
        assert len(kws) <= 5
        assert len(kws) >= 1

    def test_stop_words_removed(self):
        title = "Will the election be decided before the end of the year?"
        kws = _extract_keywords(title)
        for kw in kws:
            assert kw.lower() not in {"will", "the", "be", "end", "before", "and", "for"}

    def test_short_tokens_filtered(self):
        title = "Did US GDP go up or down?"
        kws = _extract_keywords(title)
        for kw in kws:
            assert len(kw) > 3

    def test_max_5_keywords(self):
        title = "Extremely verbose market title with many different words about finance crypto technology sports weather politics"
        kws = _extract_keywords(title)
        assert len(kws) <= 5

    def test_empty_title(self):
        assert _extract_keywords("") == []

    def test_years_removed(self):
        title = "Will party win election in 2026?"
        kws = _extract_keywords(title)
        for kw in kws:
            assert kw not in {"2024", "2025", "2026", "2027"}

    def test_returns_list(self):
        kws = _extract_keywords("Some title about Bitcoin halving")
        assert isinstance(kws, list)

class TestCheckRssForKeywords:
    @patch("agents.shared.utils.resolution_extractor.feedparser.parse")
    def test_match_found_two_keywords(self, mock_parse):
        mock_parse.return_value = _make_feed([
            _feed_entry("Trump signs executive order on tariffs"),
            _feed_entry("Sports results from yesterday"),
        ])
        result = check_rss_for_keywords(
            "https://feeds.reuters.com/reuters/topNews",
            ["Trump", "executive", "tariffs"]
        )
        assert result["found"] is True
        assert len(result["matched_keywords"]) >= 2
        assert "title" in result
        assert "link" in result

    @patch("agents.shared.utils.resolution_extractor.feedparser.parse")
    def test_no_match(self, mock_parse):
        mock_parse.return_value = _make_feed([
            _feed_entry("Completely unrelated sports story"),
            _feed_entry("Weather update for midwest region"),
        ])
        result = check_rss_for_keywords(
            "https://feeds.reuters.com/reuters/topNews",
            ["Bitcoin", "crypto", "halving"]
        )
        assert result["found"] is False

    @patch("agents.shared.utils.resolution_extractor.feedparser.parse")
    def test_one_keyword_match_not_enough(self, mock_parse):
        mock_parse.return_value = _make_feed([
            _feed_entry("Bitcoin price update today"),
        ])
        result = check_rss_for_keywords(
            "https://coindesk.com/rss",
            ["Bitcoin", "halving", "miners"]
        )
        assert result["found"] is False

    @patch("agents.shared.utils.resolution_extractor.feedparser.parse")
    def test_empty_feed(self, mock_parse):
        mock_parse.return_value = _make_feed([])
        result = check_rss_for_keywords("https://example.com/rss", ["keyword"])
        assert result["found"] is False

    @patch("agents.shared.utils.resolution_extractor.feedparser.parse")
    def test_feedparser_exception_handled(self, mock_parse):
        mock_parse.side_effect = Exception("Network error")
        result = check_rss_for_keywords("https://bad.url/rss", ["test"])
        assert result["found"] is False
        assert "error" in result

    @patch("agents.shared.utils.resolution_extractor.feedparser.parse")
    def test_case_insensitive_matching(self, mock_parse):
        mock_parse.return_value = _make_feed([
            _feed_entry("BITCOIN HALVING confirmed by MINERS today"),
        ])
        result = check_rss_for_keywords(
            "https://coindesk.com/rss",
            ["bitcoin", "halving", "miners"]
        )
        assert result["found"] is True

    @patch("agents.shared.utils.resolution_extractor.feedparser.parse")
    def test_checks_max_30_entries(self, mock_parse):
        entries = [_feed_entry(f"Unrelated story {i}") for i in range(50)]
        entries[34] = _feed_entry("Bitcoin halving miners confirm date")
        mock_parse.return_value = _make_feed(entries)
        result = check_rss_for_keywords(
            "https://coindesk.com/rss",
            ["Bitcoin", "halving", "miners"]
        )
        assert result["found"] is False

    @patch("agents.shared.utils.resolution_extractor.feedparser.parse")
    def test_match_in_summary_also_counts(self, mock_parse):
        entry = _feed_entry(
            title="Market update",
            summary="Bitcoin halving event confirmed by major miners"
        )
        mock_parse.return_value = _make_feed([entry])
        result = check_rss_for_keywords(
            "https://coindesk.com/rss",
            ["Bitcoin", "halving", "miners"]
        )
        assert result["found"] is True

class TestExtractResolutionSourceLlm:
    @patch("agents.shared.utils.resolution_extractor.httpx.AsyncClient")
    def test_known_domain_with_rss(self, mock_cls):
        mock_cls.return_value = _mock_async_client(
            return_value=_llm_response({
                "source_url": "https://apnews.com/article/test",
                "source_domain": "apnews.com",
                "resolution_type": "news_site",
                "confidence": 0.85,
            })
        )
        result = run(extract_resolution_source_llm(
            "Complex description that regex cannot parse due to unusual formatting.",
            api_key="test-key"
        ))
        assert result is not None
        assert result.domain == "apnews.com"
        assert result.rss_url == KNOWN_RSS_MAP["apnews.com"]
        assert result.extraction_method == "llm"
        assert result.confidence == pytest.approx(0.85)

    @patch("agents.shared.utils.resolution_extractor.httpx.AsyncClient")
    def test_unknown_domain_becomes_api_only(self, mock_cls):
        mock_cls.return_value = _mock_async_client(
            return_value=_llm_response({
                "source_url": "https://obscure-sports-db.io/scores",
                "source_domain": "obscure-sports-db.io",
                "resolution_type": "sports_site",
                "confidence": 0.6,
            })
        )
        result = run(extract_resolution_source_llm(
            "Resolves based on obscure-sports-db.io final scores.",
            api_key="test-key"
        ))
        assert result is not None
        assert result.rss_url is None
        assert result.resolution_type == "api_only"

    @patch("agents.shared.utils.resolution_extractor.httpx.AsyncClient")
    def test_network_error_returns_none(self, mock_cls):
        mock_cls.return_value = _mock_async_client(
            side_effect=Exception("Connection timeout")
        )
        result = run(extract_resolution_source_llm(
            "Some description.", api_key="test-key"
        ))
        assert result is None

    @patch("agents.shared.utils.resolution_extractor.httpx.AsyncClient")
    def test_malformed_json_returns_none(self, mock_cls):
        bad_resp = MagicMock()
        bad_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "not valid json {{{"}]}}]
        }
        mock_cls.return_value = _mock_async_client(return_value=bad_resp)
        result = run(extract_resolution_source_llm(
            "Some description.", api_key="test-key"
        ))
        assert result is None

    def test_empty_description_skips_llm(self):
        result = run(extract_resolution_source_llm("", api_key="test-key"))
        assert result is None

    def test_too_short_description_skips_llm(self):
        result = run(extract_resolution_source_llm("Short", api_key="test-key"))
        assert result is None

    @patch("agents.shared.utils.resolution_extractor.httpx.AsyncClient")
    def test_description_truncated_to_800_chars(self, mock_cls):
        captured_payloads = []

        async def capture_post(url, **kwargs):
            captured_payloads.append(kwargs.get("json", {}))
            return _llm_response({
                "source_url": None, "source_domain": None,
                "resolution_type": "unknown", "confidence": 0.3,
            })

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = capture_post
        mock_cls.return_value = mock_client

        long_desc = "x" * 2000
        run(extract_resolution_source_llm(long_desc, api_key="test-key"))

        if captured_payloads:
            prompt_text = str(captured_payloads[0])
            assert "x" * 801 not in prompt_text

class TestGetResolutionSource:
    def test_regex_used_when_confident(self):
        desc = "Resolves per [AP News](https://apnews.com/article/test) reporting."
        with patch("agents.shared.utils.resolution_extractor.extract_resolution_source_llm") as mock_llm:
            result = run(get_resolution_source(desc, "Test Market", "test-key"))
            mock_llm.assert_not_called()
        assert result.domain == "apnews.com"

    def test_llm_fallback_when_regex_returns_none(self):
        desc = "Outcome verified through independent third-party assessment process."
        llm_result = ResolutionSource(
            raw_url="https://reuters.com", domain="reuters.com",
            rss_url=KNOWN_RSS_MAP["reuters.com"],
            resolution_type="rss_monitorable",
            extraction_method="llm", confidence=0.75
        )
        with patch(
            "agents.shared.utils.resolution_extractor.extract_resolution_source_llm",
            new=AsyncMock(return_value=llm_result)
        ):
            result = run(get_resolution_source(desc, "Reuters Market", "test-key"))
        assert result.extraction_method == "llm"
        assert result.domain == "reuters.com"

    def test_fallback_unknown_when_both_fail(self):
        with patch("agents.shared.utils.resolution_extractor.extract_resolution_source_regex", return_value=None), \
             patch("agents.shared.utils.resolution_extractor.extract_resolution_source_llm", new=AsyncMock(return_value=None)):
            result = run(get_resolution_source("desc", "Unknown Market", "test-key"))
        assert result.resolution_type == "unknown"
        assert result.extraction_method == "fallback"
        assert result.confidence == 0.0

    def test_keywords_always_added(self):
        desc = "Resolves via UMA oracle."
        result = run(get_resolution_source(
            desc, "Will Bitcoin halving happen before June 2026?", "test-key"
        ))
        assert isinstance(result.keywords, list)
        assert len(result.keywords) > 0

    def test_oracle_not_overridden_by_llm(self):
        desc = "Resolves via UMA oracle. Source: https://apnews.com/article/test."
        with patch("agents.shared.utils.resolution_extractor.extract_resolution_source_llm") as mock_llm:
            result = run(get_resolution_source(desc, "Oracle Market", "test-key"))
            mock_llm.assert_not_called()
        assert result.resolution_type == "oracle"

    def test_llm_wins_when_regex_low_confidence(self):
        llm_result = ResolutionSource(
            raw_url="https://reuters.com", domain="reuters.com",
            rss_url=KNOWN_RSS_MAP["reuters.com"],
            resolution_type="rss_monitorable",
            extraction_method="llm", confidence=0.9
        )
        regex_result = ResolutionSource(
            raw_url=None, domain="obscure.com", rss_url=None,
            resolution_type="api_only",
            extraction_method="regex", confidence=0.5
        )
        with patch("agents.shared.utils.resolution_extractor.extract_resolution_source_regex", return_value=regex_result), \
             patch("agents.shared.utils.resolution_extractor.extract_resolution_source_llm", new=AsyncMock(return_value=llm_result)):
            result = run(get_resolution_source("some desc", "Market", "test-key"))
            assert result.extraction_method == "llm"
            assert result.domain == "reuters.com"
