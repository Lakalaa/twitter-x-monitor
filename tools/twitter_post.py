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

_RETWEET_QUERY_IDS = [
    "ojPdsZsimiJrUGLR1sjUtA",
    "uBbQzHBYhCNJYCXLECHLkQ",
]

_RETWEETERS_QUERY_IDS = [
    "i-CI8t2pJD15euZJErEDrg",
    "ojPdsZsimiJrUGLR1sjUtA",
    "UFet4wFN5PH5WyAHqUZpeg",
]

_TWEETDETAIL_QUERY_IDS = [
    "6uCvnic3m5reVuehkvHa3w",  # current (June 2026)
    "nBS-WpgA6ZG0CyNHD517JQ",
    "3XDB26fBve-MmjHaWTUZxA",
]

_FOLLOWERS_QUERY_IDS = [
    "Wp9x7NPOJ5klmf5H-350gw",        # current (June 2026)
    "iYaPJI11EY8VtCL3hrKU9A",        # BlueVerifiedFollowers fallback
]

_FOLLOWING_QUERY_IDS = [
    "XRzHZz4sLnhSgz55WGMCbg",        # current (June 2026)
]

_SCRAPE_FEATURES = json.dumps({
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
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
    }


def _http_get(url: str, headers: dict, retries: int = 3) -> tuple:
    """
    GET request with retry on 429 / transient network errors.
    Returns (status_code, body_text).
    """
    import urllib.request as _ur
    last_err = "No attempts made"
    for attempt in range(retries):
        try:
            if _HAS_CURL:
                resp = _curl.get(url, headers=headers, impersonate="chrome124", timeout=30)
                if resp.status_code == 429:
                    wait = 15 * (attempt + 1)
                    time.sleep(wait)
                    continue
                return resp.status_code, resp.text
            else:
                req = _ur.Request(url, headers=headers)
                with _ur.urlopen(req, timeout=30) as r:
                    return r.status, r.read().decode()
        except Exception as exc:
            last_err = str(exc)
            if attempt < retries - 1:
                time.sleep(4 * (attempt + 1))
    return 0, last_err


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


def retweet_post(tweet_id: str, auth_token: str, ct0: str) -> dict:
    """
    Retweet a tweet.
    Returns {"ok": True} or {"ok": False, "error": "..."}.
    """
    if not auth_token or not ct0:
        return {"ok": False, "error": "Missing auth_token or ct0"}

    headers = _headers(auth_token, ct0)
    last_error = "No query IDs to try"

    for query_id in _RETWEET_QUERY_IDS:
        url = f"https://x.com/i/api/graphql/{query_id}/CreateRetweet"
        payload = {
            "variables": {"tweet_id": tweet_id, "dark_request": False},
            "queryId": query_id,
        }
        try:
            status, body = _http_post(url, payload, headers)
            if status in (200, 201):
                data = json.loads(body)
                if data.get("data", {}).get("create_retweet"):
                    return {"ok": True}
                errors = data.get("errors", [])
                last_error = errors[0].get("message", body[:200]) if errors else body[:200]
                if "already" in last_error.lower() or "duplicate" in last_error.lower():
                    return {"ok": True, "note": "already retweeted"}
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
    action: str,            # "like", "comment", "both", or "retweet"
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

        if action == "retweet":
            res = retweet_post(tweet_id, auth_tok, ct0_val)
            entry_results.append(("retweet", res))

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
                      limit: int = 999999, no_admins: bool = False) -> dict:
    """
    Fetch users who retweeted a tweet using Twitter's GraphQL API.
    Tries multiple query IDs with retry on 429.
    Returns {"ok": True, "users": [...usernames], "count": N}
          | {"ok": False, "error": "..."}
    """
    import urllib.parse as _up

    hdrs = _headers(auth_token, ct0)
    last_error = "All query IDs failed"

    for qid in _RETWEETERS_QUERY_IDS:
        _URL = f"https://api.twitter.com/graphql/{qid}/Retweeters"
        seen: set = set()
        users: list = []
        cursor = None
        qid_ok = False
        empty_pages = 0

        for _page in range(50):
            variables = {"tweetId": str(tweet_id), "count": 20, "includePromotedContent": True}
            if cursor:
                variables["cursor"] = cursor

            params = _up.urlencode({"variables": json.dumps(variables), "features": _SCRAPE_FEATURES})
            req_url = f"{_URL}?{params}"

            status, body = _http_get(req_url, hdrs, retries=3)

            if status == 0:
                last_error = body
                break
            if status == 429:
                last_error = "Rate limited (429) — try again in a few minutes"
                break
            if status == 403:
                last_error = f"Query ID {qid} rejected (403) — trying next"
                break
            if status != 200:
                last_error = f"HTTP {status}: {body[:300]}"
                break

            qid_ok = True
            try:
                data = json.loads(body)
            except Exception:
                last_error = f"Invalid JSON from query ID {qid}"
                break

            # Surface any Twitter-side errors
            if data.get("errors"):
                errs = "; ".join(e.get("message","?") for e in data["errors"][:3])
                last_error = f"Twitter error: {errs}"
                break

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
                        if no_admins and (legacy.get("verified") or legacy.get("is_blue_verified")):
                            continue
                        seen.add(screen_name.lower())
                        users.append(screen_name)
                        found_users += 1

                    elif "cursor-bottom" in eid or content.get("cursorType") == "Bottom":
                        next_cursor = (content.get("value")
                                       or content.get("itemContent", {}).get("value"))

            if found_users == 0:
                empty_pages += 1
            else:
                empty_pages = 0

            if len(users) >= limit or not next_cursor or empty_pages >= 3:
                break
            cursor = next_cursor

        if qid_ok and users:
            break

    if not users:
        msg = last_error if last_error else "No retweeters found (tweet may have 0 retweets or auth is expired)"
        return {"ok": True, "users": [], "count": 0, "message": msg}

    return {"ok": True, "users": users, "count": len(users)}


