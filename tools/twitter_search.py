"""
twitter_search.py
=================
The strongest possible Twitter search layer for crypto complaint monitoring.

Tries FOUR independent methods in order of power, picks whichever returns data:

  Method 1 — twscrape (best)
    Proper account-pool manager. Handles rate-limits, session refresh,
    and auth lifecycle automatically. Works from datacenter IPs.

  Method 2 — XClientTransaction + direct session
    Generates the x-client-transaction-id header Twitter's frontend sends.
    Many Render IPs are not on Twitter's blocklist for authenticated calls.

  Method 3 — Webshare residential proxy
    Routes through residential IPs worldwide.

  Method 4 — IPRoyal residential proxy
    Second proxy pool, different IP range.

Usage:
    from twitter_search import search_all
    tweets = search_all(queries, auth_token, ct0, count=20)
"""
from __future__ import annotations
import asyncio, json, logging, os, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

log = logging.getLogger(__name__)

BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_QID_SEARCH = "BGd0T_j7oVwlW5U79tO_0A"
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

# ── twscrape account pool singleton (module-level so it persists per process) ──
_twscrape_client = None
_twscrape_lock   = threading.Lock()
_twscrape_ready  = False   # True once account successfully added


def _run_async_in_thread(coro, timeout: float = 60.0):
    """
    Run an async coroutine in a brand-new thread with its own event loop.
    Avoids conflicts with any existing event loop (e.g. python-telegram-bot).
    Returns the result or raises TimeoutError / the original exception.
    """
    result_box: list = []
    exc_box:    list = []

    def _worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result_box.append(loop.run_until_complete(coro))
        except Exception as e:
            exc_box.append(e)
        finally:
            loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        raise TimeoutError(f"async coroutine timed out after {timeout}s")
    if exc_box:
        raise exc_box[0]
    return result_box[0] if result_box else None


def _get_twscrape_client(auth: str, ct0: str):
    """Return a ready twscrape API client (singleton, lazy-init)."""
    global _twscrape_client, _twscrape_ready
    try:
        import twscrape  # noqa: F401
    except ImportError:
        return None

    with _twscrape_lock:
        if _twscrape_client is None:
            import twscrape as _tw
            _twscrape_client = _tw.API()

        if not _twscrape_ready:
            async def _add():
                await _twscrape_client.pool.add_account(
                    username="primary",
                    password="",
                    email="",
                    email_password="",
                    cookies=f"auth_token={auth}; ct0={ct0}",
                )
            try:
                _run_async_in_thread(_add(), timeout=30.0)
                _twscrape_ready = True
                log.info("twitter_search: twscrape account added")
            except Exception as e:
                log.warning(f"twitter_search: twscrape init failed: {e}")
                return None

    return _twscrape_client if _twscrape_ready else None


def _twscrape_search(queries: list[str], auth: str, ct0: str, count: int = 20) -> tuple[list[dict], str]:
    """Search via twscrape. Returns (tweets, status_string)."""
    client = _get_twscrape_client(auth, ct0)
    if not client:
        return [], "twscrape not installed"

    async def _run():
        results = []
        for q in queries:
            try:
                n = 0
                async for tw in client.search(q, limit=count):
                    results.append({
                        "id":       str(tw.id),
                        "text":     tw.rawContent or "",
                        "user":     tw.user.username if tw.user else "",
                        "likes":    tw.likeCount or 0,
                        "retweets": tw.retweetCount or 0,
                        "date":     tw.date.isoformat() if tw.date else "",
                        "url":      tw.url or f"https://x.com/i/web/status/{tw.id}",
                        "lang":     tw.lang or "",
                    })
                    n += 1
                    if n >= count:
                        break
            except Exception as e:
                log.warning(f"twitter_search: twscrape query error ({q[:40]}): {e}")
            await asyncio.sleep(0.5)
        return results

    try:
        # Use thread-based runner to avoid conflicts with existing event loops
        tweets = _run_async_in_thread(_run(), timeout=min(len(queries) * 8.0, 120.0))
        return tweets or [], f"twscrape {len(tweets or [])} tweets"
    except TimeoutError:
        log.warning("twitter_search: twscrape timed out")
        return [], "twscrape timeout"
    except Exception as e:
        log.warning(f"twitter_search: twscrape run error: {e}")
        return [], f"twscrape error: {str(e)[:50]}"


