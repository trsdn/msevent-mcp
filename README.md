# Microsoft Events MCP Server

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-compatible-green?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJ3aGl0ZSI+PHBhdGggZD0iTTEyIDJMMiA3djEwbDEwIDUgMTAtNVY3eiIvPjwvc3ZnPg==)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-43%20passed-brightgreen?logo=pytest&logoColor=white)](#tests)
[![Coverage](https://img.shields.io/badge/coverage-99%25-brightgreen?logo=codecov&logoColor=white)](#tests)
[![FastMCP](https://img.shields.io/badge/FastMCP-powered-purple)](https://github.com/jlowin/fastmcp)

An MCP server that exposes the **Microsoft Events API** as tools for AI assistants. Search, filter, and analyze Microsoft events (conferences, workshops, webinars) directly from Claude, Cursor, or any MCP-compatible client.

---

## Features

- **Search Events** &mdash; Full-text search with filters by topic, product, region, format, and audience
- **Event Details** &mdash; Retrieve complete event information by ID
- **Filter Discovery** &mdash; List all available filter categories and values with event counts
- **Event Statistics** &mdash; Aggregated stats by country, city, format, topic, and more
- **Multi-Locale** &mdash; Supports `de-de`, `en-us`, and other Microsoft API locales
- **In-Memory Cache** &mdash; Events are indexed on first fetch for fast subsequent lookups

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_events` | Search events with optional filters and free-text query |
| `get_event_details` | Get full details for a specific event by ID |
| `list_filters` | List all available filter categories with counts |
| `get_event_stats` | Get aggregated statistics about events |

## Quick Start

### Prerequisites

- Python 3.10+
- `pip install fastmcp`

### Installation

```bash
git clone https://github.com/your-user/msevent-mcp.git
cd msevent-mcp
pip install -r requirements.txt
```

### Run

```bash
python mcp_server.py
```

The server starts on **stdio** transport and is ready for MCP client connections.

### Claude Desktop Configuration

Add this to your Claude Desktop MCP config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "microsoft-events": {
      "command": "python3",
      "args": ["/path/to/msevent-mcp/mcp_server.py"]
    }
  }
}
```

### Claude Code Configuration

```bash
claude mcp add microsoft-events python3 /path/to/msevent-mcp/mcp_server.py
```

## Usage Examples

### Search for AI events in Germany

```
search_events(filters="topic:ai,region:europe", locale="de-de")
```

### Get all available filters

```
list_filters(locale="en-us")
```

### Get event statistics

```
get_event_stats(filters="format:digital")
```

## Tests

Run the unit tests (no network required):

```bash
pip install pytest pytest-cov
python3 -m pytest test_mcp_server.py -v -m "not live"
```

Run with coverage report:

```bash
python3 -m pytest test_mcp_server.py -m "not live" --cov=mcp_server --cov-report=term-missing
```

Run live API tests (requires network):

```bash
python3 -m pytest test_mcp_server.py -v -m "live"
```

### Test Coverage

```
Name            Stmts   Miss  Cover
---------------------------------------------
mcp_server.py      95      1    99%
```

## Architecture

```
mcp_server.py          # MCP server with 4 tools
test_mcp_server.py     # 43 unit tests + 4 live API tests
requirements.txt       # Dependencies (fastmcp)
pytest.ini             # Test configuration
```

The server uses `urllib.request` for HTTP calls (no additional dependencies beyond FastMCP) and maintains an in-memory event cache for the lifetime of the process.

## API Reference

The server communicates with the [Microsoft Events API](https://www.microsoft.com/msonecloudapi/events/cards) via POST requests. No API key is required.

### Filter Categories

| Category | Example Values |
|----------|---------------|
| `topic` | `ai`, `security`, `cloud`, `data` |
| `product` | `azure`, `m365`, `dynamics` |
| `region` | `europe`, `north-america`, `asia` |
| `format` | `digital`, `in-person`, `hybrid` |
| `audience` | `developer`, `it-pro`, `business` |

---

Built with [FastMCP](https://github.com/jlowin/fastmcp) and the [Model Context Protocol](https://modelcontextprotocol.io/).
