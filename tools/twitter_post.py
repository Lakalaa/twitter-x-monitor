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
    "H-t2v_HvFR07ZBP9aOeKoA",   # current (June 2026)
    "a1p9RWpkYKBjWv_I3WzS-A",
    "SoVnbfCycZ7fERGCwpZkYA",
    "rwpVT1eOpetM8y6CiL5MiQ",
]

_LIKE_QUERY_IDS = [
    "lI07N6Otwv1PhnEgXILM7A",   # current (June 2026)
    "ZYKSe-w7KEslx3JhSIk5LA",
]

_RETWEET_QUERY_IDS = [
    "mbRO74GrOvSfRcJnlMapnQ",   # current (June 2026)
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
    "G1uS7V_A_IqHhF9Il0K-nA",        # current (June 2026)
    "Wp9x7NPOJ5klmf5H-350gw",
    "iYaPJI11EY8VtCL3hrKU9A",        # BlueVerifiedFollowers fallback
]

_FOLLOWING_QUERY_IDS = [
    "U96721pgL7wU5QUwu2goUA",        # current (June 2026)
    "XRzHZz4sLnhSgz55WGMCbg",
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


def _proxy_dict(proxy: str) -> dict:
    """Convert a proxy URL string to a dict accepted by curl_cffi / urllib."""
    if not proxy:
        return {}
    return {"http": proxy, "https": proxy}


def _http_get(url: str, headers: dict, retries: int = 3, proxy: str = None) -> tuple:
    """
    GET request with retry on 429 / transient network errors.
    Returns (status_code, body_text).
    proxy — optional proxy URL e.g. "http://user:pass@host:port" or "socks5://..."
    """
    import urllib.request as _ur
    last_err = "No attempts made"
    proxies = _proxy_dict(proxy)
    for attempt in range(retries):
        try:
            if _HAS_CURL:
                kwargs = dict(headers=headers, impersonate="chrome124", timeout=30)
                if proxies:
                    kwargs["proxies"] = proxies
                resp = _curl.get(url, **kwargs)
                if resp.status_code == 429:
                    wait = 15 * (attempt + 1)
                    time.sleep(wait)
                    continue
                return resp.status_code, resp.text
            else:
                handlers = []
                if proxy:
                    handlers.append(_ur.ProxyHandler(proxies))
                opener = _ur.build_opener(*handlers)
                req = _ur.Request(url, headers=headers)
                with opener.open(req, timeout=30) as r:
                    return r.status, r.read().decode()
        except Exception as exc:
            last_err = str(exc)
            if attempt < retries - 1:
                time.sleep(4 * (attempt + 1))
    return 0, last_err


def _http_post(url: str, payload: dict, headers: dict, proxy: str = None) -> tuple:
    """Returns (status_code, body_text). Works with or without curl_cffi.
    proxy — optional proxy URL e.g. "http://user:pass@host:port" or "socks5://..."
    """
    proxies = _proxy_dict(proxy)
    if _HAS_CURL:
        kwargs = dict(json=payload, headers=headers, impersonate="chrome124", timeout=30)
        if proxies:
            kwargs["proxies"] = proxies
        resp = _curl.post(url, **kwargs)
        return resp.status_code, resp.text
    else:
        import urllib.request as _ur
        handlers = []
        if proxy:
            handlers.append(_ur.ProxyHandler(proxies))
        opener = _ur.build_opener(*handlers)
        req = _ur.Request(url, data=json.dumps(payload).encode(),
                          headers=headers, method="POST")
        try:
            with opener.open(req, timeout=30) as r:
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
               auth_token: str, ct0: str, proxy: str = None) -> dict:
    """
    Post a reply to a tweet.
    Returns {"ok": True, "tweet_id": "..."} or {"ok": False, "error": "..."}.
    Tries multiple GraphQL query IDs in sequence.
    proxy — optional proxy URL e.g. "http://user:pass@host:port"
    """
    if not auth_token or not ct0:
        return {"ok": False, "error": "Missing auth_token or ct0"}

    headers = _headers(auth_token, ct0)
    last_error = "No query IDs to try"

    for query_id in _CREATE_TWEET_QUERY_IDS:
        url     = f"https://x.com/i/api/graphql/{query_id}/CreateTweet"
        payload = _create_tweet_payload(text, in_reply_to_tweet_id, query_id)

        try:
            status, body = _http_post(url, payload, headers, proxy=proxy)

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