def _make_direct_session(auth: str, ct0: str):
    """curl_cffi session with XClientTransaction header (no proxy)."""
    try:
        import curl_cffi.requests as _cffi
        s = _cffi.Session(impersonate="chrome120")
    except ImportError:
        import requests as _req  # type: ignore
        s = _req.Session()

    base_headers = {
        "Authorization":             f"Bearer {BEARER}",
        "Cookie":                    f"auth_token={auth}; ct0={ct0}",
        "X-Csrf-Token":              ct0,
        "x-twitter-auth-type":       "OAuth2Session",
        "x-twitter-active-user":     "yes",
        "x-twitter-client-language": "en",
        "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer":                   "https://x.com/search",
        "Accept":                    "*/*",
        "Accept-Language":           "en-US,en;q=0.9",
        "sec-ch-ua":                 '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec-ch-ua-mobile":          "?0",
        "sec-ch-ua-platform":        '"Windows"',
        "sec-fetch-dest":            "empty",
        "sec-fetch-mode":            "cors",
        "sec-fetch-site":            "same-origin",
    }

    # Add XClientTransaction header if library available
    try:
        from XClientTransaction import XClientTransaction as _XCT
        xct = _XCT()
        # The library needs a home-page response to initialize; skip if it errors
        try:
            import requests as _r
            home = _r.get("https://x.com/", headers={"User-Agent": base_headers["User-Agent"]}, timeout=10)
            xct.home_page_response = home
            url = f"https://x.com/i/api/graphql/{_QID_SEARCH}/SearchTimeline"
            base_headers["x-client-transaction-id"] = xct.generate_transaction_id(method="GET", url=url)
        except Exception:
            pass
    except ImportError:
        pass

    s.headers.update(base_headers)
    return s


def _make_proxy_session(auth: str, ct0: str, provider: str = "webshare"):
    """curl_cffi session routed through a residential proxy."""
    proxy_url = ""
    if provider == "webshare":
        try:
            from proxy_pool import get_proxy_url
            proxy_url = get_proxy_url()
        except Exception:
            pass
    elif provider == "iproyal":
        try:
            from proxy_pool import _load_iproyal_proxy_url
            proxy_url = _load_iproyal_proxy_url()
        except Exception:
            pass

    if not proxy_url:
        return None

    try:
        import curl_cffi.requests as _cffi
        s = _cffi.Session(
            impersonate="chrome120",
            proxies={"http": proxy_url, "https": proxy_url},
        )
    except ImportError:
        return None

    s.headers.update({
        "Authorization":             f"Bearer {BEARER}",
        "Cookie":                    f"auth_token={auth}; ct0={ct0}",
        "X-Csrf-Token":              ct0,
        "x-twitter-auth-type":       "OAuth2Session",
        "x-twitter-active-user":     "yes",
        "x-twitter-client-language": "en",
        "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":                   "https://x.com/search",
        "Accept":                    "*/*",
        "sec-fetch-dest":            "empty",
        "sec-fetch-mode":            "cors",
        "sec-fetch-site":            "same-origin",
    })
    return s


def _session_search(session, query: str, count: int = 20) -> list[dict]:
    """Fire one SearchTimeline query using a requests/curl_cffi session."""
    url = f"https://x.com/i/api/graphql/{_QID_SEARCH}/SearchTimeline"
    try:
        r = session.get(url, params={
            "variables": json.dumps({
                "rawQuery":    query,
                "count":       count,
                "querySource": "typed_query",
                "product":     "Latest",
            }, separators=(",", ":")),
            "features": json.dumps(_SEARCH_FEATURES, separators=(",", ":")),
        }, timeout=20)
        if r.status_code != 200:
            log.debug(f"twitter_search: session HTTP {r.status_code} for {query[:40]}")
            return []
        data = r.json()
        instructions = (
            data.get("data", {})
                .get("search_by_raw_query", {})
                .get("search_timeline", {})
                .get("timeline", {})
                .get("instructions", [])
        )
        entries = next((i.get("entries", []) for i in instructions if i.get("type") == "TimelineAddEntries"), [])
        results = []
        for entry in entries:
            tw = (
                entry.get("content", {})
                     .get("itemContent", {})
                     .get("tweet_results", {})
                     .get("result", {})
            )
            legacy = tw.get("legacy", {})
            text = legacy.get("full_text", "")
            if not text or text.startswith("RT "):
                continue
            uid_obj = tw.get("core", {}).get("user_results", {}).get("result", {})
            screen_name = (
                uid_obj.get("legacy", {}).get("screen_name", "")
                or uid_obj.get("core", {}).get("screen_name", "")
            )
            tid = tw.get("rest_id", "")
            results.append({
                "id":       tid,
                "text":     text,
                "user":     screen_name,
                "likes":    legacy.get("favorite_count", 0),
                "retweets": legacy.get("retweet_count", 0),
                "date":     legacy.get("created_at", ""),
                "lang":     legacy.get("lang", ""),
                "url":      f"https://x.com/{screen_name}/status/{tid}" if screen_name and tid else "",
            })
        return results
    except Exception as e:
        log.debug(f"twitter_search: session_search error: {e}")
        return []


def _probe_session(session, probe_query: str, count: int = 5) -> int:
    """Return number of tweets found for the probe query. 0 means session doesn't work."""
    if session is None:
        return 0
    results = _session_search(session, probe_query, count=count)
    return len(results)


# ── Public API ────────────────────────────────────────────────────────────────

_PROBE_QUERY = "(withdrawal OR withdraw) (stuck OR failed) crypto -is:retweet min_faves:1"

# Cached working session (avoids re-probing every call)
_active_session   = None
_active_sess_name = "none"
_sess_cache_time  = 0.0
_SESS_TTL         = 30 * 60   # re-probe every 30 min


