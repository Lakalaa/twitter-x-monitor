"""
twitter-api-client — Full Twitter v1, v2 & GraphQL API implementation
GitHub: https://github.com/trevorhobenshield/twitter-api-client

Extras this tool has that twscrape does NOT:
  - Write actions (post tweets, like, retweet, follow, DM, etc.)
  - Twitter Spaces (audio download, live transcript, chat log)
  - Media download (photos, videos, HQ variants)
  - Batch queries with higher rate limits
  - Recommended users
  - Tweet scheduling
  - ProtonMail email solver support

SETUP — get cookies from browser:
  1. Log into x.com
  2. Open DevTools (F12) → Application → Cookies → https://x.com
  3. Copy `auth_token` and `ct0` values

Install: pip install -U twitter-api-client
"""

from twitter.scraper import Scraper
from twitter.account import Account
from twitter.constants import SpaceCategory


# ─────────────────────────────────────────────
# CONNECT — use cookies (most stable method)
# ─────────────────────────────────────────────

COOKIES = {
    "auth_token": "YOUR_AUTH_TOKEN_HERE",
    "ct0": "YOUR_CT0_HERE",
}

# Or load from a saved cookie file:
# scraper = Scraper(cookies="twitter.cookies")

# Or guest session (no login, limited endpoints):
# from twitter.util import init_session
# scraper = Scraper(session=init_session())


def demo_read():
    """Read / scrape data"""
    scraper = Scraper(cookies=COOKIES)

    # ─── USERS ───────────────────────────────────────────
    # Get users by username
    users = scraper.users(["elonmusk", "OpenAI"])

    # Get users by ID (preferred — higher rate limits)
    users = scraper.users_by_ids([44196397, 1068480648])

    # Get recommended users
    scraper.recommended_users()
    scraper.recommended_users([44196397])  # relative to a specific user

    # ─── FOLLOWERS / FOLLOWING ────────────────────────────
    # NOTE: Followers endpoint allows 50 requests per 15 min
    # Use cursor to resume if interrupted:
    followers, last_cursor = scraper.followers([44196397], limit=200)
    # Resume later: scraper.followers([44196397], limit=200, cursor=last_cursor)

    following = scraper.following([44196397], limit=200)

    # ─── TWEETS ──────────────────────────────────────────
    # Get tweets by tweet IDs (batch — high rate limit)
    tweets = scraper.tweets([1234567890, 9876543210])

    # Get a user's tweets
    tweets = scraper.tweets([44196397], limit=100)

    # Tweets + replies
    tweets = scraper.tweets_and_replies([44196397], limit=100)

    # Media tweets only
    media = scraper.media([44196397], limit=50)

    # Liked tweets
    likes = scraper.likes([44196397], limit=50)

    # Tweet stats (views, likes, retweets, etc.)
    stats = scraper.tweet_stats([1234567890])
    print(stats)

    # ─── MEDIA DOWNLOAD ───────────────────────────────────
    # Download photos, videos, and card media from tweets
    scraper.download_media(
        ids=[1234567890],
        photos=True,
        videos=True,
        cards=True,
        hq_img_variant=True,     # download highest quality image
        out="media/",            # save folder
        metadata_out="media.json"
    )


def demo_spaces():
    """Twitter Spaces — audio, chat, live transcript"""
    from twitter.util import init_session
    scraper = Scraper(session=init_session())  # guest session works for Spaces

    # Download audio + chat log from a Space
    scraper.spaces(rooms=["1eaJbrAPnBVJX"], audio=True, chat=True)

    # Search Spaces by category
    scraper.spaces(search=[
        {"filter": SpaceCategory.Live,     "query": "AI news"},
        {"filter": SpaceCategory.Upcoming, "query": "tech"},
        {"filter": SpaceCategory.Top,      "query": "crypto"},
    ])

    # Live transcript of an active Space
    # frequency=1 → clean/final transcript
    # frequency=2 → word-level real-time (raw)
    scraper.space_live_transcript("1zqKVPlQNApJB", frequency=1)


def demo_write():
    """Write actions — post, like, follow, DM, etc."""
    account = Account(cookies=COOKIES)

    # ─── TWEETS ──────────────────────────────────────────
    account.tweet("Hello world!")
    account.reply("This is a reply", tweet_id=1234567890)
    account.quote("Quoting this", tweet_id=1234567890)
    account.retweet(1234567890)
    account.unretweet(1234567890)
    account.untweet(1234567890)

    # Tweet with media and alt text
    account.tweet("Check this out!", media=[
        {"media": "photo.jpg", "alt": "A photo", "tagged_users": [44196397]},
        {"media": "clip.mp4",  "alt": "A video clip"},
    ])

    # Schedule a tweet (Unix timestamp)
    import time
    future = int(time.time()) + 3600  # 1 hour from now
    account.schedule_tweet("Scheduled tweet!", future)
    account.unschedule_tweet(1234567890)

    # ─── SOCIAL ACTIONS ──────────────────────────────────
    account.follow(44196397)
    account.unfollow(44196397)
    account.mute(44196397)
    account.unmute(44196397)
    account.block(44196397)
    account.unblock(44196397)

    # Like / Unlike
    account.like(1234567890)
    account.unlike(1234567890)

    # ─── DMs ─────────────────────────────────────────────
    account.dm("Hello!", receivers=[44196397])
    account.dm("Hi group!", receivers=[44196397, 1068480648], media="image.png")

    # ─── PROFILE ─────────────────────────────────────────
    account.update_profile_image("avatar.jpg")
    account.update_profile_banner("banner.jpg")
    account.update_profile(
        name="New Name",
        description="New bio",
        location="Earth",
        website="https://example.com"
    )


if __name__ == "__main__":
    demo_read()
