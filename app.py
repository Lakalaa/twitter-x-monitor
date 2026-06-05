"""
Twitter/X Monitor — Web Dashboard + Telegram Bot
Runs on Replit at port 5000.
Telegram bot accepts commands: /start /check /followers /following /complaints /status /help
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta

import schedule
from flask import Flask, jsonify, render_template, request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
import telegram_bot as tb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

CONFIG_FILE = "tools/targets.json"
STATE = {
    "running": False,
    "last_check": None,
    "next_check": None,
    "logs": [],
    "interval_minutes": 60,
    "pool_cooldowns": {},   # username -> unix timestamp when cooldown expires
}


# ─── Config helpers ───────────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {
        "twitter_auth_token": "",
        "track_followers_of": [],
        "track_following_of": [],
        "monitor_complaints": [],
        "check_interval_minutes": 60,
    }


def save_config(data: dict):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    STATE["logs"].insert(0, entry)
    STATE["logs"] = STATE["logs"][:100]  # keep last 100
    log.info(msg)


def get_scraper(db_path: str = None):
    """
    Return a ready Scweet instance.

    db_path — if given, use that file as the persistent state DB so that
              resume=True calls pick up where they left off (cursor pagination).
              If None, a unique one-shot DB is created (no state kept).
    """
    try:
        from Scweet import Scweet, ScweetConfig
    except ImportError:
        add_log("WARNING: Scweet not installed — scraping unavailable")
        return None
    import json as _json, uuid as _uuid
    config = load_config()
    # Prefer config file over env var — env var may hold a stale bearer token
    auth_token = (
        config.get("twitter_auth_token", "")
        or os.environ.get("TWITTER_AUTH_TOKEN", "")
    )
    ct0 = (
        config.get("twitter_ct0", "")
        or os.environ.get("TWITTER_CT0", "")
    )

    # Persistent DB → supplied by caller (cursor kept between calls).
    # One-shot DB  → unique UUID file, deleted after the call naturally ages out.
    # WAL mode is disabled — Replit/Render filesystems reject WAL journals.
    _db = db_path if db_path else f"scweet_{_uuid.uuid4().hex[:8]}.db"

    scfg = ScweetConfig(
        db_path=_db,
        enable_wal=False,
        concurrency=2,
        save_dir="outputs",
        save_format="json",
        min_delay_s=2.0,
        # Fetch up to 100 accounts per Twitter API page (maximum allowed)
        api_page_size=100,
        # Keep paginating even if one page comes back empty (sparse result sets)
        max_empty_pages=3,
        # Raise the per-day request budget so large follow lists aren't cut short
        daily_requests_limit=500,
        # Don't wait 30 days to retry after a single 401 — just 5 minutes
        auth_cooldown_s=300,
    )

    cookies_file = "tools/cookies.json"

    # Only write a single-account fallback if no cookies.json exists yet,
    # or if it contains only the old single "primary" entry — never overwrite
    # a multi-account pool already on disk.
    existing_pool = []
    if os.path.exists(cookies_file):
        try:
            with open(cookies_file) as _f:
                existing_pool = _json.load(_f)
        except Exception:
            existing_pool = []

    is_single_primary = (
        len(existing_pool) == 1
        and existing_pool[0].get("username") == "primary"
    )

    if not os.path.exists(cookies_file) or is_single_primary:
        if auth_token and auth_token not in ("", "YOUR_AUTH_TOKEN_HERE") and ct0:
            cookies_data = [{"username": "primary", "cookies": {"auth_token": auth_token, "ct0": ct0}}]
            os.makedirs("tools", exist_ok=True)
            with open(cookies_file, "w") as _f:
                _json.dump(cookies_data, _f)

    if os.path.exists(cookies_file):
        pool_size = len(existing_pool) if existing_pool else "?"
        add_log(f"Scweet: using cookies.json pool ({pool_size} accounts)")
        return Scweet(cookies_file=cookies_file, config=scfg)
    elif auth_token and auth_token not in ("", "YOUR_AUTH_TOKEN_HERE"):
        return Scweet(auth_token=auth_token, config=scfg)
    return None


def _scrape_with_progress(scrape_fn, kind: str, username: str):
    """
    Run scrape_fn() in the current thread while a background heartbeat sends
    a Telegram ping every 60 s so the user knows the job is alive.
    Retries once automatically if the first attempt returns 0 results.
    Returns the list of users (may be empty on total failure).
    """
    import threading as _th

    _done = _th.Event()
    _elapsed = [0]

    def _heartbeat():
        while not _done.wait(60):
            _elapsed[0] += 60
            if not _done.is_set():
                asyncio.run(tb.send_message(
                    f"⏳ Still scraping @{username} {kind}… "
                    f"{_elapsed[0] // 60} min elapsed, please wait."
                ))

    hb = _th.Thread(target=_heartbeat, daemon=True)
    hb.start()
    try:
        users = scrape_fn()
    except Exception as exc:
        _done.set()
        add_log(f"Scrape error ({kind} @{username}): {exc}")
        asyncio.run(tb.send_message(f"❌ Scrape error for @{username} {kind}: {exc}"))
        return []
    finally:
        _done.set()

    # Retry once on unexpected empty result (transient Twitter hiccup)
    if len(users) == 0:
        add_log(f"Scrape returned 0 for {kind} @{username} — retrying once…")
        asyncio.run(tb.send_message(f"⚠️ Got 0 results for @{username} {kind}, retrying once…"))
        try:
            users = scrape_fn()
        except Exception as exc2:
            add_log(f"Retry scrape error ({kind} @{username}): {exc2}")
            asyncio.run(tb.send_message(f"❌ Retry also failed for @{username} {kind}: {exc2}"))
            return []

    return users


def _cache_dir() -> str:
    d = os.path.join("outputs", "cache")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path(kind: str, username: str) -> str:
    return os.path.join(_cache_dir(), f"{kind}_{username.lower()}.json")


def _offset_path(kind: str, username: str) -> str:
    return os.path.join(_cache_dir(), f"{kind}_{username.lower()}.offset")


def read_cache(kind: str, username: str):
    """Return the full cached list, or None if not yet scraped."""
    p = _cache_path(kind, username)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


def write_cache(kind: str, username: str, users: list):
    with open(_cache_path(kind, username), "w") as f:
        json.dump(users, f)


def read_offset(kind: str, username: str) -> int:
    p = _offset_path(kind, username)
    if os.path.exists(p):
        with open(p) as f:
            return int(f.read().strip())
    return 0


def write_offset(kind: str, username: str, offset: int):
    with open(_offset_path(kind, username), "w") as f:
        f.write(str(offset))


def clear_cache(kind: str, username: str):
    """Delete cached list + offset so the next call re-scrapes from scratch."""
    for p in (_cache_path(kind, username), _offset_path(kind, username)):
        if os.path.exists(p):
            os.remove(p)


# ─── Core check logic ─────────────────────────────────────────────────────────

async def run_all_checks(triggered_by: str = "schedule"):
    config = load_config()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    add_log(f"Check started (triggered by: {triggered_by})")
    await tb.send_message(f"🔄 Check started — {now} (by: {triggered_by})")

    s = get_scraper()
    if not s:
        msg = "❌ No Twitter auth_token set. Add it in the dashboard Settings tab."
        add_log(msg)
        await tb.send_message(msg)
        return

    track_followers = config.get("track_followers_of", [])
    track_following = config.get("track_following_of", [])

    # Accounts tracked for BOTH followers and following → run full analysis
    both = [u for u in track_followers if u in track_following]
    followers_only_track = [u for u in track_followers if u not in both]
    following_only_track = [u for u in track_following if u not in both]

    for username in both:
        add_log(f"Fetching followers + following of @{username} for analysis…")
        try:
            followers = s.get_followers([username], limit=None, save=True, resume=False)
            following = s.get_following([username], limit=None, save=True, resume=False)
            add_log(f"@{username}: {len(followers)} followers, {len(following)} following — running comparison")
            await tb.send_connection_analysis(username, followers, following)
        except Exception as e:
            add_log(f"Error analysis @{username}: {e}")
            await tb.send_message(f"❌ Error running analysis for @{username}: {e}")

    for username in followers_only_track:
        add_log(f"Fetching followers of @{username}...")
        try:
            users = s.get_followers([username], limit=None, save=True, resume=False)
            add_log(f"@{username}: {len(users)} followers")
            await tb.send_users_to_telegram(users, "followers", username)
        except Exception as e:
            add_log(f"Error followers @{username}: {e}")
            await tb.send_message(f"❌ Error fetching followers @{username}: {e}")

    for username in following_only_track:
        add_log(f"Fetching following of @{username}...")
        try:
            users = s.get_following([username], limit=None, save=True, resume=False)
            add_log(f"@{username}: {len(users)} following")
            await tb.send_users_to_telegram(users, "following", username)
        except Exception as e:
            add_log(f"Error following @{username}: {e}")
            await tb.send_message(f"❌ Error fetching following @{username}: {e}")

    for item in config.get("monitor_complaints", []):
        query = item.get("query", "")
        days = item.get("since_days_ago", 7)
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        until = datetime.now().strftime("%Y-%m-%d")
        add_log(f"Checking complaints: \"{query}\"...")
        try:
            tweets = s.search(query=query, since=since, until=until, limit=500, save=True)
            add_log(f"Complaints \"{query}\": {len(tweets)} tweets")
            await tb.send_complaints_to_telegram(tweets, query, complaints_only=True)
        except Exception as e:
            add_log(f"Error complaints \"{query}\": {e}")
            await tb.send_message(f"❌ Error complaints \"{query}\": {e}")

    STATE["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    interval = config.get("check_interval_minutes", 60)
    STATE["next_check"] = (datetime.now() + timedelta(minutes=interval)).strftime("%Y-%m-%d %H:%M:%S")
    add_log("All checks complete.")
    await tb.send_message(f"✅ All checks done — next in {interval} min")


def run_checks_sync(triggered_by="schedule"):
    asyncio.run(run_all_checks(triggered_by))


# ─── Background scheduler ─────────────────────────────────────────────────────

def _keepalive_ping():
    """Ping own /health endpoint so Render free tier never idles out."""
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not render_url:
        return  # not on Render, skip
    try:
        import urllib.request as _ur
        _ur.urlopen(f"{render_url}/health", timeout=10)
    except Exception:
        pass  # ignore — just a best-effort ping


def start_scheduler():
    config = load_config()
    interval = config.get("check_interval_minutes", 60)
    STATE["interval_minutes"] = interval
    STATE["running"] = True
    schedule.clear()
    schedule.every(interval).minutes.do(run_checks_sync)
    schedule.every(10).minutes.do(_keepalive_ping)
    add_log(f"Scheduler started — every {interval} minutes")
    while STATE["running"]:
        schedule.run_pending()
        time.sleep(15)


scheduler_thread = None


# ─── Reply filter helpers ──────────────────────────────────────────────────────

def _is_admin(reply: dict) -> bool:
    """
    Return True if this scraped reply is from a verified/blue-tick (admin) account.
    Checks multiple field locations Scweet may return.
    """
    user = reply.get("user", {}) or {}
    return bool(
        user.get("blue_verified")
        or user.get("verified")
        or user.get("is_blue_verified")
        or reply.get("blue_verified")
        or reply.get("verified")
        or reply.get("is_blue_verified")
    )


def _filter_replies(replies: list, skip_admins: bool, tweet_id: str = "") -> tuple:
    """
    Filter reply list. Returns (filtered_list, total_before, admins_removed).
    Also always strips the original tweet itself.
    """
    total_before = len(replies)
    # Remove the original tweet
    if tweet_id:
        replies = [r for r in replies
                   if str(r.get("id", "")) != tweet_id
                   and str(r.get("tweet_id", "")) != tweet_id]
    admins_removed = 0
    if skip_admins:
        before = len(replies)
        replies = [r for r in replies if not _is_admin(r)]
        admins_removed = before - len(replies)
    return replies, total_before, admins_removed


def _parse_no_admins(args: str) -> tuple:
    """
    Strip --no-admins flag from args string.
    Returns (cleaned_args, skip_admins_bool).
    """
    if "--no-admins" in args:
        return args.replace("--no-admins", "").strip(), True
    return args.strip(), False


def _parse_count(args: str) -> tuple:
    """
    Extract an optional account count from args.
    The count must be the second whitespace-separated token and must be
    a plain integer (e.g. '/like <url> 50 ...' → count=50).
    Returns (cleaned_args_without_count, count_or_None).
    """
    tokens = args.split(None, 2)
    if len(tokens) >= 2 and tokens[1].isdigit():
        rest = tokens[2] if len(tokens) > 2 else ""
        cleaned = (tokens[0] + (" " + rest if rest else "")).strip()
        return cleaned, int(tokens[1])
    return args, None


# ─── Telegram Bot command handler ─────────────────────────────────────────────

async def handle_telegram_commands():
    """Poll Telegram for commands and respond.
    Works in both the group chat AND private DMs with the bot.
    Strips the @BotName suffix Telegram adds to group commands automatically.
    """
    from telegram import Bot

    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    # Fetch bot username once so we can strip @botname suffix
    bot_info = await bot.get_me()
    bot_username = bot_info.username.lower()

    offset = None
    add_log(f"Telegram bot @{bot_info.username} listening for commands (group + DM)")

    def parse_command(raw: str) -> tuple[str, str]:
        """Return (command, args) stripping /cmd@BotName and leading @."""
        raw = raw.strip()
        if not raw.startswith("/"):
            return "", ""
        # split command word from args
        parts = raw.split(None, 1)
        cmd_word = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""
        # strip @botname suffix  e.g.  /check@BloomEthereumn_bot  →  /check
        if "@" in cmd_word:
            cmd_word = cmd_word.split("@")[0]
        return cmd_word, args

    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=10, allowed_updates=["message"])
            for update in updates:
                offset = update.update_id + 1
                msg = update.message
                if not msg or not msg.text:
                    continue

                raw_text = msg.text.strip()
                if not raw_text.startswith("/"):
                    continue  # ignore non-commands

                cmd, args = parse_command(raw_text)
                chat_id = str(msg.chat.id)
                sender = f"@{msg.from_user.username}" if msg.from_user.username else msg.from_user.first_name
                add_log(f"Command '{cmd}' from {sender} in chat {chat_id}")

                # ── /start ──────────────────────────────────────────────────
                if cmd == "/start":
                    await bot.send_message(chat_id=chat_id, text=(
                        "👋 Twitter/X Monitor Bot\n\n"
                        "Commands:\n\n"
                        "/followers username — get followers (500 per call)\n"
                        "/following username — get following (500 per call)\n"
                        "/compare username — mutuals / followers-only / following-only\n"
                        "/replies <tweet_url> [--no-admins] — scrape replies\n"
                        "/mirror <src> <tgt> [--no-admins] — copy replies to another tweet\n"
                        "/replyall <tweet_url> [count] [--no-admins] <text> — reply to every commenter\n"
                        "/scrape <tweet_url> [--no-admins] — save repliers for later tagging\n"
                        "/tagusers <target_url> [count] [--no-admins] — tag saved users 5 per tweet\n"
                        "/tag <src> <tgt> [count] [--no-admins] — scrape + tag in one step\n"
                        "/complaints topic — search complaint tweets\n"
                        "/like <tweet_url> — like with all 200 accounts\n"
                        "/comment <tweet_url> [@mention] <text> — comment with all 200 accounts\n"
                        "/engage <tweet_url> [@mention] <text> — like + comment with all 200 accounts\n"
                        "/check — run all scheduled checks now\n"
                        "/status — monitoring status\n"
                        "/help — full command reference\n\n"
                        "Results are sent to the group."
                    ), disable_web_page_preview=True)

                # ── /help ───────────────────────────────────────────────────
                elif cmd == "/help":
                    await bot.send_message(chat_id=chat_id, text=(
                        "📖 Full command reference:\n\n"
                        "── Followers & Following ──\n"
                        "/followers username\n"
                        "  1st call: scrapes ALL followers, sends first 500\n"
                        "  Next calls: next 500 instantly from cache\n"
                        "/rescrape_followers username — force fresh re-scrape\n\n"
                        "/following username\n"
                        "  1st call: scrapes ALL following, sends first 500\n"
                        "  Next calls: next 500 instantly from cache\n"
                        "/rescrape_following username — force fresh re-scrape\n\n"
                        "/compare username\n"
                        "  🤝 Mutuals | 👁 Followers-only | 📤 Following-only\n\n"
                        "── Tweet Replies ──\n"
                        "/replies <tweet_url> [--no-admins]\n"
                        "  Scrape all replies — shows commenter + their text\n"
                        "  Add --no-admins to skip verified/blue-tick accounts\n\n"
                        "/mirror <source_url> <target_url> [--no-admins]\n"
                        "  Scrapes replies from source tweet and posts each one\n"
                        "  as a comment on target tweet (from your account).\n"
                        "  Format: 💬 @originaluser: their comment\n"
                        "  Add --no-admins to skip verified accounts\n"
                        "  Posts 1 reply every 8s (Twitter rate limit)\n\n"
                        "/replyall <tweet_url> [count] [--no-admins] [@mention] <text>\n"
                        "  Scrapes every reply under a tweet, then replies back\n"
                        "  to each commenter using your accounts (rotating).\n"
                        "  count = how many accounts to use (default: all)\n"
                        "  --no-admins = skip verified/blue-tick accounts\n"
                        "  Example: /replyall https://x.com/.../123 50 Thanks! 🙌\n\n"
                        "/scrape <tweet_url> [--no-admins]\n"
                        "  Scrapes all repliers from a tweet and SAVES their usernames.\n"
                        "  Run this first, then use /tagusers whenever you're ready.\n"
                        "  --no-admins = skip verified/blue-tick accounts\n"
                        "  Example: /scrape https://x.com/user/status/123\n\n"
                        "/retweeters <tweet_url> [count] [--no-admins]\n"
                        "  Scrapes users who RETWEETED/RESHARED a post and saves them.\n"
                        "  count = max retweeters to fetch (default: 200)\n"
                        "  --no-admins = skip verified/blue-tick accounts\n"
                        "  Example: /retweeters https://x.com/user/status/123 500\n\n"
                        "/tagusers <target_url> [count] [--no-admins]\n"
                        "  Tags the users saved by /scrape or /retweeters — 5 per reply.\n"
                        "  count = max users to tag (default: all saved)\n"
                        "  Each reply: @user1 @user2 @user3 @user4 @user5\n"
                        "  Posts 1 reply every 8s (Twitter rate limit)\n"
                        "  Example: /tagusers https://x.com/yourprofile/status/999 50\n\n"
                        "/tag <source_url> <target_url> [count] [--no-admins]\n"
                        "  Shortcut: scrape + tag in one step (both URLs together).\n"
                        "  Example: /tag https://x.com/src/111 https://x.com/tgt/222 50\n\n"
                        "── Bulk Engagement (account pool) ──\n"
                        "/like <tweet_url> [count]\n"
                        "  Likes the tweet — count limits how many accounts (default: all)\n\n"
                        "/retweet <tweet_url> [count]\n"
                        "  Retweets/reshares the tweet — count limits how many accounts\n"
                        "  Example: /retweet https://x.com/.../123 50\n\n"
                        "/retweetpool <tweet_url>\n"
                        "  Retweet a DIFFERENT post using the same count as saved list.\n"
                        "  E.g. saved 150 retweeters → 150 pool accounts retweet target.\n"
                        "  Run /scrape or /retweeters first to build the list.\n"
                        "  Example: /retweetpool https://x.com/.../999\n\n"
                        "/comment <tweet_url> [count] [@mention] <text>\n"
                        "  Posts a comment — count limits how many accounts (default: all)\n"
                        "  Optional: start text with @username to mention someone\n"
                        "  Example: /comment https://x.com/.../123 50 @elonmusk great!\n\n"
                        "/engage <tweet_url> [count] [@mention] <text>\n"
                        "  Likes AND comments from all 200 accounts at once\n"
                        "  Example: /engage https://x.com/.../123 Amazing project!\n\n"
                        "── Other ──\n"
                        "/complaints topic — complaint tweets (last 7 days)\n"
                        "/check — run all scheduled checks now\n"
                        "/status — monitoring status + tracked accounts\n"
                        "/help — this message\n\n"
                        "Works in this DM and in the group.\n"
                        "In group: /command@" + bot_info.username + " args"
                    ), disable_web_page_preview=True)

                # ── /status ─────────────────────────────────────────────────
                elif cmd == "/status":
                    config = load_config()
                    followers_list = config.get("track_followers_of", [])
                    following_list = config.get("track_following_of", [])
                    complaints_list = [c["query"] for c in config.get("monitor_complaints", [])]
                    interval = config.get("check_interval_minutes", 60)
                    _tok = os.environ.get("TWITTER_AUTH_TOKEN", "") or config.get("twitter_auth_token", "")
                    has_token = bool(_tok and _tok not in ("", "YOUR_AUTH_TOKEN_HERE"))
                    reply = (
                        f"📊 Monitor Status\n\n"
                        f"Twitter token: {'✅ set' if has_token else '❌ missing'}\n"
                        f"Check interval: every {interval} min\n"
                        f"Last check: {STATE['last_check'] or 'never'}\n"
                        f"Next check: {STATE['next_check'] or 'not scheduled'}\n\n"
                        f"Tracking followers of:\n" +
                        ("\n".join(f"  • @{u}" for u in followers_list) if followers_list else "  (none set)") +
                        f"\n\nTracking following of:\n" +
                        ("\n".join(f"  • @{u}" for u in following_list) if following_list else "  (none set)") +
                        f"\n\nMonitoring complaints:\n" +
                        ("\n".join(f"  • {q}" for q in complaints_list) if complaints_list else "  (none set)")
                    )
                    await bot.send_message(chat_id=chat_id, text=reply, disable_web_page_preview=True)

                # ── /check ──────────────────────────────────────────────────
                elif cmd == "/check":
                    await bot.send_message(chat_id=chat_id, text="▶️ Running all checks now… results will appear in the group.", disable_web_page_preview=True)
                    threading.Thread(target=run_checks_sync, args=("Telegram /check",), daemon=True).start()

                # ── /followers <username> ────────────────────────────────────
                elif cmd == "/followers":
                    if not args:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /followers username\n"
                            "Example: /followers elonmusk\n\n"
                            "First call: scrapes ALL followers and sends the first 500.\n"
                            "Each call after: sends the next 500 from the stored list (instant, no re-scraping).\n"
                            "When the list is exhausted it re-scrapes fresh data automatically.\n"
                            "To force a fresh re-scrape now: /rescrape_followers username"
                        ))
                    else:
                        uname = args.lstrip("@").split()[0]
                        cached = read_cache("followers", uname)
                        if cached is None:
                            await bot.send_message(chat_id=chat_id, text=(
                                f"🔍 No stored data for @{uname} followers yet.\n"
                                f"Scraping ALL followers now — this may take a while for large accounts.\n"
                                f"Results will appear in the group when ready."
                            ), disable_web_page_preview=True)
                        else:
                            offset = read_offset("followers", uname)
                            total = len(cached)
                            remaining = total - offset
                            await bot.send_message(chat_id=chat_id, text=(
                                f"📋 Sending next 500 of @{uname}'s followers from stored list.\n"
                                f"Position: {offset:,} / {total:,} ({remaining:,} remaining)"
                            ), disable_web_page_preview=True)

                        def _run_followers(u=uname):
                            users = read_cache("followers", u)
                            if users is None:
                                import sys as _sys
                                _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
                                s = get_scraper()
                                if s:
                                    add_log(f"Scraping ALL followers of @{u} via Scweet…")
                                    users = _scrape_with_progress(
                                        lambda: s.get_followers([u], limit=None, save=True, resume=False),
                                        "followers", u
                                    )
                                    if not users:
                                        return
                                else:
                                    # GraphQL fallback — works on Render without Scweet
                                    from twitter_post import scrape_followers_graphql as _sfg, get_auth_from_config as _gac
                                    auth_token, ct0 = _gac()
                                    if not auth_token or not ct0:
                                        asyncio.run(tb.send_message("❌ No Twitter auth_token/ct0 set — add them in Settings."))
                                        return
                                    add_log(f"Scraping followers of @{u} via GraphQL…")
                                    asyncio.run(tb.send_message(f"🔍 Scraping followers of @{u} via GraphQL… (fetching up to 5,000)"))
                                    result = _sfg(u, auth_token, ct0, limit=5000)
                                    if not result.get("ok"):
                                        asyncio.run(tb.send_message(f"❌ {result.get('error','Unknown error')}"))
                                        return
                                    raw = result.get("users", [])
                                    if not raw:
                                        asyncio.run(tb.send_message(f"⚠️ {result.get('message', f'No followers found for @{u}')}"))
                                        return
                                    # Normalise to the same shape the rest of the code expects
                                    users = [{"username": r["screen_name"], "displayname": r.get("name", r["screen_name"]),
                                              "followersCount": r.get("followers_count", 0),
                                              "verified": r.get("verified", False)} for r in raw]
                                add_log(f"Scraped {len(users):,} followers of @{u} — saved to cache")
                                write_cache("followers", u, users)
                                write_offset("followers", u, 0)
                                asyncio.run(tb.send_message(
                                    f"✅ Scraped {len(users):,} followers of @{u}. Sending first 500…"
                                ))

                            offset = read_offset("followers", u)
                            total  = len(users)
                            page   = users[offset:offset + 500]

                            if not page:
                                clear_cache("followers", u)
                                asyncio.run(tb.send_message(
                                    f"✅ All {total:,} followers of @{u} have been sent.\n"
                                    f"The next /followers {u} will re-scrape fresh data."
                                ))
                                return

                            asyncio.run(tb.send_users_page(page, "followers", u, offset, total))
                            new_offset = offset + len(page)
                            add_log(f"Followers @{u}: sent {new_offset:,}/{total:,}")

                            if new_offset >= total:
                                clear_cache("followers", u)
                                asyncio.run(tb.send_message(
                                    f"✅ All {total:,} followers of @{u} sent.\n\n"
                                    f"▶️ Same account again: /followers {u}\n"
                                    f"▶️ Different account: /followers otherusername\n"
                                    f"▶️ Force fresh re-scrape: /rescrape_followers {u}"
                                ))
                            else:
                                write_offset("followers", u, new_offset)
                                asyncio.run(tb.send_message(
                                    f"📄 Sent {new_offset:,} of {total:,} — {total - new_offset:,} remaining.\n\n"
                                    f"▶️ Next 500 from @{u}: /followers {u}\n"
                                    f"▶️ Different account: /followers otherusername"
                                ))

                        threading.Thread(target=_run_followers, daemon=True).start()

                # ── /rescrape_followers <username> ───────────────────────────
                elif cmd == "/rescrape_followers":
                    if not args:
                        await bot.send_message(chat_id=chat_id, text="Usage: /rescrape_followers username\nDeletes the stored list and re-scrapes all followers from scratch.")
                    else:
                        uname = args.lstrip("@").split()[0]
                        clear_cache("followers", uname)
                        await bot.send_message(chat_id=chat_id, text=f"🗑 Cache cleared for @{uname} followers. Run /followers {uname} to re-scrape everything fresh.")

                # ── /following <username> ────────────────────────────────────
                elif cmd == "/following":
                    if not args:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /following username\n"
                            "Example: /following OpenAI\n\n"
                            "First call: scrapes ALL following and sends the first 500.\n"
                            "Each call after: sends the next 500 from the stored list (instant, no re-scraping).\n"
                            "When the list is exhausted it re-scrapes fresh data automatically.\n"
                            "To force a fresh re-scrape now: /rescrape_following username"
                        ))
                    else:
                        uname = args.lstrip("@").split()[0]
                        cached = read_cache("following", uname)
                        if cached is None:
                            await bot.send_message(chat_id=chat_id, text=(
                                f"🔍 No stored data for @{uname} following yet.\n"
                                f"Scraping ALL following now — results will appear in the group when ready."
                            ), disable_web_page_preview=True)
                        else:
                            offset = read_offset("following", uname)
                            total = len(cached)
                            remaining = total - offset
                            await bot.send_message(chat_id=chat_id, text=(
                                f"📋 Sending next 500 of @{uname}'s following from stored list.\n"
                                f"Position: {offset:,} / {total:,} ({remaining:,} remaining)"
                            ), disable_web_page_preview=True)

                        def _run_following(u=uname):
                            users = read_cache("following", u)
                            if users is None:
                                import sys as _sys
                                _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
                                s = get_scraper()
                                if s:
                                    add_log(f"Scraping ALL following of @{u} via Scweet…")
                                    users = _scrape_with_progress(
                                        lambda: s.get_following([u], limit=None, save=True, resume=False),
                                        "following", u
                                    )
                                    if not users:
                                        return
                                else:
                                    # GraphQL fallback — works on Render without Scweet
                                    from twitter_post import scrape_following_graphql as _sfoG, get_auth_from_config as _gac
                                    auth_token, ct0 = _gac()
                                    if not auth_token or not ct0:
                                        asyncio.run(tb.send_message("❌ No Twitter auth_token/ct0 set — add them in Settings."))
                                        return
                                    add_log(f"Scraping following of @{u} via GraphQL…")
                                    asyncio.run(tb.send_message(f"🔍 Scraping following of @{u} via GraphQL… (fetching up to 5,000)"))
                                    result = _sfoG(u, auth_token, ct0, limit=5000)
                                    if not result.get("ok"):
                                        asyncio.run(tb.send_message(f"❌ {result.get('error','Unknown error')}"))
                                        return
                                    raw = result.get("users", [])
                                    if not raw:
                                        asyncio.run(tb.send_message(f"⚠️ {result.get('message', f'No following found for @{u}')}"))
                                        return
                                    users = [{"username": r["screen_name"], "displayname": r.get("name", r["screen_name"]),
                                              "followersCount": r.get("followers_count", 0),
                                              "verified": r.get("verified", False)} for r in raw]
                                add_log(f"Scraped {len(users):,} following of @{u} — saved to cache")
                                write_cache("following", u, users)
                                write_offset("following", u, 0)
                                asyncio.run(tb.send_message(
                                    f"✅ Scraped {len(users):,} following of @{u}. Sending first 500…"
                                ))

                            offset = read_offset("following", u)
                            total  = len(users)
                            page   = users[offset:offset + 500]

                            if not page:
                                clear_cache("following", u)
                                asyncio.run(tb.send_message(
                                    f"✅ All {total:,} following of @{u} have been sent.\n"
                                    f"The next /following {u} will re-scrape fresh data."
                                ))
                                return

                            asyncio.run(tb.send_users_page(page, "following", u, offset, total))
                            new_offset = offset + len(page)
                            add_log(f"Following @{u}: sent {new_offset:,}/{total:,}")

                            if new_offset >= total:
                                clear_cache("following", u)
                                asyncio.run(tb.send_message(
                                    f"✅ All {total:,} following of @{u} sent.\n\n"
                                    f"▶️ Same account again: /following {u}\n"
                                    f"▶️ Different account: /following otherusername\n"
                                    f"▶️ Force fresh re-scrape: /rescrape_following {u}"
                                ))
                            else:
                                write_offset("following", u, new_offset)
                                asyncio.run(tb.send_message(
                                    f"📄 Sent {new_offset:,} of {total:,} — {total - new_offset:,} remaining.\n\n"
                                    f"▶️ Next 500 from @{u}: /following {u}\n"
                                    f"▶️ Different account: /following otherusername"
                                ))

                        threading.Thread(target=_run_following, daemon=True).start()

                # ── /rescrape_following <username> ───────────────────────────
                elif cmd == "/rescrape_following":
                    if not args:
                        await bot.send_message(chat_id=chat_id, text="Usage: /rescrape_following username\nDeletes the stored list and re-scrapes all following from scratch.")
                    else:
                        uname = args.lstrip("@").split()[0]
                        clear_cache("following", uname)
                        await bot.send_message(chat_id=chat_id, text=f"🗑 Cache cleared for @{uname} following. Run /following {uname} to re-scrape everything fresh.")

                # ── /compare <username> ─────────────────────────────────────
                elif cmd == "/compare":
                    if not args:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /compare username\n"
                            "Example: /compare elonmusk\n\n"
                            "Fetches BOTH followers and following, then shows:\n"
                            "🤝 Mutuals — follow each other\n"
                            "👁 Followers only — fans (not followed back)\n"
                            "📤 Following only — they don't follow back"
                        ))
                    else:
                        uname = args.lstrip("@").split()[0]
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"▶️ Fetching followers + following of @{uname} for comparison… results will appear in the group.",
                            disable_web_page_preview=True
                        )
                        def _run_compare(u=uname):
                            s = get_scraper()
                            if not s:
                                asyncio.run(tb.send_message("❌ No Twitter auth_token set. Add it in the dashboard Settings tab."))
                                return
                            try:
                                add_log(f"Compare: fetching followers of @{u}…")
                                followers = s.get_followers([u], limit=500, save=True)
                                add_log(f"Compare: fetching following of @{u}…")
                                following = s.get_following([u], limit=500, save=True)
                                add_log(f"Compare @{u}: {len(followers)} flw / {len(following)} fwing")
                                asyncio.run(tb.send_connection_analysis(u, followers, following))
                            except Exception as e:
                                asyncio.run(tb.send_message(f"❌ Error comparing @{u}: {e}"))
                        threading.Thread(target=_run_compare, daemon=True).start()

                # ── /replies <tweet_url> [--no-admins] ───────────────────────
                elif cmd == "/replies":
                    if not args:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /replies <tweet_url> [--no-admins]\n\n"
                            "Scrapes all replies to that tweet and sends each commenter's "
                            "name + their comment to the group.\n\n"
                            "Add --no-admins to skip verified/blue-tick accounts:\n"
                            "/replies https://x.com/user/status/123 --no-admins"
                        ))
                    else:
                        tweet_url, skip_admins = _parse_no_admins(args)
                        filter_note = " (skipping verified accounts)" if skip_admins else ""
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"💬 Fetching replies for:\n{tweet_url}{filter_note}\nResults will appear in the group shortly.",
                            disable_web_page_preview=True
                        )
                        def _run_replies(url=tweet_url, skip=skip_admins):
                            sc = get_scraper()
                            if not sc:
                                asyncio.run(tb.send_message("❌ No Twitter auth_token set. Add it in the dashboard Settings tab."))
                                return
                            try:
                                import re as _re
                                m = _re.search(r"x\.com/([^/]+)/status/(\d+)", url)
                                tweet_author = m.group(1) if m else ""
                                tweet_id     = m.group(2) if m else ""

                                add_log(f"Scraping replies to tweet {tweet_id} by @{tweet_author}…")
                                query = f"conversation_id:{tweet_id}" if tweet_id else url
                                replies = sc.search(query=query, limit=500, save=True, filter_replies=False)
                                replies, total_raw, admins_removed = _filter_replies(replies, skip, tweet_id)
                                note = f" ({admins_removed} verified skipped)" if admins_removed else ""
                                add_log(f"Replies scraped: {len(replies)} for tweet {tweet_id}{note}")
                                asyncio.run(tb.send_replies_to_telegram(replies, url, tweet_author))
                                if admins_removed:
                                    asyncio.run(tb.send_message(f"ℹ️ {admins_removed} verified/admin accounts were skipped."))
                            except Exception as e:
                                add_log(f"Replies error: {e}")
                                asyncio.run(tb.send_message(f"❌ Error scraping replies: {e}"))
                        threading.Thread(target=_run_replies, daemon=True).start()

                # ── /mirror <source_url> <target_url> [--no-admins] ──────────
                elif cmd == "/mirror":
                    raw_mirror_args, skip_admins_mirror = _parse_no_admins(args)
                    parts = raw_mirror_args.strip().split()
                    if len(parts) < 2:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /mirror <source_tweet_url> <target_tweet_url> [--no-admins]\n\n"
                            "Scrapes all replies from the SOURCE tweet, then posts each one "
                            "as a reply on the TARGET tweet (from your account).\n\n"
                            "Add --no-admins to skip verified/blue-tick accounts:\n"
                            "/mirror <src> <tgt> --no-admins\n\n"
                            "Each posted comment will look like:\n"
                            "💬 @originaluser: their comment text\n\n"
                            "⚠️ Twitter rate-limits posting — large threads are posted slowly (1 every 8s)."
                        ), disable_web_page_preview=True)
                    else:
                        src_url    = parts[0]
                        target_url = parts[1]
                        filter_note = " | skipping verified accounts" if skip_admins_mirror else ""
                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"🔁 Mirror started.\n"
                                f"Source: {src_url}\n"
                                f"Target: {target_url}{filter_note}\n\n"
                                f"Scraping replies from source… will post them one by one on the target tweet."
                            ),
                            disable_web_page_preview=True
                        )
                        def _run_mirror(src=src_url, tgt=target_url, skip=skip_admins_mirror):
                            import re as _re
                            import sys as _sys
                            _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
                            import twitter_post as _tp

                            # ── 1. Resolve target tweet ID ────────────────────
                            m_tgt = _re.search(r"(?:x|twitter)\.com/[^/]+/status/(\d+)", tgt)
                            if not m_tgt:
                                asyncio.run(tb.send_message(f"❌ Could not parse target tweet ID from:\n{tgt}"))
                                return
                            target_id = m_tgt.group(1)

                            # ── 2. Scrape replies from source ─────────────────
                            sc = get_scraper()
                            if not sc:
                                asyncio.run(tb.send_message("❌ No Twitter auth_token set. Add it in the dashboard Settings tab."))
                                return

                            m_src = _re.search(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)", src)
                            src_author = m_src.group(1) if m_src else ""
                            src_id     = m_src.group(2) if m_src else ""

                            add_log(f"Mirror: scraping replies from tweet {src_id} by @{src_author}…")
                            try:
                                query   = f"conversation_id:{src_id}" if src_id else src
                                replies = sc.search(query=query, limit=500, save=True, filter_replies=False)
                                replies, _, admins_removed = _filter_replies(replies, skip, src_id)
                            except Exception as exc:
                                asyncio.run(tb.send_message(f"❌ Error scraping source tweet replies: {exc}"))
                                return

                            if not replies:
                                asyncio.run(tb.send_message(
                                    f"⚠️ No replies found for source tweet.\n"
                                    f"The tweet might have no replies, or the search returned no results."
                                ))
                                return

                            admin_note = f" ({admins_removed} verified skipped)" if admins_removed else ""
                            asyncio.run(tb.send_message(
                                f"✅ Found {len(replies):,} replies{admin_note}. "
                                f"Now posting them on target tweet (1 every 8s to avoid rate limits)…"
                            ))

                            # ── 3. Get posting credentials ────────────────────
                            auth_token, ct0 = _tp.get_auth_from_config()
                            if not auth_token or not ct0:
                                asyncio.run(tb.send_message("❌ ct0 cookie is missing. Go to Dashboard → Settings and add both auth_token AND ct0."))
                                return

                            # ── 4. Post each reply on target tweet ────────────
                            ok_count   = 0
                            fail_count = 0
                            for i, reply in enumerate(replies):
                                username = (reply.get("user", {}).get("screen_name")
                                            or reply.get("username", "unknown"))
                                text     = (reply.get("text") or reply.get("rawContent") or "").strip()
                                if not text:
                                    continue

                                prefix  = f"💬 @{username}: "
                                max_txt = 280 - len(prefix) - 3
                                body    = text[:max_txt] + ("…" if len(text) > max_txt else "")
                                post_text = prefix + body

                                result = _tp.post_reply(post_text, target_id, auth_token, ct0)
                                if result["ok"]:
                                    ok_count += 1
                                    add_log(f"Mirror: posted reply {ok_count} (@{username})")
                                else:
                                    fail_count += 1
                                    add_log(f"Mirror: post failed (@{username}): {result['error']}")
                                    if fail_count >= 3 and ok_count == 0:
                                        asyncio.run(tb.send_message(
                                            f"❌ Posting is failing (3 errors, 0 successes). Stopping.\n"
                                            f"Last error: {result['error']}\n\n"
                                            f"Check that your ct0 cookie is correct and fresh (Settings tab)."
                                        ))
                                        return

                                if (i + 1) % 25 == 0:
                                    asyncio.run(tb.send_message(
                                        f"🔁 Mirror progress: {i+1}/{len(replies)} — "
                                        f"✅ {ok_count} posted, ❌ {fail_count} failed"
                                    ))

                                time.sleep(8)

                            asyncio.run(tb.send_message(
                                f"🏁 Mirror complete!\n"
                                f"Source: {src}\n"
                                f"Target: {tgt}\n"
                                f"✅ {ok_count} replies posted | ❌ {fail_count} failed"
                            ))
                            add_log(f"Mirror done: {ok_count} posted, {fail_count} failed")

                        threading.Thread(target=_run_mirror, daemon=True).start()

                # ── /replyall <tweet_url> [count] [--no-admins] <reply_text> ─
                elif cmd == "/replyall":
                    raw_ra_args, skip_admins_ra = _parse_no_admins(args)
                    raw_ra_args, ra_count = _parse_count(raw_ra_args)
                    parts = raw_ra_args.strip().split(None, 1)
                    if len(parts) < 2:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /replyall <tweet_url> [count] [--no-admins] <reply_text>\n\n"
                            "Scrapes every reply under a tweet, then replies back to each commenter\n"
                            "using your accounts (one per commenter, rotating).\n\n"
                            "Options:\n"
                            "  count        how many accounts to use (default: all)\n"
                            "  --no-admins  skip verified/blue-tick accounts\n\n"
                            "Examples:\n"
                            "/replyall https://x.com/user/status/123 Thanks! 🙌\n"
                            "/replyall https://x.com/user/status/123 50 Thanks! 🙌\n"
                            "/replyall https://x.com/user/status/123 50 --no-admins Thanks! 🙌"
                        ), disable_web_page_preview=True)
                    else:
                        ra_tweet_url = parts[0].strip()
                        ra_reply_body = parts[1].strip()
                        ra_parts = ra_reply_body.split(None, 1)
                        ra_mention = ""
                        if ra_parts[0].startswith("@"):
                            ra_mention = ra_parts[0]
                            ra_reply_body = ra_parts[1] if len(ra_parts) > 1 else ""

                        filter_note = " | skipping verified accounts" if skip_admins_ra else ""
                        count_note  = f" | using {ra_count} accounts" if ra_count else ""
                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"🔍 Scraping replies for:\n{ra_tweet_url}{filter_note}{count_note}\n\n"
                                f"Will then reply to each commenter using your accounts."
                            ),
                            disable_web_page_preview=True
                        )

                        def _run_replyall(url=ra_tweet_url, reply_text=ra_reply_body, mention=ra_mention, skip=skip_admins_ra, n=ra_count):
                            import re as _re
                            import sys as _sys
                            _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
                            import twitter_post as _tp

                            # ── 1. Scrape replies ─────────────────────────────
                            sc = get_scraper()
                            if not sc:
                                asyncio.run(tb.send_message("❌ No Twitter auth_token set. Add it in the dashboard Settings tab."))
                                return

                            m = _re.search(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)", url)
                            tweet_author = m.group(1) if m else ""
                            tweet_id     = m.group(2) if m else ""

                            if not tweet_id:
                                asyncio.run(tb.send_message(f"❌ Could not parse tweet ID from: {url}"))
                                return

                            add_log(f"ReplyAll: scraping replies to tweet {tweet_id}…")
                            try:
                                query   = f"conversation_id:{tweet_id}"
                                replies = sc.search(query=query, limit=500, save=True, filter_replies=False)
                                replies, _, admins_removed = _filter_replies(replies, skip, tweet_id)
                            except Exception as exc:
                                asyncio.run(tb.send_message(f"❌ Error scraping replies: {exc}"))
                                return

                            if not replies:
                                asyncio.run(tb.send_message("⚠️ No replies found for that tweet."))
                                return

                            # ── 2. Load account pool ──────────────────────────
                            pool = _tp.load_account_pool()
                            if n: pool = pool[:n]
                            if not pool:
                                asyncio.run(tb.send_message("❌ Account pool empty. Check tools/cookies.json."))
                                return

                            admin_note = f" ({admins_removed} verified skipped)" if admins_removed else ""
                            asyncio.run(tb.send_message(
                                f"✅ Found {len(replies):,} replies{admin_note}.\n"
                                f"Replying to each commenter using {len(pool)} accounts…\n"
                                f"(~{len(replies)*5//60}–{len(replies)*8//60} min estimated)"
                            ))
                            add_log(f"ReplyAll: replying to {len(replies)} commenters with {len(pool)} accounts")

                            # ── 3. Reply to each commenter ────────────────────
                            ok_count   = 0
                            fail_count = 0
                            pool_idx   = 0

                            for i, reply in enumerate(replies):
                                # Get commenter's username and their tweet ID
                                commenter = (reply.get("user", {}).get("screen_name")
                                             or reply.get("username", "unknown"))
                                reply_tweet_id = str(reply.get("id") or reply.get("tweet_id") or "")
                                if not reply_tweet_id:
                                    fail_count += 1
                                    continue

                                # Pick next account from pool (rotate)
                                account = pool[pool_idx % len(pool)]
                                pool_idx += 1
                                auth_tok = account.get("cookies", {}).get("auth_token", "")
                                ct0_val  = account.get("cookies", {}).get("ct0", "")
                                acct_name = account.get("username", f"account_{pool_idx}")

                                if not auth_tok or not ct0_val:
                                    fail_count += 1
                                    continue

                                # Build reply text: optional @mention + "@commenter " + body
                                parts_txt = []
                                if mention:
                                    parts_txt.append(mention)
                                parts_txt.append(f"@{commenter}")
                                if reply_text:
                                    parts_txt.append(reply_text)
                                post_text = " ".join(parts_txt)
                                if len(post_text) > 280:
                                    post_text = post_text[:277] + "…"

                                result = _tp.post_reply(post_text, reply_tweet_id, auth_tok, ct0_val)
                                if result.get("ok"):
                                    ok_count += 1
                                    add_log(f"ReplyAll: ✅ replied to @{commenter} (via @{acct_name})")
                                else:
                                    fail_count += 1
                                    add_log(f"ReplyAll: ❌ failed @{commenter}: {result.get('error','?')}")

                                # Progress every 25
                                if (i + 1) % 25 == 0:
                                    asyncio.run(tb.send_message(
                                        f"↩️ ReplyAll progress: {i+1}/{len(replies)}\n"
                                        f"✅ {ok_count} sent | ❌ {fail_count} failed"
                                    ))

                                time.sleep(5)  # rate-limit friendly

                            asyncio.run(tb.send_message(
                                f"🏁 ReplyAll complete!\n"
                                f"Tweet: {url}\n"
                                f"Commenters found: {len(replies)}\n"
                                f"✅ {ok_count} replies sent | ❌ {fail_count} failed"
                            ))
                            add_log(f"ReplyAll done: {ok_count} sent, {fail_count} failed")

                        threading.Thread(target=_run_replyall, daemon=True).start()

                # ── /tag <source_url> <target_url> [count] [--no-admins] ───
                elif cmd == "/tag":
                    raw_tag_args, skip_admins_tag = _parse_no_admins(args)
                    raw_tag_args, tag_count = _parse_count(raw_tag_args)
                    tag_parts = raw_tag_args.strip().split()
                    if len(tag_parts) < 2:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /tag <source_url> <target_url> [count] [--no-admins]\n\n"
                            "Scrapes all repliers from SOURCE tweet, then mentions them\n"
                            "in groups of 5 as replies on TARGET tweet.\n"
                            "Each reply looks like: @user1 @user2 @user3 @user4 @user5\n\n"
                            "Options:\n"
                            "  count        max users to tag total (default: all)\n"
                            "  --no-admins  skip verified/blue-tick accounts\n\n"
                            "Examples:\n"
                            "/tag https://x.com/a/status/111 https://x.com/b/status/222\n"
                            "/tag https://x.com/a/status/111 https://x.com/b/status/222 50\n"
                            "/tag https://x.com/a/status/111 https://x.com/b/status/222 50 --no-admins"
                        ), disable_web_page_preview=True)
                    else:
                        tag_src_url = tag_parts[0]
                        tag_tgt_url = tag_parts[1]
                        filter_note = " | skip verified" if skip_admins_tag else ""
                        count_note  = f" | max {tag_count} users" if tag_count else ""
                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"🏷 Tag started.\n"
                                f"Source: {tag_src_url}\n"
                                f"Target: {tag_tgt_url}{filter_note}{count_note}\n\n"
                                f"Scraping repliers… will tag them 5 per tweet."
                            ),
                            disable_web_page_preview=True
                        )

                        def _run_tag(src=tag_src_url, tgt=tag_tgt_url, skip=skip_admins_tag, max_users=tag_count):
                            import re as _re
                            import sys as _sys
                            _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
                            import twitter_post as _tp

                            # ── 1. Resolve target tweet ID ────────────────────
                            m_tgt = _re.search(r"(?:x|twitter)\.com/[^/]+/status/(\d+)", tgt)
                            if not m_tgt:
                                asyncio.run(tb.send_message(f"❌ Could not parse target tweet ID from:\n{tgt}"))
                                return
                            target_id = m_tgt.group(1)

                            m_src = _re.search(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)", src)
                            src_id = m_src.group(2) if m_src else ""

                            # ── 2. Scrape replies from source ─────────────────
                            sc = get_scraper()
                            if not sc:
                                asyncio.run(tb.send_message("❌ No Twitter auth_token set. Add it in Settings tab."))
                                return

                            add_log(f"Tag: scraping replies from tweet {src_id}…")
                            try:
                                query   = f"conversation_id:{src_id}" if src_id else src
                                replies = sc.search(query=query, limit=500, save=True, filter_replies=False)
                                replies, _, admins_removed = _filter_replies(replies, skip, src_id)
                            except Exception as exc:
                                asyncio.run(tb.send_message(f"❌ Error scraping source tweet: {exc}"))
                                return

                            # ── 3. Collect unique usernames ───────────────────
                            seen = set()
                            usernames = []
                            for r in replies:
                                u = (r.get("user", {}).get("screen_name") or r.get("username", "")).strip()
                                if u and u.lower() not in seen:
                                    seen.add(u.lower())
                                    usernames.append(u)

                            if max_users:
                                usernames = usernames[:max_users]

                            if not usernames:
                                asyncio.run(tb.send_message("⚠️ No users found to tag."))
                                return

                            batches = [usernames[i:i+5] for i in range(0, len(usernames), 5)]
                            admin_note = f" ({admins_removed} verified skipped)" if admins_removed else ""
                            asyncio.run(tb.send_message(
                                f"✅ {len(usernames)} unique users{admin_note} → "
                                f"{len(batches)} batches of 5.\n"
                                f"Posting tag replies on target tweet…"
                            ))

                            # ── 4. Load account pool ──────────────────────────
                            pool = _tp.load_account_pool()
                            if not pool:
                                asyncio.run(tb.send_message("❌ Account pool empty. Check tools/cookies.json."))
                                return

                            auth_token, ct0 = _tp.get_auth_from_config()
                            if not auth_token or not ct0:
                                asyncio.run(tb.send_message("❌ ct0 missing. Add both auth_token AND ct0 in Settings."))
                                return

                            # ── 5. Post each batch of 5 mentions ─────────────
                            ok_count   = 0
                            fail_count = 0
                            pool_idx   = 0

                            for i, batch in enumerate(batches):
                                post_text = " ".join(f"@{u}" for u in batch)

                                # Rotate accounts
                                account  = pool[pool_idx % len(pool)]
                                pool_idx += 1
                                auth_tok = account.get("cookies", {}).get("auth_token", "") or auth_token
                                ct0_val  = account.get("cookies", {}).get("ct0", "")        or ct0
                                acct_name = account.get("username", f"acct_{pool_idx}")

                                result = _tp.post_reply(post_text, target_id, auth_tok, ct0_val)
                                if result.get("ok"):
                                    ok_count += 1
                                    add_log(f"Tag: ✅ batch {i+1} posted via @{acct_name}: {post_text[:60]}")
                                else:
                                    fail_count += 1
                                    add_log(f"Tag: ❌ batch {i+1} failed: {result.get('error','?')}")
                                    if fail_count >= 3 and ok_count == 0:
                                        asyncio.run(tb.send_message(
                                            f"❌ Tag posting is failing (3 errors, 0 successes). Stopping.\n"
                                            f"Last error: {result.get('error','?')}\n"
                                            f"Check your ct0 cookie in Settings."
                                        ))
                                        return

                                if (i + 1) % 20 == 0:
                                    asyncio.run(tb.send_message(
                                        f"🏷 Tag progress: {i+1}/{len(batches)} batches\n"
                                        f"✅ {ok_count} posted | ❌ {fail_count} failed"
                                    ))

                                time.sleep(8)

                            asyncio.run(tb.send_message(
                                f"🏁 Tag complete!\n"
                                f"Users tagged: {len(usernames)}\n"
                                f"Batches: {len(batches)} (5 per tweet)\n"
                                f"✅ {ok_count} posted | ❌ {fail_count} failed"
                            ))
                            add_log(f"Tag done: {ok_count} batches posted, {fail_count} failed")

                        threading.Thread(target=_run_tag, daemon=True).start()

                # ── /scrape <tweet_url> [--no-admins] ───────────────────────
                elif cmd == "/scrape":
                    raw_sc_args, skip_admins_sc = _parse_no_admins(args)
                    sc_parts = raw_sc_args.strip().split()
                    if not sc_parts:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /scrape <tweet_url> [--no-admins]\n\n"
                            "Scrapes all repliers from a tweet and saves their usernames.\n"
                            "Then use /tagusers to tag them under any post.\n\n"
                            "Options:\n"
                            "  --no-admins  skip verified/blue-tick accounts\n\n"
                            "Examples:\n"
                            "/scrape https://x.com/user/status/123\n"
                            "/scrape https://x.com/user/status/123 --no-admins"
                        ), disable_web_page_preview=True)
                    else:
                        sc_url = sc_parts[0]
                        filter_note = " | skipping verified" if skip_admins_sc else ""
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"🔍 Scraping repliers from:\n{sc_url}{filter_note}\n\nWill save usernames for use with /tagusers",
                            disable_web_page_preview=True
                        )

                        def _run_scrape(url=sc_url, skip=skip_admins_sc):
                            import re as _re, json as _json, sys as _sys
                            _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))

                            m = _re.search(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)", url)
                            src_id = m.group(2) if m else ""
                            if not src_id:
                                asyncio.run(tb.send_message(f"❌ Could not parse tweet ID from: {url}"))
                                return

                            add_log(f"Scrape: fetching replies from tweet {src_id}…")
                            sc = get_scraper()
                            usernames = []
                            admins_removed = 0

                            if sc:
                                try:
                                    replies = sc.search(query=f"conversation_id:{src_id}", limit=500, save=True, filter_replies=False)
                                    replies, _, admins_removed = _filter_replies(replies, skip, src_id)
                                except Exception as exc:
                                    asyncio.run(tb.send_message(f"❌ Scweet error: {exc}"))
                                    return
                                seen = set()
                                for r in replies:
                                    u = (r.get("user", {}).get("screen_name") or r.get("username", "")).strip()
                                    if u and u.lower() not in seen:
                                        seen.add(u.lower()); usernames.append(u)
                            else:
                                # GraphQL fallback — works on Render where Scweet is not installed
                                from twitter_post import scrape_replies_graphql as _srg, get_auth_from_config as _gac
                                auth_token, ct0 = _gac()
                                if not auth_token or not ct0:
                                    asyncio.run(tb.send_message("❌ No Twitter auth_token/ct0 set — add them in Settings."))
                                    return
                                result = _srg(src_id, auth_token, ct0, limit=500, no_admins=skip)
                                if not result.get("ok"):
                                    asyncio.run(tb.send_message(f"❌ GraphQL error: {result.get('error','Unknown')}"))
                                    return
                                usernames = result.get("users", [])
                                if not usernames:
                                    notice = result.get("message", "No replies found")
                                    asyncio.run(tb.send_message(f"⚠️ {notice}"))
                                    return

                            if not usernames:
                                asyncio.run(tb.send_message("⚠️ No users found in replies."))
                                return

                            save_path = os.path.join(os.path.dirname(__file__), "tools", "scraped_users.json")
                            with open(save_path, "w") as f:
                                _json.dump({"source": url, "users": usernames}, f, indent=2)

                            admin_note = f" ({admins_removed} verified skipped)" if admins_removed else ""
                            add_log(f"Scrape: saved {len(usernames)} users from tweet {src_id}")
                            asyncio.run(tb.send_message(
                                f"✅ Scraped & saved {len(usernames):,} unique users{admin_note}.\n"
                                f"Source: {url}\n\n"
                                f"Now run /tagusers to tag them under any post.\n"
                                f"Example: /tagusers https://x.com/yourprofile/status/999"
                            ))

                        threading.Thread(target=_run_scrape, daemon=True).start()

                # ── /retweeters <tweet_url> [count] [--no-admins] ────────────
                elif cmd == "/retweeters":
                    raw_rt_args, skip_admins_rt = _parse_no_admins(args)
                    raw_rt_args, rt_count = _parse_count(raw_rt_args)
                    rt_parts = raw_rt_args.strip().split()
                    if not rt_parts:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /retweeters <tweet_url> [count] [--no-admins]\n\n"
                            "Scrapes users who retweeted a post and saves their usernames.\n"
                            "Then use /tagusers to tag them under any post.\n\n"
                            "Options:\n"
                            "  count        max retweeters to fetch (default: 200)\n"
                            "  --no-admins  skip verified/blue-tick accounts\n\n"
                            "Examples:\n"
                            "/retweeters https://x.com/user/status/123\n"
                            "/retweeters https://x.com/user/status/123 500 --no-admins"
                        ), disable_web_page_preview=True)
                    else:
                        import re as _re3
                        rt_url   = rt_parts[0]
                        rt_limit = rt_count or 200
                        m3 = _re3.search(r"(?:x|twitter)\.com/[^/]+/status/(\d+)", rt_url)
                        rt_tid = m3.group(1) if m3 else ""
                        if not rt_tid:
                            await bot.send_message(chat_id=chat_id, text=f"❌ Could not parse tweet ID from: {rt_url}", disable_web_page_preview=True)
                        else:
                            filter_note = " | skipping verified" if skip_admins_rt else ""
                            await bot.send_message(
                                chat_id=chat_id,
                                text=f"🔁 Fetching up to {rt_limit} retweeters from:\n{rt_url}{filter_note}\n\nWill save usernames for use with /tagusers",
                                disable_web_page_preview=True
                            )

                            def _run_retweeters(tid=rt_tid, url=rt_url, lim=rt_limit, skip=skip_admins_rt):
                                import sys as _sys, json as _json2
                                _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
                                from twitter_post import scrape_retweeters as _sr, get_auth_from_config as _gac
                                auth_token, ct0 = _gac()
                                if not auth_token or not ct0:
                                    asyncio.run(tb.send_message("❌ No Twitter auth_token/ct0 set. Add them in Settings tab."))
                                    return
                                result = _sr(tid, auth_token, ct0, limit=lim, no_admins=skip)
                                if not result.get("ok"):
                                    asyncio.run(tb.send_message(f"❌ Error fetching retweeters: {result.get('error','Unknown')}"))
                                    return
                                users = result.get("users", [])
                                if not users:
                                    asyncio.run(tb.send_message("⚠️ No retweeters found (tweet may have 0 retweets or auth is expired)."))
                                    return
                                save_path = os.path.join(os.path.dirname(__file__), "tools", "scraped_users.json")
                                with open(save_path, "w") as _f:
                                    _json2.dump({"source": f"retweeters:{url}", "users": users}, _f, indent=2)
                                admin_note = " (verified skipped)" if skip else ""
                                add_log(f"Retweeters bot: saved {len(users)} users from tweet {tid}")
                                asyncio.run(tb.send_message(
                                    f"✅ Saved {len(users):,} retweeters{admin_note}.\n"
                                    f"Source: {url}\n\n"
                                    f"Now run /tagusers to tag them under any post.\n"
                                    f"Example: /tagusers https://x.com/yourprofile/status/999"
                                ))

                            threading.Thread(target=_run_retweeters, daemon=True).start()

                # ── /retweetpool <target_url> ─────────────────────────────────
                elif cmd == "/retweetpool":
                    rp_parts = args.strip().split() if args else []
                    if not rp_parts:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /retweetpool <tweet_url>\n\n"
                            "Retweets the target post using the same number of pool accounts\n"
                            "as the currently saved users list.\n\n"
                            "Example: /retweetpool https://x.com/user/status/999\n\n"
                            "Run /scrape or /retweeters first to build the saved list."
                        ), disable_web_page_preview=True)
                    else:
                        rp_url = rp_parts[0]
                        save_path = os.path.join(os.path.dirname(__file__), "tools", "scraped_users.json")
                        if not os.path.exists(save_path):
                            await bot.send_message(chat_id=chat_id, text="❌ No saved users found. Run /scrape or /retweeters first.")
                        else:
                            try:
                                with open(save_path) as _f:
                                    _saved = json.load(_f)
                                rp_count = len(_saved.get("users", []))
                                rp_src = _saved.get("source", "")
                            except Exception:
                                rp_count = 0; rp_src = ""
                            if rp_count == 0:
                                await bot.send_message(chat_id=chat_id, text="❌ Saved users list is empty. Run /scrape or /retweeters first.")
                            else:
                                await bot.send_message(
                                    chat_id=chat_id,
                                    text=f"🔁 Retweeting post with {rp_count} pool accounts…\nTarget: {rp_url}",
                                    disable_web_page_preview=True
                                )
                                def _run_retweetpool(url=rp_url, n=rp_count):
                                    import sys as _sys; _sys.path.insert(0, "tools")
                                    from twitter_post import bulk_engage, load_account_pool
                                    pool = load_account_pool()
                                    if n: pool = pool[:n]
                                    result = bulk_engage(url, action="retweet", accounts=pool, delay_min=2.0, delay_max=6.0)
                                    if "error" in result:
                                        asyncio.run(tb.send_message(f"❌ Retweet failed: {result['error']}"))
                                    else:
                                        asyncio.run(tb.send_message(
                                            f"🔁 Done! Retweeted post with {result['total']} accounts.\n"
                                            f"✅ {result['ok']} retweeted | ❌ {result['fail']} failed\n"
                                            f"Target: {url}"
                                        ))
                                threading.Thread(target=_run_retweetpool, daemon=True).start()

                # ── /tagusers <target_url> [count] [--no-admins] ─────────────
                elif cmd == "/tagusers":
                    raw_tu_args, skip_admins_tu = _parse_no_admins(args)
                    raw_tu_args, tu_count = _parse_count(raw_tu_args)
                    tu_parts = raw_tu_args.strip().split()
                    if not tu_parts:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /tagusers <target_url> [count] [--no-admins]\n\n"
                            "Tags the users saved by /scrape — 5 per reply — under the target tweet.\n\n"
                            "Options:\n"
                            "  count        max users to tag (default: all saved)\n"
                            "  --no-admins  skip verified accounts from the saved list\n\n"
                            "Examples:\n"
                            "/tagusers https://x.com/yourprofile/status/999\n"
                            "/tagusers https://x.com/yourprofile/status/999 50\n"
                            "/tagusers https://x.com/yourprofile/status/999 50 --no-admins\n\n"
                            "Run /scrape first to build the user list."
                        ), disable_web_page_preview=True)
                    else:
                        tu_url = tu_parts[0]
                        filter_note = " | skip verified" if skip_admins_tu else ""
                        count_note  = f" | max {tu_count} users" if tu_count else ""
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"🏷 TagUsers started.\nTarget: {tu_url}{filter_note}{count_note}\n\nLoading saved users…",
                            disable_web_page_preview=True
                        )

                        def _run_tagusers(tgt=tu_url, skip=skip_admins_tu, max_users=tu_count):
                            import re as _re, json as _json, sys as _sys
                            _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
                            import twitter_post as _tp

                            # ── 1. Load saved users ───────────────────────────
                            save_path = os.path.join(os.path.dirname(__file__), "tools", "scraped_users.json")
                            if not os.path.exists(save_path):
                                asyncio.run(tb.send_message(
                                    "❌ No saved users found. Run /scrape <tweet_url> first."
                                ))
                                return
                            try:
                                with open(save_path) as f:
                                    data = _json.load(f)
                                usernames = data.get("users", [])
                                source    = data.get("source", "unknown")
                            except Exception as exc:
                                asyncio.run(tb.send_message(f"❌ Could not read scraped_users.json: {exc}"))
                                return

                            if not usernames:
                                asyncio.run(tb.send_message("❌ Saved user list is empty. Run /scrape first."))
                                return

                            # Optionally filter verified from saved list
                            if skip:
                                usernames = [u for u in usernames if u]  # already filtered at scrape time

                            if max_users:
                                usernames = usernames[:max_users]

                            # ── 2. Resolve target tweet ID ────────────────────
                            m_tgt = _re.search(r"(?:x|twitter)\.com/[^/]+/status/(\d+)", tgt)
                            if not m_tgt:
                                asyncio.run(tb.send_message(f"❌ Could not parse target tweet ID from:\n{tgt}"))
                                return
                            target_id = m_tgt.group(1)

                            # ── 3. Load account pool ──────────────────────────
                            pool = _tp.load_account_pool()
                            if not pool:
                                asyncio.run(tb.send_message("❌ Account pool empty. Check tools/cookies.json."))
                                return

                            auth_token, ct0 = _tp.get_auth_from_config()
                            if not auth_token or not ct0:
                                asyncio.run(tb.send_message("❌ ct0 missing. Add both auth_token AND ct0 in Settings."))
                                return

                            batches = [usernames[i:i+5] for i in range(0, len(usernames), 5)]
                            asyncio.run(tb.send_message(
                                f"✅ {len(usernames)} saved users → {len(batches)} batches of 5.\n"
                                f"Source was: {source}\n"
                                f"Posting under: {tgt}"
                            ))

                            # ── 4. Post each batch of 5 ───────────────────────
                            ok_count   = 0
                            fail_count = 0
                            pool_idx   = 0

                            for i, batch in enumerate(batches):
                                post_text = " ".join(f"@{u}" for u in batch)

                                account   = pool[pool_idx % len(pool)]
                                pool_idx += 1
                                auth_tok  = account.get("cookies", {}).get("auth_token", "") or auth_token
                                ct0_val   = account.get("cookies", {}).get("ct0", "")        or ct0
                                acct_name = account.get("username", f"acct_{pool_idx}")

                                result = _tp.post_reply(post_text, target_id, auth_tok, ct0_val)
                                if result.get("ok"):
                                    ok_count += 1
                                    add_log(f"TagUsers: ✅ batch {i+1} via @{acct_name}: {post_text[:60]}")
                                else:
                                    fail_count += 1
                                    add_log(f"TagUsers: ❌ batch {i+1} failed: {result.get('error','?')}")
                                    if fail_count >= 3 and ok_count == 0:
                                        asyncio.run(tb.send_message(
                                            f"❌ Posting failing (3 errors, 0 successes). Stopping.\n"
                                            f"Error: {result.get('error','?')}\n"
                                            f"Check your ct0 cookie in Settings."
                                        ))
                                        return

                                if (i + 1) % 20 == 0:
                                    asyncio.run(tb.send_message(
                                        f"🏷 TagUsers progress: {i+1}/{len(batches)} batches\n"
                                        f"✅ {ok_count} posted | ❌ {fail_count} failed"
                                    ))

                                time.sleep(8)

                            asyncio.run(tb.send_message(
                                f"🏁 TagUsers complete!\n"
                                f"Users tagged: {len(usernames)}\n"
                                f"Batches: {len(batches)} (5 per tweet)\n"
                                f"✅ {ok_count} posted | ❌ {fail_count} failed"
                            ))
                            add_log(f"TagUsers done: {ok_count} batches posted, {fail_count} failed")

                        threading.Thread(target=_run_tagusers, daemon=True).start()

                # ── /like <tweet_url> ───────────────────────────────────────
                elif cmd == "/like":
                    if not args:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /like <tweet_url> [count]\n"
                            "  count — optional: how many accounts to use (default: all)\n\n"
                            "Examples:\n"
                            "/like https://x.com/user/status/123\n"
                            "/like https://x.com/user/status/123 50"
                        ))
                    else:
                        like_args, like_count = _parse_count(args)
                        tweet_url = like_args.split()[0]
                        count_note = f" ({like_count} accounts)" if like_count else " (all accounts)"
                        await bot.send_message(chat_id=chat_id, text=f"❤️ Liking tweet{count_note}…\n{tweet_url}", disable_web_page_preview=True)
                        def _run_like(url=tweet_url, n=like_count, cid=chat_id):
                            import sys as _sys; _sys.path.insert(0, "tools")
                            from twitter_post import bulk_engage, load_account_pool
                            pool = load_account_pool()
                            if n: pool = pool[:n]
                            result = bulk_engage(url, action="like", accounts=pool, delay_min=2.0, delay_max=6.0)
                            if "error" in result:
                                asyncio.run(tb.send_message(f"❌ Like failed: {result['error']}"))
                            else:
                                asyncio.run(tb.send_message(
                                    f"❤️ Like complete!\n"
                                    f"Tweet: {url}\n"
                                    f"Accounts used: {result['total']}\n"
                                    f"✅ {result['ok']} liked | ❌ {result['fail']} failed"
                                ))
                        threading.Thread(target=_run_like, daemon=True).start()

                # ── /retweet <tweet_url> [count] ─────────────────────────────
                elif cmd == "/retweet":
                    if not args:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /retweet <tweet_url> [count]\n"
                            "  count — optional: how many accounts to retweet with (default: all)\n\n"
                            "Examples:\n"
                            "/retweet https://x.com/user/status/123\n"
                            "/retweet https://x.com/user/status/123 50"
                        ), disable_web_page_preview=True)
                    else:
                        rt_args2, rt_count2 = _parse_count(args)
                        rt_url2 = rt_args2.split()[0]
                        count_note = f" ({rt_count2} accounts)" if rt_count2 else " (all accounts)"
                        await bot.send_message(chat_id=chat_id, text=f"🔁 Retweeting{count_note}…\n{rt_url2}", disable_web_page_preview=True)
                        def _run_retweet(url=rt_url2, n=rt_count2):
                            import sys as _sys; _sys.path.insert(0, "tools")
                            from twitter_post import bulk_engage, load_account_pool
                            pool = load_account_pool()
                            if n: pool = pool[:n]
                            result = bulk_engage(url, action="retweet", accounts=pool, delay_min=2.0, delay_max=6.0)
                            if "error" in result:
                                asyncio.run(tb.send_message(f"❌ Retweet failed: {result['error']}"))
                            else:
                                asyncio.run(tb.send_message(
                                    f"🔁 Retweet complete!\n"
                                    f"Tweet: {url}\n"
                                    f"Accounts used: {result['total']}\n"
                                    f"✅ {result['ok']} retweeted | ❌ {result['fail']} failed"
                                ))
                        threading.Thread(target=_run_retweet, daemon=True).start()

                # ── /comment <tweet_url> [count] <text> ──────────────────────
                elif cmd == "/comment":
                    cmt_args, cmt_count = _parse_count(args)
                    parts = cmt_args.split(None, 1)
                    if len(parts) < 2:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /comment <tweet_url> [count] <text>\n"
                            "  count — optional: how many accounts to use (default: all)\n\n"
                            "Examples:\n"
                            "/comment https://x.com/user/status/123 Great post!\n"
                            "/comment https://x.com/user/status/123 50 Great post!\n"
                            "/comment https://x.com/user/status/123 50 @elonmusk check this!"
                        ))
                    else:
                        tweet_url = parts[0]
                        comment_body = parts[1]
                        cb_parts = comment_body.split(None, 1)
                        mention_tag = ""
                        if cb_parts[0].startswith("@"):
                            mention_tag = cb_parts[0]
                            comment_body = cb_parts[1] if len(cb_parts) > 1 else ""
                        count_note = f" ({cmt_count} accounts)" if cmt_count else " (all accounts)"
                        await bot.send_message(chat_id=chat_id, text=f"💬 Commenting on tweet{count_note}…\n{tweet_url}", disable_web_page_preview=True)
                        def _run_comment(url=tweet_url, text=comment_body, mention=mention_tag, n=cmt_count):
                            import sys as _sys; _sys.path.insert(0, "tools")
                            from twitter_post import bulk_engage, load_account_pool
                            pool = load_account_pool()
                            if n: pool = pool[:n]
                            result = bulk_engage(url, action="comment", comment_text=text, mention=mention, accounts=pool, delay_min=4.0, delay_max=10.0)
                            if "error" in result:
                                asyncio.run(tb.send_message(f"❌ Comment failed: {result['error']}"))
                            else:
                                asyncio.run(tb.send_message(
                                    f"💬 Comment complete!\n"
                                    f"Tweet: {url}\n"
                                    f"Text: {mention+' ' if mention else ''}{text}\n"
                                    f"Accounts used: {result['total']}\n"
                                    f"✅ {result['ok']} posted | ❌ {result['fail']} failed"
                                ))
                        threading.Thread(target=_run_comment, daemon=True).start()

                # ── /engage <tweet_url> [count] <text> ───────────────────────
                elif cmd == "/engage":
                    eng_args, eng_count = _parse_count(args)
                    parts = eng_args.split(None, 1)
                    if len(parts) < 2:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /engage <tweet_url> [count] <comment_text>\n"
                            "  count — optional: how many accounts to use (default: all)\n"
                            "Likes AND comments with the specified accounts.\n\n"
                            "Examples:\n"
                            "/engage https://x.com/user/status/123 Amazing project!\n"
                            "/engage https://x.com/user/status/123 50 Amazing project!"
                        ))
                    else:
                        tweet_url = parts[0]
                        comment_body = parts[1]
                        cb_parts = comment_body.split(None, 1)
                        mention_tag = ""
                        if cb_parts[0].startswith("@"):
                            mention_tag = cb_parts[0]
                            comment_body = cb_parts[1] if len(cb_parts) > 1 else ""
                        count_note = f" ({eng_count} accounts)" if eng_count else " (all accounts)"
                        await bot.send_message(chat_id=chat_id, text=f"🚀 Engaging (like + comment){count_note}…\n{tweet_url}", disable_web_page_preview=True)
                        def _run_engage(url=tweet_url, text=comment_body, mention=mention_tag, n=eng_count):
                            import sys as _sys; _sys.path.insert(0, "tools")
                            from twitter_post import bulk_engage, load_account_pool
                            pool = load_account_pool()
                            if n: pool = pool[:n]
                            result = bulk_engage(url, action="both", comment_text=text, mention=mention, accounts=pool, delay_min=4.0, delay_max=10.0)
                            if "error" in result:
                                asyncio.run(tb.send_message(f"❌ Engage failed: {result['error']}"))
                            else:
                                asyncio.run(tb.send_message(
                                    f"🚀 Engage complete!\n"
                                    f"Tweet: {url}\n"
                                    f"Text: {mention+' ' if mention else ''}{text}\n"
                                    f"Accounts used: {result['total']}\n"
                                    f"✅ {result['ok']} accounts | ❌ {result['fail']} failed"
                                ))
                        threading.Thread(target=_run_engage, daemon=True).start()

                # ── /complaints <query> ──────────────────────────────────────
                elif cmd == "/complaints":
                    if not args:
                        await bot.send_message(chat_id=chat_id, text="Usage: /complaints your topic\nExample: /complaints your brand name")
                    else:
                        query = args
                        since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
                        until = datetime.now().strftime("%Y-%m-%d")
                        await bot.send_message(chat_id=chat_id, text=f"▶️ Searching complaints for \"{query}\"… results will appear in the group.", disable_web_page_preview=True)
                        def _run_complaints(q=query, s_=since, u=until):
                            sc = get_scraper()
                            if not sc:
                                asyncio.run(tb.send_message("❌ No Twitter auth_token set. Add it in the dashboard Settings tab."))
                                return
                            try:
                                tweets = sc.search(query=q, since=s_, until=u, limit=300, save=True)
                                add_log(f"Complaints \"{q}\": {len(tweets)} tweets")
                                asyncio.run(tb.send_complaints_to_telegram(tweets, q, complaints_only=True))
                            except Exception as e:
                                asyncio.run(tb.send_message(f"❌ Error searching complaints \"{q}\": {e}"))
                        threading.Thread(target=_run_complaints, daemon=True).start()

        except Exception as e:
            add_log(f"Telegram poll error: {e}")
            await asyncio.sleep(5)

        await asyncio.sleep(1)


def start_telegram_listener():
    asyncio.run(handle_telegram_commands())


# ─── Flask routes ─────────────────────────────────────────────────────────────

@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def api_get_config():
    config = load_config()
    config.pop("twitter_auth_token", None)  # never expose token via API
    return jsonify(config)


@app.route("/api/config", methods=["POST"])
def api_save_config():
    data = request.json
    existing = load_config()
    # Preserve auth token if not sent
    if "twitter_auth_token" not in data or not data["twitter_auth_token"]:
        data["twitter_auth_token"] = existing.get("twitter_auth_token", "")
    save_config(data)
    # Restart scheduler with new interval
    global scheduler_thread
    STATE["running"] = False
    time.sleep(1)
    STATE["interval_minutes"] = data.get("check_interval_minutes", 60)
    schedule.clear()
    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()
    return jsonify({"ok": True})


ENV_FILE = ".env"


def load_env_secrets() -> dict:
    """Load secrets from .env file (fallback when env vars not set by platform)."""
    secrets = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    secrets[k.strip()] = v.strip().strip('"').strip("'")
    return secrets


def save_env_secret(key: str, value: str):
    """Write/update a single key in the .env file."""
    secrets = load_env_secrets()
    secrets[key] = value
    lines = [f'{k}="{v}"' for k, v in secrets.items()]
    with open(ENV_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
    # Also apply immediately to the running process
    os.environ[key] = value


def get_secret(key: str, default: str = "") -> str:
    """Get a secret: env var first, then .env file, then default."""
    val = os.environ.get(key, "")
    if val:
        return val
    return load_env_secrets().get(key, default)


@app.route("/api/secrets", methods=["GET"])
def api_get_secrets():
    """Return masked status of each secret (never expose actual values)."""
    return jsonify({
        "has_twitter_token": bool(get_secret("TWITTER_AUTH_TOKEN") or (
            load_config().get("twitter_auth_token", "") not in ("", "YOUR_AUTH_TOKEN_HERE")
        )),
        "has_telegram_token": bool(get_secret("TELEGRAM_BOT_TOKEN")),
        "has_chat_id": bool(get_secret("TELEGRAM_CHAT_ID")),
        "telegram_chat_id_preview": (
            get_secret("TELEGRAM_CHAT_ID")[:6] + "…" if get_secret("TELEGRAM_CHAT_ID") else ""
        ),
    })


@app.route("/api/secrets", methods=["POST"])
def api_save_secrets():
    data = request.json
    saved = []

    twitter_token = data.get("twitter_token", "").strip()
    if twitter_token:
        config = load_config()
        config["twitter_auth_token"] = twitter_token
        save_config(config)
        save_env_secret("TWITTER_AUTH_TOKEN", twitter_token)
        saved.append("Twitter auth_token")

    twitter_ct0 = data.get("twitter_ct0", "").strip()
    if twitter_ct0:
        config = load_config()
        config["twitter_ct0"] = twitter_ct0
        save_config(config)
        save_env_secret("TWITTER_CT0", twitter_ct0)
        saved.append("Twitter ct0 (CSRF token)")

    tg_token = data.get("telegram_token", "").strip()
    if tg_token:
        save_env_secret("TELEGRAM_BOT_TOKEN", tg_token)
        saved.append("Telegram bot token")

    chat_id = data.get("telegram_chat_id", "").strip()
    if chat_id:
        save_env_secret("TELEGRAM_CHAT_ID", chat_id)
        saved.append("Telegram chat ID")

    # ── Auto-sync changed secrets to Render ────────────────────────────────────
    render_synced = False
    render_key = os.environ.get("RENDER_API_KEY", "")
    render_svc  = os.environ.get("RENDER_SERVICE_ID", "srv-d7s2rud7vvec738tlff0")
    if render_key and saved:
        try:
            import urllib.request as _ur, urllib.error as _ue
            def _rreq(method, path, payload=None):
                url = f"https://api.render.com/v1{path}"
                _d  = json.dumps(payload).encode() if payload else None
                req = _ur.Request(url, data=_d, method=method, headers={
                    "Authorization": f"Bearer {render_key}",
                    "Accept": "application/json", "Content-Type": "application/json"
                })
                with _ur.urlopen(req, timeout=15) as r:
                    return r.status, r.read()

            # Get current Render env vars
            _, body = _rreq("GET", f"/services/{render_svc}/env-vars")
            current_evs = json.loads(body) if body.strip() else []
            ev_map = {}
            for item in current_evs:
                ev = item.get("envVar", item)
                ev_map[ev.get("key", "")] = ev.get("value", "")

            # Apply changes
            cfg_now = load_config()
            overrides = {
                "TWITTER_AUTH_TOKEN": cfg_now.get("twitter_auth_token", ev_map.get("TWITTER_AUTH_TOKEN", "")),
                "TWITTER_CT0":        cfg_now.get("twitter_ct0",        ev_map.get("TWITTER_CT0", "")),
            }
            if tg_token:  overrides["TELEGRAM_BOT_TOKEN"] = tg_token
            if chat_id:   overrides["TELEGRAM_CHAT_ID"]   = chat_id

            ev_map.update({k: v for k, v in overrides.items() if v})
            env_vars = [{"key": k, "value": v} for k, v in ev_map.items()]

            st, _ = _rreq("PUT", f"/services/{render_svc}/env-vars", env_vars)
            if st == 200:
                # Trigger redeploy so new vars take effect
                _rreq("POST", f"/services/{render_svc}/deploys", {})
                render_synced = True
                saved.append("✅ Render env vars updated + redeploy triggered")
        except Exception as _e:
            saved.append(f"⚠️ Render sync failed: {_e}")

    return jsonify({"ok": True, "saved": saved, "render_synced": render_synced})


@app.route("/api/token", methods=["POST"])
def api_save_token():
    data = request.json
    config = load_config()
    config["twitter_auth_token"] = data.get("token", "")
    save_config(config)
    return jsonify({"ok": True})


@app.route("/api/account-pool")
def api_account_pool():
    """Return info about the multi-account scraping pool for dashboard display."""
    cookies_file = "tools/cookies.json"
    if not os.path.exists(cookies_file):
        return jsonify({"total": 0, "active": 0, "cooldown_count": 0, "accounts": []})
    try:
        with open(cookies_file) as f:
            pool = json.load(f)
    except Exception:
        return jsonify({"total": 0, "active": 0, "cooldown_count": 0, "accounts": []})

    now = time.time()
    # Expire old cooldowns
    STATE["pool_cooldowns"] = {k: v for k, v in STATE["pool_cooldowns"].items() if v > now}
    cooldowns = STATE["pool_cooldowns"]

    accounts = []
    for entry in pool:
        uname = entry.get("username", "?")
        cd_until = cooldowns.get(uname)
        if cd_until and cd_until > now:
            mins_left = max(1, int((cd_until - now) / 60))
            status = f"cooldown ({mins_left}m)"
            state = "cooldown"
        else:
            status = "active"
            state = "active"
        accounts.append({"username": uname, "status": status, "state": state})

    in_cooldown = sum(1 for a in accounts if a["state"] == "cooldown")
    return jsonify({
        "total": len(pool),
        "active": len(pool) - in_cooldown,
        "cooldown_count": in_cooldown,
        "accounts": accounts,
    })


@app.route("/api/engage", methods=["POST"])
def api_engage():
    data = request.json or {}
    tweet_url    = data.get("tweet_url", "").strip()
    action       = data.get("action", "like").strip()
    comment_text = data.get("comment_text", "").strip()
    mention      = data.get("mention", "").strip()
    count        = data.get("count")
    try: count = int(count) if count else None
    except (ValueError, TypeError): count = None

    if not tweet_url:
        return jsonify({"ok": False, "error": "tweet_url is required"}), 400
    if action not in ("like", "comment", "both", "retweet"):
        return jsonify({"ok": False, "error": "action must be like, comment, both, or retweet"}), 400
    if action in ("comment", "both") and not comment_text and not mention:
        return jsonify({"ok": False, "error": "comment_text or mention is required for comment/both"}), 400

    import uuid
    job_id = uuid.uuid4().hex[:8]
    STATE.setdefault("engage_jobs", {})[job_id] = {
        "tweet_url": tweet_url, "action": action,
        "status": "running", "done": 0, "total": 0,
        "ok": 0, "fail": 0, "started_at": datetime.now().isoformat(),
        "finished_at": None,
    }

    def _run(jid=job_id, url=tweet_url, act=action, text=comment_text, tag=mention, n=count):
        import sys as _sys; _sys.path.insert(0, "tools")
        from twitter_post import bulk_engage, load_account_pool
        job = STATE["engage_jobs"][jid]
        pool = load_account_pool()
        if n: pool = pool[:n]
        job["total"] = len(pool)

        def progress(done, total, username, status_str):
            job["done"] = done
            job["total"] = total
            job["ok"]   = sum(1 for r in job.get("results", []) if r.get("ok"))
            job["fail"] = done - job["ok"]

        job["results"] = []
        result = bulk_engage(url, action=act, comment_text=text, mention=tag,
                             accounts=pool, delay_min=3.0, delay_max=8.0, progress_cb=progress)
        job.update({
            "status": "done",
            "ok": result.get("ok", 0),
            "fail": result.get("fail", 0),
            "total": result.get("total", 0),
            "done": result.get("total", 0),
            "finished_at": datetime.now().isoformat(),
            "error": result.get("error", ""),
        })
        add_log(f"Engage job {jid}: {act} on {url} — ✅{result.get('ok',0)} ❌{result.get('fail',0)}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/engage/<job_id>")
def api_engage_status(job_id):
    job = STATE.get("engage_jobs", {}).get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


# ── Reply All ──────────────────────────────────────────────────────────────────

@app.route("/api/replyall", methods=["POST"])
def api_replyall():
    data        = request.json or {}
    tweet_url   = data.get("tweet_url", "").strip()
    reply_text  = data.get("reply_text", "").strip()
    mention     = data.get("mention", "").strip()
    no_admins   = bool(data.get("no_admins", False))
    count       = data.get("count")
    try: count = int(count) if count else None
    except (ValueError, TypeError): count = None

    if not tweet_url:
        return jsonify({"ok": False, "error": "tweet_url is required"}), 400
    if not reply_text and not mention:
        return jsonify({"ok": False, "error": "reply_text is required"}), 400

    import uuid
    job_id = uuid.uuid4().hex[:8]
    STATE.setdefault("replyall_jobs", {})[job_id] = {
        "tweet_url": tweet_url, "status": "running",
        "done": 0, "total": 0, "ok": 0, "fail": 0,
        "started_at": datetime.now().isoformat(), "finished_at": None, "log": []
    }

    def _run(jid=job_id, url=tweet_url, rtext=reply_text, mtag=mention, skip=no_admins, n=count):
        import re as _re, sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
        import twitter_post as _tp
        job = STATE["replyall_jobs"][jid]

        def _log(msg):
            job["log"] = (job.get("log") or [])[-49:] + [msg]
            add_log(msg)

        sc = get_scraper()
        if not sc:
            job.update({"status": "error", "log": ["❌ No Twitter auth_token set"]})
            return

        m = _re.search(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)", url)
        tweet_id = m.group(2) if m else ""
        if not tweet_id:
            job.update({"status": "error", "log": [f"❌ Could not parse tweet ID from {url}"]})
            return

        _log(f"Scraping replies for tweet {tweet_id}…")
        try:
            replies = sc.search(query=f"conversation_id:{tweet_id}", limit=500, save=True, filter_replies=False)
            replies, _, admins_removed = _filter_replies(replies, skip, tweet_id)
        except Exception as exc:
            job.update({"status": "error", "log": [f"❌ Scrape error: {exc}"]})
            return

        if not replies:
            job.update({"status": "done", "log": ["⚠️ No replies found for that tweet."]})
            return

        pool = _tp.load_account_pool()
        if n: pool = pool[:n]
        if not pool:
            job.update({"status": "error", "log": ["❌ Account pool empty"]})
            return

        job["total"] = len(replies)
        admin_note = f" ({admins_removed} verified skipped)" if admins_removed else ""
        _log(f"Found {len(replies)} replies{admin_note}. Replying with {len(pool)} accounts…")

        ok_count = fail_count = pool_idx = 0
        for i, reply in enumerate(replies):
            commenter      = (reply.get("user", {}).get("screen_name") or reply.get("username", "unknown"))
            reply_tweet_id = str(reply.get("id") or reply.get("tweet_id") or "")
            if not reply_tweet_id:
                fail_count += 1; job["fail"] = fail_count; continue

            account = pool[pool_idx % len(pool)]; pool_idx += 1
            auth_tok  = account.get("cookies", {}).get("auth_token", "")
            ct0_val   = account.get("cookies", {}).get("ct0", "")
            if not auth_tok or not ct0_val:
                fail_count += 1; job["fail"] = fail_count; continue

            parts_txt = []
            if mtag: parts_txt.append(mtag)
            parts_txt.append(f"@{commenter}")
            if rtext: parts_txt.append(rtext)
            post_text = " ".join(parts_txt)[:280]

            res = _tp.post_reply(post_text, reply_tweet_id, auth_tok, ct0_val)
            if res.get("ok"):
                ok_count += 1
            else:
                fail_count += 1
            job["done"] = i + 1
            job["ok"]   = ok_count
            job["fail"] = fail_count
            time.sleep(5)

        job.update({"status": "done", "finished_at": datetime.now().isoformat()})
        _log(f"ReplyAll done: ✅{ok_count} sent ❌{fail_count} failed")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/replyall/<job_id>")
def api_replyall_status(job_id):
    job = STATE.get("replyall_jobs", {}).get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


# ── Scrape & Tag Users ─────────────────────────────────────────────────────────

@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    data      = request.json or {}
    tweet_url = data.get("tweet_url", "").strip()
    no_admins = bool(data.get("no_admins", False))
    if not tweet_url:
        return jsonify({"ok": False, "error": "tweet_url is required"}), 400

    import uuid
    job_id = uuid.uuid4().hex[:8]
    STATE.setdefault("scrape_jobs", {})[job_id] = {
        "status": "running", "count": 0, "source": tweet_url,
        "started_at": datetime.now().isoformat(), "finished_at": None, "message": "Scraping…"
    }

    def _run(jid=job_id, url=tweet_url, skip=no_admins):
        import re as _re, json as _jj, sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
        job = STATE["scrape_jobs"][jid]

        m = _re.search(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)", url)
        src_id = m.group(2) if m else ""
        if not src_id:
            job.update({"status": "error", "message": f"❌ Could not parse tweet ID from {url}"}); return

        sc = get_scraper()
        usernames = []
        admins_removed = 0

        if sc:
            # Path 1: Scweet (available locally, not on Render)
            try:
                job.update({"message": "🔍 Scraping replies via Scweet…"})
                replies = sc.search(query=f"conversation_id:{src_id}", limit=500, save=True, filter_replies=False)
                replies, _, admins_removed = _filter_replies(replies, skip, src_id)
            except Exception as exc:
                job.update({"status": "error", "message": f"❌ Scweet error: {exc}"}); return

            seen = set()
            for r in replies:
                u = (r.get("user", {}).get("screen_name") or r.get("username", "")).strip()
                if u and u.lower() not in seen:
                    seen.add(u.lower()); usernames.append(u)
        else:
            # Path 2: GraphQL TweetDetail fallback (works on Render without Scweet)
            from twitter_post import scrape_replies_graphql as _srg, get_auth_from_config as _gac
            auth_token, ct0 = _gac()
            if not auth_token or not ct0:
                job.update({"status": "error", "message": "❌ No Twitter auth_token/ct0 set — add them in Settings"}); return

            job.update({"message": "🔍 Scraping replies via GraphQL…"})
            result = _srg(src_id, auth_token, ct0, limit=500, no_admins=skip)
            if not result.get("ok"):
                job.update({"status": "error", "message": f"❌ {result.get('error','Unknown error')}"}); return

            usernames = result.get("users", [])
            if not usernames:
                notice = result.get("message", "No replies found")
                job.update({"status": "done", "count": 0, "finished_at": datetime.now().isoformat(),
                            "message": f"⚠️ {notice}"}); return

        save_path = os.path.join(os.path.dirname(__file__), "tools", "scraped_users.json")
        with open(save_path, "w") as f:
            _jj.dump({"source": url, "users": usernames}, f, indent=2)

        admin_note = f" ({admins_removed} verified skipped)" if admins_removed else ""
        add_log(f"Scrape API: saved {len(usernames)} users from tweet {src_id}")
        job.update({
            "status": "done", "count": len(usernames), "source": url,
            "finished_at": datetime.now().isoformat(),
            "message": f"✅ Saved {len(usernames)} users{admin_note}"
        })

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/scrape/<job_id>")
def api_scrape_status(job_id):
    job = STATE.get("scrape_jobs", {}).get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


@app.route("/api/scrape/clear", methods=["POST"])
def api_scrape_clear():
    save_path = os.path.join(os.path.dirname(__file__), "tools", "scraped_users.json")
    if os.path.exists(save_path):
        os.remove(save_path)
        add_log("Scraped users list cleared via dashboard")
    return jsonify({"ok": True})


@app.route("/api/scrape/saved")
def api_scrape_saved():
    save_path = os.path.join(os.path.dirname(__file__), "tools", "scraped_users.json")
    if not os.path.exists(save_path):
        return jsonify({"count": 0, "source": None, "users": []})
    try:
        with open(save_path) as f:
            data = json.load(f)
        return jsonify({"count": len(data.get("users", [])), "source": data.get("source", ""), "users": data.get("users", [])[:10]})
    except Exception:
        return jsonify({"count": 0, "source": None, "users": []})


@app.route("/api/retweeters", methods=["POST"])
def api_retweeters():
    data      = request.json or {}
    tweet_url = data.get("tweet_url", "").strip()
    no_admins = bool(data.get("no_admins", False))
    limit     = data.get("limit", 999999)
    try: limit = int(limit)
    except (ValueError, TypeError): limit = 999999
    if not tweet_url:
        return jsonify({"ok": False, "error": "tweet_url is required"}), 400

    import uuid, re as _re2
    m = _re2.search(r"(?:x|twitter)\.com/[^/]+/status/(\d+)", tweet_url)
    tweet_id = m.group(1) if m else ""
    if not tweet_id:
        return jsonify({"ok": False, "error": "Could not parse tweet ID from URL"}), 400

    job_id = uuid.uuid4().hex[:8]
    STATE.setdefault("retweet_jobs", {})[job_id] = {
        "status": "running", "count": 0, "source": tweet_url,
        "started_at": datetime.now().isoformat(), "finished_at": None,
        "message": "Fetching retweeters…"
    }

    def _run(jid=job_id, tid=tweet_id, url=tweet_url, skip=no_admins, lim=limit):
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
        from twitter_post import scrape_retweeters as _sr, get_auth_from_config as _gac
        job = STATE["retweet_jobs"][jid]

        auth_token, ct0 = _gac()
        if not auth_token or not ct0:
            job.update({"status": "error", "message": "❌ No Twitter auth_token/ct0 set — add them in Settings"}); return

        result = _sr(tid, auth_token, ct0, limit=lim, no_admins=skip)
        if not result.get("ok"):
            job.update({"status": "error", "message": f"❌ {result.get('error', 'Unknown error')}"}); return

        users = result.get("users", [])
        if not users:
            job.update({"status": "done", "count": 0, "message": "⚠️ No retweeters found (tweet may have 0 retweets or auth is expired)"}); return

        save_path = os.path.join(os.path.dirname(__file__), "tools", "scraped_users.json")
        with open(save_path, "w") as f:
            json.dump({"source": f"retweeters:{url}", "users": users}, f, indent=2)

        add_log(f"Retweeters: saved {len(users)} users from tweet {tid}")
        job.update({
            "status": "done", "count": len(users), "source": url,
            "finished_at": datetime.now().isoformat(),
            "message": f"✅ Saved {len(users)} retweeters"
        })

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/retweeters/<job_id>")
def api_retweeters_status(job_id):
    job = STATE.get("retweet_jobs", {}).get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


@app.route("/api/tagusers", methods=["POST"])
def api_tagusers():
    data       = request.json or {}
    target_url = data.get("target_url", "").strip()
    no_admins  = bool(data.get("no_admins", False))
    count      = data.get("count")
    try: count = int(count) if count else None
    except (ValueError, TypeError): count = None

    if not target_url:
        return jsonify({"ok": False, "error": "target_url is required"}), 400

    save_path = os.path.join(os.path.dirname(__file__), "tools", "scraped_users.json")
    if not os.path.exists(save_path):
        return jsonify({"ok": False, "error": "No saved users. Run /scrape first."}), 400

    import uuid
    job_id = uuid.uuid4().hex[:8]
    STATE.setdefault("tagusers_jobs", {})[job_id] = {
        "status": "running", "done": 0, "total": 0, "ok": 0, "fail": 0,
        "started_at": datetime.now().isoformat(), "finished_at": None, "message": "Starting…"
    }

    def _run(jid=job_id, tgt=target_url, skip=no_admins, max_u=count):
        import re as _re, json as _jj, sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
        import twitter_post as _tp
        job = STATE["tagusers_jobs"][jid]

        try:
            with open(save_path) as f:
                saved = _jj.load(f)
            usernames = saved.get("users", [])
        except Exception as exc:
            job.update({"status": "error", "message": f"❌ Could not read saved users: {exc}"}); return

        if max_u: usernames = usernames[:max_u]
        if not usernames:
            job.update({"status": "done", "message": "⚠️ Saved user list is empty"}); return

        m_tgt = _re.search(r"(?:x|twitter)\.com/[^/]+/status/(\d+)", tgt)
        if not m_tgt:
            job.update({"status": "error", "message": f"❌ Could not parse target tweet ID from {tgt}"}); return
        target_id = m_tgt.group(1)

        pool = _tp.load_account_pool()
        if not pool:
            job.update({"status": "error", "message": "❌ Account pool empty"}); return

        auth_token, ct0 = _tp.get_auth_from_config()
        if not auth_token or not ct0:
            job.update({"status": "error", "message": "❌ ct0 missing — add both auth_token AND ct0 in Settings"}); return

        batches  = [usernames[i:i+5] for i in range(0, len(usernames), 5)]
        job["total"]   = len(batches)
        job["message"] = f"Tagging {len(usernames)} users in {len(batches)} batches…"
        add_log(f"TagUsers API: {len(usernames)} users → {len(batches)} batches on {tgt}")

        ok_count = fail_count = pool_idx = 0
        for i, batch in enumerate(batches):
            post_text = " ".join(f"@{u}" for u in batch)
            account   = pool[pool_idx % len(pool)]; pool_idx += 1
            auth_tok  = account.get("cookies", {}).get("auth_token", "") or auth_token
            ct0_val   = account.get("cookies", {}).get("ct0", "")        or ct0

            res = _tp.post_reply(post_text, target_id, auth_tok, ct0_val)
            if res.get("ok"):
                ok_count += 1
            else:
                fail_count += 1
                if fail_count >= 3 and ok_count == 0:
                    job.update({"status": "error", "message": f"❌ Posting failing: {res.get('error','?')}"}); return
            job["done"] = i + 1
            job["ok"]   = ok_count
            job["fail"] = fail_count
            time.sleep(8)

        job.update({
            "status": "done", "finished_at": datetime.now().isoformat(),
            "message": f"✅ Done! {ok_count} batches posted, {fail_count} failed"
        })
        add_log(f"TagUsers API done: ✅{ok_count} ❌{fail_count}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/tagusers/<job_id>")
def api_tagusers_status(job_id):
    job = STATE.get("tagusers_jobs", {}).get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


# ── Tag Followers / Following ──────────────────────────────────────────────────

@app.route("/api/tagfollowers", methods=["POST"])
def api_tagfollowers():
    data       = request.json or {}
    kind       = data.get("kind", "").strip()       # "followers" or "following"
    username   = data.get("username", "").strip()
    target_url = data.get("target_url", "").strip()
    count      = data.get("count")
    try: count = int(count) if count else None
    except (ValueError, TypeError): count = None

    if kind not in ("followers", "following"):
        return jsonify({"ok": False, "error": "kind must be 'followers' or 'following'"}), 400
    if not username:
        return jsonify({"ok": False, "error": "username is required"}), 400
    if not target_url:
        return jsonify({"ok": False, "error": "target_url is required"}), 400

    cache_file = _cache_path(kind, username)
    if not os.path.exists(cache_file):
        return jsonify({"ok": False, "error": f"No cached {kind} for @{username}. Run /{kind} {username} in Telegram first."}), 400

    import uuid
    job_id = uuid.uuid4().hex[:8]
    STATE.setdefault("tagfollowers_jobs", {})[job_id] = {
        "status": "running", "done": 0, "total": 0, "ok": 0, "fail": 0,
        "started_at": datetime.now().isoformat(), "finished_at": None, "message": "Starting…"
    }

    def _run(jid=job_id, knd=kind, uname=username, tgt=target_url, max_u=count):
        import re as _re, json as _jj, sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
        import twitter_post as _tp
        job = STATE["tagfollowers_jobs"][jid]

        try:
            with open(cache_file) as f:
                cached = _jj.load(f)
            # Cache is list of user dicts with "username" / "screen_name" keys, or plain strings
            usernames = []
            for item in cached:
                if isinstance(item, str):
                    usernames.append(item)
                elif isinstance(item, dict):
                    u = item.get("username") or item.get("screen_name") or item.get("handle") or ""
                    if u: usernames.append(u.lstrip("@"))
        except Exception as exc:
            job.update({"status": "error", "message": f"❌ Could not read cache: {exc}"}); return

        if max_u: usernames = usernames[:max_u]
        if not usernames:
            job.update({"status": "done", "message": "⚠️ Cache is empty"}); return

        m_tgt = _re.search(r"(?:x|twitter)\.com/[^/]+/status/(\d+)", tgt)
        if not m_tgt:
            job.update({"status": "error", "message": f"❌ Could not parse target tweet ID from {tgt}"}); return
        target_id = m_tgt.group(1)

        pool = _tp.load_account_pool()
        if not pool:
            job.update({"status": "error", "message": "❌ Account pool empty"}); return

        auth_token, ct0 = _tp.get_auth_from_config()
        if not auth_token or not ct0:
            job.update({"status": "error", "message": "❌ ct0 missing — add both auth_token AND ct0 in Settings"}); return

        batches = [usernames[i:i+5] for i in range(0, len(usernames), 5)]
        job["total"]   = len(batches)
        job["message"] = f"Tagging {len(usernames)} {knd} of @{uname} in {len(batches)} batches…"
        add_log(f"TagFollowers API: {len(usernames)} {knd} → {len(batches)} batches on {tgt}")

        ok_count = fail_count = pool_idx = 0
        for i, batch in enumerate(batches):
            post_text = " ".join(f"@{u}" for u in batch)
            account   = pool[pool_idx % len(pool)]; pool_idx += 1
            auth_tok  = account.get("cookies", {}).get("auth_token", "") or auth_token
            ct0_val   = account.get("cookies", {}).get("ct0", "")        or ct0

            res = _tp.post_reply(post_text, target_id, auth_tok, ct0_val)
            if res.get("ok"):
                ok_count += 1
            else:
                fail_count += 1
                if fail_count >= 3 and ok_count == 0:
                    job.update({"status": "error", "message": f"❌ Posting failing: {res.get('error','?')}"}); return
            job["done"] = i + 1
            job["ok"]   = ok_count
            job["fail"] = fail_count
            time.sleep(8)

        job.update({
            "status": "done", "finished_at": datetime.now().isoformat(),
            "message": f"✅ Done! {ok_count} batches posted, {fail_count} failed"
        })
        add_log(f"TagFollowers API done: ✅{ok_count} ❌{fail_count}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/tagfollowers/<job_id>")
def api_tagfollowers_status(job_id):
    job = STATE.get("tagfollowers_jobs", {}).get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


@app.route("/api/cache-status")
def api_cache_status():
    """Return info about all cached scrape results (for dashboard display)."""
    cache_dir = _cache_dir()
    results = []
    if os.path.exists(cache_dir):
        import glob as _glob
        for f in sorted(_glob.glob(os.path.join(cache_dir, "*.json"))):
            fname = os.path.basename(f)
            # Parse kind_username.json
            parts = fname.replace(".json", "").split("_", 1)
            if len(parts) != 2:
                continue
            kind, username = parts
            try:
                with open(f) as fh:
                    data = json.load(fh)
                count = len(data) if isinstance(data, list) else 0
            except Exception:
                count = 0
            offset_file = f.replace(".json", ".offset")
            offset = 0
            if os.path.exists(offset_file):
                try:
                    with open(offset_file) as fh:
                        offset = int(fh.read().strip())
                except Exception:
                    pass
            mtime = os.path.getmtime(f)
            results.append({
                "kind": kind,
                "username": username,
                "count": count,
                "offset": offset,
                "remaining": max(0, count - offset),
                "scraped_at": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
            })
    return jsonify(results)


@app.route("/api/status")
def api_status():
    config = load_config()
    token = os.environ.get("TWITTER_AUTH_TOKEN", "") or config.get("twitter_auth_token", "")
    has_token = bool(token and token not in ("", "YOUR_AUTH_TOKEN_HERE"))
    return jsonify({
        "running": STATE["running"],
        "last_check": STATE["last_check"],
        "next_check": STATE["next_check"],
        "interval_minutes": STATE["interval_minutes"],
        "has_token": has_token,
        "logs": STATE["logs"][:20],
    })


@app.route("/api/check", methods=["POST"])
def api_check_now():
    threading.Thread(target=run_checks_sync, args=("Dashboard",), daemon=True).start()
    return jsonify({"ok": True, "message": "Check started"})


@app.route("/api/start", methods=["POST"])
def api_start():
    global scheduler_thread
    if not STATE["running"]:
        scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
        scheduler_thread.start()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    STATE["running"] = False
    schedule.clear()
    return jsonify({"ok": True})


# ─── Health check (required by Render) ───────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "running": STATE["running"]}), 200


# ─── Startup — works with both `python app.py` and gunicorn ──────────────────

def _start_background_services():
    tg_thread = threading.Thread(target=start_telegram_listener, daemon=True)
    tg_thread.start()
    sched_thread = threading.Thread(target=start_scheduler, daemon=True)
    sched_thread.start()
    add_log("App started")


# gunicorn imports this module, so we use a flag to avoid double-starting
_started = False


@app.before_request
def _ensure_started():
    global _started
    if not _started:
        _started = True
        _start_background_services()


if __name__ == "__main__":
    _start_background_services()
    _started = True
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
