# twscrape

## Overview
A Python CLI library and package for scraping Twitter/X data using the Twitter GraphQL and Search APIs. Supports multiple authorized accounts to bypass rate limits, with SNScrape-compatible data models.

## Tech Stack
- **Language**: Python 3.10+
- **HTTP Client**: `httpx` (async)
- **Database**: `aiosqlite` (SQLite for account session storage)
- **HTML Parsing**: `beautifulsoup4`
- **2FA/Auth**: `pyotp`
- **Logging**: `loguru`
- **CLI Entry Point**: `twscrape.cli:run`
- **Build System**: `hatchling`
- **Testing**: `pytest`, `pytest-asyncio`, `pytest-httpx`

## Project Structure
- `twscrape/` — Core library package
  - `api.py` — Main high-level API
  - `accounts_pool.py` — Multi-account management and rate limiting
  - `cli.py` — CLI implementation
  - `db.py` — SQLite persistence layer
  - `models.py` — Tweet/User data models
  - `login.py` / `imap.py` — Login flow and email verification
- `tests/` — Test suite with mocked API responses
- `examples/` — Usage examples
- `pyproject.toml` — Project metadata and dependencies

## Installation
```bash
pip install -e ".[dev]"
```

## Usage
```bash
twscrape --help
twscrape add_accounts accounts.txt username:password:email:email_password
twscrape login_accounts
twscrape search "query" --limit 20
```

## Running Tests
```bash
pytest tests/ -q
```

## Extra Tools (tools/ folder)
Three additional GitHub tools have been integrated alongside twscrape:

| Tool | Package | Best For |
|---|---|---|
| **Scweet** | `Scweet` | Bulk tweet/follower scraping, multi-account, updated Apr 2026 |
| **twitter-api-client** | `twitter-api-client` | Write actions, Spaces audio, media download, batch queries |
| **TweeterPy** | `tweeterpy` | Simple profile lookups, easy username/password login |

See `tools/README.md` for full comparison and usage guide.

## Notes
- This is a CLI tool/library with no frontend or web server
- Requires authorized Twitter/X accounts to function
- No workflow is configured since this is not a web application
- Private/locked accounts cannot be accessed by any tool — this is a server-side restriction by Twitter/X
