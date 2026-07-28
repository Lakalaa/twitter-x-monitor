"""
twitter_search.py  —  Maximum-coverage Twitter search for crypto complaint monitoring
======================================================================================

SEARCH ENGINES tried in priority order:

  1. SocialData.tools  (PRIMARY — strongest)
     Professional Twitter data API. Works from ANY IP, no IP blocking possible,
     real-time results, up to 100 tweets/request, runs 8 queries in parallel.
     Needs env var: SOCIALDATA_API_KEY
     Sign up: https://socialdata.tools  (~$25/month or pay-per-use)

  2. twikit  (free fallback)
     Python Twitter client that handles XClientTransaction properly.
     Bootstraps via Webshare proxy to get the home-page token, then searches
     directly. Works from server IPs once initialised.

  3. Webshare residential proxy session
     Rotates through residential IPs to bypass datacenter IP blocks.

  4. IPRoyal residential proxy session
     Second residential IP pool, different IP range.

Usage:
    from twitter_search import search_all, quick_test
    tweets, status = search_all(queries, auth_token, ct0, count=20)
"""
from __future__ import annotations
import asyncio, json, logging, os, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed

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

_PROBE_QUERY = "(withdrawal OR withdraw) (stuck OR failed) crypto -is:retweet min_faves:1"

# ── Module-level singletons ────────────────────────────────────────────────────
_twikit_client    = None
_twikit_ready     = False
_twikit_lock      = threading.Lock()

_active_session   = None   # cached fallback session
_active_sess_name = "none"
_sess_cache_time  = 0.0
_SESS_TTL         = 30 * 60


# ══════════════════════════════════════════════════════════════════════════════
# Method 1 — SocialData.tools
# ══════════════════════════════════════════════════════════════════════════════

_SD_BASE = "https://api.socialdata.tools"

def _socialdata_key() -> str:
    return os.environ.get("SOCIALDATA_API_KEY", "")


def _socialdata_one(query: str, count: int = 20) -> list[dict]:
    """Fire one SocialData search query. Returns tweet dicts."""
    key = _socialdata_key()
    if not key:
        return []
    try:
        import urllib.request as _ur, urllib.parse as _up
        params = _up.urlencode({"query": query, "type": "Latest"})
        req = _ur.Request(
            f"{_SD_BASE}/twitter/search?{params}",
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
                "User-Agent": "CryptoComplaintMonitor/1.0",
            },
        )
        with _ur.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        raw_tweets = data.get("tweets", [])[:count]
        results = []
        for t in raw_tweets:
            text = t.get("full_text", "")
            if not text or text.startswith("RT "):
                continue
            uid  = t.get("user", {}).get("screen_name", "")
            tid  = t.get("id_str", "")
            results.append({
                "id":       tid,
                "text":     text,
                "user":     uid,
                "likes":    t.get("favorite_count", 0),
                "retweets": t.get("retweet_count", 0),
                "date":     t.get("created_at", ""),
                "lang":     t.get("lang", ""),
                "url":      f"https://x.com/{uid}/status/{tid}" if uid and tid else "",
            })
        return results
    except Exception as e:
        log.debug(f"twitter_search: socialdata query error ({query[:40]}): {e}")
        return []


def _socialdata_search(
    queries: list[str],
    count:   int = 20,
    parallel: int = 8,
) -> tuple[list[dict], str]:
    """Run all queries via SocialData.tools in parallel."""
    if not _socialdata_key():
        return [], "no SOCIALDATA_API_KEY"

    lock  = threading.Lock()
    found: list[dict] = []
    seen:  set[str]   = set()

    def _run(q: str) -> None:
        tweets = _socialdata_one(q, count)
        with lock:
            for t in tweets:
                tid = t.get("id", "")
                if tid and tid not in seen:
                    seen.add(tid)
                    t["search_query"] = q
                    found.append(t)
        time.sleep(0.15)   # gentle pacing — SocialData allows high concurrency

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = [pool.submit(_run, q) for q in queries]
        for f in as_completed(futures, timeout=90):
            try:
                f.result()
            except Exception:
                pass

    status = f"socialdata ({len(found)}t/{len(queries)}q)"
    log.info(f"twitter_search: {status}")
    return found, status