def scrape_replies_graphql(tweet_id: str, auth_token: str, ct0: str,
                           limit: int = 999999, no_admins: bool = False) -> dict:
    """
    Fetch users who replied to a tweet using Twitter's TweetDetail GraphQL API.
    Used as a fallback when Scweet is not installed (e.g. on Render).
    Returns {"ok": True, "users": [...usernames], "count": N}
          | {"ok": False, "error": "..."}
    """
    import urllib.parse as _up

    hdrs = _headers(auth_token, ct0)
    last_error = "All TweetDetail query IDs failed"
    any_qid_ok = False

    for qid in _TWEETDETAIL_QUERY_IDS:
        _URL = f"https://api.twitter.com/graphql/{qid}/TweetDetail"
        seen: set = set()
        users: list = []
        cursor = None
        qid_ok = False
        empty_pages = 0

        for _page in range(50):
            variables = {
                "focalTweetId": str(tweet_id),
                "with_rux_injections": False,
                "rankingMode": "Relevance",
                "includePromotedContent": True,
                "withCommunity": True,
                "withQuickPromoteEligibilityTweetFields": True,
                "withBirdwatchNotes": True,
                "withVoice": True,
            }
            if cursor:
                variables["cursor"] = cursor

            params = _up.urlencode({
                "variables": json.dumps(variables),
                "features": _SCRAPE_FEATURES,
                "fieldToggles": json.dumps({
                    "withArticleRichContentState": True,
                    "withArticlePlainText": False,
                    "withGrokAnalyze": False,
                    "withDisallowedReplyControls": False,
                }),
            })
            req_url = f"{_URL}?{params}"

            status, body = _http_get(req_url, hdrs, retries=3)

            if status == 0:
                last_error = body
                break
            if status == 429:
                last_error = "Rate limited (429) — try again in a few minutes"
                break
            if status in (403, 400):
                last_error = f"Query ID {qid} rejected ({status}) — trying next"
                break
            if status != 200:
                last_error = f"HTTP {status}: {body[:300]}"
                break

            qid_ok = True
            try:
                data = json.loads(body)
            except Exception:
                last_error = f"Invalid JSON from query ID {qid}"
                break

            if data.get("errors"):
                errs = "; ".join(e.get("message","?") for e in data["errors"][:3])
                last_error = f"Twitter error: {errs}"
                break

            instructions = (
                data.get("data", {})
                    .get("threaded_conversation_with_injections_v2", {})
                    .get("instructions", [])
            )

            next_cursor = None
            found_users = 0

            def _extract_user(tweet_result):
                return (
                    tweet_result.get("core", {})
                                .get("user_results", {})
                                .get("result", {})
                                .get("legacy", {})
                )

            for instr in instructions:
                for entry in instr.get("entries", []):
                    eid = entry.get("entryId", "")
                    content = entry.get("content", {})

                    # Skip the focal tweet itself
                    if eid == f"tweet-{tweet_id}":
                        continue

                    # Single reply tweet
                    if eid.startswith("tweet-"):
                        tr = content.get("itemContent", {}).get("tweet_results", {}).get("result", {})
                        legacy = _extract_user(tr)
                        sn = legacy.get("screen_name", "").strip()
                        if sn and sn.lower() not in seen:
                            if no_admins and (legacy.get("verified") or legacy.get("is_blue_verified")):
                                pass
                            else:
                                seen.add(sn.lower()); users.append(sn); found_users += 1

                    # Threaded reply module (multiple replies in one entry)
                    elif content.get("entryType") == "TimelineTimelineModule":
                        for item in content.get("items", []):
                            tr = (item.get("item", {})
                                      .get("itemContent", {})
                                      .get("tweet_results", {})
                                      .get("result", {}))
                            legacy = _extract_user(tr)
                            sn = legacy.get("screen_name", "").strip()
                            if sn and sn.lower() not in seen:
                                if no_admins and (legacy.get("verified") or legacy.get("is_blue_verified")):
                                    pass
                                else:
                                    seen.add(sn.lower()); users.append(sn); found_users += 1

                    # Bottom cursor for pagination
                    if "cursor-bottom" in eid or content.get("cursorType") == "Bottom":
                        next_cursor = (content.get("value")
                                       or content.get("itemContent", {}).get("value"))

            if found_users == 0:
                empty_pages += 1
            else:
                empty_pages = 0

            if len(users) >= limit or not next_cursor or empty_pages >= 3:
                break
            cursor = next_cursor

        if qid_ok:
            any_qid_ok = True
        if qid_ok and users:
            break

    if not users:
        msg = "No replies found for this tweet" if any_qid_ok else last_error
        return {"ok": True, "users": [], "count": 0, "message": msg}

    return {"ok": True, "users": users, "count": len(users)}


