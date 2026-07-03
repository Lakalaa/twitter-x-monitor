"""
twitter_feed_monitor.py
Watches specific X/Twitter account timelines and classifies tweets as:
  - announcement  : official news, launches, updates
  - link          : important links, blog posts, docs
  - complaint     : issues reported by the account or replies from users
  - admin         : pinned/important admin messages
  - general       : regular tweet (filtered out by default)

Uses the Scweet GraphQL client (same engine the dashboard's follower/following
scraping uses) — the old Twitter v1.1 REST endpoints (statuses/user_timeline,
search/tweets) were shut down by X and always return 404.
"""
from __future__ import annotations
import re
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
    """Return {category, priority}."""
    ann   = _score(text, _ANNOUNCE_KW)
    link  = _score(text, _LINK_KW)
    comp  = _score(text, _COMPLAINT_KW)
    admin = _score(text, _ADMIN_KW)
    user_issue = _score(text, _USER_ISSUE_KW)

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

    if link >= 1:
        return {"category": "link", "priority": 2}

    if comp >= 1:
        return {"category": "complaint", "priority": 2}

    if user_issue >= 1 and is_reply:
        return {"category": "user_issue", "priority": 2}

    return {"category": "general", "priority": 1}


def _has_url(text: str) -> bool:
    return bool(re.search(r'https?://', text))


# ── Tweet dict field helpers (Scweet schema) ────────────────────────────────

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


def _tweet_is_reply(tw: dict) -> bool:
    return bool(tw.get("in_reply_to_screen_name") or tw.get("is_reply"))


# ── Main async fetch (uses a Scweet client instance) ────────────────────────

async def afetch_feed(
    scraper,
    usernames: list[str],
    since_ids: Optional[dict] = None,
    min_priority: int = 2,
    include_replies: bool = True,
) -> dict:
    """
    Fetch and classify recent tweets from multiple accounts using an
    already-constructed Scweet client (scraper). since_ids is currently
    unused for de-dup (handled by caller via seen tweet-id cache) but kept
    for interface compatibility.
    Returns {"items": [...], "fetched_at": "..."}
    """
    since_ids = since_ids or {}
    items: list[dict] = []

    try:
        tweets = await scraper.aget_profile_tweets(usernames, limit=15, save=False)
    except Exception as e:
        print(f"[feed] Error fetching timelines for {usernames}: {e}")
        tweets = []

    for tw in tweets:
        text     = _tweet_text(tw)
        is_reply = _tweet_is_reply(tw)
        account  = _tweet_user(tw)
        clf      = classify_tweet(text, is_reply=is_reply)

        if clf["priority"] < min_priority:
            continue

        items.append({
            "account":   account,
            "tweet_id":  _tweet_id(tw),
            "text":      text[:500],
            "url":       _tweet_url(tw),
            "date":      _tweet_date(tw),
            "is_reply":  is_reply,
            "is_rt":     False,
            "likes":     _tweet_likes(tw),
            "retweets":  _tweet_retweets(tw),
            "has_url":   _has_url(text),
            **clf,
        })

    if include_replies:
        for username in usernames:
            try:
                replies = await scraper.asearch(to_users=[username], limit=15, save=False)
            except Exception as e:
                print(f"[feed] Error fetching replies to @{username}: {e}")
                replies = []
            for tw in replies:
                sender = _tweet_user(tw)
                if sender.lower() == username.lower():
                    continue
                text = _tweet_text(tw)
                clf  = classify_tweet(text, is_reply=True)
                if clf["priority"] < min_priority:
                    continue
                items.append({
                    "account":   username,
                    "from_user": sender,
                    "tweet_id":  _tweet_id(tw),
                    "text":      text[:400],
                    "url":       _tweet_url(tw),
                    "date":      _tweet_date(tw),
                    "is_reply":  True,
                    "is_rt":     False,
                    "likes":     _tweet_likes(tw),
                    "retweets":  _tweet_retweets(tw),
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

    unique.sort(key=lambda x: (x["priority"], x["date"]), reverse=True)

    return {
        "items": unique,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def fetch_feed(scraper, usernames: list[str], **kwargs) -> dict:
    """Sync wrapper — only call this from a context with NO running event loop."""
    import asyncio
    return asyncio.run(afetch_feed(scraper, usernames, **kwargs))


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

    if from_u:
        who = f'👤 <a href="https://twitter.com/{from_u}">@{esc(from_u)}</a> → <a href="https://twitter.com/{account}">@{esc(account)}</a>'
    else:
        who = f'<a href="https://twitter.com/{account}">@{esc(account)}</a>'

    stats = ""
    if likes or rts:
        stats = f"❤️ {likes:,}  🔁 {rts:,}"

    lines = [
        f"{pri} <b>{header}</b>",
        who,
        f'<a href="{url}">{esc(text[:300])}</a>' if url else esc(text[:300]),
    ]
    if stats:
        lines.append(stats)
    lines.append(f"<i>🕐 {date}</i>")

    return "\n".join(lines)
