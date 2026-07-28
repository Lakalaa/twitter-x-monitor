---
name: X Scraper Architecture
description: Core architecture of the Twitter/X scraper — which endpoints work from where, QID rotation, proxy setup, and account pool structure.
---

# X Scraper Architecture

## Endpoints & IP restrictions
- **UserByScreenName** + **UserTweets** + **TweetDetail** — work from GCP/datacenter IPs ✅
- **SearchTimeline** — blocked from GCP/datacenter IPs; requires residential proxy ⚠️

## Current GraphQL QIDs (extracted from main.933c177a.js on 2026-07-28)
- `UserByScreenName`: `Gb-d6r0vxPOADdG62OEBpQ` (verified working)
- `UserTweets`: `eoJ5zbv51Z_KVl81v9PmLQ` (verified working)
- `TweetDetail`: `559hs_YZNV4IgA3Z6zIIuw` (verified working)
- `SearchTimeline`: `BGd0T_j7oVwlW5U79tO_0A` (extracted; proxy connectivity needed)

**Why:** Twitter rotates QIDs with every bundle deployment (~weeks). When any endpoint returns 404, call `_auto_refresh_qids()` in x_scraper.py — it fetches the live bundle via proxy and re-extracts all QIDs.

**How to extract fresh QIDs manually:**
1. Use Webshare proxy `http://yabmqllc:4crhjum3ddg2@p.webshare.io:80` with curl_cffi
2. Fetch `https://x.com/explore` to get current bundle URL (e.g. `main.933c177a.js`)
3. Fetch the bundle and grep for `queryId:"XXX",operationName:"SearchTimeline"` pattern

## Proxy setup (Webshare residential)
- Host: `p.webshare.io:80`
- Credentials from API: `GET https://proxy.webshare.io/api/v2/proxy/config/` with `Authorization: Token $WEBSHARE_API_KEY`
- Returns `username` and `password` fields (cached 6h in proxy_pool.py)
- `make_proxied_session(auth, ct0, bearer)` in `tools/proxy_pool.py` creates a proxied curl_cffi session
- SearchTimeline via proxy returns 404 (empty body) in some cases — investigate response format

## Account pool structure
- **Static list**: 103 categories, 1,062 accounts in `_ACCOUNTS` dict
- **DeFiLlama**: ~5,978 additional protocol handles fetched from `api.llama.fi/protocols` every 12h (one API call, no key needed)
- **CoinGecko**: dynamic trending + category discovery, refreshes every 2h
- **CoinGecko bg enrichment**: top 500 coins by mktcap, 24h refresh
- **Total pool**: ~7,040+ accounts

## screen_name location in responses
- `UserTweets`: screen_name at `result.core.user_results.result.core.screen_name`
- `TweetDetail`: screen_name at `result.core.user_results.result.legacy.screen_name`

## Known hardcoded IDs (zero API calls)
14 priority accounts in `_KNOWN_USER_IDS` in x_scraper.py — BinanceHelpDesk, CoinbaseSupport, KrakenSupport, TrustWalletApp, AaveAave, JupiterExchange, Arbitrum, Base, Circle, Uniswap, Phantom, HyperliquidX, MagicEden, PeckShieldAlert.
