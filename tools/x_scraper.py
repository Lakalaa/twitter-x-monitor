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

# QIDs extracted from main.933c177a.js on 2026-07-28 via residential proxy bundle fetch.
# When any endpoint returns 404, call _auto_refresh_qids() to re-extract from the live bundle.
_QID_USER_BY_SCREEN   = "Gb-d6r0vxPOADdG62OEBpQ"
_QID_USER_TWEETS      = "eoJ5zbv51Z_KVl81v9PmLQ"
_QID_TWEET_DETAIL     = "559hs_YZNV4IgA3Z6zIIuw"
_QID_SEARCH_TIMELINE  = "BGd0T_j7oVwlW5U79tO_0A"   # requires residential proxy (blocked from GCP IPs)

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

# ── Hardcoded numeric user IDs for priority accounts ─────────────────────────
# These never change once assigned. Having them avoids UserByScreenName API
# calls for the most critical accounts, eliminating 429 failures on fresh deploys.
# Populated from /api/test-twitter?bulk=1 on 2026-07-28.
_KNOWN_USER_IDS: dict[str, str] = {
    "binancehelpdesk":       "1026411197669625858",
    "coinbasesupport":       "2555176531",
    "krakensupport":         "2771348308",
    "trustwalletapp":        "1426108217025867781",
    "aaveaave":              "1719825249397604352",
    "jupiterexchange":       "1446489618208067586",
    "arbitrum":              "1332033418088099843",
    "base":                  "1628067904083181570",
    "circle":                "2151686839",
    "uniswap":               "984188226826010624",
    "phantom":               "1379053041995890695",
    "hyperliquidx":          "1527020295059648513",
    "magiceden":             "1433121559057559555",
    "peckshieldalert":       "1128606567354359808",
}

# ── Per-account rate-limit cooldown tracking ─────────────────────────────────
# When we get 429 on an account, skip it for 16 minutes so we don't waste
# every cycle hammering blocked accounts. Resets naturally as windows expire.
_RATE_LIMIT_COOLDOWN: dict[str, float] = {}   # lower(screen_name) → unblocked_at
_RL_WINDOW = 16 * 60  # 16 min — Twitter's 15-min window + 1-min buffer
_LAST_FETCH_STATUS: dict[str, object] = {}   # lower(screen_name) → last HTTP result (debug)

def _is_rl(name: str) -> bool:
    return time.time() < _RATE_LIMIT_COOLDOWN.get(name.lower(), 0)

def _mark_rl(name: str) -> None:
    _RATE_LIMIT_COOLDOWN[name.lower()] = time.time() + _RL_WINDOW


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
    """Resolve @screen_name → numeric user ID, using cache then hardcoded IDs then API."""
    key = screen_name.lower()
    if key in cache:
        return cache[key]
    # Check hardcoded known IDs — zero API calls for priority accounts
    if key in _KNOWN_USER_IDS:
        uid = _KNOWN_USER_IDS[key]
        cache[key] = uid
        return uid
    if _is_rl(screen_name):
        return None  # still in cooldown, skip
    url = f"https://x.com/i/api/graphql/{_QID_USER_BY_SCREEN}/UserByScreenName"
    try:
        r = session.get(url, params={
            "variables": json.dumps({"screen_name": screen_name, "withSafetyModeUserFields": True}, separators=(",", ":")),
            "features":  json.dumps(_USER_FEATURES, separators=(",", ":")),
        }, timeout=15)
        if r.status_code == 429:
            import logging
            logging.warning(f"x_scraper: rate-limited (429) resolving @{screen_name}, cooldown 16 min")
            _mark_rl(screen_name)
            return None
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
    if _is_rl(screen_name):
        return []  # still in 16-min cooldown from a previous 429
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
            logging.warning(f"x_scraper: rate-limited (429) for @{screen_name}, cooldown 16 min")
            _mark_rl(screen_name)
            _LAST_FETCH_STATUS[screen_name.lower()] = 429
            return []
        if r.status_code == 200:
            tweets = _find_tweets(r.json())
            for t in tweets:
                if not t["user"]:
                    t["user"] = screen_name
                t["url"] = f"https://x.com/{t['user']}/status/{t['id']}" if t["id"] else ""
            _LAST_FETCH_STATUS[screen_name.lower()] = f"200-{len(tweets)}tweets"
            return tweets
        import logging
        logging.warning(f"x_scraper: UserTweets HTTP {r.status_code} for @{screen_name}")
        _LAST_FETCH_STATUS[screen_name.lower()] = r.status_code
    except Exception as _e:
        import logging
        logging.warning(f"x_scraper: UserTweets exception for @{screen_name}: {_e}")
        _LAST_FETCH_STATUS[screen_name.lower()] = f"exc:{type(_e).__name__}"
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