def probe_and_select_session(auth: str, ct0: str) -> tuple:
    """Find the first working search session. Returns (session_or_None, name_str)."""
    global _active_session, _active_sess_name, _sess_cache_time

    now = time.time()
    if _active_session and now - _sess_cache_time < _SESS_TTL:
        return _active_session, _active_sess_name

    sessions = [
        ("direct",   lambda: _make_direct_session(auth, ct0)),
        ("webshare", lambda: _make_proxy_session(auth, ct0, "webshare")),
        ("iproyal",  lambda: _make_proxy_session(auth, ct0, "iproyal")),
    ]

    for name, factory in sessions:
        try:
            sess = factory()
            if sess is None:
                continue
            hits = _probe_session(sess, _PROBE_QUERY, count=5)
            if hits > 0:
                log.info(f"twitter_search: probe OK via {name} ({hits} hits)")
                _active_session   = sess
                _active_sess_name = name
                _sess_cache_time  = now
                return sess, name
            log.info(f"twitter_search: probe empty via {name}")
        except Exception as e:
            log.warning(f"twitter_search: probe error ({name}): {e}")

    _active_session   = None
    _active_sess_name = "none"
    return None, "none"


def search_all(
    queries:  list[str],
    auth:     str,
    ct0:      str,
    count:    int = 20,
    parallel: int = 8,
) -> tuple[list[dict], str]:
    """
    Search ALL of Twitter for every query in `queries`.

    Tries twscrape first (best), then falls back to session-based search.
    Runs `parallel` queries concurrently.
    Returns (deduplicated_tweet_list, status_string).
    """
    if not auth or not ct0:
        return [], "no credentials"

    # ── Method 1: twscrape ───────────────────────────────────────────────────
    try:
        import twscrape  # noqa: F401
        tweets, status = _twscrape_search(queries, auth, ct0, count)
        if tweets:
            log.info(f"twitter_search: twscrape returned {len(tweets)} tweets for {len(queries)} queries")
            return _dedup(tweets), f"twscrape ({len(tweets)}t/{len(queries)}q)"
        log.info(f"twitter_search: twscrape returned 0 — {status}")
    except ImportError:
        pass
    except Exception as e:
        log.warning(f"twitter_search: twscrape top-level error: {e}")

    # ── Methods 2-4: session-based (direct → webshare → iproyal) ────────────
    sess, sess_name = probe_and_select_session(auth, ct0)
    if sess is None:
        return [], "all methods failed (SearchTimeline blocked)"

    results_lock = threading.Lock()
    all_tweets: list[dict] = []
    seen_ids: set[str] = set()

    def _run_query(q: str) -> None:
        tweets = _session_search(sess, q, count=count)
        with results_lock:
            for t in tweets:
                tid = t.get("id", "")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    all_tweets.append(t)
        time.sleep(0.5)

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = [pool.submit(_run_query, q) for q in queries]
        for f in as_completed(futures, timeout=120):
            try:
                f.result()
            except Exception:
                pass

    status = f"{sess_name} ({len(all_tweets)}t/{len(queries)}q)"
    log.info(f"twitter_search: {status}")
    return _dedup(all_tweets), status


def _dedup(tweets: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out:  list[dict] = []
    for t in tweets:
        tid = t.get("id", "")
        if tid and tid not in seen:
            seen.add(tid)
            out.append(t)
    return out


def quick_test(auth: str, ct0: str) -> dict:
    """
    Diagnostic: test every search method independently.
    Returns a dict with results per method so /api/test-search can report them.
    """
    results = {}

    # twscrape
    try:
        import twscrape  # noqa
        tweets, status = _twscrape_search([_PROBE_QUERY], auth, ct0, count=5)
        results["twscrape"] = {"tweets": len(tweets), "status": status}
    except ImportError:
        results["twscrape"] = {"tweets": 0, "status": "not installed"}
    except Exception as e:
        results["twscrape"] = {"tweets": 0, "status": str(e)[:80]}

    # Direct
    try:
        s = _make_direct_session(auth, ct0)
        n = _probe_session(s, _PROBE_QUERY)
        results["direct"] = {"tweets": n, "status": "ok" if n else "empty"}
    except Exception as e:
        results["direct"] = {"tweets": 0, "status": str(e)[:80]}

    # Webshare proxy
    try:
        s = _make_proxy_session(auth, ct0, "webshare")
        n = _probe_session(s, _PROBE_QUERY) if s else 0
        results["webshare"] = {"tweets": n, "status": "ok" if n else ("no proxy" if s is None else "empty")}
    except Exception as e:
        results["webshare"] = {"tweets": 0, "status": str(e)[:80]}

    # IPRoyal proxy
    try:
        s = _make_proxy_session(auth, ct0, "iproyal")
        n = _probe_session(s, _PROBE_QUERY) if s else 0
        results["iproyal"] = {"tweets": n, "status": "ok" if n else ("no proxy" if s is None else "empty")}
    except Exception as e:
        results["iproyal"] = {"tweets": 0, "status": str(e)[:80]}

    working = [k for k, v in results.items() if v["tweets"] > 0]
    results["_working"] = working
    results["_probe_query"] = _PROBE_QUERY
    return results
