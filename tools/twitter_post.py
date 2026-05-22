"""
Twitter posting — reply/comment, like, and bulk engagement using cookie auth.
Uses curl_cffi to impersonate Chrome and call Twitter's internal GraphQL API.
"""

import json
import os
import time
import random

try:
    from curl_cffi import requests as _curl
    _HAS_CURL = True
except ImportError:
    _HAS_CURL = False

# Twitter's public bearer token (same one used by the web client)
_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# GraphQL query IDs — try in order until one works
_CREATE_TWEET_QUERY_IDS = [
    "a1p9RWpkYKBjWv_I3WzS-A",
    "SoVnbfCycZ7fERGCwpZkYA",
    "rwpVT1eOpetM8y6CiL5MiQ",
]

_LIKE_QUERY_IDS = [
    "lI07N6Otwv1PhnEgXILM7A",
    "ZYKSe-w7KEslx3JhSIk5LA",
]


def _headers(auth_token: str, ct0: str) -> dict:
    return {
        "authorization": f"Bearer {_BEARER}",
        "content-type": "application/json",
        "x-csrf-token": ct0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "cookie": f"auth_token={auth_token}; ct0={ct0}",
        "referer": "https://x.com/",
        "origin": "https://x.com",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }


def _http_post(url: str, payload: dict, headers: dict) -> tuple:
    """Returns (status_code, body_text). Works with or without curl_cffi."""
    if _HAS_CURL:
        resp = _curl.post(url, json=payload, headers=headers,
                          impersonate="chrome124", timeout=30)
        return resp.status_code, resp.text
    else:
        import urllib.request as _ur
        req = _ur.Request(url, data=json.dumps(payload).encode(),
                          headers=headers, method="POST")
        try:
            with _ur.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode()
        except Exception as exc:
            raise exc


