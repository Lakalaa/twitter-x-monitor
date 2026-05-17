"""
Twitter posting — reply/comment on tweets using cookie auth.
Uses curl_cffi to impersonate Chrome and call Twitter's internal GraphQL API.
"""

import json
import os
import time

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
            if _HAS_CURL:
                resp = _curl.post(
                    url, json=payload, headers=headers,
                    impersonate="chrome124", timeout=30
                )
                status = resp.status_code
                body   = resp.text
            else:
                import urllib.request as _ur
                req = _ur.Request(
                    url,
                    data=json.dumps(payload).encode(),
                    headers=headers,
                    method="POST"
                )
                with _ur.urlopen(req, timeout=30) as r:
                    status = r.status
                    body   = r.read().decode()

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
                # GraphQL 200 with errors
                errors = data.get("errors", [])
                last_error = errors[0].get("message", body[:200]) if errors else body[:200]
            else:
                last_error = f"HTTP {status}: {body[:200]}"

        except Exception as exc:
            last_error = str(exc)

        time.sleep(1)

    return {"ok": False, "error": last_error}


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