def like_tweet(tweet_id: str, auth_token: str, ct0: str, proxy: str = None) -> dict:
    """
    Like a tweet.
    Returns {"ok": True} or {"ok": False, "error": "..."}.
    proxy — optional proxy URL e.g. "http://user:pass@host:port"
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
            status, body = _http_post(url, payload, headers, proxy=proxy)
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


def retweet_post(tweet_id: str, auth_token: str, ct0: str, proxy: str = None) -> dict:
    """
    Retweet a tweet.
    Returns {"ok": True} or {"ok": False, "error": "..."}.
    proxy — optional proxy URL e.g. "http://user:pass@host:port"
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
            status, body = _http_post(url, payload, headers, proxy=proxy)
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


def test_proxy(proxy: str, timeout: int = 8) -> dict:
    """
    Check if a proxy is alive by making a GET to http://ip-api.com/json through it.
    Returns {"alive": bool, "ms": int, "ip": str, "country": str, "error": str}
    Fast-fails within `timeout` seconds.
    """
    import time as _t
    if not proxy:
        return {"alive": False, "ms": 0, "ip": "", "country": "", "error": "no proxy configured"}
    proxies = _proxy_dict(proxy)
    t0 = _t.time()
    try:
        if _HAS_CURL:
            resp = _curl.get(
                "http://ip-api.com/json",
                proxies=proxies,
                impersonate="chrome124",
                timeout=timeout,
            )
            ms = int((_t.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "alive": True, "ms": ms,
                    "ip": data.get("query", ""),
                    "country": data.get("countryCode", ""),
                    "error": "",
                }
            return {"alive": False, "ms": ms, "ip": "", "country": "", "error": f"HTTP {resp.status_code}"}
        else:
            import urllib.request as _ur
            handler = _ur.ProxyHandler(proxies)
            opener  = _ur.build_opener(handler)
            req = _ur.Request("http://ip-api.com/json",
                              headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=timeout) as r:
                ms = int((_t.time() - t0) * 1000)
                import json as _j
                data = _j.loads(r.read())
                return {
                    "alive": True, "ms": ms,
                    "ip": data.get("query", ""),
                    "country": data.get("countryCode", ""),
                    "error": "",
                }
    except Exception as exc:
        ms = int((_t.time() - t0) * 1000)
        return {"alive": False, "ms": ms, "ip": "", "country": "", "error": str(exc)[:120]}


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
    action: str,                  # "like", "comment", "both", or "retweet"
    comment_text: str = "",
    comment_texts: list = None,   # rotating list — one per account (random pick each time)
    mention: str = "",            # @username to prepend to every comment
    tag_n_followers: int = 0,     # append N follower @mentions to each comment
    followers_pool: list = None,  # list of screen_names to pick from for tagging
    accounts: list = None,        # subset of pool; if None, uses full pool
    delay_min: float = 3.0,
    delay_max: float = 8.0,
    progress_cb=None,             # callback(done, total, username, result_str)
) -> dict:
    """
    Run like/comment/both/retweet on a tweet using the full account pool.
    If comment_texts is a non-empty list, each account picks one at random
    (so every post looks different — avoids spam detection).

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

    # Normalise rotating list — filter blanks, shuffle once for variety
    _texts = [t.strip() for t in (comment_texts or []) if str(t).strip()]
    if _texts:
        random.shuffle(_texts)

    # Normalise followers pool — flat list of screen_names, shuffle for variety
    _fpool = []
    for u in (followers_pool or []):
        sn = (u if isinstance(u, str) else u.get("screen_name", "")).strip().lstrip("@")
        if sn:
            _fpool.append(sn)
    random.shuffle(_fpool)
    _fn = max(0, int(tag_n_followers or 0))
    # Track global follower index so consecutive accounts get different followers
    _fi = [0]   # mutable box so inner function can mutate

    def _pick_text(index: int) -> str:
        """Return the comment for this account: rotating list > single text > ""."""
        if _texts:
            return _texts[index % len(_texts)]
        return comment_text

    def _pick_followers() -> str:
        """Pick _fn unique followers from the pool, cycling if needed."""
        if not _fpool or _fn == 0:
            return ""
        picks = []
        for _ in range(_fn):
            picks.append("@" + _fpool[_fi[0] % len(_fpool)])
            _fi[0] += 1
        return " ".join(picks)

    # Build comment text: optional @mention prefix + body + follower tags
    def build_comment(base_text: str) -> str:
        parts = []
        if mention:
            tag = mention if mention.startswith("@") else f"@{mention}"
            parts.append(tag)
        if base_text:
            parts.append(base_text)
        ftags = _pick_followers()
        if ftags:
            parts.append(ftags)
        return " ".join(parts) if parts else ""

    # ── Pre-verify proxies once before the loop (parallel, fast) ─────────────
    import concurrent.futures as _cf
    _all_raw_proxies = list({e.get("proxy") for e in pool if e.get("proxy")})
    _live_proxies: list = []

    def _probe(p):
        r = test_proxy(p, timeout=5)
        return p if r["alive"] else None

    with _cf.ThreadPoolExecutor(max_workers=150) as _ex:
        for _p in _ex.map(_probe, _all_raw_proxies):
            if _p:
                _live_proxies.append(_p)

    _live_proxy_set = set(_live_proxies)
    random.shuffle(_live_proxies)
    # ── End pre-verification ──────────────────────────────────────────────────

    results = []
    ok_count = 0
    fail_count = 0
    total = len(pool)

    for i, entry in enumerate(pool):
        username = entry.get("username", f"account_{i}")
        cookies  = entry.get("cookies", {})
        auth_tok = cookies.get("auth_token", "")
        ct0_val  = cookies.get("ct0", "")
        proxy    = entry.get("proxy") or None   # per-account proxy

        if not auth_tok or not ct0_val:
            results.append({"username": username, "action": action, "ok": False, "detail": "missing credentials"})
            fail_count += 1
            if progress_cb:
                progress_cb(i + 1, total, username, "❌ missing creds")
            continue

        # ── Proxy gate: use pre-verified live proxy pool ──────────────────────
        if not proxy or proxy not in _live_proxy_set:
            # Pick next from live pool (round-robin)
            if not _live_proxies:
                results.append({"username": username, "action": action, "ok": False,
                                 "detail": "no live proxies available"})
                fail_count += 1
                if progress_cb:
                    progress_cb(i + 1, total, username, "⛔ no live proxy")
                continue
            proxy = _live_proxies[i % len(_live_proxies)]
        # ── End proxy gate ────────────────────────────────────────────────────

        entry_results = []

        if action in ("like", "both"):
            res = like_tweet(tweet_id, auth_tok, ct0_val, proxy=proxy)
            entry_results.append(("like", res))

        if action == "retweet":
            res = retweet_post(tweet_id, auth_tok, ct0_val, proxy=proxy)
            entry_results.append(("retweet", res))

        if action in ("comment", "both"):
            text = build_comment(_pick_text(i))
            if text:
                res = post_reply(text, tweet_id, auth_tok, ct0_val, proxy=proxy)
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


def scrape_replies_with_keywords(
    tweet_id: str,
    auth_token: str,
    ct0: str,
    limit: int = 9999,
    no_admins: bool = False,
    skip_bots: bool = False,
    keywords: list = None,      # OR logic — keep reply if ANY keyword found in text
    min_length: int = 0,        # also keep replies whose text is >= this many chars (long/deep msgs)
    max_age_minutes: int = 0,   # also keep replies posted within this many minutes (0 = off)
    progress_cb=None,           # callback(collected, scanned) — called after every page
) -> dict:
    """
    Scrape replies to a tweet, capture the reply TEXT, and filter by keywords / length / recency.
    A reply is kept when it matches ANY keyword  OR  text >= min_length  OR  posted within max_age_minutes.
    If all three filters are absent/0, every reply is kept.
    Returns {"ok": True, "users": [{"screen_name":…,"name":…,"text":…,"verified":…}], "count": N}
    Paginates up to 600 pages (~12 000+ replies) to hit large targets like 5 643.
    """
    import urllib.parse as _up

    import datetime as _dt
    kw_lower   = [k.lower() for k in (keywords or [])]
    use_length = int(min_length or 0)
    use_age    = int(max_age_minutes or 0)

    def _parse_tw_date(s: str):
        """Parse Twitter's 'Mon Jan 01 00:00:00 +0000 2024' format → UTC datetime."""
        try:
            return _dt.datetime.strptime(s, "%a %b %d %H:%M:%S +0000 %Y").replace(
                tzinfo=_dt.timezone.utc)
        except Exception:
            return None

    _now_utc = _dt.datetime.now(_dt.timezone.utc)

    def _matches(text: str, created_at: str = "") -> bool:
        # Accept all if no filters set
        if not kw_lower and use_length == 0 and use_age == 0:
            return True
        # Keyword hit
        t = text.lower()
        if kw_lower and any(k in t for k in kw_lower):
            return True
        # Long/deep message hit
        if use_length > 0 and len(text.strip()) >= use_length:
            return True
        # Recency hit — posted within max_age_minutes
        if use_age > 0 and created_at:
            ts = _parse_tw_date(created_at)
            if ts and (_now_utc - ts).total_seconds() <= use_age * 60:
                return True
        return False

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
        scanned = 0

        for _page in range(600):
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
            status, body = _http_get(f"{_URL}?{params}", hdrs, retries=3)

            if status == 0:   last_error = body; break
            if status == 429: last_error = "Rate limited (429)"; break
            if status in (403, 400): last_error = f"Query {qid} rejected ({status})"; break
            if status != 200: last_error = f"HTTP {status}: {body[:200]}"; break

            qid_ok = True
            try:
                data = json.loads(body)
            except Exception:
                last_error = "Invalid JSON"; break
            if data.get("errors"):
                last_error = "; ".join(e.get("message","?") for e in data["errors"][:3]); break

            instructions = (
                data.get("data", {})
                    .get("threaded_conversation_with_injections_v2", {})
                    .get("instructions", [])
            )

            def _extract_tr(tr):
                """Extract (user_legacy, tweet_full_text, created_at_str) from a tweet_result."""
                legacy_u = (tr.get("core", {})
                              .get("user_results", {})
                              .get("result", {})
                              .get("legacy", {}))
                legacy_t = tr.get("legacy", {})
                return legacy_u, legacy_t.get("full_text", ""), legacy_t.get("created_at", "")

            next_cursor = None
            found_this_page = 0

            for instr in instructions:
                for entry in instr.get("entries", []):
                    eid = entry.get("entryId", "")
                    content = entry.get("content", {})

                    if eid == f"tweet-{tweet_id}":
                        continue

                    candidates = []
                    if eid.startswith("tweet-"):
                        tr = content.get("itemContent", {}).get("tweet_results", {}).get("result", {})
                        candidates = [tr]
                    elif content.get("entryType") == "TimelineTimelineModule":
                        for item in content.get("items", []):
                            tr = (item.get("item", {})
                                      .get("itemContent", {})
                                      .get("tweet_results", {})
                                      .get("result", {}))
                            candidates.append(tr)

                    for tr in candidates:
                        legacy_u, text, created_at = _extract_tr(tr)
                        sn = legacy_u.get("screen_name", "").strip()
                        if not sn or sn.lower() in seen:
                            continue
                        scanned += 1
                        is_verified = (legacy_u.get("verified") or legacy_u.get("is_blue_verified"))
                        if no_admins and is_verified:
                            continue
                        if skip_bots and _is_likely_bot(legacy_u):
                            continue
                        if not _matches(text, created_at):
                            continue
                        seen.add(sn.lower())
                        users.append({
                            "screen_name": sn,
                            "name": legacy_u.get("name", sn),
                            "text": text[:280],
                            "verified": bool(is_verified),
                            "created_at": created_at,
                        })
                        found_this_page += 1

                    if "cursor-bottom" in eid or content.get("cursorType") == "Bottom":
                        next_cursor = (content.get("value")
                                       or content.get("itemContent", {}).get("value"))

            if found_this_page == 0:
                empty_pages += 1
            else:
                empty_pages = 0

            if progress_cb:
                progress_cb(len(users), scanned)

            if len(users) >= limit or not next_cursor or empty_pages >= 5:
                break
            cursor = next_cursor

        if qid_ok:
            any_qid_ok = True
        if qid_ok and users:
            break

    if not users:
        msg = "No matching replies found" if any_qid_ok else last_error
        return {"ok": True, "users": [], "count": 0, "message": msg}
    return {"ok": True, "users": users[:limit], "count": min(len(users), limit)}