def _create_tweet_payload(text: str, reply_to_id: str, query_id: str) -> dict:
    return {
        "variables": {
            "tweet_text": text,
            "reply": {
                "in_reply_to_tweet_id": reply_to_id,
                "exclude_reply_user_ids": [],
            },
            "dark_request": False,
            "media": {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
        },
        "features": {
            "tweetypie_unmention_optimization_enabled": True,
            "responsive_web_edit_tweet_api_enabled": True,
            "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
            "view_counts_everywhere_api_enabled": True,
            "longform_notetweets_consumption_enabled": True,
            "responsive_web_twitter_article_tweet_consumption_enabled": False,
            "tweet_awards_web_tipping_enabled": False,
            "longform_notetweets_rich_text_read_enabled": True,
            "longform_notetweets_inline_media_enabled": True,
            "rweb_video_timestamps_enabled": True,
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "freedom_of_speech_not_reach_fetch_enabled": True,
            "standardized_nudges_misinfo": True,
            "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
            "interactive_text_enabled": True,
            "responsive_web_text_conversations_enabled": False,
            "responsive_web_enhance_cards_enabled": False,
        },
        "queryId": query_id,
    }


def post_reply(text: str, in_reply_to_tweet_id: str,
               auth_token: str, ct0: str) -> dict:
    """
    Post a reply to a tweet.
    Returns {"ok": True, "tweet_id": "..."} or {"ok": False, "error": "..."}.
    Tries multiple GraphQL query IDs in sequence.
    """
    if not auth_token or not ct0:
        return {"ok": False, "error": "Missing auth_token or ct0"}

    headers = _headers(auth_token, ct0)
    last_error = "No query IDs to try"

    for query_id in _CREATE_TWEET_QUERY_IDS:
        url     = f"https://x.com/i/api/graphql/{query_id}/CreateTweet"
        payload = _create_tweet_payload(text, in_reply_to_tweet_id, query_id)

        try:
            status, body = _http_post(url, payload, headers)

            if status in (200, 201):
                data = json.loads(body)
                tweet_id = (
                    data.get("data", {})
                        .get("create_tweet", {})
                        .get("tweet_results", {})
                        .get("result", {})
                        .get("rest_id", "")
                )
                if tweet_id:
                    return {"ok": True, "tweet_id": tweet_id}
                errors = data.get("errors", [])
                last_error = errors[0].get("message", body[:200]) if errors else body[:200]
            else:
                last_error = f"HTTP {status}: {body[:200]}"

        except Exception as exc:
            last_error = str(exc)

        time.sleep(1)

    return {"ok": False, "error": last_error}


def like_tweet(tweet_id: str, auth_token: str, ct0: str) -> dict:
    """
    Like a tweet.
    Returns {"ok": True} or {"ok": False, "error": "..."}.
    """
    if not auth_token or not ct0:
        return {"ok": False, "error": "Missing auth_token or ct0"}

    headers = _headers(auth_token, ct0)
    last_error = "No query IDs to try"

    for query_id in _LIKE_QUERY_IDS:
        url = f"https://x.com/i/api/graphql/{query_id}/FavoriteTweet"
        payload = {
            "variables": {"tweet_id": tweet_id},
            "queryId": query_id,
        }
        try:
            status, body = _http_post(url, payload, headers)
            if status in (200, 201):
                data = json.loads(body)
                # Success: {"data": {"favorite_tweet": "Done"}}
                if data.get("data", {}).get("favorite_tweet"):
                    return {"ok": True}
                errors = data.get("errors", [])
                last_error = errors[0].get("message", body[:200]) if errors else body[:200]
                # Already liked is still fine
                if "already" in last_error.lower():
                    return {"ok": True, "note": "already liked"}
            else:
                last_error = f"HTTP {status}: {body[:200]}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1)

    return {"ok": False, "error": last_error}


def extract_tweet_id(url_or_id: str) -> str:
    """Extract tweet ID from a URL like https://x.com/user/status/123456789 or return as-is."""
    s = url_or_id.strip().rstrip("/")
    if s.isdigit():
        return s
    parts = s.split("/")
    for i, p in enumerate(parts):
        if p == "status" and i + 1 < len(parts):
            tid = parts[i + 1].split("?")[0]
            if tid.isdigit():
                return tid
    return s


def load_account_pool(cookies_file: str = "tools/cookies.json") -> list:
    """Load the multi-account pool from cookies.json."""
    if not os.path.exists(cookies_file):
        return []
    try:
        with open(cookies_file) as f:
            return json.load(f)
    except Exception:
        return []


def bulk_engage(
    tweet_url: str,
    action: str,            # "like", "comment", or "both"
    comment_text: str = "",
    mention: str = "",      # @username to prepend to every comment
    accounts: list = None,  # subset of pool; if None, uses full pool
    delay_min: float = 3.0,
    delay_max: float = 8.0,
    progress_cb=None,       # callback(done, total, username, result_str)
) -> dict:
    """
    Run like/comment/both on a tweet using the full account pool.

    Returns:
        {
          "tweet_id": str,
          "action": str,
          "total": int,
          "ok": int,
          "fail": int,
          "results": [{"username": ..., "action": ..., "ok": bool, "detail": ...}]
        }
    """
    tweet_id = extract_tweet_id(tweet_url)
    if not tweet_id or not tweet_id.isdigit():
        return {"ok": 0, "fail": 0, "error": f"Could not parse tweet ID from: {tweet_url}"}

    pool = accounts if accounts is not None else load_account_pool()
    if not pool:
        return {"ok": 0, "fail": 0, "error": "Account pool is empty. Check tools/cookies.json."}

    # Build comment text: optional @mention prefix
    def build_comment(base_text: str) -> str:
        parts = []
        if mention:
            tag = mention if mention.startswith("@") else f"@{mention}"
            parts.append(tag)
        if base_text:
            parts.append(base_text)
        return " ".join(parts) if parts else ""

    results = []
    ok_count = 0
    fail_count = 0
    total = len(pool)

    for i, entry in enumerate(pool):
        username = entry.get("username", f"account_{i}")
        cookies  = entry.get("cookies", {})
        auth_tok = cookies.get("auth_token", "")
        ct0_val  = cookies.get("ct0", "")

        if not auth_tok or not ct0_val:
            results.append({"username": username, "action": action, "ok": False, "detail": "missing credentials"})
            fail_count += 1
            if progress_cb:
                progress_cb(i + 1, total, username, "❌ missing creds")
            continue

        entry_results = []

        if action in ("like", "both"):
            res = like_tweet(tweet_id, auth_tok, ct0_val)
            entry_results.append(("like", res))

        if action in ("comment", "both"):
            text = build_comment(comment_text)
            if text:
                res = post_reply(text, tweet_id, auth_tok, ct0_val)
            else:
                res = {"ok": False, "error": "No comment text provided"}
            entry_results.append(("comment", res))

        # Aggregate success for this account
        all_ok = all(r["ok"] for _, r in entry_results)
        detail = "; ".join(
            f"{a}={'ok' if r['ok'] else r.get('error','?')}"
            for a, r in entry_results
        )
        results.append({"username": username, "action": action, "ok": all_ok, "detail": detail})
        if all_ok:
            ok_count += 1
        else:
            fail_count += 1

        if progress_cb:
            status_str = "✅" if all_ok else f"❌ {detail}"
            progress_cb(i + 1, total, username, status_str)

        # Rate-limit friendly delay between accounts
        if i < total - 1:
            time.sleep(random.uniform(delay_min, delay_max))

    return {
        "tweet_id": tweet_id,
        "action": action,
        "total": total,
        "ok": ok_count,
        "fail": fail_count,
        "results": results,
    }


def scrape_retweeters(tweet_id: str, auth_token: str, ct0: str,
                      limit: int = 200, no_admins: bool = False) -> dict:
    """
    Fetch users who retweeted a tweet using Twitter's GraphQL API.
    Returns {"ok": True, "users": [...usernames], "count": N}
          | {"ok": False, "error": "..."}
    """
    _QID = "i-CI8t2pJD15euZJErEDrg"
    _URL = f"https://x.com/i/api/graphql/{_QID}/Retweeters"
    _FEATURES = json.dumps({
        "rweb_tipjar_consumption_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "communities_web_enable_tweet_community_results_fetch": True,
        "c9s_tweet_anatomy_moderator_badge_enabled": True,
        "articles_preview_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "responsive_web_twitter_article_tweet_consumption_enabled": True,
        "tweet_awards_web_tipping_enabled": False,
        "creator_subscriptions_quote_tweet_preview_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
        "rweb_video_timestamps_enabled": True,
        "longform_notetweets_rich_text_read_enabled": True,
        "longform_notetweets_inline_media_enabled": True,
        "responsive_web_enhance_cards_enabled": False,
    })

    hdrs = _headers(auth_token, ct0)
    seen: set = set()
    users: list = []
    cursor = None

    for _page in range(50):  # max 50 pages × 20 = 1000
        variables = {"tweetId": str(tweet_id), "count": 20, "includePromotedContent": True}
        if cursor:
            variables["cursor"] = cursor

        import urllib.request as _ur, urllib.parse as _up
        params = _up.urlencode({"variables": json.dumps(variables), "features": _FEATURES})
        req_url = f"{_URL}?{params}"

        try:
            if _HAS_CURL:
                resp = _curl.get(req_url, headers=hdrs, impersonate="chrome124", timeout=30)
                status, body = resp.status_code, resp.text
            else:
                req = _ur.Request(req_url, headers=hdrs)
                with _ur.urlopen(req, timeout=30) as r:
                    status, body = r.status, r.read().decode()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if status != 200:
            return {"ok": False, "error": f"HTTP {status}: {body[:200]}"}

        try:
            data = json.loads(body)
        except Exception:
            return {"ok": False, "error": "Invalid JSON response"}

        instructions = (
            data.get("data", {})
                .get("retweeters_timeline", {})
                .get("timeline", {})
                .get("instructions", [])
        )

        next_cursor = None
        found_users = 0
        for instr in instructions:
            for entry in instr.get("entries", []):
                eid = entry.get("entryId", "")
                content = entry.get("content", {})

                # User entry
                if eid.startswith("user-"):
                    legacy = (
                        content.get("itemContent", {})
                               .get("user_results", {})
                               .get("result", {})
                               .get("legacy", {})
                    )
                    screen_name = legacy.get("screen_name", "").strip()
                    if not screen_name or screen_name.lower() in seen:
                        continue
                    # Skip verified if --no-admins
                    if no_admins and (legacy.get("verified") or legacy.get("is_blue_verified")):
                        continue
                    seen.add(screen_name.lower())
                    users.append(screen_name)
                    found_users += 1

                # Cursor entry
                elif "cursor-bottom" in eid or content.get("cursorType") == "Bottom":
                    next_cursor = content.get("value") or content.get("itemContent", {}).get("value")

        if len(users) >= limit or not next_cursor or found_users == 0:
            break
        cursor = next_cursor

    if not users:
        return {"ok": True, "users": [], "count": 0, "message": "No retweeters found"}

    users = users[:limit]
    return {"ok": True, "users": users, "count": len(users)}


def get_auth_from_config() -> tuple:
    """
    Return (auth_token, ct0) from tools/targets.json or env vars.
    Falls back gracefully.
    """
    config_path = os.path.join(os.path.dirname(__file__), "targets.json")
    auth_token, ct0 = "", ""

    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            auth_token = cfg.get("twitter_auth_token", "")
            ct0        = cfg.get("twitter_ct0", "")
        except Exception:
            pass

    auth_token = auth_token or os.environ.get("TWITTER_AUTH_TOKEN", "")
    ct0        = ct0        or os.environ.get("TWITTER_CT0", "")
    return auth_token, ct0
