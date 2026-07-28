---
name: X Scraper Architecture
description: Core architecture of the Twitter/X scraper — endpoints, QIDs, proxy setup, account pool sources, and search query structure.
---

# X Scraper Architecture

## Endpoints & IP restrictions
- **UserByScreenName** + **UserTweets** + **TweetDetail** — work from GCP/datacenter IPs ✅
- **SearchTimeline** — blocked from GCP/datacenter IPs; requires residential proxy ⚠️

## Current GraphQL QIDs (extracted from main.933c177a.js on 2026-07-28)
- `UserByScreenName`: `Gb-d6r0vxPOADdG62OEBpQ` (verified working)
- `UserTweets`: `eoJ5zbv51Z_KVl81v9PmLQ` (verified working)
- `TweetDetail`: `559hs_YZNV4IgA3Z6zIIuw` (verified working)
- `SearchTimeline`: `BGd0T_j7oVwlW5U79tO_0A` (extracted; proxy connectivity TBD)

**Why:** Twitter rotates QIDs with every bundle deployment (~weeks). When any endpoint returns 404, call `_auto_refresh_qids()` in x_scraper.py — it fetches the live bundle via proxy and re-extracts all QIDs.

**How to extract fresh QIDs manually:**
1. Use Webshare proxy `p.webshare.io:80` with curl_cffi (credentials from `WEBSHARE_API_KEY`)
2. Fetch `https://x.com/explore` to get current bundle URL (e.g. `main.933c177a.js`)
3. Fetch the bundle and grep for `queryId:"XXX",operationName:"SearchTimeline"` pattern

## Proxy setup (Webshare residential)
- Host: `p.webshare.io:80`
- Credentials from API: `GET https://proxy.webshare.io/api/v2/proxy/config/` with `Authorization: Token $WEBSHARE_API_KEY`
- Returns `username` and `password` fields (cached 6h in proxy_pool.py)
- `make_proxied_session(auth, ct0, bearer)` in `tools/proxy_pool.py` creates a proxied curl_cffi session
- SearchTimeline via proxy returning 404 empty body — likely Cloudflare challenge on proxy IP; investigate response format

## Account pool structure (7,040+ total)
- **Static list**: 103 categories, 1,062 accounts in `_ACCOUNTS` dict
- **DeFiLlama**: ~5,978 additional protocol handles from `api.llama.fi/protocols`, refreshes every 12h (one API call, no key)
- **CoinGecko full scraper**: ALL ~17,851 coins scraped in background (Phase 1: fetch all IDs sorted by mktcap ~3min; Phase 2: fetch handles at 1/2.5s ~3h), weekly refresh, saves progress every 50 coins
- **CoinGecko dynamic**: trending + 6 rotating categories + top 24h gainers, refreshes every 4h
- **Total pool**: 7,040+ static+DeFiLlama, eventually 10,000+ as CG full scraper completes

## SearchTimeline queries (154 total, 20/cycle, parallel 4 threads × 5 queries)
- Tier 1 (16): broad complaints — catch any crypto project
- Tier 2 (22): every major L1/L2 chain (ETH/SOL/BNB/MATIC/ARB/OP/BASE/AVAX/TRX/TON...)
- Tier 3 (42): CEX + wallet + DeFi protocol-specific complaints
- Tier 4 (30): NFT/gaming, stablecoins/RWA, memecoins, perps/derivatives
- Tier 5 (38): 9 non-English languages (ES/PT/KO/ZH/TR/RU/ID/HI/VI/AR/JA)
- Tier 6 (12): emerging categories (AI agents, DePIN, SocialFi, prediction markets)
- Full rotation every ~8 cycles (~2 hours)

## screen_name location in responses
- `UserTweets`: screen_name at `result.core.user_results.result.core.screen_name`
- `TweetDetail`: screen_name at `result.core.user_results.result.legacy.screen_name`

## Known hardcoded IDs (zero API calls)
14 priority accounts in `_KNOWN_USER_IDS` in x_scraper.py — BinanceHelpDesk, CoinbaseSupport, KrakenSupport, TrustWalletApp, AaveAave, JupiterExchange, Arbitrum, Base, Circle, Uniswap, Phantom, HyperliquidX, MagicEden, PeckShieldAlert.
