"""
x_scraper.py
Direct X/Twitter scraper using UserByScreenName + UserTweets GraphQL endpoints.
These endpoints work from datacenter IPs unlike SearchTimeline.
"""
from __future__ import annotations
import json, os, re, time
from typing import Optional

try:
    import curl_cffi.requests as _cffi
    _HAS_CFFI = True
except ImportError:
    import requests as _req
    _HAS_CFFI = False

BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

_QID_USER_BY_SCREEN   = "2qvSHpkWTMS9i0zJAwDNiA"
_QID_USER_TWEETS      = "RIylB10EGWyBSs4ZXpQjCw"
_QID_TWEET_DETAIL     = "VWFGPVAGkZMGRKGe3GFFnA"   # confirmed working Jul-2026

_USER_FEATURES = {
    "hidden_profile_subscriptions_enabled": True,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
}
_TWEET_FEATURES = {
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}

_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "targets.json")
_USER_ID_CACHE_FILE = os.path.join(os.path.dirname(__file__), "x_user_id_cache.json")


def _load_creds() -> tuple[str, str]:
    """Return (auth_token, ct0) from config or env."""
    auth = os.environ.get("TWITTER_AUTH_TOKEN_COOKIE", "")
    ct0  = os.environ.get("TWITTER_CT0", "")
    if not auth and os.path.exists(_CONFIG_FILE):
        try:
            cfg = json.load(open(_CONFIG_FILE))
            auth = cfg.get("twitter_auth_token", "")
            ct0  = cfg.get("twitter_ct0", "")
        except Exception:
            pass
    return auth, ct0


def _make_session(auth: str, ct0: str):
    if _HAS_CFFI:
        s = _cffi.Session(impersonate="chrome120")
    else:
        s = _req.Session()
    s.headers.update({
        "Authorization":          f"Bearer {BEARER}",
        "Cookie":                 f"auth_token={auth}; ct0={ct0}",
        "X-Csrf-Token":           ct0,
        "User-Agent":             "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-twitter-active-user":  "yes",
        "x-twitter-auth-type":    "OAuth2Session",
        "x-twitter-client-language": "en",
    })
    return s


def _load_user_id_cache() -> dict:
    if os.path.exists(_USER_ID_CACHE_FILE):
        try:
            return json.load(open(_USER_ID_CACHE_FILE))
        except Exception:
            pass
    return {}


