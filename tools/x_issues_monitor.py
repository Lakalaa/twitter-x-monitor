"""
x_issues_monitor.py
Scrapes X/Twitter broadly (not tied to any specific account) for trending
"issue" tweets across categories: staking, yield/rewards, AI (agents/tokens),
and general trending crypto issues. Only tweets that mention an actual
token/cashtag (e.g. $SOL, $PEPE) are surfaced — plain chatter with no token
name is skipped.

Uses the same Twitter v1.1 search/tweets.json endpoint as twitter_feed_monitor.py.
"""
from __future__ import annotations
import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

_BASE = "https://api.twitter.com/1.1"

# ── Search queries per category ──────────────────────────────────────────────
# Each query targets X natively for issue-style chatter. min_faves keeps out
# pure noise. Twitter's search operators: min_faves, -filter:retweets, lang:en

_QUERIES: dict[str, list[str]] = {
    "staking": [
        "staking issue OR staking bug OR validator down OR slashing (min_faves:3) -filter:retweets lang:en",
        "unstake stuck OR unstaking delay OR staking rewards not showing (min_faves:2) -filter:retweets lang:en",
    ],
    "yield_reward": [
        "yield exploit OR vault drained OR farm hack OR rewards not claiming (min_faves:3) -filter:retweets lang:en",
        "airdrop scam OR rewards missing OR claim not working (min_faves:2) -filter:retweets lang:en",
    ],
    "ai": [
        "AI agent scam OR AI token rug OR AI agent exploit (min_faves:3) -filter:retweets lang:en",
        "AI crypto issue OR AI token bug OR agent wallet drained (min_faves:2) -filter:retweets lang:en",
    ],
    "trending": [
        "crypto exploit OR crypto hack OR rug pull (min_faves:10) -filter:retweets lang:en",
        "depeg OR bridge hack OR smart contract exploit (min_faves:5) -filter:retweets lang:en",
    ],
}

_CAT_HEADER = {
    "staking":     "🥩 STAKING ISSUE",
    "yield_reward": "💰 YIELD/REWARD ISSUE",
    "ai":          "🤖 AI TOKEN ISSUE",
    "trending":    "🔥 TRENDING ISSUE",
}

# Cashtag / token-name pattern: $ABC (2-10 letters/digits)
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


def _headers(auth_token: str, ct0: str) -> dict:
    return {
        "Authorization": f"Bearer {_bearer_from_env()}",
        "Cookie": f"auth_token={auth_token}; ct0={ct0}",
        "x-csrf-token": ct0,
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "en",
        "x-twitter-active-user": "yes",
    }


def _bearer_from_env() -> str:
    return os.environ.get(
        "TWITTER_BEARER",
        "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
        "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
    )


def _search(query: str, auth_token: str, ct0: str, count: int = 20) -> list[dict]:
    params = (
        f"q={urllib.request.pathname2url(query)}&count={count}"
        f"&tweet_mode=extended&result_type=recent"
    )
    url = f"{_BASE}/search/tweets.json?{params}"
    hdrs = _headers(auth_token, ct0)
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
            return data.get("statuses", [])
    except urllib.error.HTTPError as e:
        print(f"[x_issues] HTTP {e.code} for query '{query[:40]}...': {e.read()[:200]}")
        return []
    except Exception as e:
        print(f"[x_issues] Error for query '{query[:40]}...': {e}")
        return []


def _tweet_text(tw: dict) -> str:
    rt = tw.get("retweeted_status")
    if rt:
        return rt.get("full_text") or rt.get("text", "")
    return tw.get("full_text") or tw.get("text", "")


def _tweet_url(tw: dict) -> str:
    uid = tw.get("user", {}).get("screen_name", "")
    tid = tw.get("id_str", "")
    return f"https://twitter.com/{uid}/status/{tid}" if uid and tid else ""


def _parse_date(tw: dict) -> str:
    created = tw.get("created_at", "")
    try:
        dt = datetime.strptime(created, "%a %b %d %H:%M:%S +0000 %Y")
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return created[:10] if created else ""


def fetch_issues(
    auth_token: str,
    ct0: str,
    categories: Optional[list[str]] = None,
    seen_ids: Optional[set] = None,
    per_query_count: int = 20,
) -> list[dict]:
    """
    Search X broadly across issue categories. Only returns tweets that
    mention a real token/cashtag (e.g. $SOL) — everything else is dropped.
    """
    categories = categories or list(_QUERIES.keys())
    seen_ids = seen_ids or set()
    items: list[dict] = []
    dedup: set = set()

    for cat in categories:
        for query in _QUERIES.get(cat, []):
            tweets = _search(query, auth_token, ct0, count=per_query_count)
            for tw in tweets:
                tid = tw.get("id_str", "")
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
                    "date":      _parse_date(tw),
                    "user":      tw.get("user", {}).get("screen_name", ""),
                    "likes":     tw.get("favorite_count", 0),
                    "retweets":  tw.get("retweet_count", 0),
                    "tokens":    tokens,
                })

    items.sort(key=lambda x: (x["likes"] + x["retweets"], x["date"]), reverse=True)
    return items


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
        f'👤 <a href="https://twitter.com/{user}">@{esc(user)}</a>',
        f'<a href="{url}">{esc(text[:300])}</a>' if url else esc(text[:300]),
        f"❤️ {likes:,}  🔁 {rts:,}",
        f"<i>🕐 {date}</i>",
    ]
    return "\n".join(lines)