def _resolve_user_id(username: str, auth_token: str, ct0: str) -> str:
    """Resolve a Twitter screen_name to a numeric user ID via GraphQL."""
    import urllib.parse as _up
    _QUERY_IDS = ["IGgvgiOx4QZndDHuD3x9TQ", "xmU6X_CKVnQ5BltcLoxFGA", "G3KGOASz96M-Ou3vSqKxfA"]
    hdrs = _headers(auth_token, ct0)
    features = json.dumps({
        "hidden_profile_likes_enabled": True,
        "hidden_profile_subscriptions_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "subscriptions_verification_info_is_identity_verified_enabled": True,
        "subscriptions_verification_info_verified_since_enabled": True,
        "highlights_tweets_tab_ui_enabled": True,
        "responsive_web_twitter_article_notes_tab_enabled": True,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "responsive_web_graphql_timeline_navigation_enabled": True,
    })
    for qid in _QUERY_IDS:
        url = f"https://api.twitter.com/graphql/{qid}/UserByScreenName"
        params = _up.urlencode({
            "variables": json.dumps({"screen_name": username, "withSafetyModeUserFields": True}),
            "features": features,
        })
        status, body = _http_get(f"{url}?{params}", hdrs, retries=2)
        if status == 200:
            try:
                data = json.loads(body)
                uid = (data.get("data", {})
                           .get("user", {})
                           .get("result", {})
                           .get("rest_id", ""))
                if uid:
                    return uid
            except Exception:
                continue
    return ""


