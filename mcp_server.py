#!/usr/bin/env python3
"""
MCP Server for Microsoft Events API.
Exposes live event data via MCP tools using FastMCP.
"""

import json
import time
import urllib.error
import urllib.request
from collections import Counter

from fastmcp import FastMCP

API_URL = "https://www.microsoft.com/msonecloudapi/events/cards"
DEFAULT_LOCALE = "de-de"
PAGE_SIZE = 100
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds, doubles each retry

# In-memory index: every card seen by any API call is stored here.
# Key: (locale, event_id) -> raw card dict.
# Lives for the lifetime of the MCP server process.
_seen_cards: dict[tuple[str, str], dict] = {}

mcp = FastMCP("Microsoft Events")


def fetch_page(locale: str, filters: str, top: int, skip: int, query: str = "") -> dict:
    """Fetch a single page from the Microsoft Events API with retry on failure."""
    payload = json.dumps({
        "locale": locale,
        "top": top,
        "skip": skip,
        "filters": filters,
        "scenario": "Events",
        "query": query,
    }).encode("utf-8")

    last_error = None
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
    raise last_error


def _index_cards(locale: str, cards: list[dict]):
    """Add cards to the in-memory index."""
    for card in cards:
        eid = card.get("content", {}).get("id", "")
        if eid:
            _seen_cards[(locale, eid)] = card


def fetch_all_cards(locale: str, filters: str, query: str = "", max_pages: int = 20) -> tuple[list[dict], dict]:
    """Fetch all events matching the criteria. Returns (cards, metadata)."""
    meta = fetch_page(locale, filters, top=0, skip=0, query=query)
    total = meta.get("totalCount", 0)
    if total == 0:
        return [], meta

    all_cards = []
    for skip in range(0, min(total, max_pages * PAGE_SIZE), PAGE_SIZE):
        data = fetch_page(locale, filters, PAGE_SIZE, skip, query=query)
        cards = data.get("cards", [])
        all_cards.extend(cards)
        _index_cards(locale, cards)

    return all_cards, meta


def parse_card(card: dict) -> dict:
    """Extract structured data from a raw API card."""
    content = card.get("content", {})
    location = content.get("location", {}) or {}
    dates = content.get("eventDates", {}) or {}
    action = content.get("action", {}) or {}

    return {
        "id": content.get("id", ""),
        "name": content.get("name", ""),
        "title": content.get("title", ""),
        "description": content.get("description", ""),
        "format": content.get("format", ""),
        "format_english": content.get("formatEnglishName", ""),
        "link": action.get("href", ""),
        "city": location.get("city", ""),
        "state": location.get("state", ""),
        "country": location.get("country", ""),
        "start_date": dates.get("startDate", ""),
        "end_date": dates.get("endDate", ""),
        "filter_ids": content.get("filterIds", []),
    }


@mcp.tool()
def search_events(filters: str = "", query: str = "", locale: str = DEFAULT_LOCALE) -> str:
    """Search Microsoft Events with optional filters and free-text query.

    IMPORTANT: Before searching, call list_filters first to discover the
    available filter categories and their exact values. Do not guess filter
    values — they must match exactly (e.g. "dynamics-365", not "dynamics").

    Args:
        filters: Comma-separated filter string, e.g. "topic:ai,product:dynamics-365,format:digital".
                 Use list_filters to get all available category:value pairs.
        query: Optional free-text search query.
        locale: API locale (default: de-de). Use en-us for English results.

    Returns:
        JSON with total count and list of events including title, dates, location, and link.
    """
    cards, meta = fetch_all_cards(locale, filters, query=query)
    events = [parse_card(c) for c in cards]
    return json.dumps({
        "total_count": meta.get("totalCount", 0),
        "returned": len(events),
        "events": events,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_event_details(event_id: str, locale: str = DEFAULT_LOCALE) -> str:
    """Get details for a specific event by its ID.

    Args:
        event_id: The event ID (from search_events results).
        locale: API locale (default: de-de).

    Returns:
        JSON with full event details, or error if not found.
    """
    # Look up in the in-memory index (populated by previous search_events calls)
    card = _seen_cards.get((locale, event_id))

    if not card:
        # Event not seen yet — fetch all events to find it
        # (the API has no get-by-id endpoint)
        fetch_all_cards(locale, "", max_pages=50)
        card = _seen_cards.get((locale, event_id))

    if not card:
        return json.dumps({"error": f"Event '{event_id}' not found"})

    parsed = parse_card(card)
    parsed["raw_content"] = card.get("content", {})
    return json.dumps(parsed, ensure_ascii=False, indent=2)


@mcp.tool()
def list_filters(locale: str = DEFAULT_LOCALE) -> str:
    """List all available filter categories and their exact values with event counts.

    Call this tool first before using search_events or get_event_stats with
    filters. It returns the exact category:value pairs that the API accepts.

    Args:
        locale: API locale (default: de-de).

    Returns:
        JSON with filter categories (e.g. topic, product, format, region,
        audience, industry, primary-language), each containing available
        values and their event counts.
    """
    meta = fetch_page(locale, "", top=0, skip=0)
    facets = meta.get("facets", [])

    categories: dict[str, list[dict]] = {}
    for f in facets:
        fid = f.get("id", "")
        count = f.get("count", 0)
        if ":" in fid and count > 0:
            cat, val = fid.split(":", 1)
            categories.setdefault(cat, []).append({"value": val, "count": count})

    # Sort values by count descending
    for cat in categories:
        categories[cat].sort(key=lambda x: -x["count"])

    return json.dumps({
        "total_events": meta.get("totalCount", 0),
        "categories": categories,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_event_stats(filters: str = "", locale: str = DEFAULT_LOCALE) -> str:
    """Get statistics about events: counts by format, topic, product, region, etc.

    Uses the API facets for fast, accurate counts (single API call).
    Use list_filters first to discover valid filter values.

    Args:
        filters: Optional comma-separated filter string, e.g. "topic:ai".
                 Use list_filters to get all available category:value pairs.
        locale: API locale (default: de-de).

    Returns:
        JSON with event statistics broken down by various dimensions.
    """
    meta = fetch_page(locale, filters, top=0, skip=0)
    facets = meta.get("facets", [])

    categories: dict[str, list[dict]] = {}
    for f in facets:
        fid = f.get("id", "")
        count = f.get("count", 0)
        if ":" in fid:
            cat, val = fid.split(":", 1)
            categories.setdefault(cat, []).append({"name": val, "count": count})

    # Sort each category by count descending, limit to top 20
    for cat in categories:
        categories[cat].sort(key=lambda x: -x["count"])
        categories[cat] = categories[cat][:20]

    return json.dumps({
        "total_events": meta.get("totalCount", 0),
        "categories": categories,
    }, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio", log_level="ERROR")