def _scrape_v1_list(endpoint: str, username: str, auth_token: str, ct0: str,
                    limit: int = 999999, start_cursor: str = "-1") -> dict:
    """
    Scrape followers or following using Twitter v1.1 REST API with cursor pagination.
    endpoint: 'followers/list' (followers) or 'friends/list' (following).
    start_cursor: resume from a saved cursor position ("-1" means start from beginning).
    Returns {"ok": True, "users": [...], "count": N, "next_cursor": "..."} or {"ok": False, "error": "..."}
    next_cursor is "0" when the end of the list has been reached.
    """
    import urllib.parse as _up
    hdrs = _headers(auth_token, ct0)
    users: list = []
    seen: set = set()
    cursor = start_cursor or "-1"
    last_next_cursor = "0"

    for _page in range(500):
        params = _up.urlencode({
            "screen_name": username,
            "count": 200,
            "cursor": cursor,
            "skip_status": True,
            "include_user_entities": False,
        })
        url = f"https://api.twitter.com/1.1/{endpoint}.json?{params}"
        status, body = _http_get(url, hdrs, retries=3)

        if status == 429:
            return {"ok": False, "error": "Rate limited (429) — try again in a few minutes"}
        if status == 401:
            return {"ok": False, "error": "Auth token rejected (401) — update auth_token + ct0 in Settings"}
        if status != 200:
            if users:
                break
            return {"ok": False, "error": f"HTTP {status}: {body[:200]}"}

        try:
            data = json.loads(body)
        except Exception:
            return {"ok": False, "error": "Invalid JSON from Twitter API"}

        if isinstance(data, dict) and data.get("errors"):
            err = data["errors"][0] if isinstance(data["errors"], list) else data["errors"]
            return {"ok": False, "error": str(err)}

        for u in data.get("users", []):
            sn = u.get("screen_name", "").strip()
            if not sn or sn.lower() in seen:
                continue
            seen.add(sn.lower())
            users.append({
                "screen_name": sn,
                "name": u.get("name", sn),
                "followers_count": u.get("followers_count", 0),
                "following_count": u.get("friends_count", 0),
                "tweet_count": u.get("statuses_count", 0),
                "verified": u.get("verified", False),
                "created_at": u.get("created_at", ""),
                "description": u.get("description", ""),
            })

        last_next_cursor = str(data.get("next_cursor_str", "0"))
        if not last_next_cursor or last_next_cursor == "0" or len(users) >= limit:
            break
        cursor = last_next_cursor

    # If we stopped due to hitting the limit, last_next_cursor is the resume point.
    # If we stopped because the list ended, last_next_cursor is "0".
    return {
        "ok": True,
        "users": users[:limit],
        "count": min(len(users), limit),
        "next_cursor": last_next_cursor if len(users) >= limit else "0",
    }


