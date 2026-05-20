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

def start_scheduler():
    config = load_config()
    interval = config.get("check_interval_minutes", 60)
    STATE["interval_minutes"] = interval
    STATE["running"] = True
    schedule.clear()
    schedule.every(interval).minutes.do(run_checks_sync)
    add_log(f"Scheduler started — every {interval} minutes")
    while STATE["running"]:
        schedule.run_pending()
        time.sleep(15)


scheduler_thread = None


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
                        "/replies <tweet_url> — scrape all replies to a tweet\n"
                        "/mirror <src_url> <tgt_url> — copy replies from one tweet onto another\n"
                        "/replyall <tweet_url> <text> — reply to every commenter using 200 accounts\n"
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
                        "/replies <tweet_url>\n"
                        "  Scrape all replies — shows commenter + their text\n\n"
                        "/mirror <source_url> <target_url>\n"
                        "  Scrapes replies from source tweet and posts each one\n"
                        "  as a comment on target tweet (from your account).\n"
                        "  Format: 💬 @originaluser: their comment\n"
                        "  Posts 1 reply every 8s (Twitter rate limit)\n\n"
                        "/replyall <tweet_url> [@mention] <text>\n"
                        "  Scrapes every reply under a tweet, then replies back\n"
                        "  to each commenter using your 200 accounts (rotating).\n"
                        "  One account replies to one commenter, then next account, etc.\n"
                        "  Optional @mention is prepended to every reply.\n"
                        "  Example: /replyall https://x.com/.../123 Thanks! 🙌\n"
                        "  Example with mention: /replyall https://x.com/.../123 @elonmusk check this!\n\n"
                        "── Bulk Engagement (200 accounts) ──\n"
                        "/like <tweet_url>\n"
                        "  Likes the tweet from all 200 accounts\n\n"
                        "/comment <tweet_url> [@mention] <text>\n"
                        "  Posts a comment from all 200 accounts\n"
                        "  Optional: start with @username to mention someone\n"
                        "  Example: /comment https://x.com/.../123 @elonmusk great!\n\n"
                        "/engage <tweet_url> [@mention] <text>\n"
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
                                s = get_scraper()
                                if not s:
                                    asyncio.run(tb.send_message("❌ No Twitter auth_token set. Add it in the dashboard Settings tab."))
                                    return
                                add_log(f"Scraping ALL followers of @{u}…")
                                users = _scrape_with_progress(
                                    lambda: s.get_followers([u], limit=None, save=True, resume=False),
                                    "followers", u
                                )
                                if not users:
                                    return
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
                                    f"✅ That was the last batch — all {total:,} followers of @{u} sent.\n"
                                    f"The next /followers {u} will re-scrape fresh data."
                                ))
                            else:
                                write_offset("followers", u, new_offset)
                                asyncio.run(tb.send_message(
                                    f"📄 Sent {new_offset:,} of {total:,} total.\n"
                                    f"Run /followers {u} again for the next 500."
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
                                s = get_scraper()
                                if not s:
                                    asyncio.run(tb.send_message("❌ No Twitter auth_token set. Add it in the dashboard Settings tab."))
                                    return
                                add_log(f"Scraping ALL following of @{u}…")
                                users = _scrape_with_progress(
                                    lambda: s.get_following([u], limit=None, save=True, resume=False),
                                    "following", u
                                )
                                if not users:
                                    return
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
                                    f"✅ That was the last batch — all {total:,} following of @{u} sent.\n"
                                    f"The next /following {u} will re-scrape fresh data."
                                ))
                            else:
                                write_offset("following", u, new_offset)
                                asyncio.run(tb.send_message(
                                    f"📄 Sent {new_offset:,} of {total:,} total.\n"
                                    f"Run /following {u} again for the next 500."
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

                # ── /replies <tweet_url> ─────────────────────────────────────
                elif cmd == "/replies":
                    if not args:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /replies <tweet_url>\n"
                            "Example: /replies https://x.com/elonmusk/status/123456789\n\n"
                            "Scrapes all replies to that tweet and sends each commenter's "
                            "name + their comment to the group."
                        ))
                    else:
                        tweet_url = args.strip()
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"💬 Fetching replies for:\n{tweet_url}\nResults will appear in the group shortly.",
                            disable_web_page_preview=True
                        )
                        def _run_replies(url=tweet_url):
                            sc = get_scraper()
                            if not sc:
                                asyncio.run(tb.send_message("❌ No Twitter auth_token set. Add it in the dashboard Settings tab."))
                                return
                            try:
                                # Extract tweet ID and author from URL
                                # URL format: https://x.com/username/status/TWEET_ID
                                import re as _re
                                m = _re.search(r"x\.com/([^/]+)/status/(\d+)", url)
                                tweet_author = m.group(1) if m else ""
                                tweet_id     = m.group(2) if m else ""

                                add_log(f"Scraping replies to tweet {tweet_id} by @{tweet_author}…")
                                # Search for replies: replies to the tweet are tweets
                                # that start with "@tweet_author" and reference the tweet id
                                # Scweet search supports conversation_id filter
                                query = f"conversation_id:{tweet_id}" if tweet_id else url
                                replies = sc.search(
                                    query=query,
                                    limit=500,
                                    save=True,
                                    filter_replies=False,
                                )
                                # Filter out the original tweet itself
                                if tweet_id:
                                    replies = [r for r in replies
                                               if str(r.get("id", "")) != tweet_id
                                               and str(r.get("tweet_id", "")) != tweet_id]
                                add_log(f"Replies scraped: {len(replies)} for tweet {tweet_id}")
                                asyncio.run(tb.send_replies_to_telegram(replies, url, tweet_author))
                            except Exception as e:
                                add_log(f"Replies error: {e}")
                                asyncio.run(tb.send_message(f"❌ Error scraping replies: {e}"))
                        threading.Thread(target=_run_replies, daemon=True).start()

                # ── /mirror <source_tweet_url> <target_tweet_url> ────────────
                elif cmd == "/mirror":
                    parts = args.strip().split()
                    if len(parts) < 2:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /mirror <source_tweet_url> <target_tweet_url>\n\n"
                            "Scrapes all replies from the SOURCE tweet, then posts each one "
                            "as a reply on the TARGET tweet (from your account).\n\n"
                            "Example:\n"
                            "/mirror https://x.com/someone/status/111 https://x.com/you/status/222\n\n"
                            "Each posted comment will look like:\n"
                            "💬 @originaluser: their comment text\n\n"
                            "⚠️ Twitter rate-limits posting — large threads are posted slowly (1 every 8s)."
                        ), disable_web_page_preview=True)
                    else:
                        src_url    = parts[0]
                        target_url = parts[1]
                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"🔁 Mirror started.\n"
                                f"Source: {src_url}\n"
                                f"Target: {target_url}\n\n"
                                f"Scraping replies from source… will post them one by one on the target tweet."
                            ),
                            disable_web_page_preview=True
                        )
                        def _run_mirror(src=src_url, tgt=target_url):
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
                                if src_id:
                                    replies = [r for r in replies
                                               if str(r.get("id", "")) != src_id
                                               and str(r.get("tweet_id", "")) != src_id]
                            except Exception as exc:
                                asyncio.run(tb.send_message(f"❌ Error scraping source tweet replies: {exc}"))
                                return

                            if not replies:
                                asyncio.run(tb.send_message(
                                    f"⚠️ No replies found for source tweet.\n"
                                    f"The tweet might have no replies, or the search returned no results."
                                ))
                                return

                            asyncio.run(tb.send_message(
                                f"✅ Found {len(replies):,} replies. "
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

                                # Format: attribution + original text (Twitter max 280 chars)
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
                                    # If we hit auth/rate errors early, abort
                                    if fail_count >= 3 and ok_count == 0:
                                        asyncio.run(tb.send_message(
                                            f"❌ Posting is failing (3 errors, 0 successes). Stopping.\n"
                                            f"Last error: {result['error']}\n\n"
                                            f"Check that your ct0 cookie is correct and fresh (Settings tab)."
                                        ))
                                        return

                                # Progress update every 25 posts
                                if (i + 1) % 25 == 0:
                                    asyncio.run(tb.send_message(
                                        f"🔁 Mirror progress: {i+1}/{len(replies)} — "
                                        f"✅ {ok_count} posted, ❌ {fail_count} failed"
                                    ))

                                time.sleep(8)  # stay within Twitter's rate limits

                            asyncio.run(tb.send_message(
                                f"🏁 Mirror complete!\n"
                                f"Source: {src}\n"
                                f"Target: {tgt}\n"
                                f"✅ {ok_count} replies posted | ❌ {fail_count} failed"
                            ))
                            add_log(f"Mirror done: {ok_count} posted, {fail_count} failed")

                        threading.Thread(target=_run_mirror, daemon=True).start()

                # ── /replyall <tweet_url> <reply_text> ──────────────────────
                elif cmd == "/replyall":
                    parts = args.strip().split(None, 1)
                    if len(parts) < 2:
                        await bot.send_message(chat_id=chat_id, text=(
                            "Usage: /replyall <tweet_url> <reply_text>\n\n"
                            "Scrapes every reply under a tweet, then replies back to each commenter\n"
                            "using your 200 accounts (one account per commenter, rotating).\n\n"
                            "Optional @mention prefix:\n"
                            "/replyall <url> @username your text\n\n"
                            "Example:\n"
                            "/replyall https://x.com/user/status/123 Thanks for your comment! 🙌"
                        ), disable_web_page_preview=True)
                    else:
                        ra_tweet_url = parts[0].strip()
                        ra_reply_body = parts[1].strip()
                        # Optional @mention prefix
                        ra_parts = ra_reply_body.split(None, 1)
                        ra_mention = ""
                        if ra_parts[0].startswith("@"):
                            ra_mention = ra_parts[0]
                            ra_reply_body = ra_parts[1] if len(ra_parts) > 1 else ""

                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"🔍 Scraping replies for:\n{ra_tweet_url}\n\n"
                                f"Will then reply to each commenter using your 200 accounts."
                            ),
                            disable_web_page_preview=True
                        )

                        def _run_replyall(url=ra_tweet_url, reply_text=ra_reply_body, mention=ra_mention):
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
                                # Remove the original tweet itself
                                replies = [r for r in replies
                                           if str(r.get("id", "")) != tweet_id
                                           and str(r.get("tweet_id", "")) != tweet_id]
                            except Exception as exc:
                                asyncio.run(tb.send_message(f"❌ Error scraping replies: {exc}"))
                                return

                            if not replies:
                                asyncio.run(tb.send_message("⚠️ No replies found for that tweet."))
                                return

                            # ── 2. Load account pool ──────────────────────────
                            pool = _tp.load_account_pool()
                            if not pool:
                                asyncio.run(tb.send_message("❌ Account pool empty. Check tools/cookies.json."))
                                return

                            asyncio.run(tb.send_message(
                                f"✅ Found {len(replies):,} replies.\n"
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

                # ── /like <tweet_url> ───────────────────────────────────────
                elif cmd == "/like":
                    if not args:
                        await bot.send_message(chat_id=chat_id, text="Usage: /like <tweet_url>\nExample: /like https://x.com/user/status/123456789")
                    else:
                        tweet_url = args.split()[0]
                        await bot.send_message(chat_id=chat_id, text=f"❤️ Liking tweet with all 200 accounts…\n{tweet_url}", disable_web_page_preview=True)
                        def _run_like(url=tweet_url, cid=chat_id):
                            from twitter_post import bulk_engage
                            result = bulk_engage(url, action="like", delay_min=2.0, delay_max=6.0)
                            if "error" in result:
                                asyncio.run(tb.send_message(f"❌ Like failed: {result['error']}"))
                            else:
                                asyncio.run(tb.send_message(
                                    f"❤️ Like complete!\n"
                                    f"Tweet: {url}\n"
                                    f"✅ {result['ok']} liked | ❌ {result['fail']} failed"
                                ))
                        threading.Thread(target=_run_like, daemon=True).start()

                # ── /comment <tweet_url> <text> ──────────────────────────────
                elif cmd == "/comment":
                    parts = args.split(None, 1)
                    if len(parts) < 2:
                        await bot.send_message(chat_id=chat_id, text="Usage: /comment <tweet_url> <text>\nOptional @mention: /comment <url> @username your comment\nExample: /comment https://x.com/user/status/123 Great post!")
                    else:
                        tweet_url = parts[0]
                        comment_body = parts[1]
                        # If first word is a @mention, extract it
                        cb_parts = comment_body.split(None, 1)
                        mention_tag = ""
                        if cb_parts[0].startswith("@"):
                            mention_tag = cb_parts[0]
                            comment_body = cb_parts[1] if len(cb_parts) > 1 else ""
                        await bot.send_message(chat_id=chat_id, text=f"💬 Commenting on tweet with all 200 accounts…\n{tweet_url}", disable_web_page_preview=True)
                        def _run_comment(url=tweet_url, text=comment_body, mention=mention_tag, cid=chat_id):
                            from twitter_post import bulk_engage
                            result = bulk_engage(url, action="comment", comment_text=text, mention=mention, delay_min=4.0, delay_max=10.0)
                            if "error" in result:
                                asyncio.run(tb.send_message(f"❌ Comment failed: {result['error']}"))
                            else:
                                asyncio.run(tb.send_message(
                                    f"💬 Comment complete!\n"
                                    f"Tweet: {url}\n"
                                    f"Text: {mention+' ' if mention else ''}{text}\n"
                                    f"✅ {result['ok']} posted | ❌ {result['fail']} failed"
                                ))
                        threading.Thread(target=_run_comment, daemon=True).start()

                # ── /engage <tweet_url> <text> ───────────────────────────────
                elif cmd == "/engage":
                    parts = args.split(None, 1)
                    if len(parts) < 2:
                        await bot.send_message(chat_id=chat_id, text="Usage: /engage <tweet_url> <comment_text>\nLikes AND comments with all 200 accounts.\nExample: /engage https://x.com/user/status/123 Amazing project!")
                    else:
                        tweet_url = parts[0]
                        comment_body = parts[1]
                        cb_parts = comment_body.split(None, 1)
                        mention_tag = ""
                        if cb_parts[0].startswith("@"):
                            mention_tag = cb_parts[0]
                            comment_body = cb_parts[1] if len(cb_parts) > 1 else ""
                        await bot.send_message(chat_id=chat_id, text=f"🚀 Engaging (like + comment) with all 200 accounts…\n{tweet_url}", disable_web_page_preview=True)
                        def _run_engage(url=tweet_url, text=comment_body, mention=mention_tag, cid=chat_id):
                            from twitter_post import bulk_engage
                            result = bulk_engage(url, action="both", comment_text=text, mention=mention, delay_min=4.0, delay_max=10.0)
                            if "error" in result:
                                asyncio.run(tb.send_message(f"❌ Engage failed: {result['error']}"))
                            else:
                                asyncio.run(tb.send_message(
                                    f"🚀 Engage complete!\n"
                                    f"Tweet: {url}\n"
                                    f"Text: {mention+' ' if mention else ''}{text}\n"
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

    return jsonify({"ok": True, "saved": saved})


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
    """
    Trigger bulk engagement on a tweet.
    Body: { "tweet_url": "...", "action": "like|comment|both",
            "comment_text": "...", "mention": "@user" }
    Runs in a background thread; returns immediately with a job ID.
    """
    data = request.json or {}
    tweet_url    = data.get("tweet_url", "").strip()
    action       = data.get("action", "like").strip()
    comment_text = data.get("comment_text", "").strip()
    mention      = data.get("mention", "").strip()

    if not tweet_url:
        return jsonify({"ok": False, "error": "tweet_url is required"}), 400
    if action not in ("like", "comment", "both"):
        return jsonify({"ok": False, "error": "action must be like, comment, or both"}), 400
    if action in ("comment", "both") and not comment_text and not mention:
        return jsonify({"ok": False, "error": "comment_text or mention is required for comment/both"}), 400

    import uuid
    job_id = uuid.uuid4().hex[:8]

    # Store progress in STATE
    STATE.setdefault("engage_jobs", {})[job_id] = {
        "tweet_url": tweet_url, "action": action,
        "status": "running", "done": 0, "total": 0,
        "ok": 0, "fail": 0, "started_at": datetime.now().isoformat(),
        "finished_at": None,
    }

    def _run(jid=job_id, url=tweet_url, act=action, text=comment_text, tag=mention):
        from twitter_post import bulk_engage
        job = STATE["engage_jobs"][jid]
        pool = []
        cookies_file = "tools/cookies.json"
        if os.path.exists(cookies_file):
            try:
                with open(cookies_file) as f:
                    pool = json.load(f)
            except Exception:
                pass
        job["total"] = len(pool)

        def progress(done, total, username, status_str):
            job["done"] = done
            job["total"] = total
            job["ok"]   = sum(1 for r in job.get("results", []) if r.get("ok"))
            job["fail"] = done - job["ok"]

        job["results"] = []
        result = bulk_engage(url, action=act, comment_text=text, mention=tag,
                             delay_min=3.0, delay_max=8.0, progress_cb=progress)
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
