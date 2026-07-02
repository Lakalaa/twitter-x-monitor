"""
twitter_feed_monitor.py
Watches specific X/Twitter account timelines and classifies tweets as:
  - announcement  : official news, launches, updates
  - link          : important links, blog posts, docs
  - complaint     : issues reported by the account or replies from users
  - admin         : pinned/important admin messages
  - general       : regular tweet (filtered out by default)

Uses Twitter v1.1 statuses/user_timeline — no extra libraries needed.
"""
from __future__ import annotations
import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

# ── Classification keywords ──────────────────────────────────────────────────

_ANNOUNCE_KW = [
    "announcing", "announcement", "we're live", "just launched", "introducing",
    "new release", "v2", "v3", "update", "upgrade", "migration",
    "mainnet", "testnet", "integrat", "partner", "partnership",
    "listed", "listing", "ama", "join us", "go live", "now live",
    "important", "breaking", "major", "milestone",
    "📢", "📣", "🔔", "🚀", "🎉", "🎊", "🔥", "⚡", "🌟", "✅",
]

_LINK_KW = [
    "read more", "blog post", "full post", "article", "medium.com",
    "docs", "documentation", "tutorial", "guide", "learn more",
    "whitepaper", "report", "research", "audit", "security report",
    "github", "proposal", "vote", "governance", "snapshot.org",
    "forum", "thread", "👇", "🔗", "📄", "📝",
]

_COMPLAINT_KW = [
    "issue", "bug", "fix", "fixed", "resolved", "patch", "hotfix",
    "down", "outage", "degraded", "maintenance", "investigating",
    "incident", "postmortem", "root cause", "we are aware",
    "we're aware", "we are looking", "looking into", "working on",
    "sorry", "apolog", "inconvenien", "delay", "slow", "stuck",
    "failed", "error", "not working", "paused", "halt",
    "⚠️", "🛑", "❌", "🔴", "🚨",
]

_ADMIN_KW = [
    "pinned", "admin", "team", "official", "ceo", "cto", "founder",
    "reminder", "psa", "heads up", "fyi", "notice",
    "📌", "🗣️",
]

_USER_ISSUE_KW = [
    "help", "support", "can't", "cannot", "doesn't work", "not working",
    "why is", "why isn't", "anyone else", "same issue", "same problem",
    "when will", "where is", "lost", "missing", "stuck", "refund",
    "scam", "rugpull", "rug", "exit", "drained", "please fix",
    "still down", "still not", "broken", "failed",
]


def _score(text: str, keywords: list[str]) -> int:
    t = text.lower()
    return sum(1 for kw in keywords if kw in t)


def classify_tweet(text: str, is_reply: bool = False,
                   is_retweet: bool = False) -> dict:
    """Return {category, priority, is_reply, is_retweet}."""
    ann   = _score(text, _ANNOUNCE_KW)
    link  = _score(text, _LINK_KW)
    comp  = _score(text, _COMPLAINT_KW)
    admin = _score(text, _ADMIN_KW)
    user_issue = _score(text, _USER_ISSUE_KW)

    # Replies FROM users to the account are flagged as user issues
    if is_reply and user_issue >= 1:
        return {"category": "user_issue", "priority": 3}

    if comp >= 2 or any(kw in text.lower() for kw in
                        ("outage", "incident", "investigating", "we are aware",
                         "we're aware", "paused", "halted", "postmortem")):
        return {"category": "complaint", "priority": 4}

    if ann >= 2:
        return {"category": "announcement", "priority": 4}

    if ann >= 1:
        return {"category": "announcement", "priority": 3}

    if admin >= 1:
        return {"category": "admin", "priority": 3}

    if link >= 2:
        return {"category": "link", "priority": 2}

    if link >= 1:
        return {"category": "link", "priority": 2}

    if comp >= 1:
        return {"category": "complaint", "priority": 2}

    if user_issue >= 1 and is_reply:
        return {"category": "user_issue", "priority": 2}

    return {"category": "general", "priority": 1}


def _has_url(text: str) -> bool:
    return bool(re.search(r'https?://', text))


# ── Twitter v1.1 API fetch ───────────────────────────────────────────────────

_BASE = "https://api.twitter.com/1.1"


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


def _fetch_timeline(username: str, auth_token: str, ct0: str,
                    count: int = 30, since_id: Optional[str] = None) -> list[dict]:
    """Fetch recent tweets from a user's timeline via v1.1 API."""
    params = (
        f"screen_name={username}&count={count}&tweet_mode=extended"
        f"&include_rts=true&exclude_replies=false"
    )
    if since_id:
        params += f"&since_id={since_id}"

    url = f"{_BASE}/statuses/user_timeline.json?{params}"
    hdrs = _headers(auth_token, ct0)

    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read()[:300]
        print(f"[feed] HTTP {e.code} for @{username}: {body}")
        return []
    except Exception as e:
        print(f"[feed] Error fetching @{username}: {e}")
        return []


def _fetch_replies_to(username: str, auth_token: str, ct0: str,
                      count: int = 20) -> list[dict]:
    """Search for recent replies directed AT an account (user issues/complaints)."""
    query = f"to:{username}"
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
    except Exception as e:
        print(f"[feed] Error fetching replies to @{username}: {e}")
        return []


def _tweet_text(tw: dict) -> str:
    """Extract full text (handles retweets and extended tweets)."""
    rt = tw.get("retweeted_status")
    if rt:
        return rt.get("full_text") or rt.get("text", "")
    return tw.get("full_text") or tw.get("text", "")


