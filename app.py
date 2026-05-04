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


def get_scraper():
    try:
        from Scweet import Scweet, ScweetConfig
    except ImportError:
        add_log("WARNING: Scweet not installed — scraping unavailable")
        return None
    config = load_config()
    # Also accept token from environment variable
    auth_token = (
        os.environ.get("TWITTER_AUTH_TOKEN", "")
        or config.get("twitter_auth_token", "")
    )
    cookies_file = "tools/cookies.json"
    scfg = ScweetConfig(concurrency=2, save_dir="outputs", save_format="json", min_delay_s=2.0)
    if os.path.exists(cookies_file):
        return Scweet(cookies_file=cookies_file, config=scfg)
    elif auth_token and auth_token not in ("", "YOUR_AUTH_TOKEN_HERE"):
        return Scweet(auth_token=auth_token, config=scfg)
    return None


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
                        "Use these commands here or in the group:\n\n"
                        "/check — run all checks right now\n"
                        "/followers username — get someone's followers\n"
                        "/following username — get someone's following list\n"
                        "/complaints topic — search complaint tweets\n"
                        "/status — see what's being monitored\n"
                        "/help — show this message\n\n"
                        "Results are always sent to the group."
                    ), disable_web_page_preview=True)

                # ── /help ───────────────────────────────────────────────────
                elif cmd == "/help":
                    await bot.send_message(chat_id=chat_id, text=(
                        "📖 Commands:\n\n"
                        "/check — run all scheduled checks immediately\n"
                        "/followers username — get follower list of an account\n"
                        "/following username — get following list of an account\n"
                        "/compare username — full comparison:\n"
                        "  🤝 Mutuals | 👁 Followers-only | 📤 Following-only\n"
                        "/complaints topic — search complaint tweets (last 7 days)\n"
                        "/status — tracked accounts + last/next check times\n"
                        "/help — show this message\n\n"
                        "💡 Tip: add the same account to both 'Track followers of'\n"
                        "and 'Track following of' in the dashboard — scheduled\n"
                        "checks will automatically run the full /compare analysis.\n\n"
                        "Works in this chat and in the group.\n"
                        "In the group: /compare@" + bot_info.username + " username"
                    ), disable_web_page_preview=True)

                # ── /status ─────────────────────────────────────────────────
                elif cmd == "/status":
                    config = load_config()
                    followers_list = config.get("track_followers_of", [])
                    following_list = config.get("track_following_of", [])
                    complaints_list = [c["query"] for c in config.get("monitor_complaints", [])]
                    interval = config.get("check_interval_minutes", 60)
                    has_token = bool(config.get("twitter_auth_token") and config["twitter_auth_token"] != "YOUR_AUTH_TOKEN_HERE")
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
                        await bot.send_message(chat_id=chat_id, text="Usage: /followers username\nExample: /followers elonmusk")
                    else:
                        uname = args.lstrip("@").split()[0]
                        await bot.send_message(chat_id=chat_id, text=f"▶️ Fetching followers of @{uname}… results will appear in the group.", disable_web_page_preview=True)
                        def _run_followers(u=uname):
                            s = get_scraper()
                            if not s:
                                asyncio.run(tb.send_message("❌ No Twitter auth_token set. Add it in the dashboard Settings tab."))
                                return
                            try:
                                users = s.get_followers([u], limit=500, save=True)
                                add_log(f"Followers @{u}: {len(users)}")
                                asyncio.run(tb.send_users_to_telegram(users, "followers", u))
                            except Exception as e:
                                asyncio.run(tb.send_message(f"❌ Error fetching followers of @{u}: {e}"))
                        threading.Thread(target=_run_followers, daemon=True).start()

                # ── /following <username> ────────────────────────────────────
                elif cmd == "/following":
                    if not args:
                        await bot.send_message(chat_id=chat_id, text="Usage: /following username\nExample: /following OpenAI")
                    else:
                        uname = args.lstrip("@").split()[0]
                        await bot.send_message(chat_id=chat_id, text=f"▶️ Fetching following of @{uname}… results will appear in the group.", disable_web_page_preview=True)
                        def _run_following(u=uname):
                            s = get_scraper()
                            if not s:
                                asyncio.run(tb.send_message("❌ No Twitter auth_token set. Add it in the dashboard Settings tab."))
                                return
                            try:
                                users = s.get_following([u], limit=500, save=True)
                                add_log(f"Following @{u}: {len(users)}")
                                asyncio.run(tb.send_users_to_telegram(users, "following", u))
                            except Exception as e:
                                asyncio.run(tb.send_message(f"❌ Error fetching following of @{u}: {e}"))
                        threading.Thread(target=_run_following, daemon=True).start()

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
        saved.append("Twitter token")

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


@app.route("/api/status")
def api_status():
    config = load_config()
    has_token = bool(config.get("twitter_auth_token") and config["twitter_auth_token"] != "YOUR_AUTH_TOKEN_HERE")
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
