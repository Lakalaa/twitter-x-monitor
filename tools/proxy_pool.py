"""
proxy_pool.py
Rotating residential proxy management via Webshare.
Used to make SearchTimeline requests from residential IPs
(Twitter blocks this endpoint from datacenter/GCP IPs).
"""
from __future__ import annotations
import os, json, time, urllib.request, logging

_WEBSHARE_KEY = os.environ.get("WEBSHARE_API_KEY", "")
_IPROYAL_KEY  = os.environ.get("IPROYAL_API_KEY", "")

# Cached proxy credentials
_creds: tuple[str, str] | None = None
_creds_fetched: float = 0.0
_CREDS_TTL = 6 * 3600   # re-fetch every 6 hours

# Webshare rotating residential endpoint
_WS_HOST = "p.webshare.io"
_WS_PORT = 80


def _load_webshare_creds() -> tuple[str, str]:
    """Return (username, password) for Webshare rotating proxy."""
    global _creds, _creds_fetched
    now = time.time()
    if _creds and now - _creds_fetched < _CREDS_TTL:
        return _creds
    if not _WEBSHARE_KEY:
        return ("", "")
    try:
        req = urllib.request.Request(
            "https://proxy.webshare.io/api/v2/proxy/config/",
            headers={"Authorization": f"Token {_WEBSHARE_KEY}"}
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            cfg = json.loads(r.read())
        user = cfg.get("username", "")
        pwd  = cfg.get("password", "")
        if user and pwd:
            _creds = (user, pwd)
            _creds_fetched = now
            return (user, pwd)
    except Exception as e:
        logging.warning(f"proxy_pool: Webshare creds error: {e}")
    return ("", "")


def get_proxy_url() -> str:
    """
    Return an HTTP proxy URL for residential IP routing.
    Usage:  proxies = {"http": url, "https": url}
    Returns empty string if proxy unavailable.
    """
    user, pwd = _load_webshare_creds()
    if user and pwd:
        return f"http://{user}:{pwd}@{_WS_HOST}:{_WS_PORT}"
    return ""


def make_proxied_session(auth: str, ct0: str, bearer: str):
    """
    Create a curl_cffi Session identical to x_scraper._make_session but
    routed through a residential proxy. Returns None if no proxy available.
    """
    proxy_url = get_proxy_url()
    if not proxy_url:
        return None
    try:
        import curl_cffi.requests as _cffi
        s = _cffi.Session(
            impersonate="chrome120",
            proxies={"http": proxy_url, "https": proxy_url},
        )
        s.headers.update({
            "Authorization":             f"Bearer {bearer}",
            "Cookie":                    f"auth_token={auth}; ct0={ct0}",
            "X-Csrf-Token":              ct0,
            "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "x-twitter-active-user":     "yes",
            "x-twitter-auth-type":       "OAuth2Session",
            "x-twitter-client-language": "en",
        })
        return s
    except ImportError:
        logging.warning("proxy_pool: curl_cffi not available — proxy sessions disabled")
        return None