def _resolve_user_id(username: str, auth_token: str, ct0: str) -> str:
    """Resolve a Twitter screen_name to a numeric user ID via GraphQL."""
    import urllib.parse as _up
    _QUERY_IDS = ["IGgvgiOx4QZndDHuD3x9TQ", "G3KGOASz96M-Qu0nwmGXNg", "xmU6X_CKVnQ5BltcLoxFGA", "G3KGOASz96M-Ou3vSqKxfA"]
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


def _is_likely_bot(legacy: dict) -> bool:
    """
    Heuristic bot / spam / group account detector.
    Returns True if the account looks like a bot, group, or spam account.
    Signals used:
      • default_profile_image = True  (never set a profile pic)
      • statuses_count < 5            (almost no tweets)
      • followers_count < 3           (basically no audience)
      • account age < 30 days         (brand-new burner)
      • no description AND default profile
    """
    if legacy.get("default_profile_image"):
        return True
    if legacy.get("statuses_count", 999) < 5:
        return True
    if legacy.get("followers_count", 999) < 3:
        return True
    ca = legacy.get("created_at", "")
    if ca:
        try:
            from datetime import datetime, timezone as _tz
            dt = datetime.strptime(ca, "%a %b %d %H:%M:%S +0000 %Y").replace(tzinfo=_tz.utc)
            if (datetime.now(_tz.utc) - dt).days < 30:
                return True
        except Exception:
            pass
    if not legacy.get("description", "").strip() and legacy.get("default_profile", True):
        return True
    return False