# ══════════════════════════════════════════════════════════════════════════════
# Method 2 — twikit (free, handles XClientTransaction internally)
# ══════════════════════════════════════════════════════════════════════════════

def _run_async_in_thread(coro, timeout: float = 60.0):
    """Run an asyncio coroutine in a fresh thread+loop (avoids conflicts)."""
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
        raise TimeoutError(f"async task timed out after {timeout}s")
    if exc_box:
        raise exc_box[0]
    return result_box[0] if result_box else None


def _get_twikit_client(auth: str, ct0: str):
    """Return a ready twikit Client. Bootstraps XCT via Webshare proxy if needed."""
    global _twikit_client, _twikit_ready
    try:
        import twikit  # noqa: F401
    except ImportError:
        return None

    with _twikit_lock:
        if _twikit_client is None:
            import twikit as _tw
            _twikit_client = _tw.Client("en-US")

        if not _twikit_ready:
            # Bootstrap XClientTransaction via Webshare proxy home-page fetch
            async def _init():
                import bs4, re as _re, json as _j, urllib.request as _ur
                ws_key = os.environ.get("WEBSHARE_API_KEY", "")
                if not ws_key:
                    raise RuntimeError("no WEBSHARE_API_KEY for twikit bootstrap")

                # Get proxy credentials
                req = _ur.Request("https://proxy.webshare.io/api/v2/proxy/config/",
                                  headers={"Authorization": f"Token {ws_key}"})
                with _ur.urlopen(req, timeout=12) as r:
                    creds = _j.loads(r.read())
                ws_user, ws_pass = creds.get("username", ""), creds.get("password", "")
                proxy_url = f"http://{ws_user}:{ws_pass}@p.webshare.io:80"

                # Fetch Twitter home page via proxy WITH auth cookies
                import httpx
                transport = httpx.AsyncHTTPTransport(proxy=proxy_url)
                async with httpx.AsyncClient(
                    transport=transport, timeout=25,
                    headers={
                        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept":          "text/html,application/xhtml+xml",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Cookie":          f"auth_token={auth}; ct0={ct0}",
                    },
                ) as http:
                    r = await http.get("https://x.com/home")
                    html = r.text

                # Parse and inject into twikit's transaction engine
                soup = bs4.BeautifulSoup(html, "html.parser")
                if len(soup.find_all()) < 10:
                    raise RuntimeError("proxy returned empty home page")

                ct = _twikit_client.client_transaction
                ct.home_page_response = soup

                # Find ondemand.s.XXXa.js URL in the page
                m = _re.search(r"ondemand\.s\.([a-f0-9]+)a\.js", html)
                if not m:
                    # Also check abs.twimg.com script tags
                    for tag in soup.find_all("script", src=True):
                        src = tag["src"]
                        mm = _re.search(r"ondemand\.s\.([a-f0-9]+)a\.js", src)
                        if mm:
                            m = mm
                            break

                if m:
                    od_url = f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{m.group(1)}a.js"
                    r2 = await http.get(od_url)
                    indices = _re.findall(r"KeyByteIndices=\[(\d+),(\d+)\]", r2.text)
                    if indices:
                        ct.DEFAULT_ROW_INDEX = int(indices[0][0])
                        ct.DEFAULT_KEY_BYTES_INDICES = [int(indices[0][1])]
                        log.info(f"twitter_search: twikit XCT indices loaded from {od_url[-30:]}")

                # Get key from meta tag
                meta = soup.find("meta", {"name": "twitter-site-verification"})
                if meta:
                    ct.key = meta.get("content", "")
                    import base64
                    ct.key_bytes = list(base64.b64decode(ct.key.encode()))

                # Set cookies on the client
                _twikit_client.set_cookies({"auth_token": auth, "ct0": ct0})
                log.info("twitter_search: twikit bootstrapped via Webshare proxy")

            try:
                _run_async_in_thread(_init(), timeout=40.0)
                _twikit_ready = True
            except Exception as e:
                log.warning(f"twitter_search: twikit bootstrap failed: {e}")
                return None

    return _twikit_client if _twikit_ready else None