def _save_user_id_cache(cache: dict):
    try:
        with open(_USER_ID_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


def _find_tweets(obj, found=None):
    """Recursively extract tweet dicts from any GraphQL response."""
    if found is None:
        found = []
    if isinstance(obj, dict):
        if "legacy" in obj and "rest_id" in obj and "core" in obj:
            legacy = obj.get("legacy", {})
            text = legacy.get("full_text", "")
            if text and not text.startswith("RT "):
                # UserTweets: screen_name at result.core.screen_name
                # TweetDetail: screen_name at result.legacy.screen_name (core is empty)
                ur = obj.get("core", {}).get("user_results", {}).get("result", {})
                screen_name = (
                    ur.get("core", {}).get("screen_name", "")
                    or ur.get("legacy", {}).get("screen_name", "")
                )
                found.append({
                    "id":       obj.get("rest_id", ""),
                    "text":     text,
                    "likes":    legacy.get("favorite_count", 0),
                    "retweets": legacy.get("retweet_count", 0),
                    "date":     legacy.get("created_at", ""),
                    "user":     screen_name,
                    "lang":     legacy.get("lang", ""),
                })
        for v in obj.values():
            _find_tweets(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _find_tweets(item, found)
    return found


def get_user_id(screen_name: str, session, cache: dict) -> Optional[str]:
    """Resolve @screen_name → numeric user ID, using cache."""
    key = screen_name.lower()
    if key in cache:
        return cache[key]
    url = f"https://x.com/i/api/graphql/{_QID_USER_BY_SCREEN}/UserByScreenName"
    try:
        r = session.get(url, params={
            "variables": json.dumps({"screen_name": screen_name, "withSafetyModeUserFields": True}, separators=(",", ":")),
            "features":  json.dumps(_USER_FEATURES, separators=(",", ":")),
        }, timeout=15)
        if r.status_code == 200:
            data = r.json()
            uid = data.get("data", {}).get("user", {}).get("result", {}).get("rest_id", "")
            if uid:
                cache[key] = uid
                return uid
    except Exception:
        pass
    return None


def fetch_user_tweets(user_id: str, screen_name: str, session, count: int = 20) -> list[dict]:
    """Fetch recent non-RT tweets from a user."""
    url = f"https://x.com/i/api/graphql/{_QID_USER_TWEETS}/UserTweets"
    try:
        r = session.get(url, params={
            "variables": json.dumps({
                "userId": user_id,
                "count": count,
                "includePromotedContent": False,
                "withQuickPromoteEligibilityTweetFields": True,
                "withVoice": True,
                "withV2Timeline": True,
            }, separators=(",", ":")),
            "features": json.dumps(_TWEET_FEATURES, separators=(",", ":")),
        }, timeout=15)
        if r.status_code == 429:
            import logging
            logging.warning(f"x_scraper: rate-limited (429) for @{screen_name}, skipping")
            return []
        if r.status_code == 200:
            tweets = _find_tweets(r.json())
            for t in tweets:
                if not t["user"]:
                    t["user"] = screen_name
                t["url"] = f"https://x.com/{t['user']}/status/{t['id']}" if t["id"] else ""
            return tweets
    except Exception:
        pass
    return []


def _parse_twitter_date(date_str: str):
    """Parse Twitter's created_at string to a UTC timestamp."""
    import email.utils
    try:
        return email.utils.parsedate_to_datetime(date_str).timestamp()
    except Exception:
        return 0.0


_DETAIL_FEATURES = {
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "articles_preview_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}


def fetch_tweet_replies(tweet_id: str, session, max_age_hours: int = 48) -> list[dict]:
    """
    Fetch reply tweets on a given tweet via TweetDetail GraphQL.
    Returns reply tweets from ANY user — this is how we capture real user complaints.
    Only returns replies newer than max_age_hours.
    """
    import time as _time
    import logging
    cutoff = _time.time() - max_age_hours * 3600
    url = f"https://x.com/i/api/graphql/{_QID_TWEET_DETAIL}/TweetDetail"
    try:
        r = session.get(url, params={
            "variables": json.dumps({
                "focalTweetId": tweet_id,
                "referrer": "tweet",
                "count": 40,
                "with_rux_injections": False,
                "rankingMode": "Recency",
                "includePromotedContent": False,
                "withCommunity": True,
                "withQuickPromoteEligibilityTweetFields": False,
                "withBirdwatchNotes": False,
                "withVoice": True,
            }, separators=(",", ":")),
            "features": json.dumps(_DETAIL_FEATURES, separators=(",", ":")),
            "fieldToggles": '{"withArticleRichContentState":false}',
        }, timeout=15)
        if r.status_code == 429:
            logging.warning(f"x_scraper: rate-limited (429) fetching replies for {tweet_id}")
            return []
        if r.status_code != 200:
            return []
        raw = r.json()
        all_tweets = _find_tweets(raw)
        replies = []
        for t in all_tweets:
            if t.get("id") == tweet_id:
                continue  # skip the focal tweet itself
            ts = _parse_twitter_date(t.get("date", ""))
            if ts and ts < cutoff:
                continue
            t["url"] = f"https://x.com/{t['user']}/status/{t['id']}" if t.get("id") else ""
            t["is_reply"] = True
            replies.append(t)
        return replies
    except Exception as e:
        import logging
        logging.warning(f"x_scraper: fetch_tweet_replies({tweet_id}) error: {e}")
        return []


def fetch_tweets_from_accounts(
    screen_names: list[str],
    tweets_per_account: int = 10,
    max_age_hours: int = 48,
) -> list[dict]:
    """
    Fetch recent tweets from a list of @screen_names.
    Returns merged, deduplicated list sorted by engagement.
    Only returns tweets newer than max_age_hours.
    """
    import time as _time
    auth, ct0 = _load_creds()
    if not auth or not ct0:
        return []

    session   = _make_session(auth, ct0)
    cache     = _load_user_id_cache()
    all_tweets: list[dict] = []
    seen_ids: set = set()
    cutoff = _time.time() - max_age_hours * 3600

    for screen_name in screen_names:
        uid = get_user_id(screen_name, session, cache)
        if not uid:
            continue
        tweets = fetch_user_tweets(uid, screen_name, session, count=tweets_per_account)
        for t in tweets:
            tid = t.get("id", "")
            if not tid or tid in seen_ids:
                continue
            ts = _parse_twitter_date(t.get("date", ""))
            if ts and ts < cutoff:
                continue
            seen_ids.add(tid)
            all_tweets.append(t)
        _time.sleep(0.5)

    _save_user_id_cache(cache)
    all_tweets.sort(key=lambda x: (x.get("likes", 0) + x.get("retweets", 0)), reverse=True)
    return all_tweets
