"""
x_issues_monitor.py
Scrapes X/Twitter broadly (not tied to any specific account) for trending
"issue" tweets across categories: staking, yield/rewards, AI (agents/tokens),
and general trending crypto issues. Only tweets that mention an actual
token/cashtag (e.g. $SOL, $PEPE) are surfaced — plain chatter with no token
name is skipped.

Uses the Scweet GraphQL client (same engine the dashboard's follower/following
scraping uses) — the old Twitter v1.1 search/tweets.json endpoint was shut
down by X and always returns 404.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Optional

# ── Search queries per category ──────────────────────────────────────────────

_QUERIES: dict[str, list[str]] = {
    "trending": [
        "crypto trending OR crypto news OR crypto update",
        "bitcoin news OR ethereum news OR altcoin trending",
        "DeFi update OR DeFi news OR protocol update",
        "crypto market OR crypto price OR token launch",
    ],
    "defi": [
        "DeFi issue OR liquidity problem OR protocol down",
        "staking update OR validator update OR network congestion",
        "yield farming OR staking rewards OR APY update",
    ],
    "layer1_layer2": [
        "Ethereum update OR Solana update OR BNB update",
        "L2 update OR rollup news OR layer2 issue",
        "network outage OR chain issue OR blockchain down",
    ],
    "tokens": [
        "token listing OR new token OR token update",
        "memecoin trending OR altcoin pump OR coin news",
        "crypto airdrop OR token distribution OR snapshot",
    ],
}

_CAT_HEADER = {
    "trending":      "🔥 TRENDING CRYPTO",
    "defi":          "🏦 DEFI UPDATE",
    "layer1_layer2": "⛓️ NETWORK UPDATE",
    "tokens":        "🪙 TOKEN NEWS",
}

_CASHTAG_RE = re.compile(r"\$[A-Za-z][A-Za-z0-9]{1,9}\b")


def extract_tokens(text: str) -> list[str]:
    """Return unique cashtag-style token symbols found in text, e.g. ['$SOL']."""
    found = _CASHTAG_RE.findall(text)
    seen, out = set(), []
    for t in found:
        u = t.upper()
        if u not in seen:
            seen.add(u)
            out.append(t)
    return out


def _tweet_text(tw: dict) -> str:
    return tw.get("text") or tw.get("rawContent", "") or ""


def _tweet_id(tw: dict) -> str:
    return str(tw.get("id") or tw.get("tweet_id") or tw.get("id_str") or "")


def _tweet_url(tw: dict) -> str:
    return tw.get("tweet_url") or tw.get("url") or ""


def _tweet_date(tw: dict) -> str:
    ts = tw.get("timestamp") or tw.get("date") or tw.get("created_at") or ""
    return str(ts)[:19].replace("T", " ") if ts else ""


def _tweet_user(tw: dict) -> str:
    u = tw.get("user", {}) or {}
    return u.get("screen_name") or tw.get("username", "") or ""


def _tweet_likes(tw: dict) -> int:
    return tw.get("likes", tw.get("likeCount", 0)) or 0


def _tweet_retweets(tw: dict) -> int:
    return tw.get("retweets", tw.get("retweetCount", 0)) or 0


async def afetch_issues(
    scraper,
    categories: Optional[list[str]] = None,
    seen_ids: Optional[set] = None,
    per_query_count: int = 20,
) -> list[dict]:
    """
    Search X broadly across issue categories using an already-constructed
    Scweet client (scraper). Only returns tweets that mention a real
    token/cashtag (e.g. $SOL) — everything else is dropped.
    """
    categories = categories or list(_QUERIES.keys())
    seen_ids = seen_ids or set()
    items: list[dict] = []
    dedup: set = set()

    for cat in categories:
        for query in _QUERIES.get(cat, []):
            try:
                tweets = await scraper.asearch(query=query, limit=per_query_count, save=False)
            except Exception as e:
                print(f"[x_issues] Error for query '{query[:40]}...': {e}")
                continue

            for tw in tweets:
                tid = _tweet_id(tw)
                if not tid or tid in seen_ids or tid in dedup:
                    continue
                text = _tweet_text(tw)
                tokens = extract_tokens(text)
                if not tokens:
                    continue  # skip — no token name mentioned
                dedup.add(tid)
                items.append({
                    "category":  cat,
                    "tweet_id":  tid,
                    "text":      text[:500],
                    "url":       _tweet_url(tw),
                    "date":      _tweet_date(tw),
                    "user":      _tweet_user(tw),
                    "likes":     _tweet_likes(tw),
                    "retweets":  _tweet_retweets(tw),
                    "tokens":    tokens,
                })

    items.sort(key=lambda x: (x["likes"] + x["retweets"], x["date"]), reverse=True)
    return items


def fetch_issues(scraper, **kwargs) -> list[dict]:
    """Sync wrapper — only call this from a context with NO running event loop."""
    import asyncio
    return asyncio.run(afetch_issues(scraper, **kwargs))


def format_issue_for_telegram(item: dict) -> str:
    cat    = item.get("category", "trending")
    header = _CAT_HEADER.get(cat, "🔥 TRENDING ISSUE")
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
        f"{tok_line}",
        f'👤 <a href="https://twitter.com/{user}">@{esc(user)}</a>' if user else "",
        f'<a href="{url}">{esc(text[:300])}</a>' if url else esc(text[:300]),
        f"❤️ {likes:,}  🔁 {rts:,}",
        f"<i>🕐 {date}</i>",
    ]
    return "\n".join(l for l in lines if l)