# ── SearchTimeline — global keyword search via residential proxy ──────────────
# SearchTimeline is blocked from GCP/datacenter IPs but works via Webshare residential proxies.
# This allows searching ALL of Twitter for specific complaint keywords, not just monitored
# accounts' reply threads. Dramatically expands worldwide coverage.
_SEARCH_FEATURES = {
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


def _refresh_qids_from_bundle(proxy_session=None) -> dict:
    """
    Fetch Twitter's main JS bundle and re-extract all GraphQL queryIds.
    Called automatically when any endpoint returns 404 (rotated QIDs).
    Uses proxy session so the bundle fetch comes from a residential IP.
    """
    import re as _re, logging as _log
    try:
        sess = proxy_session
        if sess is None:
            if _HAS_CFFI:
                sess = _cffi.Session(impersonate="chrome120")
            else:
                sess = _req.Session()
            sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        # Fetch home page to find bundle URL
        r = sess.get("https://x.com/explore", timeout=20, headers={"Accept": "text/html,application/xhtml+xml"})
        html = r.text
        bundle_urls = _re.findall(r'https://abs\.twimg\.com/responsive-web/client-web/main\.[^"]+\.js', html)
        if not bundle_urls:
            _log.warning("x_scraper: QID refresh — no bundle URL in Twitter home page")
            return {}
        rb = sess.get(bundle_urls[0], timeout=40)
        js = rb.text
        ops: dict[str, str] = {}
        for m in _re.finditer(r'queryId:"([^"]{10,})",operationName:"([^"]+)"', js):
            ops[m.group(2)] = m.group(1)
        _log.info(f"x_scraper: QID refresh — extracted {len(ops)} ops from {bundle_urls[0][-40:]}")
        return ops
    except Exception as exc:
        import logging
        logging.warning(f"x_scraper: QID bundle refresh error: {exc}")
        return {}


def _auto_refresh_qids(proxy_session=None) -> None:
    """Re-extract all QIDs from the live bundle when 404s indicate they've rotated."""
    global _QID_USER_BY_SCREEN, _QID_USER_TWEETS, _QID_TWEET_DETAIL, _QID_SEARCH_TIMELINE
    ops = _refresh_qids_from_bundle(proxy_session)
    if ops.get("SearchTimeline"):  _QID_SEARCH_TIMELINE = ops["SearchTimeline"]
    if ops.get("UserTweets"):      _QID_USER_TWEETS     = ops["UserTweets"]
    if ops.get("UserByScreenName"):_QID_USER_BY_SCREEN  = ops["UserByScreenName"]
    if ops.get("TweetDetail"):     _QID_TWEET_DETAIL    = ops["TweetDetail"]


def search_keyword_complaints(
    query: str,
    proxy_session,
    count: int = 20,
) -> list:
    """
    Search ALL of Twitter for a complaint keyword using SearchTimeline GraphQL.
    Requires a residential proxy session (proxy_pool.make_proxied_session) —
    this endpoint is blocked from GCP/datacenter IPs.

    Returns tweet dicts in the same format as fetch_user_tweets.
    Returns [] on 429 or if proxy_session is None.
    Auto-refreshes QIDs on 404 (rotated bundle).

    Example query: 'withdrawal stuck crypto -is:retweet lang:en'
    """
    import logging
    if proxy_session is None:
        return []
    url = f"https://x.com/i/api/graphql/{_QID_SEARCH_TIMELINE}/SearchTimeline"
    try:
        r = proxy_session.get(url, params={
            "variables": json.dumps({
                "rawQuery":    query,
                "count":       count,
                "querySource": "typed_query",
                "product":     "Latest",
            }, separators=(",", ":")),
            "features": json.dumps(_SEARCH_FEATURES, separators=(",", ":")),
        }, timeout=20)
        if r.status_code == 404:
            logging.warning("x_scraper: SearchTimeline 404 — QIDs rotated, refreshing from bundle")
            _auto_refresh_qids(proxy_session)
            return []
        if r.status_code == 429:
            logging.warning(f"x_scraper: SearchTimeline 429 — rate limited for: {query[:50]}")
            return []
        if r.status_code != 200:
            logging.warning(f"x_scraper: SearchTimeline HTTP {r.status_code} for: {query[:50]}")
            return []
        tweets = _find_tweets(r.json())
        result = []
        for t in tweets:
            text = t.get("text", "")
            if not text or text.startswith("RT "):
                continue
            if t.get("id") and t.get("user"):
                t["url"] = f"https://x.com/{t['user']}/status/{t['id']}"
            result.append(t)
        return result
    except Exception as exc:
        logging.warning(f"x_scraper: search_keyword_complaints error ({query[:40]}): {exc}")
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
