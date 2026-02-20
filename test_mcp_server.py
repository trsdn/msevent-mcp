"""
Comprehensive tests for the Microsoft Events MCP Server.

Tests cover:
- parse_card: structured data extraction from raw API cards
- _index_cards: in-memory caching
- fetch_page: HTTP request building and response parsing
- fetch_all_cards: pagination logic
- MCP tools: search_events, get_event_details, list_filters, get_event_stats
- Edge cases: empty data, missing fields, malformed responses
"""

import json
from unittest.mock import patch, MagicMock
from io import BytesIO

import pytest

import mcp_server
from mcp_server import (
    parse_card,
    _index_cards,
    _seen_cards,
    fetch_page,
    fetch_all_cards,
    search_events,
    get_event_details,
    list_filters,
    get_event_stats,
    API_URL,
    DEFAULT_LOCALE,
    PAGE_SIZE,
    MAX_RETRIES,
    RETRY_BACKOFF,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_card(
    event_id="evt-001",
    name="Test Event",
    title="Test Event Title",
    description="A test event",
    fmt="Digital",
    fmt_en="Digital",
    city="Berlin",
    state="Berlin",
    country="Germany",
    start="2025-06-01",
    end="2025-06-02",
    href="https://example.com/event",
    filter_ids=None,
):
    """Helper to create a realistic card dict."""
    return {
        "content": {
            "id": event_id,
            "name": name,
            "title": title,
            "description": description,
            "format": fmt,
            "formatEnglishName": fmt_en,
            "location": {"city": city, "state": state, "country": country},
            "eventDates": {"startDate": start, "endDate": end},
            "action": {"href": href},
            "filterIds": filter_ids or ["topic:ai", "region:europe"],
        }
    }


def _make_api_response(cards=None, total_count=None, facets=None):
    """Helper to create a realistic API response."""
    if cards is None:
        cards = []
    if total_count is None:
        total_count = len(cards)
    resp = {"cards": cards, "totalCount": total_count}
    if facets is not None:
        resp["facets"] = facets
    return resp


def _mock_urlopen(response_data):
    """Create a mock for urllib.request.urlopen returning JSON data."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the in-memory card cache before each test."""
    _seen_cards.clear()
    yield
    _seen_cards.clear()


# ---------------------------------------------------------------------------
# parse_card
# ---------------------------------------------------------------------------

class TestParseCard:
    def test_full_card(self):
        card = _make_card()
        result = parse_card(card)

        assert result["id"] == "evt-001"
        assert result["name"] == "Test Event"
        assert result["title"] == "Test Event Title"
        assert result["description"] == "A test event"
        assert result["format"] == "Digital"
        assert result["format_english"] == "Digital"
        assert result["city"] == "Berlin"
        assert result["state"] == "Berlin"
        assert result["country"] == "Germany"
        assert result["start_date"] == "2025-06-01"
        assert result["end_date"] == "2025-06-02"
        assert result["link"] == "https://example.com/event"
        assert result["filter_ids"] == ["topic:ai", "region:europe"]

    def test_empty_card(self):
        result = parse_card({})

        assert result["id"] == ""
        assert result["name"] == ""
        assert result["title"] == ""
        assert result["description"] == ""
        assert result["format"] == ""
        assert result["city"] == ""
        assert result["country"] == ""
        assert result["link"] == ""
        assert result["filter_ids"] == []

    def test_card_with_none_subobjects(self):
        """API sometimes returns None for location, dates, action."""
        card = {
            "content": {
                "id": "evt-002",
                "name": "Online Event",
                "location": None,
                "eventDates": None,
                "action": None,
            }
        }
        result = parse_card(card)

        assert result["id"] == "evt-002"
        assert result["city"] == ""
        assert result["start_date"] == ""
        assert result["link"] == ""

    def test_card_with_partial_content(self):
        card = {"content": {"id": "evt-003", "title": "Partial"}}
        result = parse_card(card)

        assert result["id"] == "evt-003"
        assert result["title"] == "Partial"
        assert result["name"] == ""
        assert result["filter_ids"] == []


# ---------------------------------------------------------------------------
# _index_cards
# ---------------------------------------------------------------------------

class TestIndexCards:
    def test_index_single_card(self):
        card = _make_card(event_id="idx-001")
        _index_cards("de-de", [card])

        assert ("de-de", "idx-001") in _seen_cards
        assert _seen_cards[("de-de", "idx-001")] is card

    def test_index_multiple_cards(self):
        cards = [_make_card(event_id=f"idx-{i}") for i in range(5)]
        _index_cards("en-us", cards)

        assert len(_seen_cards) == 5
        for i in range(5):
            assert ("en-us", f"idx-{i}") in _seen_cards

    def test_index_skips_cards_without_id(self):
        card = {"content": {"name": "No ID"}}
        _index_cards("de-de", [card])

        assert len(_seen_cards) == 0

    def test_index_empty_id_skipped(self):
        card = {"content": {"id": ""}}
        _index_cards("de-de", [card])

        assert len(_seen_cards) == 0

    def test_index_different_locales(self):
        card = _make_card(event_id="multi-locale")
        _index_cards("de-de", [card])
        _index_cards("en-us", [card])

        assert ("de-de", "multi-locale") in _seen_cards
        assert ("en-us", "multi-locale") in _seen_cards

    def test_index_overwrites_same_key(self):
        card1 = _make_card(event_id="dup", name="First")
        card2 = _make_card(event_id="dup", name="Updated")

        _index_cards("de-de", [card1])
        _index_cards("de-de", [card2])

        assert _seen_cards[("de-de", "dup")]["content"]["name"] == "Updated"


# ---------------------------------------------------------------------------
# fetch_page
# ---------------------------------------------------------------------------

class TestFetchPage:
    @patch("mcp_server.urllib.request.urlopen")
    def test_basic_request(self, mock_urlopen):
        response_data = _make_api_response(total_count=42)
        mock_urlopen.return_value = _mock_urlopen(response_data)

        result = fetch_page("de-de", "", top=10, skip=0)

        assert result["totalCount"] == 42
        mock_urlopen.assert_called_once()

    @patch("mcp_server.urllib.request.urlopen")
    def test_request_payload(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen(_make_api_response())

        fetch_page("en-us", "topic:ai", top=50, skip=100, query="azure")

        call_args = mock_urlopen.call_args
        request_obj = call_args[0][0]

        assert request_obj.full_url == API_URL
        assert request_obj.get_header("Content-type") == "application/json"

        payload = json.loads(request_obj.data.decode("utf-8"))
        assert payload["locale"] == "en-us"
        assert payload["filters"] == "topic:ai"
        assert payload["top"] == 50
        assert payload["skip"] == 100
        assert payload["query"] == "azure"
        assert payload["scenario"] == "Events"

    @patch("mcp_server.urllib.request.urlopen")
    def test_timeout_is_set(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen(_make_api_response())

        fetch_page("de-de", "", top=0, skip=0)

        call_args = mock_urlopen.call_args
        assert call_args[1].get("timeout") == 60 or call_args[0][1] == 60

    @patch("mcp_server.time.sleep")
    @patch("mcp_server.urllib.request.urlopen")
    def test_retry_on_timeout(self, mock_urlopen, mock_sleep):
        """Should retry on timeout and succeed on second attempt."""
        response_data = _make_api_response(total_count=10)
        mock_urlopen.side_effect = [
            TimeoutError("timed out"),
            _mock_urlopen(response_data),
        ]

        result = fetch_page("de-de", "", top=10, skip=0)

        assert result["totalCount"] == 10
        assert mock_urlopen.call_count == 2
        mock_sleep.assert_called_once_with(RETRY_BACKOFF)

    @patch("mcp_server.time.sleep")
    @patch("mcp_server.urllib.request.urlopen")
    def test_retry_on_urlerror(self, mock_urlopen, mock_sleep):
        """Should retry on URLError."""
        import urllib.error
        response_data = _make_api_response(total_count=5)
        mock_urlopen.side_effect = [
            urllib.error.URLError("connection refused"),
            _mock_urlopen(response_data),
        ]

        result = fetch_page("de-de", "", top=5, skip=0)

        assert result["totalCount"] == 5
        assert mock_urlopen.call_count == 2

    @patch("mcp_server.time.sleep")
    @patch("mcp_server.urllib.request.urlopen")
    def test_retry_exponential_backoff(self, mock_urlopen, mock_sleep):
        """Backoff should double each retry."""
        response_data = _make_api_response(total_count=1)
        mock_urlopen.side_effect = [
            TimeoutError("timed out"),
            TimeoutError("timed out"),
            _mock_urlopen(response_data),
        ]

        result = fetch_page("de-de", "", top=1, skip=0)

        assert result["totalCount"] == 1
        assert mock_urlopen.call_count == 3
        assert mock_sleep.call_args_list[0][0][0] == RETRY_BACKOFF      # 2s
        assert mock_sleep.call_args_list[1][0][0] == RETRY_BACKOFF * 2  # 4s

    @patch("mcp_server.time.sleep")
    @patch("mcp_server.urllib.request.urlopen")
    def test_retry_exhausted_raises(self, mock_urlopen, mock_sleep):
        """Should raise after all retries are exhausted."""
        mock_urlopen.side_effect = TimeoutError("timed out")

        with pytest.raises(TimeoutError):
            fetch_page("de-de", "", top=10, skip=0)

        assert mock_urlopen.call_count == MAX_RETRIES

    @patch("mcp_server.time.sleep")
    @patch("mcp_server.urllib.request.urlopen")
    def test_no_sleep_on_last_attempt(self, mock_urlopen, mock_sleep):
        """Should not sleep after the final failed attempt."""
        mock_urlopen.side_effect = TimeoutError("timed out")

        with pytest.raises(TimeoutError):
            fetch_page("de-de", "", top=10, skip=0)

        # MAX_RETRIES attempts, but only MAX_RETRIES-1 sleeps
        assert mock_sleep.call_count == MAX_RETRIES - 1


# ---------------------------------------------------------------------------
# fetch_all_cards
# ---------------------------------------------------------------------------

class TestFetchAllCards:
    @patch("mcp_server.fetch_page")
    def test_empty_results(self, mock_fetch):
        mock_fetch.return_value = _make_api_response(total_count=0)

        cards, meta = fetch_all_cards("de-de", "")

        assert cards == []
        assert meta["totalCount"] == 0
        mock_fetch.assert_called_once()

    @patch("mcp_server.fetch_page")
    def test_single_page(self, mock_fetch):
        test_cards = [_make_card(event_id=f"sp-{i}") for i in range(3)]

        mock_fetch.side_effect = [
            _make_api_response(total_count=3),           # metadata call
            _make_api_response(cards=test_cards),         # data call
        ]

        cards, meta = fetch_all_cards("de-de", "")

        assert len(cards) == 3
        assert meta["totalCount"] == 3
        assert len(_seen_cards) == 3

    @patch("mcp_server.fetch_page")
    def test_multiple_pages(self, mock_fetch):
        page1 = [_make_card(event_id=f"p1-{i}") for i in range(100)]
        page2 = [_make_card(event_id=f"p2-{i}") for i in range(50)]

        mock_fetch.side_effect = [
            _make_api_response(total_count=150),
            _make_api_response(cards=page1),
            _make_api_response(cards=page2),
        ]

        cards, meta = fetch_all_cards("de-de", "")

        assert len(cards) == 150
        assert len(_seen_cards) == 150
        assert mock_fetch.call_count == 3  # 1 meta + 2 data pages

    @patch("mcp_server.fetch_page")
    def test_max_pages_limit(self, mock_fetch):
        """Ensures pagination stops at max_pages."""
        mock_fetch.side_effect = [
            _make_api_response(total_count=500),       # meta
            _make_api_response(cards=[_make_card(event_id=f"mp-{i}") for i in range(100)]),
            _make_api_response(cards=[_make_card(event_id=f"mp-{i+100}") for i in range(100)]),
        ]

        cards, meta = fetch_all_cards("de-de", "", max_pages=2)

        assert len(cards) == 200
        # 1 meta call + 2 data pages = 3 calls
        assert mock_fetch.call_count == 3

    @patch("mcp_server.fetch_page")
    def test_query_passed_through(self, mock_fetch):
        mock_fetch.side_effect = [
            _make_api_response(total_count=1),
            _make_api_response(cards=[_make_card()]),
        ]

        fetch_all_cards("en-us", "topic:ai", query="cloud")

        # Check meta call includes query
        meta_call = mock_fetch.call_args_list[0]
        assert meta_call[1].get("query", meta_call[0][4] if len(meta_call[0]) > 4 else "") == "cloud" or \
               "cloud" in str(mock_fetch.call_args_list)


# ---------------------------------------------------------------------------
# MCP Tool: search_events
# ---------------------------------------------------------------------------

class TestSearchEvents:
    @patch("mcp_server.fetch_all_cards")
    def test_basic_search(self, mock_fetch):
        cards = [_make_card(event_id=f"se-{i}") for i in range(3)]
        mock_fetch.return_value = (cards, {"totalCount": 3})

        result = json.loads(search_events())

        assert result["total_count"] == 3
        assert result["returned"] == 3
        assert len(result["events"]) == 3

    @patch("mcp_server.fetch_all_cards")
    def test_search_with_filters(self, mock_fetch):
        mock_fetch.return_value = ([], {"totalCount": 0})

        result = json.loads(search_events(filters="topic:ai", locale="en-us"))

        mock_fetch.assert_called_once_with("en-us", "topic:ai", query="")
        assert result["total_count"] == 0
        assert result["returned"] == 0

    @patch("mcp_server.fetch_all_cards")
    def test_search_with_query(self, mock_fetch):
        mock_fetch.return_value = ([_make_card()], {"totalCount": 1})

        result = json.loads(search_events(query="azure"))

        mock_fetch.assert_called_once_with("de-de", "", query="azure")

    @patch("mcp_server.fetch_all_cards")
    def test_search_events_parsed_correctly(self, mock_fetch):
        card = _make_card(
            event_id="detail-1",
            name="AI Summit",
            city="Munich",
            country="Germany",
        )
        mock_fetch.return_value = ([card], {"totalCount": 1})

        result = json.loads(search_events())
        event = result["events"][0]

        assert event["id"] == "detail-1"
        assert event["name"] == "AI Summit"
        assert event["city"] == "Munich"
        assert event["country"] == "Germany"


# ---------------------------------------------------------------------------
# MCP Tool: get_event_details
# ---------------------------------------------------------------------------

class TestGetEventDetails:
    def test_found_in_cache(self):
        card = _make_card(event_id="cached-1")
        _seen_cards[("de-de", "cached-1")] = card

        result = json.loads(get_event_details("cached-1"))

        assert result["id"] == "cached-1"
        assert "raw_content" in result

    @patch("mcp_server.fetch_all_cards")
    def test_fetches_when_not_cached(self, mock_fetch):
        card = _make_card(event_id="remote-1")

        def side_effect(locale, filters, max_pages=20):
            _seen_cards[("de-de", "remote-1")] = card
            return ([card], {"totalCount": 1})

        mock_fetch.side_effect = side_effect

        result = json.loads(get_event_details("remote-1"))

        assert result["id"] == "remote-1"
        mock_fetch.assert_called_once_with("de-de", "", max_pages=50)

    @patch("mcp_server.fetch_all_cards")
    def test_not_found(self, mock_fetch):
        mock_fetch.return_value = ([], {"totalCount": 0})

        result = json.loads(get_event_details("nonexistent"))

        assert "error" in result
        assert "nonexistent" in result["error"]

    def test_different_locale(self):
        card = _make_card(event_id="locale-1")
        _seen_cards[("en-us", "locale-1")] = card

        # Should not find with de-de
        with patch("mcp_server.fetch_all_cards") as mock_fetch:
            mock_fetch.return_value = ([], {"totalCount": 0})
            result = json.loads(get_event_details("locale-1", locale="de-de"))
            assert "error" in result

        # Should find with en-us
        result = json.loads(get_event_details("locale-1", locale="en-us"))
        assert result["id"] == "locale-1"

    def test_raw_content_included(self):
        card = _make_card(event_id="raw-1")
        _seen_cards[("de-de", "raw-1")] = card

        result = json.loads(get_event_details("raw-1"))

        assert result["raw_content"]["id"] == "raw-1"
        assert result["raw_content"]["name"] == "Test Event"


# ---------------------------------------------------------------------------
# MCP Tool: list_filters
# ---------------------------------------------------------------------------

class TestListFilters:
    @patch("mcp_server.fetch_page")
    def test_basic_filters(self, mock_fetch):
        facets = [
            {"id": "topic:ai", "count": 50},
            {"id": "topic:security", "count": 30},
            {"id": "format:digital", "count": 80},
            {"id": "region:europe", "count": 40},
        ]
        mock_fetch.return_value = _make_api_response(total_count=100, facets=facets)

        result = json.loads(list_filters())

        assert result["total_events"] == 100
        cats = result["categories"]
        assert "topic" in cats
        assert "format" in cats
        assert "region" in cats
        assert len(cats["topic"]) == 2
        # Sorted by count descending
        assert cats["topic"][0]["value"] == "ai"
        assert cats["topic"][0]["count"] == 50
        assert cats["topic"][1]["value"] == "security"

    @patch("mcp_server.fetch_page")
    def test_filters_exclude_zero_count(self, mock_fetch):
        facets = [
            {"id": "topic:ai", "count": 10},
            {"id": "topic:old", "count": 0},
        ]
        mock_fetch.return_value = _make_api_response(total_count=10, facets=facets)

        result = json.loads(list_filters())
        topics = result["categories"]["topic"]

        assert len(topics) == 1
        assert topics[0]["value"] == "ai"

    @patch("mcp_server.fetch_page")
    def test_filters_without_colon_skipped(self, mock_fetch):
        facets = [
            {"id": "malformed", "count": 5},
            {"id": "topic:valid", "count": 10},
        ]
        mock_fetch.return_value = _make_api_response(total_count=10, facets=facets)

        result = json.loads(list_filters())
        assert "malformed" not in result["categories"]
        assert "topic" in result["categories"]

    @patch("mcp_server.fetch_page")
    def test_empty_facets(self, mock_fetch):
        mock_fetch.return_value = _make_api_response(total_count=0, facets=[])

        result = json.loads(list_filters())

        assert result["total_events"] == 0
        assert result["categories"] == {}


# ---------------------------------------------------------------------------
# MCP Tool: get_event_stats
# ---------------------------------------------------------------------------

class TestGetEventStats:
    @patch("mcp_server.fetch_page")
    def test_basic_stats(self, mock_fetch):
        facets = [
            {"id": "topic:ai", "count": 50},
            {"id": "topic:security", "count": 30},
            {"id": "format:digital", "count": 80},
            {"id": "format:in-person", "count": 20},
            {"id": "region:europe", "count": 40},
            {"id": "product:azure", "count": 60},
            {"id": "audience:developer", "count": 45},
        ]
        mock_fetch.return_value = _make_api_response(total_count=100, facets=facets)

        result = json.loads(get_event_stats())

        assert result["total_events"] == 100
        cats = result["categories"]

        # Topic stats sorted by count desc
        topics = {item["name"]: item["count"] for item in cats["topic"]}
        assert topics["ai"] == 50
        assert topics["security"] == 30

        # Format stats
        formats = {item["name"]: item["count"] for item in cats["format"]}
        assert formats["digital"] == 80
        assert formats["in-person"] == 20

        # Product, region, audience present
        assert "product" in cats
        assert "region" in cats
        assert "audience" in cats

    @patch("mcp_server.fetch_page")
    def test_stats_with_filters(self, mock_fetch):
        mock_fetch.return_value = _make_api_response(total_count=0, facets=[])

        get_event_stats(filters="topic:ai", locale="en-us")

        mock_fetch.assert_called_once_with("en-us", "topic:ai", top=0, skip=0)

    @patch("mcp_server.fetch_page")
    def test_empty_stats(self, mock_fetch):
        mock_fetch.return_value = _make_api_response(total_count=0, facets=[])

        result = json.loads(get_event_stats())

        assert result["total_events"] == 0
        assert result["categories"] == {}

    @patch("mcp_server.fetch_page")
    def test_stats_sorted_descending(self, mock_fetch):
        facets = [
            {"id": "topic:small", "count": 5},
            {"id": "topic:big", "count": 100},
            {"id": "topic:medium", "count": 50},
        ]
        mock_fetch.return_value = _make_api_response(total_count=100, facets=facets)

        result = json.loads(get_event_stats())
        topics = result["categories"]["topic"]

        assert topics[0]["name"] == "big"
        assert topics[1]["name"] == "medium"
        assert topics[2]["name"] == "small"

    @patch("mcp_server.fetch_page")
    def test_stats_max_20_per_category(self, mock_fetch):
        facets = [{"id": f"topic:item-{i}", "count": i} for i in range(30)]
        mock_fetch.return_value = _make_api_response(total_count=100, facets=facets)

        result = json.loads(get_event_stats())

        assert len(result["categories"]["topic"]) == 20

    @patch("mcp_server.fetch_page")
    def test_stats_facets_without_colon_skipped(self, mock_fetch):
        facets = [
            {"id": "malformed", "count": 10},
            {"id": "topic:valid", "count": 5},
        ]
        mock_fetch.return_value = _make_api_response(total_count=10, facets=facets)

        result = json.loads(get_event_stats())

        assert "malformed" not in result["categories"]
        assert "topic" in result["categories"]


# ---------------------------------------------------------------------------
# Edge cases and integration-like tests
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_parse_card_deeply_nested_missing(self):
        """Card with content but all sub-dicts missing."""
        card = {"content": {}}
        result = parse_card(card)
        assert result["id"] == ""
        assert result["link"] == ""

    @patch("mcp_server.fetch_all_cards")
    def test_search_events_returns_valid_json(self, mock_fetch):
        """Ensure output is always valid JSON."""
        mock_fetch.return_value = ([], {"totalCount": 0})
        raw = search_events()
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)

    @patch("mcp_server.fetch_all_cards")
    def test_unicode_in_events(self, mock_fetch):
        """Ensure unicode characters (German umlauts etc.) are preserved."""
        card = _make_card(
            event_id="uni-1",
            name="Künstliche Intelligenz Konferenz",
            city="München",
            country="Österreich",
        )
        mock_fetch.return_value = ([card], {"totalCount": 1})

        result = json.loads(search_events())
        event = result["events"][0]

        assert event["name"] == "Künstliche Intelligenz Konferenz"
        assert event["city"] == "München"
        assert event["country"] == "Österreich"

    def test_index_cards_with_missing_content(self):
        """Cards without 'content' key should not crash."""
        _index_cards("de-de", [{"other": "data"}])
        assert len(_seen_cards) == 0

    @patch("mcp_server.fetch_page")
    def test_get_event_stats_facets_without_colon(self, mock_fetch):
        """Facets without colon separator should be ignored gracefully."""
        facets = [
            {"id": "malformed", "count": 5},
            {"id": "topic:ai", "count": 10},
        ]
        mock_fetch.return_value = _make_api_response(total_count=10, facets=facets)

        result = json.loads(get_event_stats())
        assert "topic" in result["categories"]
        assert "malformed" not in result["categories"]

    def test_default_locale(self):
        assert DEFAULT_LOCALE == "de-de"

    def test_page_size(self):
        assert PAGE_SIZE == 100

    def test_api_url(self):
        assert "microsoft.com" in API_URL
        assert "events" in API_URL


# ---------------------------------------------------------------------------
# Live API smoke test (optional, skipped by default)
# ---------------------------------------------------------------------------

class TestLiveAPI:
    """These tests hit the real Microsoft Events API.
    Run with: pytest -m live
    """

    @pytest.mark.live
    def test_live_fetch_page(self):
        result = fetch_page("de-de", "", top=1, skip=0)
        assert "totalCount" in result
        assert isinstance(result["totalCount"], int)

    @pytest.mark.live
    def test_live_search_events(self):
        result = json.loads(search_events(locale="de-de"))
        assert "total_count" in result
        assert "events" in result
        assert isinstance(result["events"], list)

    @pytest.mark.live
    def test_live_list_filters(self):
        result = json.loads(list_filters(locale="de-de"))
        assert "total_events" in result
        assert "categories" in result

    @pytest.mark.live
    def test_live_get_event_stats(self):
        result = json.loads(get_event_stats(locale="de-de"))
        assert "total_events" in result
        assert "categories" in result
        assert isinstance(result["categories"], dict)
        assert len(result["categories"]) > 0
