"""
TweeterPy — Simple Twitter/X data extraction
GitHub: https://github.com/iSarabjitDhiman/TweeterPy

Good for: quick profile lookups, simple follower/following extraction,
          easy login without managing cookies manually.

Install: pip install -U tweeterpy
"""

from tweeterpy import TweeterPy


def demo():
    twitter = TweeterPy()

    # ─── LOGIN ────────────────────────────────────────────
    # Option A: username + password
    twitter.login("your_username", "your_password", "your_email@example.com")

    # Option B: with 2FA secret (TOTP)
    # twitter.login("username", "password", "email", totp_secret="YOUR_2FA_SECRET")

    # Option C: using cookies
    # twitter.generate_session(auth_token="YOUR_AUTH_TOKEN")

    # ─── USER LOOKUP ──────────────────────────────────────
    # Get user ID from username
    user_id = twitter.get_user_id("elonmusk")
    print(f"User ID: {user_id}")

    # Get full user profile data
    user_data = twitter.get_user_info("elonmusk")
    print(user_data)

    # ─── FOLLOWERS / FOLLOWING ────────────────────────────
    # Get followers (returns list of user objects)
    followers = twitter.get_followers(user_id, total=500)
    print(f"Fetched {len(followers)} followers")

    # Get following
    following = twitter.get_following(user_id, total=500)
    print(f"Fetched {len(following)} following")

    # ─── TWEETS ──────────────────────────────────────────
    # Get a user's tweets
    tweets = twitter.get_user_tweets(user_id, total=100)

    # Get tweets and replies
    tweets = twitter.get_user_tweets(user_id, total=100, replies=True)

    # Get media tweets
    media = twitter.get_user_media(user_id, total=50)

    # Get liked tweets
    likes = twitter.get_user_likes(user_id, total=50)

    # ─── TWEET DETAILS ────────────────────────────────────
    tweet = twitter.get_tweet(tweet_id=1234567890)
    print(tweet)

    # Replies to a tweet
    replies = twitter.get_tweet_replies(tweet_id=1234567890, total=100)

    # ─── SEARCH ──────────────────────────────────────────
    results = twitter.search("python programming", total=100)
    for tweet in results:
        print(tweet)

    # ─── NOTIFICATIONS ────────────────────────────────────
    # Get your own account notifications
    notifications = twitter.get_notifications()
    print(notifications)


if __name__ == "__main__":
    demo()