def scrape_followers_graphql(username: str, auth_token: str, ct0: str,
                             limit: int = 999999) -> dict:
    """
    Fetch followers of a Twitter account via GraphQL (no Scweet needed).
    Returns {"ok": True, "users": [...dicts with screen_name/name/followers_count], "count": N}
           | {"ok": False, "error": "..."}
    """
    import urllib.parse as _up

    uid = _resolve_user_id(username, auth_token, ct0)
    if not uid:
        return {"ok": False, "error": f"Could not resolve @{username} to a user ID"}

    _QUERY_IDS = _FOLLOWERS_QUERY_IDS + ["9-uRROAZQPxhkXIbT5hFZA", "djdTXizios1zWqhGkmHB8A"]
    hdrs = _headers(auth_token, ct0)
    last_error = "All query IDs failed"

    for qid in _QUERY_IDS:
        _URL = f"https://api.twitter.com/graphql/{qid}/Followers"
        seen: set = set()
        users: list = []
        cursor = None
        qid_ok = False
        empty_pages = 0

        for _page in range(100):
            variables = {"userId": uid, "count": 20, "includePromotedContent": False}
            if cursor:
                variables["cursor"] = cursor
            params = _up.urlencode({"variables": json.dumps(variables), "features": _SCRAPE_FEATURES})
            status, body = _http_get(f"{_URL}?{params}", hdrs, retries=3)

            if status in (403, 400):
                last_error = f"Query ID {qid} rejected ({status})"
                break
            if status == 429:
                last_error = "Rate limited (429) — try again in a few minutes"
                break
            if status == 0:
                last_error = body; break
            if status != 200:
                last_error = f"HTTP {status}: {body[:200]}"; break

            qid_ok = True
            try:
                data = json.loads(body)
            except Exception:
                last_error = "Invalid JSON"; break

            if data.get("errors"):
                last_error = "; ".join(e.get("message","?") for e in data["errors"][:2])
                break

            instructions = (
                data.get("data", {})
                    .get("user", {})
                    .get("result", {})
                    .get("timeline", {})
                    .get("timeline", {})
                    .get("instructions", [])
            )

            next_cursor = None
            found = 0
            for instr in instructions:
                for entry in instr.get("entries", []):
                    eid = entry.get("entryId", "")
                    content = entry.get("content", {})
                    if eid.startswith("user-"):
                        legacy = (content.get("itemContent", {})
                                         .get("user_results", {})
                                         .get("result", {})
                                         .get("legacy", {}))
                        sn = legacy.get("screen_name", "").strip()
                        if sn and sn.lower() not in seen:
                            seen.add(sn.lower())
                            users.append({
                                "screen_name": sn,
                                "name": legacy.get("name", sn),
                                "followers_count": legacy.get("followers_count", 0),
                                "verified": legacy.get("verified", False) or legacy.get("is_blue_verified", False),
                            })
                            found += 1
                    if "cursor-bottom" in eid or content.get("cursorType") == "Bottom":
                        next_cursor = content.get("value") or content.get("itemContent", {}).get("value")

            empty_pages = 0 if found else empty_pages + 1
            if len(users) >= limit or not next_cursor or empty_pages >= 3:
                break
            cursor = next_cursor

        if qid_ok and users:
            break

    if not users:
        return {"ok": True, "users": [], "count": 0,
                "message": last_error or f"No followers found for @{username}"}
    return {"ok": True, "users": users, "count": len(users)}