def _twikit_search(queries: list[str], auth: str, ct0: str, count: int = 20) -> tuple[list[dict], str]:
    """Search via twikit. Returns (tweets, status_string)."""
    client = _get_twikit_client(auth, ct0)
    if not client:
        return [], "twikit unavailable"

    async def _run():
        results = []
        for q in queries[:20]:   # twikit: max 20 queries to stay within rate limits
            try:
                batch = await client.search_tweet(q, product="Latest", count=count)
                for tw in batch:
                    if tw.text and not tw.text.startswith("RT "):
                        tid = str(tw.id)
                        results.append({
                            "id":       tid,
                            "text":     tw.text,
                            "user":     tw.user.screen_name if tw.user else "",
                            "likes":    tw.favorite_count or 0,
                            "retweets": tw.retweet_count or 0,
                            "date":     tw.created_at or "",
                            "lang":     tw.lang or "",
                            "url":      f"https://x.com/{tw.user.screen_name}/status/{tid}" if tw.user else "",
                            "search_query": q,
                        })
            except Exception as e:
                log.warning(f"twitter_search: twikit query error ({q[:40]}): {e}")
            await asyncio.sleep(0.6)
        return results

    try:
        tweets = _run_async_in_thread(_run(), timeout=min(len(queries) * 5.0, 100.0))
        tweets = tweets or []
        return tweets, f"twikit ({len(tweets)}t/{min(len(queries),20)}q)"
    except TimeoutError:
        return [], "twikit timeout"
    except Exception as e:
        return [], f"twikit error: {str(e)[:50]}"


# ══════════════════════════════════════════════════════════════════════════════
# Methods 3 & 4 — Residential proxy sessions (Webshare / IPRoyal)
# ══════════════════════════════════════════════════════════════════════════════

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


def _session_search_one(session, query: str, count: int = 20) -> list[dict]:
    """Fire one SearchTimeline call via a requests/curl_cffi session."""
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
            return []
        instructions = (
            r.json()
             .get("data", {})
             .get("search_by_raw_query", {})
             .get("search_timeline", {})
             .get("timeline", {})
             .get("instructions", [])
        )
        entries = next(
            (i.get("entries", []) for i in instructions if i.get("type") == "TimelineAddEntries"), []
        )
        results = []
        for entry in entries:
            tw = (entry.get("content", {})
                       .get("itemContent", {})
                       .get("tweet_results", {})
                       .get("result", {}))
            legacy = tw.get("legacy", {})
            text   = legacy.get("full_text", "")
            if not text or text.startswith("RT "):
                continue
            uid_obj = tw.get("core", {}).get("user_results", {}).get("result", {})
            screen  = uid_obj.get("legacy", {}).get("screen_name", "")
            tid     = tw.get("rest_id", "")
            results.append({
                "id":       tid,
                "text":     text,
                "user":     screen,
                "likes":    legacy.get("favorite_count", 0),
                "retweets": legacy.get("retweet_count", 0),
                "date":     legacy.get("created_at", ""),
                "lang":     legacy.get("lang", ""),
                "url":      f"https://x.com/{screen}/status/{tid}" if screen and tid else "",
            })
        return results
    except Exception:
        return []


def _probe_session(session, count: int = 5) -> int:
    if session is None:
        return 0
    return len(_session_search_one(session, _PROBE_QUERY, count))


def _session_search_all(session, queries: list[str], count: int = 20, parallel: int = 6) -> list[dict]:
    lock  = threading.Lock()
    found: list[dict] = []
    seen:  set[str]   = set()

    def _run(q: str):
        for t in _session_search_one(session, q, count):
            tid = t.get("id", "")
            with lock:
                if tid and tid not in seen:
                    seen.add(tid)
                    t["search_query"] = q
                    found.append(t)
        time.sleep(0.5)

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = [pool.submit(_run, q) for q in queries]
        for f in as_completed(futures, timeout=90):
            try:
                f.result()
            except Exception:
                pass
    return found