def _tweet_url(tw: dict) -> str:
    uid  = tw.get("user", {}).get("screen_name", "")
    tid  = tw.get("id_str", "")
    return f"https://twitter.com/{uid}/status/{tid}" if uid and tid else ""


def _parse_date(tw: dict) -> str:
    created = tw.get("created_at", "")
    try:
        dt = datetime.strptime(created, "%a %b %d %H:%M:%S +0000 %Y")
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return created[:10] if created else ""


# ── Main feed fetch function ─────────────────────────────────────────────────

def fetch_feed(
    usernames: list[str],
    auth_token: str,
    ct0: str,
    since_ids: Optional[dict] = None,
    min_priority: int = 2,
    include_replies: bool = True,
) -> dict:
    """
    Fetch and classify tweets from multiple accounts.
    since_ids: {username: last_seen_tweet_id} for incremental fetching.
    Returns {
        "items": [...classified tweets...],
        "new_since_ids": {username: latest_id},
        "fetched_at": "..."
    }
    """
    since_ids = since_ids or {}
    items: list[dict] = []
    new_since_ids: dict = dict(since_ids)

    for username in usernames:
        since_id = since_ids.get(username.lower())
        tweets = _fetch_timeline(username, auth_token, ct0, count=30,
                                 since_id=since_id)

        if tweets:
            latest_id = tweets[0].get("id_str")
            if latest_id:
                new_since_ids[username.lower()] = latest_id

        for tw in tweets:
            text     = _tweet_text(tw)
            is_rt    = bool(tw.get("retweeted_status"))
            is_reply = bool(tw.get("in_reply_to_screen_name"))
            clf      = classify_tweet(text, is_reply=is_reply, is_retweet=is_rt)

            if clf["priority"] < min_priority:
                continue

            items.append({
                "account":   username,
                "tweet_id":  tw.get("id_str", ""),
                "text":      text[:500],
                "url":       _tweet_url(tw),
                "date":      _parse_date(tw),
                "is_reply":  is_reply,
                "is_rt":     is_rt,
                "likes":     tw.get("favorite_count", 0),
                "retweets":  tw.get("retweet_count", 0),
                "has_url":   _has_url(text),
                **clf,
            })

        # Also fetch user replies directed AT this account
        if include_replies:
            replies = _fetch_replies_to(username, auth_token, ct0, count=15)
            for tw in replies:
                sender = tw.get("user", {}).get("screen_name", "")
                if sender.lower() == username.lower():
                    continue  # skip the account's own tweets
                text  = _tweet_text(tw)
                clf   = classify_tweet(text, is_reply=True)
                if clf["priority"] < min_priority:
                    continue
                items.append({
                    "account":   username,
                    "from_user": sender,
                    "tweet_id":  tw.get("id_str", ""),
                    "text":      text[:400],
                    "url":       _tweet_url(tw),
                    "date":      _parse_date(tw),
                    "is_reply":  True,
                    "is_rt":     False,
                    "likes":     tw.get("favorite_count", 0),
                    "retweets":  tw.get("retweet_count", 0),
                    "has_url":   _has_url(text),
                    **clf,
                })

    # De-dup by tweet_id
    seen_ids: set = set()
    unique: list[dict] = []
    for it in items:
        tid = it.get("tweet_id", "")
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            unique.append(it)

    # Sort: priority desc, then date desc
    unique.sort(key=lambda x: (x["priority"], x["date"]), reverse=True)

    return {
        "items": unique,
        "new_since_ids": new_since_ids,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ── Telegram formatter ───────────────────────────────────────────────────────

_CAT_HEADER = {
    "announcement": "📢 ANNOUNCEMENT",
    "link":         "🔗 IMPORTANT LINK",
    "complaint":    "⚠️ ISSUE / COMPLAINT",
    "admin":        "📌 ADMIN MESSAGE",
    "user_issue":   "🆘 USER ISSUE",
    "general":      "📄 UPDATE",
}

_PRIORITY_ICON = {1: "▪️", 2: "🔵", 3: "🟡", 4: "🟠", 5: "🔴"}


def format_tweet_for_telegram(item: dict) -> str:
    """Format a single classified tweet as a Telegram HTML message."""
    cat      = item.get("category", "general")
    priority = item.get("priority", 1)
    account  = item.get("account", "")
    text     = item.get("text", "")
    url      = item.get("url", "")
    date     = item.get("date", "")
    from_u   = item.get("from_user", "")
    likes    = item.get("likes", 0)
    rts      = item.get("retweets", 0)

    header = _CAT_HEADER.get(cat, "📄 UPDATE")
    pri    = _PRIORITY_ICON.get(priority, "▪️")

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Who posted
    if from_u:
        who = f'👤 <a href="https://twitter.com/{from_u}">@{esc(from_u)}</a> → <a href="https://twitter.com/{account}">@{esc(account)}</a>'
    else:
        who = f'<a href="https://twitter.com/{account}">@{esc(account)}</a>'

    # Stats
    stats = ""
    if likes or rts:
        stats = f"❤️ {likes:,}  🔁 {rts:,}"

    # Build message
    lines = [
        f"{pri} <b>{header}</b>",
        who,
        f'<a href="{url}">{esc(text[:300])}</a>' if url else esc(text[:300]),
    ]
    if stats:
        lines.append(stats)
    lines.append(f"<i>🕐 {date}</i>")

    return "\n".join(lines)
