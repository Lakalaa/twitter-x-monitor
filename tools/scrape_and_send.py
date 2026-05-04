"""
Twitter/X Scraper → Telegram Group
===================================
Main script: scrapes Twitter data and sends everything to your Telegram group.

USAGE:
  # Scrape followers and send to Telegram
  python tools/scrape_and_send.py followers elonmusk --limit 500

  # Scrape following and send to Telegram
  python tools/scrape_and_send.py following OpenAI --limit 500

  # Search complaint tweets and send to Telegram
  python tools/scrape_and_send.py complaints "your brand name" --since 2025-01-01

  # Search all tweets (not just complaints) and send to Telegram
  python tools/scrape_and_send.py tweets "keyword" --limit 200

  # Get profile info for users and send to Telegram
  python tools/scrape_and_send.py profiles elonmusk OpenAI NASA

  # Test Telegram connection only
  python tools/scrape_and_send.py test

SETUP REQUIRED:
  - TELEGRAM_BOT_TOKEN secret (from @BotFather)
  - TELEGRAM_CHAT_ID secret (group chat ID)
  - Twitter auth_token in AUTH_TOKEN below (from browser cookies)
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import telegram_bot as tb

try:
    from Scweet import Scweet, ScweetConfig
except ImportError:
    print("ERROR: Scweet not installed. Run: pip install -U Scweet")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION — fill these in
# ─────────────────────────────────────────────────────────────────────────────

AUTH_TOKEN = "YOUR_AUTH_TOKEN_HERE"  # paste your Twitter auth_token here
# OR use multiple accounts:
COOKIES_FILE = "tools/cookies.json"   # path to cookies.json if you have multiple accounts

# Scweet settings
SCWEET_CONFIG = ScweetConfig(
    concurrency=3,
    save_dir="outputs",
    save_format="json",
    min_delay_s=2.0,
)


def get_scraper() -> Scweet:
    """Get Scweet instance — uses cookies.json if available, else single auth_token."""
    import os
    if os.path.exists(COOKIES_FILE):
        print(f"Using multi-account setup from {COOKIES_FILE}")
        return Scweet(cookies_file=COOKIES_FILE, config=SCWEET_CONFIG)
    elif AUTH_TOKEN != "YOUR_AUTH_TOKEN_HERE":
        print("Using single auth_token")
        return Scweet(auth_token=AUTH_TOKEN, config=SCWEET_CONFIG)
    else:
        print("ERROR: No auth_token configured.")
        print("  Option 1: Edit AUTH_TOKEN in this file")
        print("  Option 2: Create tools/cookies.json with your accounts")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
#  SCRAPE + SEND FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

async def run_followers(username: str, limit: int):
    """Scrape all followers and send to Telegram."""
    print(f"\n[1/2] Scraping followers of @{username} (limit={limit})...")
    s = get_scraper()
    users = s.get_followers(
        [username],
        limit=limit,
        raw_json=False,
        save=True,
        save_format="json",
        resume=True,
    )
    print(f"  Scraped {len(users)} followers")
    print(f"\n[2/2] Sending to Telegram...")
    await tb.send_users_to_telegram(users, "followers", username, batch_size=20)


async def run_following(username: str, limit: int):
    """Scrape all following and send to Telegram."""
    print(f"\n[1/2] Scraping following of @{username} (limit={limit})...")
    s = get_scraper()
    users = s.get_following(
        [username],
        limit=limit,
        raw_json=False,
        save=True,
        save_format="json",
        resume=True,
    )
    print(f"  Scraped {len(users)} following")
    print(f"\n[2/2] Sending to Telegram...")
    await tb.send_users_to_telegram(users, "following", username, batch_size=20)


async def run_complaints(query: str, since: str, until: str, limit: int):
    """Search for complaint tweets and send to Telegram."""
    print(f"\n[1/2] Searching complaints about: \"{query}\"")
    print(f"  Date range: {since} → {until} | limit={limit}")
    s = get_scraper()
    tweets = s.search(
        query=query,
        since=since,
        until=until,
        limit=limit,
        save=True,
        save_format="json",
        resume=True,
    )
    print(f"  Scraped {len(tweets)} tweets total")
    print(f"\n[2/2] Filtering complaints and sending to Telegram...")
    await tb.send_complaints_to_telegram(tweets, query, complaints_only=True)


async def run_tweets(query: str, since: str, until: str, limit: int):
    """Search all tweets (not just complaints) and send to Telegram."""
    print(f"\n[1/2] Searching tweets: \"{query}\"")
    s = get_scraper()
    tweets = s.search(
        query=query,
        since=since,
        until=until,
        limit=limit,
        save=True,
        save_format="json",
        resume=True,
    )
    print(f"  Scraped {len(tweets)} tweets")
    print(f"\n[2/2] Sending all tweets to Telegram...")
    await tb.send_complaints_to_telegram(tweets, query, complaints_only=False)


async def run_profiles(usernames: list):
    """Get profile info for multiple users and send to Telegram."""
    print(f"\n[1/2] Fetching profiles: {usernames}")
    s = get_scraper()
    profiles = s.get_user_info(usernames, save=True, save_format="json")
    print(f"  Fetched {len(profiles)} profiles")
    print(f"\n[2/2] Sending to Telegram...")
    await tb.send_profile_info(profiles)


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def default_since():
    return (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

def default_until():
    return datetime.now().strftime("%Y-%m-%d")


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Twitter/X and send results to Telegram",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tools/scrape_and_send.py test
  python tools/scrape_and_send.py followers elonmusk --limit 1000
  python tools/scrape_and_send.py following OpenAI --limit 500
  python tools/scrape_and_send.py complaints "your company" --since 2025-01-01
  python tools/scrape_and_send.py tweets "python programming" --limit 100
  python tools/scrape_and_send.py profiles elonmusk OpenAI NASA
        """
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # test
    subparsers.add_parser("test", help="Test Telegram bot connection")

    # followers
    p_followers = subparsers.add_parser("followers", help="Scrape followers and send to Telegram")
    p_followers.add_argument("username", help="Twitter username (without @)")
    p_followers.add_argument("--limit", type=int, default=500, help="Max followers to fetch (default: 500)")

    # following
    p_following = subparsers.add_parser("following", help="Scrape following list and send to Telegram")
    p_following.add_argument("username", help="Twitter username (without @)")
    p_following.add_argument("--limit", type=int, default=500, help="Max following to fetch (default: 500)")

    # complaints
    p_complaints = subparsers.add_parser("complaints", help="Search complaint tweets and send to Telegram")
    p_complaints.add_argument("query", help="Search query (brand name, hashtag, keyword)")
    p_complaints.add_argument("--since", default=default_since(), help="Start date YYYY-MM-DD (default: 30 days ago)")
    p_complaints.add_argument("--until", default=default_until(), help="End date YYYY-MM-DD (default: today)")
    p_complaints.add_argument("--limit", type=int, default=500, help="Max tweets to scan (default: 500)")

    # tweets (all, not just complaints)
    p_tweets = subparsers.add_parser("tweets", help="Search all tweets and send to Telegram")
    p_tweets.add_argument("query", help="Search query")
    p_tweets.add_argument("--since", default=default_since(), help="Start date YYYY-MM-DD")
    p_tweets.add_argument("--until", default=default_until(), help="End date YYYY-MM-DD")
    p_tweets.add_argument("--limit", type=int, default=200, help="Max tweets (default: 200)")

    # profiles
    p_profiles = subparsers.add_parser("profiles", help="Get user profiles and send to Telegram")
    p_profiles.add_argument("usernames", nargs="+", help="Twitter usernames (without @)")

    args = parser.parse_args()

    if args.command == "test":
        asyncio.run(tb.test_connection())

    elif args.command == "followers":
        asyncio.run(run_followers(args.username, args.limit))

    elif args.command == "following":
        asyncio.run(run_following(args.username, args.limit))

    elif args.command == "complaints":
        asyncio.run(run_complaints(args.query, args.since, args.until, args.limit))

    elif args.command == "tweets":
        asyncio.run(run_tweets(args.query, args.since, args.until, args.limit))

    elif args.command == "profiles":
        asyncio.run(run_profiles(args.usernames))


if __name__ == "__main__":
    main()
