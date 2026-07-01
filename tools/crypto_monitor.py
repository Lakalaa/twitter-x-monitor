"""
crypto_monitor.py — Crypto news scraper + AI-style classifier
Sources: CryptoPanic (public), Reddit RSS, CoinGecko trending, Fear & Greed Index
No API keys required.
"""
from __future__ import annotations
import json
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

# ── Category keyword sets ────────────────────────────────────────────────────

_STAKING_KW = [
    "staking", "stake", "unstake", "validator", "delegation", "slashing",
    "slash", "missed reward", "missed epoch", "downtime penalty", "jailed",
    "offline validator", "withdrawal delay", "unbonding", "liquid staking",
    "restaking", "lsd", "lrt", "eigenlayer", "rocketpool", "lido",
]

_COMPLAINT_KW = [
    "exploit", "hack", "hacked", "rug", "rugpull", "rug pull", "scam",
    "fraud", "stolen", "drained", "attacked", "vulnerability", "bug",
    "critical", "emergency", "suspend", "halt", "frozen", "lost funds",
    "complaint", "issue", "problem", "broken", "failed transaction",
    "stuck", "pending", "not received", "missing", "delay", "outage",
    "down", "unavailable", "crash", "error", "alert", "warning",
]

_REWARD_KW = [
    "airdrop", "air drop", "reward", "incentive", "distribute", "distribution",
    "claim", "eligible", "snapshot", "bonus", "yield", "apr", "apy",
    "farming", "liquidity mining", "points", "season", "campaign",
    "giveaway", "retroactive", "vest", "vesting", "unlock", "TGE",
]

_DEFI_KW = [
    "defi", "dex", "amm", "liquidity pool", "lp", "tvl", "protocol",
    "bridge", "cross-chain", "swap", "lending", "borrowing", "collateral",
    "liquidation", "flash loan",
]

# Common crypto token name patterns
_TOKEN_RE = re.compile(
    r'\b([A-Z]{2,8})\b(?=\s*(?:token|coin|protocol|network|staking|airdrop|reward|price|down|up|hack))',
    re.IGNORECASE
)
_DOLLAR_TOKEN_RE = re.compile(r'\$([A-Z]{2,8})\b')

_KNOWN_TOKENS = {
    "BTC", "ETH", "SOL", "BNB", "AVAX", "MATIC", "ARB", "OP", "ADA", "DOT",
    "LINK", "UNI", "AAVE", "CRV", "MKR", "SNX", "COMP", "YFI", "SUSHI",
    "DYDX", "GMX", "PENDLE", "LDO", "RPL", "EIGEN", "TIA", "INJ", "SEI",
    "APT", "SUI", "ATOM", "OSMO", "JUNO", "NEAR", "FTM", "TON", "XRP",
    "TRX", "HBAR", "ALGO", "VET", "FIL", "SAND", "MANA", "AXS", "IMX",
    "GALA", "CHZ", "DOGE", "SHIB", "PEPE", "FLOKI", "WIF", "BONK",
    "PUMP", "PUMPFUN",
}


def _fetch(url: str, timeout: int = 10, ua: str = "Mozilla/5.0 CryptoMonitor/1.0") -> Optional[dict | list]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua,
                                                    "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def _fetch_text(url: str, timeout: int = 10, ua: str = "Mozilla/5.0 CryptoMonitor/1.0") -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


# ── AI-style scoring / classification ───────────────────────────────────────

def _score_text(text: str, keywords: list[str]) -> int:
    t = text.lower()
    return sum(1 for kw in keywords if kw in t)


def classify_item(title: str, body: str = "") -> dict:
    """Return category, priority (1-5), and extracted tokens for a news item."""
    combined = f"{title} {body}".lower()
    full_raw  = f"{title} {body}"

    staking_score   = _score_text(combined, _STAKING_KW)
    complaint_score = _score_text(combined, _COMPLAINT_KW)
    reward_score    = _score_text(combined, _REWARD_KW)
    defi_score      = _score_text(combined, _DEFI_KW)

    # Category
    if complaint_score >= 2 or any(kw in combined for kw in ("hack", "exploit", "rug", "stolen", "drained")):
        category = "complaint"
        priority = min(5, 3 + complaint_score)
    elif staking_score >= 1:
        category = "staking"
        priority = min(4, 2 + staking_score)
    elif reward_score >= 1:
        category = "reward"
        priority = min(4, 2 + reward_score)
    elif defi_score >= 1:
        category = "defi"
        priority = 2
    else:
        category = "general"
        priority = 1

    # Extract mentioned tokens
    tokens = set()
    for m in _DOLLAR_TOKEN_RE.finditer(full_raw):
        tokens.add(m.group(1).upper())
    for m in _TOKEN_RE.finditer(full_raw):
        t = m.group(1).upper()
        if t in _KNOWN_TOKENS:
            tokens.add(t)

    return {"category": category, "priority": priority, "tokens": sorted(tokens)}