def scrape_following_graphql(username: str, auth_token: str, ct0: str,
                              limit: int = 999999) -> dict:
    """
    Fetch accounts a Twitter user follows via GraphQL (no Scweet needed).
    Returns same shape as scrape_followers_graphql.
    """
    import urllib.parse as _up

    uid = _resolve_user_id(username, auth_token, ct0)
    if not uid:
        return {"ok": False, "error": f"Could not resolve @{username} to a user ID"}

    _QUERY_IDS = _FOLLOWING_QUERY_IDS + ["iSicc7LrzWGBgDPL0tM_TQ", "f0q-KKOTxb1yFpQEEfFmFQ"]
    hdrs = _headers(auth_token, ct0)
    last_error = "All query IDs failed"

    for qid in _QUERY_IDS:
        _URL = f"https://api.twitter.com/graphql/{qid}/Following"
        seen: set = set()
        users: list = []
        cursor = None
        qid_ok = False
        empty_pages = 0

        for _page in range(100):
            variables = {"userId": uid, "count": 20, "includePromotedContent": False}
            if cursor:
                variables["cursor"] = cursor
            params = _up.urlencode({"variables": json.dumps(variables), "features": _SCRAPE_FEATURES})
            status, body = _http_get(f"{_URL}?{params}", hdrs, retries=3)

            if status in (403, 400):
                last_error = f"Query ID {qid} rejected ({status})"
                break
            if status == 429:
                last_error = "Rate limited (429)"; break
            if status == 0:
                last_error = body; break
            if status != 200:
                last_error = f"HTTP {status}: {body[:200]}"; break

            qid_ok = True
            try:
                data = json.loads(body)
            except Exception:
                last_error = "Invalid JSON"; break

            if data.get("errors"):
                last_error = "; ".join(e.get("message","?") for e in data["errors"][:2])
                break

            instructions = (
                data.get("data", {})
                    .get("user", {})
                    .get("result", {})
                    .get("timeline", {})
                    .get("timeline", {})
                    .get("instructions", [])
            )

            next_cursor = None
            found = 0
            for instr in instructions:
                for entry in instr.get("entries", []):
                    eid = entry.get("entryId", "")
                    content = entry.get("content", {})
                    if eid.startswith("user-"):
                        legacy = (content.get("itemContent", {})
                                         .get("user_results", {})
                                         .get("result", {})
                                         .get("legacy", {}))
                        sn = legacy.get("screen_name", "").strip()
                        if sn and sn.lower() not in seen:
                            seen.add(sn.lower())
                            users.append({
                                "screen_name": sn,
                                "name": legacy.get("name", sn),
                                "followers_count": legacy.get("followers_count", 0),
                                "verified": legacy.get("verified", False) or legacy.get("is_blue_verified", False),
                            })
                            found += 1
                    if "cursor-bottom" in eid or content.get("cursorType") == "Bottom":
                        next_cursor = content.get("value") or content.get("itemContent", {}).get("value")

            empty_pages = 0 if found else empty_pages + 1
            if len(users) >= limit or not next_cursor or empty_pages >= 3:
                break
            cursor = next_cursor

        if qid_ok and users:
            break

    if not users:
        return {"ok": True, "users": [], "count": 0,
                "message": last_error or f"No following found for @{username}"}
    return {"ok": True, "users": users, "count": len(users)}


def auto_refresh_ct0(auth_token: str) -> str:
    """
    Visit x.com with just auth_token — Twitter responds by setting a fresh ct0
    CSRF cookie. Returns the ct0 value, or "" on failure.
    """
    import http.cookiejar
    try:
        cj  = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        req = urllib.request.Request("https://x.com/", headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Cookie": f"auth_token={auth_token}",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        opener.open(req, timeout=15)
        ct0 = next((c.value for c in cj if c.name == "ct0"), "")
        if ct0:
            # Persist back to targets.json and env so callers always get it
            config_path = os.path.join(os.path.dirname(__file__), "targets.json")
            try:
                cfg = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        cfg = json.load(f)
                cfg["twitter_ct0"] = ct0
                with open(config_path, "w") as f:
                    json.dump(cfg, f, indent=2)
            except Exception:
                pass
            os.environ["TWITTER_CT0"] = ct0
        return ct0
    except Exception:
        return ""


def get_auth_from_config() -> tuple:
    """
    Return (auth_token, ct0) from tools/targets.json or env vars.
    If ct0 is missing, auto-refreshes it by visiting x.com with auth_token.
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

    # If we have an auth_token but no ct0, get one automatically
    if auth_token and not ct0:
        ct0 = auto_refresh_ct0(auth_token)

    return auth_token, ct0