def _probe_and_select_proxy_session(auth: str, ct0: str):
    """Try webshare → iproyal, return first that gives results."""
    global _active_session, _active_sess_name, _sess_cache_time
    now = time.time()
    if _active_session and now - _sess_cache_time < _SESS_TTL:
        return _active_session, _active_sess_name

    for provider in ("webshare", "iproyal"):
        try:
            sess = _make_proxy_session(auth, ct0, provider)
            if sess and _probe_session(sess) > 0:
                _active_session, _active_sess_name, _sess_cache_time = sess, provider, now
                return sess, provider
        except Exception:
            pass

    return None, "none"


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def search_all(
    queries:  list[str],
    auth:     str,
    ct0:      str,
    count:    int = 20,
    parallel: int = 8,
) -> tuple[list[dict], str]:
    """
    Search ALL of Twitter for every query.

    Priority order:
      1. SocialData.tools API  (needs SOCIALDATA_API_KEY env var)
      2. twikit                (free; needs Webshare proxy for bootstrap)
      3. Proxy session         (Webshare or IPRoyal residential)

    Returns (deduplicated_tweet_list, status_string).
    """
    if not auth or not ct0:
        return [], "no credentials"

    # ── 1. SocialData.tools ──────────────────────────────────────────────────
    if _socialdata_key():
        tweets, status = _socialdata_search(queries, count=count, parallel=parallel)
        if tweets:
            return _dedup(tweets), status
        log.info(f"twitter_search: socialdata returned 0 ({status})")

    # ── 2. twikit ────────────────────────────────────────────────────────────
    try:
        import twikit  # noqa: F401
        tweets, status = _twikit_search(queries, auth, ct0, count)
        if tweets:
            return _dedup(tweets), status
        log.info(f"twitter_search: twikit returned 0 ({status})")
    except ImportError:
        pass

    # ── 3. Proxy session ─────────────────────────────────────────────────────
    sess, name = _probe_and_select_proxy_session(auth, ct0)
    if sess:
        tweets = _session_search_all(sess, queries, count=count, parallel=parallel)
        status = f"{name} ({len(tweets)}t/{len(queries)}q)"
        return _dedup(tweets), status

    return [], "all methods failed — add SOCIALDATA_API_KEY for guaranteed coverage"


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
    Diagnostic: test every engine independently.
    Returns per-engine results for /api/test-search.
    """
    out = {}

    # SocialData
    if _socialdata_key():
        try:
            r = _socialdata_one(_PROBE_QUERY, count=5)
            out["socialdata"] = {"tweets": len(r), "status": "ok" if r else "empty/0"}
        except Exception as e:
            out["socialdata"] = {"tweets": 0, "status": str(e)[:80]}
    else:
        out["socialdata"] = {"tweets": 0, "status": "no API key — sign up at socialdata.tools"}

    # twikit
    try:
        import twikit  # noqa
        try:
            tweets, status = _twikit_search([_PROBE_QUERY], auth, ct0, count=5)
            out["twikit"] = {"tweets": len(tweets), "status": status}
        except Exception as e:
            out["twikit"] = {"tweets": 0, "status": str(e)[:80]}
    except ImportError:
        out["twikit"] = {"tweets": 0, "status": "not installed"}

    # Webshare proxy
    try:
        s = _make_proxy_session(auth, ct0, "webshare")
        n = _probe_session(s) if s else 0
        out["webshare"] = {"tweets": n, "status": "ok" if n else ("no proxy" if s is None else "empty")}
    except Exception as e:
        out["webshare"] = {"tweets": 0, "status": str(e)[:80]}

    # IPRoyal proxy
    try:
        s = _make_proxy_session(auth, ct0, "iproyal")
        n = _probe_session(s) if s else 0
        out["iproyal"] = {"tweets": n, "status": "ok" if n else ("no proxy" if s is None else "empty")}
    except Exception as e:
        out["iproyal"] = {"tweets": 0, "status": str(e)[:80]}

    out["_working"]      = [k for k, v in out.items() if not k.startswith("_") and v["tweets"] > 0]
    out["_probe_query"]  = _PROBE_QUERY
    out["_instructions"] = (
        "Add SOCIALDATA_API_KEY env var on Render for guaranteed unlimited coverage. "
        "Sign up at https://socialdata.tools — ~$25/month or pay-per-tweet."
    )
    return out
