"""
Twitter/X Monitor — Auto-tracking Bot
======================================
Reads targets.json, monitors all listed accounts, and sends updates
to your Telegram group automatically on a schedule.

SETUP:
  1. Edit tools/targets.json — add the accounts you want to track
  2. Add your Twitter auth_token to targets.json
  3. Run: python tools/monitor.py

It will then run forever, checking every X minutes (set in targets.json).
Every check sends new followers/following alerts and complaint tweets to Telegram.

STOP: Press Ctrl+C
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta

import schedule

sys.path.insert(0, os.path.dirname(__file__))
import telegram_bot as tb

try:
    from Scweet import Scweet, ScweetConfig
except ImportError:
    print("ERROR: Scweet not installed. Run: pip install -U Scweet")
    sys.exit(1)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "targets.json")


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        print(f"ERROR: Config file not found: {CONFIG_FILE}")
        print("  Create tools/targets.json and fill in your accounts.")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)


def get_scraper(config: dict) -> Scweet:
    cookies_file = os.path.join(os.path.dirname(__file__), "cookies.json")
    auth_token = config.get("twitter_auth_token", "")

    scweet_config = ScweetConfig(
        concurrency=3,
        save_dir="outputs",
        save_format="json",
        min_delay_s=2.0,
    )

    if os.path.exists(cookies_file):
        print("  Using multi-account cookies.json")
        return Scweet(cookies_file=cookies_file, config=scweet_config)
    elif auth_token and auth_token != "YOUR_AUTH_TOKEN_HERE":
        print("  Using single auth_token")
        return Scweet(auth_token=auth_token, config=scweet_config)
    else:
        print("ERROR: No Twitter auth_token set.")
        print("  Edit tools/targets.json and set twitter_auth_token")
        sys.exit(1)


async def run_checks():
    """Run all checks defined in targets.json and send results to Telegram."""
    config = load_config()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*50}")
    print(f"Running checks at {now}")
    print(f"{'='*50}")

    await tb.send_message(f"🔄 Starting scheduled check — {now}")

    s = get_scraper(config)

    # ── Check followers ───────────────────────────────────────────────────────
    for username in config.get("track_followers_of", []):
        print(f"\n→ Checking followers of @{username}...")
        try:
            users = s.get_followers(
                [username],
                limit=None,     # get ALL followers
                save=True,
                save_format="json",
                resume=False,
            )
            print(f"  Got {len(users)} followers")
            await tb.send_users_to_telegram(users, "followers", username, batch_size=20)
        except Exception as e:
            msg = f"❌ Error checking followers of @{username}: {e}"
            print(f"  {msg}")
            await tb.send_message(msg)

    # ── Check following ───────────────────────────────────────────────────────
    for username in config.get("track_following_of", []):
        print(f"\n→ Checking following of @{username}...")
        try:
            users = s.get_following(
                [username],
                limit=None,     # get ALL following
                save=True,
                save_format="json",
                resume=False,
            )
            print(f"  Got {len(users)} following")
            await tb.send_users_to_telegram(users, "following", username, batch_size=20)
        except Exception as e:
            msg = f"❌ Error checking following of @{username}: {e}"
            print(f"  {msg}")
            await tb.send_message(msg)

    # ── Check complaints ──────────────────────────────────────────────────────
    for item in config.get("monitor_complaints", []):
        query = item.get("query", "")
        days = item.get("since_days_ago", 7)
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        until = datetime.now().strftime("%Y-%m-%d")

        print(f"\n→ Checking complaints: \"{query}\" (last {days} days)...")
        try:
            tweets = s.search(
                query=query,
                since=since,
                until=until,
                limit=500,
                save=True,
                save_format="json",
            )
            print(f"  Got {len(tweets)} tweets")
            await tb.send_complaints_to_telegram(tweets, query, complaints_only=True)
        except Exception as e:
            msg = f"❌ Error checking complaints for \"{query}\": {e}"
            print(f"  {msg}")
            await tb.send_message(msg)

    await tb.send_message(f"✅ All checks done — next check in {config.get('check_interval_minutes', 60)} min")
    print(f"\nAll checks complete.")


def run_sync():
    """Sync wrapper to run the async checks."""
    asyncio.run(run_checks())


def main():
    config = load_config()
    interval = config.get("check_interval_minutes", 60)

    print("Twitter/X Monitor started")
    print(f"Config: {CONFIG_FILE}")
    print(f"Check interval: every {interval} minutes")
    print(f"Tracking followers of: {config.get('track_followers_of', [])}")
    print(f"Tracking following of: {config.get('track_following_of', [])}")
    print(f"Monitoring complaints: {[c['query'] for c in config.get('monitor_complaints', [])]}")
    print(f"\nPress Ctrl+C to stop\n")

    # Run once immediately on start
    print("Running first check now...")
    run_sync()

    # Then schedule repeating checks
    schedule.every(interval).minutes.do(run_sync)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
