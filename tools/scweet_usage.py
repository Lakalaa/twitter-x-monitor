"""
Scweet — Twitter/X scraper (actively maintained, updated April 2026)
GitHub: https://github.com/Altimis/Scweet

SETUP:
  1. Log into x.com in your browser
  2. Open DevTools (F12) → Application → Cookies → https://x.com
  3. Copy the value of `auth_token`

Install: pip install -U Scweet
"""

from Scweet import Scweet

# ─────────────────────────────────────────────
# CONNECT — pick ONE of these options:
# ─────────────────────────────────────────────

# Option A: single auth_token (quickest)
# s = Scweet(auth_token="YOUR_AUTH_TOKEN_HERE")

# Option B: with proxy (recommended to avoid bans)
# s = Scweet(auth_token="YOUR_AUTH_TOKEN_HERE", proxy="http://user:pass@host:port")

# Option C: multiple accounts via cookies.json
# cookies.json format:
# [
#   {"username": "account1", "cookies": {"auth_token": "..."}},
#   {"username": "account2", "cookies": {"auth_token": "..."}, "proxy": "http://..."}
# ]
# s = Scweet(cookies_file="cookies.json")

# Option D: reuse existing session (after first run)
# s = Scweet(db_path="scweet_state.db")


def demo():
    s = Scweet(auth_token="YOUR_AUTH_TOKEN_HERE")

    # ─── SEARCH TWEETS ───────────────────────────────────
    # Search by keyword
    tweets = s.search("python programming", limit=100)
    print(f"Found {len(tweets)} tweets")

    # Search with date range
    tweets = s.search(
        "climate change",
        since="2024-01-01",
        until="2024-06-01",
        limit=500
    )

    # Search by hashtag
    tweets = s.search("#AI", limit=200)

    # Search tweets from a specific user
    tweets = s.search("from:elonmusk", limit=100)

    # Search with language filter
    tweets = s.search("breaking news lang:en", limit=100)

    # Exclude retweets
    tweets = s.search("python -filter:retweets", limit=100)

    # Print tweet data
    for tweet in tweets[:3]:
        print(tweet)

    # ─── USER PROFILE TWEETS ─────────────────────────────
    # Get all tweets from a user's timeline
    tweets = s.get_profile_tweets(["elonmusk"], limit=200)
    tweets = s.get_profile_tweets(["elonmusk", "OpenAI"], limit=100)

    # ─── FOLLOWERS / FOLLOWING ────────────────────────────
    # Get followers of a user
    followers = s.get_followers(["elonmusk"], limit=500)
    print(f"Fetched {len(followers)} followers")

    # Get who a user is following
    following = s.get_following(["elonmusk"], limit=500)
    print(f"Fetched {len(following)} following")

    # ─── USER PROFILE INFO ────────────────────────────────
    # Get bio, follower count, verification status, etc.
    profile = s.get_user_info(["elonmusk"])
    print(profile)

    # ─── SAVE OUTPUT ──────────────────────────────────────
    # All methods support saving results automatically:
    tweets = s.search("AI tools", limit=100, save=True, save_dir="./output")
    # Saves as JSON to ./output/


if __name__ == "__main__":
    demo()
