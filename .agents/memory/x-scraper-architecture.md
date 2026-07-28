---
name: X Scraper Architecture
description: Full architecture of the Twitter/X crypto complaint monitor deployed on Render
---

## Service
- Render service ID: `srv-d7s2rud7vvec738tlff0`
- URL: `https://twitter-x-monitor.onrender.com`
- GitHub: `https://github.com/Lakalaa/twitter-x-monitor` (branch: main, auto-deploy)
- Wrong service to ignore: `srv-d7s3ob8g4nts73d2tdsg` / `monitor-bot-wfqc.onrender.com`

## Search Engine Priority (tools/twitter_search.py)

1. **SocialData.tools** (PRIMARY — CONFIRMED WORKING from Render)
   - Env var: `SOCIALDATA_API_KEY`
   - Key format: `<digits>|<long_token>` (Laravel Sanctum)
   - Endpoint: `GET https://api.socialdata.tools/twitter/search?query=...&type=Latest`
   - Header: `Authorization: Bearer {key}`
   - Response field: `tweets[].full_text`, `tweets[].user.screen_name`, `tweets[].id_str`
   - 8 queries in parallel, ~0.15s pacing between them
   - Works from ANY IP — no blocking possible
   - **Why:** All other methods (direct, Webshare proxy, IPRoyal, twikit, twscrape) return 0 from Render datacenter IPs. Twitter specifically blocks SearchTimeline from non-browser sessions on datacenter IPs.

2. **twikit** (free fallback — bootstrapped via Webshare proxy for XClientTransaction)
   - Currently fails from Render — XCT `ondemand.s.XXXa.js` ref not found in proxied home page
   - Keep in requirements.txt for future retry

3. **Webshare / IPRoyal residential proxy** — currently return empty (Twitter blocks these too for search)

## Confirmed Blocked from Render (datacenter IPs)
- Direct session (GraphQL SearchTimeline): empty
- Webshare residential proxy: empty
- IPRoyal residential proxy: "no proxy" (key format issue)
- twscrape: event loop timeout
- twikit: XClientTransaction init fails

## Query Counts
- Step 0 (global search): 40 queries/cycle via search_all()
- Scan cycle: every 15 min
- DeFiLlama: 7782 protocols, 103 categories

## Auth env vars on Render
- `TWITTER_AUTH_TOKEN` — raw token (not used by scraper directly)
- `TWITTER_AUTH_TOKEN_COOKIE` — what x_scraper.py reads (same value as above)
- `TWITTER_CT0` — ct0 cookie
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `WEBSHARE_API_KEY`
- `IPROYAL_API_KEY`
- `SOCIALDATA_API_KEY` — PRIMARY search engine key

## x_scraper.py QIDs (confirmed from bundle main.d15fd02a.js, 2026-07-28)
- UserTweets:       `eoJ5zbv51Z`
- UserByScreenName: `Gb-d6r0vx`
- TweetDetail:      `559hs_YZ`
- SearchTimeline:   `BGd0T_j7`  (also hardcoded in twitter_search.py)

## Confirmed Working (2026-07-28)
- SocialData 40-query cycle → 682 tweets per scan
- 1 complaint sent to Telegram per confirmed scan
- Telegram conflict resolved by deleting wrong service `srv-d7s3ob8g4nts73d2tdsg` (not just suspending)

## Bug fixed
- `x_issues_monitor.py` line 2368: `_ALL_ACCOUNTS.keys()` → `_ALL_ACCOUNTS` (it's a list, not dict)

## Notes
- `_load_creds()` in x_scraper.py reads `TWITTER_AUTH_TOKEN_COOKIE` (not `TWITTER_AUTH_TOKEN`)
- screen_name lookup quirk: some accounts have location in unexpected field
- Hardcoded user IDs for 14 priority accounts in x_issues_monitor.py `_KNOWN_USER_IDS`
- 5 missing IDs still: Bybit_CS, MetaMask_Support, LedgerSupport, OKXSupport, Tether_to
- `STATE["last_check"]` in app.py belongs to the OLD Scweet scheduler, not X issues monitor — always None for X-issues scans; use logs to verify scan health instead
