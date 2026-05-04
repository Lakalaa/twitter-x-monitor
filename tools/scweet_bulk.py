"""
SCWEET — Fastest Bulk Twitter/X Scraper (Updated April 2026)
GitHub: https://github.com/Altimis/Scweet

WHY SCWEET IS THE BEST FOR BULK + SPEED:
  - Concurrent parallel workers (up to N accounts at once)
  - Auto rate-limit handling — backs off and switches accounts automatically
  - Resume interrupted scrapes from checkpoint (never lose progress)
  - Self-healing query IDs — auto-fetches fresh IDs from X when they expire
  - Saves results to CSV/JSON automatically
  - Per-account proxies — run 10 accounts = 10x speed
  - All async methods available for even higher throughput

SETUP (3 steps):
  1. Log into x.com in your browser
  2. DevTools (F12) → Application → Cookies → https://x.com
  3. Copy the `auth_token` value (and optionally `ct0`)

For maximum speed: add multiple accounts to cookies.json (see below)
"""

import asyncio
from Scweet import Scweet, ScweetConfig, ScweetDB


# ════════════════════════════════════════════════════════════
#  CONNECT — choose your setup
# ════════════════════════════════════════════════════════════

def connect_single(auth_token: str, proxy: str = None) -> Scweet:
    """Single account. Good for testing."""
    return Scweet(auth_token=auth_token, proxy=proxy)


def connect_multi(cookies_file: str = "cookies.json") -> Scweet:
    """
    Multiple accounts for maximum speed and throughput.

    cookies.json format:
    [
      {
        "username": "account1",
        "cookies": {"auth_token": "abc123"},
        "proxy": "http://user1:pass1@host1:8080"
      },
      {
        "username": "account2",
        "cookies": {"auth_token": "xyz456"},
        "proxy": "http://user2:pass2@host2:8080"
      }
    ]
    More accounts = faster scraping. Each account runs independently.
    """
    config = ScweetConfig(
        concurrency=5,           # parallel workers (set to number of accounts)
        save_dir="outputs",      # where to save results
        save_format="json",      # "csv", "json", or "both"
        daily_requests_limit=30, # max API requests per account per day
        daily_tweets_limit=600,  # max tweets per account per day
        min_delay_s=2.0,         # min seconds between requests
        requests_per_min=30,     # rate limit per account per minute
        max_empty_pages=2,       # stop after 2 consecutive empty pages
        api_page_size=100,       # tweets per API page (max 100 = fastest)
    )
    return Scweet(cookies_file=cookies_file, config=config)


def connect_resume(db_path: str = "scweet_state.db") -> Scweet:
    """Reuse existing session — no need to provide cookies again."""
    return Scweet(db_path=db_path)


# ════════════════════════════════════════════════════════════
#  BULK TWEET SEARCH
# ════════════════════════════════════════════════════════════

def bulk_search(s: Scweet, query: str, since: str, until: str, limit: int = None):
    """
    Search tweets in bulk with full filter support.
    Set limit=None to scrape everything until exhausted.
    """
    tweets = s.search(
        query=query,
        since=since,
        until=until,
        limit=limit,
        save=True,
        save_format="json",
        resume=True,        # resume from checkpoint if interrupted
    )
    print(f"Collected {len(tweets)} tweets")
    return tweets


def bulk_search_advanced(s: Scweet):
    """Search with every available filter — most powerful query."""
    tweets = s.search(
        since="2024-01-01",
        until="2025-01-01",
        limit=10000,

        # keyword filters
        all_words=["AI", "machine learning"],      # ALL must appear
        any_words=["ChatGPT", "Claude", "Gemini"], # ANY can appear
        exact_phrases=["large language model"],     # exact match
        exclude_words=["advertisement", "promo"],  # exclude these

        # hashtag filters
        hashtags_any=["AI", "MachineLearning"],
        hashtags_exclude=["spam"],

        # user filters
        from_users=["OpenAI", "AnthropicAI"],      # from these accounts
        to_users=["elonmusk"],                     # replies to these users
        mentioning_users=["sama"],                 # mentioning these users

        # content type
        tweet_type="originals_only",  # options: all / originals_only /
                                      # replies_only / retweets_only /
                                      # exclude_replies / exclude_retweets

        # account type
        verified_only=False,
        blue_verified_only=False,

        # media
        has_images=False,
        has_videos=False,
        has_links=True,
        has_mentions=False,
        has_hashtags=True,

        # engagement minimums
        min_likes=10,
        min_replies=2,
        min_retweets=5,

        # language
        lang="en",

        # location
        # geocode="37.7749,-122.4194,50km",  # near San Francisco, 50km radius
        # near="New York",
        # within="25mi",

        # display
        display_type="Latest",  # "Top" or "Latest"

        # output
        save=True,
        save_format="both",         # save CSV and JSON
        save_name="ai_tweets_2024",
    )
    print(f"Collected {len(tweets)} tweets")
    return tweets


async def bulk_search_async(s: Scweet, query: str, since: str, until: str, limit: int):
    """Async version — use inside an async context for higher throughput."""
    tweets = await s.asearch(query=query, since=since, until=until, limit=limit)
    print(f"Async collected {len(tweets)} tweets")
    return tweets


# ════════════════════════════════════════════════════════════
#  BULK FOLLOWERS / FOLLOWING
# ════════════════════════════════════════════════════════════

def bulk_followers(s: Scweet, usernames: list, limit: int = None):
    """
    Get followers for one or many accounts.
    Set limit=None to get ALL followers (warning: large accounts have millions).
    """
    users = s.get_followers(
        usernames,
        limit=limit,
        raw_json=True,       # include full GraphQL payload for each user
        save=True,
        save_format="json",
        resume=True,
    )
    print(f"Collected {len(users)} followers")
    for u in users[:5]:
        print(f"  @{u['username']} — {u['followers_count']:,} followers — verified: {u['blue_verified']}")
    return users