def scrape_followers_graphql(username: str, auth_token: str, ct0: str,
                             limit: int = 999999,
                             skip_verified: bool = False,
                             skip_bots: bool = False,
                             start_cursor: str = "-1") -> dict:
    """
    Fetch followers of a Twitter account.
    Tries v1.1 REST API first (most reliable), falls back to GraphQL.
    start_cursor: resume from a saved cursor ("-1" = start of list).
    Returns {"ok": True, "users": [...], "count": N, "next_cursor": "..."} | {"ok": False, "error": "..."}
    next_cursor "0" means end of follower list reached.
    """
    # ── Primary: v1.1 REST API ──────────────────────────────────────────────
    result = _scrape_v1_list("followers/list", username, auth_token, ct0, limit, start_cursor)
    if result.get("ok") and result.get("users"):
        users = result["users"]
        if skip_verified:
            users = [u for u in users if not u.get("verified")]
        if skip_bots:
            users = [u for u in users if not _is_likely_bot(u)]
        return {"ok": True, "users": users, "count": len(users), "next_cursor": result.get("next_cursor", "0")}
    v1_error = result.get("error", "v1.1 returned 0 results")

    # ── Fallback: GraphQL ───────────────────────────────────────────────────
    import urllib.parse as _up

    uid = _resolve_user_id(username, auth_token, ct0)
    if not uid:
        return {"ok": False, "error": f"Could not resolve @{username} — {v1_error}"}

    _QUERY_IDS = _FOLLOWERS_QUERY_IDS + ["9-uRROAZQPxhkXIbT5hFZA", "djdTXizios1zWqhGkmHB8A"]
    hdrs = _headers(auth_token, ct0)
    last_error = v1_error

    for qid in _QUERY_IDS:
        for domain in ["twitter.com/i/api", "api.twitter.com"]:
            _URL = f"https://{domain}/graphql/{qid}/Followers"
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

                if status in (404, 403, 400):
                    last_error = f"GraphQL {qid} rejected ({status})"
                    break
                if status == 429:
                    last_error = "Rate limited (429)"
                    break
                if status != 200:
                    last_error = f"HTTP {status}"; break

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
                            result_obj = (content.get("itemContent", {})
                                                 .get("user_results", {})
                                                 .get("result", {}))
                            legacy = result_obj.get("legacy", {})
                            core   = result_obj.get("core", {})
                            sn = (legacy.get("screen_name") or core.get("screen_name", "")).strip()
                            if not sn or sn.lower() in seen:
                                continue
                            is_verified = (legacy.get("verified", False)
                                           or legacy.get("is_blue_verified", False)
                                           or bool(result_obj.get("is_blue_verified")))
                            if skip_verified and is_verified:
                                continue
                            if skip_bots and _is_likely_bot(legacy):
                                continue
                            seen.add(sn.lower())
                            users.append({
                                "screen_name": sn,
                                "name": legacy.get("name") or core.get("name", sn),
                                "followers_count": legacy.get("followers_count", 0),
                                "following_count": legacy.get("friends_count", 0),
                                "tweet_count": legacy.get("statuses_count", 0),
                                "verified": is_verified,
                                "created_at": legacy.get("created_at") or core.get("created_at", ""),
                                "description": legacy.get("description", ""),
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
        if users:
            break

    if not users:
        return {"ok": True, "users": [], "count": 0,
                "message": last_error or f"No followers found for @{username}"}
    return {"ok": True, "users": users[:limit], "count": min(len(users), limit)}


def scrape_following_graphql(username: str, auth_token: str, ct0: str,
                              limit: int = 999999,
                              start_cursor: str = "-1") -> dict:
    """
    Fetch accounts a Twitter user follows.
    Tries v1.1 REST API first (most reliable), falls back to GraphQL.
    start_cursor: resume from a saved cursor ("-1" = start of list).
    Returns same shape as scrape_followers_graphql (includes next_cursor).
    """
    # ── Primary: v1.1 REST API ──────────────────────────────────────────────
    result = _scrape_v1_list("friends/list", username, auth_token, ct0, limit, start_cursor)
    if result.get("ok") and result.get("users"):
        return result  # already includes next_cursor
    v1_error = result.get("error", "v1.1 returned 0 results")

    # ── Fallback: GraphQL ───────────────────────────────────────────────────
    import urllib.parse as _up

    uid = _resolve_user_id(username, auth_token, ct0)
    if not uid:
        return {"ok": False, "error": f"Could not resolve @{username} — {v1_error}"}

    _QUERY_IDS = _FOLLOWING_QUERY_IDS + ["iSicc7LrzWGBgDPL0tM_TQ", "f0q-KKOTxb1yFpQEEfFmFQ"]
    hdrs = _headers(auth_token, ct0)
    last_error = v1_error

    for qid in _QUERY_IDS:
        for domain in ["twitter.com/i/api", "api.twitter.com"]:
            _URL = f"https://{domain}/graphql/{qid}/Following"
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

                if status in (404, 403, 400):
                    last_error = f"GraphQL {qid} rejected ({status})"
                    break
                if status == 429:
                    last_error = "Rate limited (429)"; break
                if status != 200:
                    last_error = f"HTTP {status}"; break

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
                            result_obj = (content.get("itemContent", {})
                                                 .get("user_results", {})
                                                 .get("result", {}))
                            legacy = result_obj.get("legacy", {})
                            core   = result_obj.get("core", {})
                            sn = (legacy.get("screen_name") or core.get("screen_name", "")).strip()
                            if sn and sn.lower() not in seen:
                                seen.add(sn.lower())
                                users.append({
                                    "screen_name": sn,
                                    "name": legacy.get("name") or core.get("name", sn),
                                    "followers_count": legacy.get("followers_count", 0),
                                    "following_count": legacy.get("friends_count", 0),
                                    "tweet_count": legacy.get("statuses_count", 0),
                                    "verified": (legacy.get("verified", False)
                                                 or legacy.get("is_blue_verified", False)
                                                 or bool(result_obj.get("is_blue_verified"))),
                                    "created_at": legacy.get("created_at") or core.get("created_at", ""),
                                    "description": legacy.get("description", ""),
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
        if users:
            break

    if not users:
        return {"ok": True, "users": [], "count": 0,
                "message": last_error or f"No following found for @{username}"}
    return {"ok": True, "users": users[:limit], "count": min(len(users), limit)}


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
