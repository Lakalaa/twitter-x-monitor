"""
x_issues_monitor.py
Monitors top crypto X/Twitter accounts for trending issues, price moves,
DeFi updates, and token news. Sends matched tweets to Telegram.

Uses direct UserTweets GraphQL endpoint (works from datacenter IPs) instead
of SearchTimeline (which is blocked by Cloudflare on cloud server IPs).
"""
from __future__ import annotations
import re
from typing import Optional

from x_scraper import fetch_tweets_from_accounts

_CASHTAG_RE   = re.compile(r"\$[A-Za-z][A-Za-z0-9]{1,9}\b")
_CRYPTO_KW_RE = re.compile(
    r"\b(bitcoin|ethereum|solana|bnb|crypto|defi|nft|token|blockchain|"
    r"altcoin|memecoin|airdrop|staking|yield|liquidit|protocol|rollup|"
    r"layer2|l2|smart.?contract|on.?chain|dex|cex|wallet|hack|exploit|"
    r"rug.?pull|pump|dump|listing|launch|upgrade|fork|halving|etf|"
    r"btc|eth|sol|usdt|usdc|matic|avax|dot|ada|xrp|doge|shib|pepe)\b",
    re.IGNORECASE,
)

_CATEGORIES = {
    "bitcoin":   ["Bitcoin", "DocumentingBTC", "BitcoinMagazine", "saylor"],
    "ethereum":  ["ethereum", "VitalikButerin", "sassal0x", "ultrasoundmoney"],
    "defi":      ["DefiLlama", "AaveAave", "Uniswap", "MakerDAO", "CurveFinance"],
    "altcoins":  ["solana", "Polkadot", "Avalancheavax", "cosmos"],
    "market":    ["WatcherGuru", "lookonchain", "CryptoCapo_", "inversebrah"],
    "exchanges": ["binance", "cz_binance", "coinbase", "Bybit_Official"],
}

_ALL_ACCOUNTS: list[str] = []
for _accs in _CATEGORIES.values():
    for _a in _accs:
        if _a not in _ALL_ACCOUNTS:
            _ALL_ACCOUNTS.append(_a)

_CAT_HEADER = {
    "bitcoin":   "₿ BITCOIN UPDATE",
    "ethereum":  "⟠ ETHEREUM UPDATE",
    "defi":      "🏦 DEFI UPDATE",
    "altcoins":  "🔵 ALTCOIN NEWS",
    "market":    "📊 MARKET ALERT",
    "exchanges": "🏛️ EXCHANGE NEWS",
    "misc":      "🔥 CRYPTO UPDATE",
}

_ACCOUNT_TO_CAT: dict[str, str] = {}
for _cat, _accs in _CATEGORIES.items():
    for _a in _accs:
        _ACCOUNT_TO_CAT[_a.lower()] = _cat


def _is_crypto_relevant(text: str) -> bool:
    return bool(_CASHTAG_RE.search(text) or _CRYPTO_KW_RE.search(text))


def extract_tokens(text: str) -> list[str]:
    found = _CASHTAG_RE.findall(text)
    seen, out = set(), []
    for t in found:
        u = t.upper()
        if u not in seen:
            seen.add(u)
            out.append(t)
    return out


def _guess_category(tweet: dict) -> str:
    user = tweet.get("user", "").lower()
    return _ACCOUNT_TO_CAT.get(user, "misc")


def fetch_issues(
    seen_ids: Optional[set] = None,
    per_account: int = 15,
) -> list[dict]:
    """
    Fetch recent tweets from curated crypto accounts.
    Returns only crypto-relevant, unseen tweets sorted by engagement.
    """
    seen_ids = seen_ids or set()
    raw = fetch_tweets_from_accounts(_ALL_ACCOUNTS, tweets_per_account=per_account)

    items = []
    for tw in raw:
        tid  = tw.get("id", "")
        text = tw.get("text", "")
        if not tid or tid in seen_ids:
            continue
        if not _is_crypto_relevant(text):
            continue
        tokens = extract_tokens(text)
        cat    = _guess_category(tw)
        items.append({
            "category":  cat,
            "tweet_id":  tid,
            "text":      text[:500],
            "url":       tw.get("url", ""),
            "date":      tw.get("date", ""),
            "user":      tw.get("user", ""),
            "likes":     tw.get("likes", 0),
            "retweets":  tw.get("retweets", 0),
            "tokens":    tokens,
        })

    items.sort(key=lambda x: (x["likes"] + x["retweets"]), reverse=True)
    return items


async def afetch_issues(
    scraper=None,
    categories=None,
    seen_ids: Optional[set] = None,
    per_query_count: int = 20,
) -> list[dict]:
    """Async wrapper — scraper arg kept for backward compat, not used."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fetch_issues(seen_ids=seen_ids, per_account=per_query_count))


def format_issue_for_telegram(item: dict) -> str:
    cat    = item.get("category", "misc")
    header = _CAT_HEADER.get(cat, "🔥 CRYPTO UPDATE")
    text   = item.get("text", "")
    url    = item.get("url", "")
    date   = item.get("date", "")
    user   = item.get("user", "")
    tokens = item.get("tokens", [])
    likes  = item.get("likes", 0)
    rts    = item.get("retweets", 0)

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    tok_line = " ".join(f"<b>{esc(t.upper())}</b>" for t in tokens)

    lines = [
        f"🔴 <b>{header}</b>",
        tok_line if tok_line else "",
        f'👤 <a href="https://x.com/{user}">@{esc(user)}</a>' if user else "",
        f'<a href="{url}">{esc(text[:300])}</a>' if url else esc(text[:300]),
        f"❤️ {likes:,}  🔁 {rts:,}",
        f"<i>🕐 {date}</i>" if date else "",
    ]
    return "\n".join(l for l in lines if l)