# ── Source scrapers ──────────────────────────────────────────────────────────

def fetch_cryptopanic(filter_kind: str = "all", limit: int = 20) -> list[dict]:
    """
    Fetch public posts from CryptoPanic (no API key required for public feed).
    filter_kind: "all" | "rising" | "hot" | "bullish" | "bearish" | "important"
    """
    url = f"https://cryptopanic.com/api/v1/posts/?public=true&filter={filter_kind}&limit={limit}"
    data = _fetch(url)
    if not data or "results" not in data:
        return []

    items = []
    for post in data["results"]:
        title = post.get("title", "")
        body  = post.get("body") or ""
        url_  = post.get("url") or post.get("source", {}).get("url", "")
        published = post.get("published_at", "")[:10]
        currencies = [c["code"].upper() for c in (post.get("currencies") or [])]

        clf = classify_item(title, body)
        if currencies:
            for c in currencies:
                clf["tokens"].append(c)
            clf["tokens"] = sorted(set(clf["tokens"]))

        items.append({
            "source": "CryptoPanic",
            "title": title,
            "url": url_,
            "date": published,
            **clf,
        })
    return items


def fetch_reddit(subreddit: str = "CryptoCurrency", limit: int = 20) -> list[dict]:
    """Fetch new posts from a subreddit via the public JSON endpoint."""
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}"
    data = _fetch(url, ua="CryptoMonitorBot/1.0 (Telegram bot)")
    if not data:
        return []

    posts = (data.get("data") or {}).get("children", [])
    items = []
    for p in posts:
        pd = p.get("data", {})
        title = pd.get("title", "")
        body  = pd.get("selftext", "") or ""
        link  = "https://reddit.com" + pd.get("permalink", "")
        created = datetime.fromtimestamp(pd.get("created_utc", 0), tz=timezone.utc).strftime("%Y-%m-%d")
        score = pd.get("score", 0)

        clf = classify_item(title, body[:500])
        clf["priority"] = max(clf["priority"], min(3, score // 200 + 1))

        items.append({
            "source": f"r/{subreddit}",
            "title": title[:200],
            "url": link,
            "date": created,
            **clf,
        })
    return items


def fetch_coingecko_trending() -> list[dict]:
    """Fetch CoinGecko trending coins."""
    data = _fetch("https://api.coingecko.com/api/v3/search/trending")
    if not data:
        return []

    items = []
    coins = (data.get("coins") or [])[:7]
    for c in coins:
        item = c.get("item", {})
        symbol = item.get("symbol", "").upper()
        name   = item.get("name", "")
        rank   = item.get("market_cap_rank") or "?"
        items.append({
            "source": "CoinGecko Trending",
            "title": f"🔥 {name} ({symbol}) trending — market cap rank #{rank}",
            "url": f"https://www.coingecko.com/en/coins/{item.get('id','')}",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "category": "trending",
            "priority": 2,
            "tokens": [symbol] if symbol else [],
        })
    return items


def fetch_fear_greed() -> Optional[dict]:
    """Fetch the Crypto Fear & Greed Index."""
    data = _fetch("https://api.alternative.me/fng/?limit=1")
    if not data:
        return None
    d = (data.get("data") or [{}])[0]
    return {
        "value": int(d.get("value", 0)),
        "label": d.get("value_classification", "Unknown"),
        "timestamp": d.get("timestamp", ""),
    }


# ── Main fetch function ──────────────────────────────────────────────────────

def fetch_all(
    *,
    min_priority: int = 1,
    category_filter: Optional[str] = None,
    include_trending: bool = True,
    limit_per_source: int = 15,
) -> dict:
    """
    Fetch and classify crypto news from all sources.
    Returns {"items": [...], "fear_greed": {...}, "fetched_at": "..."}
    Items sorted by priority desc then date desc.
    """
    items: list[dict] = []

    items += fetch_cryptopanic("hot", limit_per_source)
    items += fetch_reddit("CryptoCurrency", limit_per_source)
    items += fetch_reddit("defi", limit_per_source // 2)

    if include_trending:
        items += fetch_coingecko_trending()

    # De-duplicate by title similarity
    seen: set[str] = set()
    unique: list[dict] = []
    for it in items:
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(it)

    # Apply filters
    if category_filter and category_filter != "all":
        unique = [i for i in unique if i["category"] == category_filter]
    unique = [i for i in unique if i["priority"] >= min_priority]

    # Sort: priority desc, date desc
    unique.sort(key=lambda x: (x["priority"], x["date"]), reverse=True)

    fg = fetch_fear_greed()

    return {
        "items": unique,
        "fear_greed": fg,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ── Telegram formatter ───────────────────────────────────────────────────────

_CAT_EMOJI = {
    "complaint": "🚨",
    "staking":   "🥩",
    "reward":    "🎁",
    "defi":      "⚗️",
    "trending":  "🔥",
    "general":   "📰",
}

_PRIORITY_BAR = {1: "▫️", 2: "🔵", 3: "🟡", 4: "🟠", 5: "🔴"}


def format_items_for_telegram(
    items: list[dict],
    fear_greed: Optional[dict],
    fetched_at: str,
    header: str = "📡 <b>Crypto Intelligence Report</b>",
    max_items: int = 15,
) -> list[str]:
    """
    Format fetched items into Telegram HTML message chunks (max 4096 chars each).
    Returns a list of message strings to send sequentially.
    """
    lines: list[str] = []

    # Header
    lines.append(header)
    lines.append(f"🕐 {fetched_at}")
    if fear_greed:
        v   = fear_greed["value"]
        lbl = fear_greed["label"]
        bar = "🟢" if v >= 60 else ("🟡" if v >= 40 else "🔴")
        lines.append(f"{bar} Fear &amp; Greed: <b>{v}/100</b> — {lbl}")
    lines.append("")

    shown = items[:max_items]

    # Group by category
    by_cat: dict[str, list[dict]] = {}
    for it in shown:
        by_cat.setdefault(it["category"], []).append(it)

    cat_order = ["complaint", "staking", "reward", "defi", "trending", "general"]
    for cat in cat_order:
        group = by_cat.get(cat, [])
        if not group:
            continue
        emoji = _CAT_EMOJI.get(cat, "📌")
        cat_label = cat.upper()
        lines.append(f"{emoji} <b>{cat_label}</b>")
        for it in group:
            pri   = _PRIORITY_BAR.get(it["priority"], "▫️")
            title = it["title"][:160].replace("<", "&lt;").replace(">", "&gt;")
            url   = it.get("url", "")
            tok   = ("  <i>" + " ".join(f"${t}" for t in it["tokens"][:4]) + "</i>") if it["tokens"] else ""
            src   = it.get("source", "")
            date  = it.get("date", "")
            if url:
                lines.append(f"{pri} <a href='{url}'>{title}</a>{tok}")
            else:
                lines.append(f"{pri} {title}{tok}")
            lines.append(f"    └ {src} · {date}")
        lines.append("")

    if not shown:
        lines.append("No items matched the filter.")

    # Split into ≤4096-char chunks
    chunks: list[str] = []
    current = ""
    for line in lines:
        addition = line + "\n"
        if len(current) + len(addition) > 4000:
            if current:
                chunks.append(current.rstrip())
            current = addition
        else:
            current += addition
    if current.strip():
        chunks.append(current.rstrip())

    return chunks or ["No crypto news found."]


def build_digest(
    category_filter: Optional[str] = None,
    min_priority: int = 1,
    max_items: int = 20,
) -> tuple[list[str], int]:
    """
    High-level: fetch + format. Returns (message_chunks, item_count).
    category_filter: None/"all"/"complaint"/"staking"/"reward"/"defi"/"trending"
    """
    cat = category_filter if category_filter != "all" else None
    data = fetch_all(
        min_priority=min_priority,
        category_filter=cat,
        include_trending=(cat in (None, "trending")),
    )
    items = data["items"]
    chunks = format_items_for_telegram(
        items,
        data["fear_greed"],
        data["fetched_at"],
        header=_header_for_filter(category_filter),
        max_items=max_items,
    )
    return chunks, len(items)


def _header_for_filter(f: Optional[str]) -> str:
    headers = {
        "complaint": "🚨 <b>Crypto Alerts — Hacks, Scams &amp; Issues</b>",
        "staking":   "🥩 <b>Staking News &amp; Issues</b>",
        "reward":    "🎁 <b>Crypto Rewards &amp; Airdrops</b>",
        "defi":      "⚗️ <b>DeFi News</b>",
        "trending":  "🔥 <b>Trending Tokens</b>",
    }
    return headers.get(f or "", "📡 <b>Crypto Intelligence Report</b>")