def bulk_following(s: Scweet, usernames: list, limit: int = None):
    """Get who a list of accounts follows."""
    users = s.get_following(
        usernames,
        limit=limit,
        raw_json=True,
        save=True,
        save_format="json",
        resume=True,
    )
    print(f"Collected {len(users)} following")
    return users


async def bulk_followers_async(s: Scweet, usernames: list, limit: int):
    """Async followers fetch."""
    users = await s.aget_followers(usernames, limit=limit)
    return users


async def bulk_following_async(s: Scweet, usernames: list, limit: int):
    """Async following fetch."""
    users = await s.aget_following(usernames, limit=limit)
    return users


# ════════════════════════════════════════════════════════════
#  BULK PROFILE TIMELINE TWEETS
# ════════════════════════════════════════════════════════════

def bulk_profile_tweets(s: Scweet, usernames: list, limit: int = None):
    """Scrape full tweet history for multiple accounts at once."""
    tweets = s.get_profile_tweets(
        usernames,
        limit=limit,
        save=True,
        save_format="json",
        resume=True,
    )
    print(f"Collected {len(tweets)} profile tweets")
    return tweets


async def bulk_profile_tweets_async(s: Scweet, usernames: list, limit: int):
    """Async profile timeline fetch."""
    tweets = await s.aget_profile_tweets(usernames, limit=limit)
    return tweets


# ════════════════════════════════════════════════════════════
#  BULK PROFILE INFO
# ════════════════════════════════════════════════════════════

def bulk_user_info(s: Scweet, usernames: list):
    """
    Get profile data for many accounts at once:
    bio, follower count, verification, created_at, etc.
    """
    profiles = s.get_user_info(
        usernames,
        save=True,
        save_format="json",
    )
    for p in profiles:
        print(
            f"@{p['username']} | {p['followers_count']:,} followers | "
            f"verified: {p['blue_verified']} | protected: {p['protected']}"
        )
    return profiles


async def bulk_user_info_async(s: Scweet, usernames: list):
    """Async profile info fetch."""
    profiles = await s.aget_user_info(usernames)
    return profiles


# ════════════════════════════════════════════════════════════
#  ACCOUNT MANAGEMENT
# ════════════════════════════════════════════════════════════

def check_accounts(db_path: str = "scweet_state.db"):
    """See status of all accounts in the pool."""
    db = ScweetDB(db_path)
    summary = db.accounts_summary()
    print(f"Total accounts : {summary['total']}")
    print(f"Eligible now   : {summary['eligible']}")
    print(f"Cooling down   : {summary['cooling_down']}")
    print(f"Unusable       : {summary['unusable']}")

    accounts = db.list_accounts()
    for acc in accounts:
        print(f"  - {acc}")


def repair_account(username: str, db_path: str = "scweet_state.db"):
    """Reset cooldowns and clear leases for a stuck account."""
    db = ScweetDB(db_path)
    result = db.repair_account(username)
    print(result)


# ════════════════════════════════════════════════════════════
#  SELF-HEALING: Auto-refresh Twitter query IDs
# ════════════════════════════════════════════════════════════

def connect_with_fresh_ids(cookies_file: str = "cookies.json") -> Scweet:
    """
    Twitter/X rotates its internal GraphQL query IDs periodically.
    When IDs go stale, requests return 404 errors.
    This fetches the latest IDs from X's main.js on startup.
    Adds ~3-5 seconds to startup but guarantees fresh IDs.
    """
    return Scweet(
        cookies_file=cookies_file,
        manifest_scrape_on_init=True,
    )


# ════════════════════════════════════════════════════════════
#  EXAMPLE RUNS
# ════════════════════════════════════════════════════════════

def example_single_account():
    """Quick test with one account."""
    s = connect_single(auth_token="YOUR_AUTH_TOKEN_HERE")

    tweets = s.search("python AI tools", since="2025-01-01", until="2025-06-01", limit=200)
    print(f"Got {len(tweets)} tweets")


def example_multi_account_bulk():
    """
    Maximum speed bulk run with multiple accounts.
    Requires cookies.json with multiple accounts configured.
    """
    s = connect_multi("cookies.json")

    # Scrape 50,000 tweets from the last year
    tweets = bulk_search(s, "artificial intelligence", "2024-01-01", "2025-01-01", limit=50000)

    # Scrape full follower lists for several large accounts
    followers = bulk_followers(s, ["OpenAI", "AnthropicAI", "GoogleDeepMind"], limit=5000)

    # Scrape tweet history for multiple accounts
    profile_tweets = bulk_profile_tweets(s, ["elonmusk", "sama", "ylecun"], limit=1000)


async def example_async_parallel():
    """
    Run multiple scraping tasks in true parallel using async.
    This is the absolute fastest approach.
    """
    s = connect_multi("cookies.json")

    # Run search + followers + profile tweets all at the same time
    search_task = s.asearch("climate change", since="2024-01-01", limit=1000)
    followers_task = s.aget_followers(["nasa"], limit=500)
    profile_task = s.aget_profile_tweets(["bbcnews"], limit=200)

    tweets, followers, profile_tweets = await asyncio.gather(
        search_task, followers_task, profile_task
    )

    print(f"Tweets: {len(tweets)}, Followers: {len(followers)}, Profile: {len(profile_tweets)}")


if __name__ == "__main__":
    # Choose your example:
    example_single_account()

    # For bulk multi-account run:
    # example_multi_account_bulk()

    # For maximum async parallel run:
    # asyncio.run(example_async_parallel())
