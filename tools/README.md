# Extra Twitter/X Scraping Tools

This folder contains ready-to-use scripts for three powerful Twitter/X tools found on GitHub that complement `twscrape`.

---

## Tool Comparison

| Feature | twscrape | Scweet | twitter-api-client | TweeterPy |
|---|---|---|---|---|
| Search tweets | ✅ | ✅ | ✅ | ✅ |
| Followers / Following | ✅ | ✅ | ✅ | ✅ |
| User profiles | ✅ | ✅ | ✅ | ✅ |
| Multi-account pooling | ✅ | ✅ | ❌ | ❌ |
| Async / parallel | ✅ | ✅ | ❌ | ❌ |
| Write actions (post, like, DM) | ❌ | ❌ | ✅ | ❌ |
| Twitter Spaces (audio/chat) | ❌ | ❌ | ✅ | ❌ |
| Media download | ❌ | ❌ | ✅ | ❌ |
| Batch queries (high rate limits) | ❌ | ❌ | ✅ | ❌ |
| Tweet scheduling | ❌ | ❌ | ✅ | ❌ |
| Last updated | 2025 | Apr 2026 | Apr 2024 | 2024 |

---

## ⚠️ Important Limitation

**None of these tools can access private/locked accounts.**

All tools work by mimicking a logged-in Twitter/X user. If an account is private (padlock icon) or has hidden their followers list in privacy settings, that data is inaccessible to everyone — including these tools. This is enforced at Twitter/X's server level and cannot be bypassed.

---

## 1. Scweet — Best for bulk tweet & follower scraping

**File:** `scweet_usage.py`  
**GitHub:** https://github.com/Altimis/Scweet  
**Why use it:** Actively maintained (updated April 2026), handles multi-account pooling, rate limits, SQLite session persistence, and proxy support automatically.

```bash
pip install -U Scweet
```

**Setup:**
1. Log into x.com in your browser
2. DevTools (F12) → Application → Cookies → `https://x.com`
3. Copy the `auth_token` value

**Key capabilities:**
- Search by keyword, hashtag, date range, language, user, engagement
- Full follower/following lists at scale
- Profile timeline scraping
- Auto rate-limit handling and session resume

---

## 2. twitter-api-client — Best for write actions & Spaces

**File:** `twitter_api_client_usage.py`  
**GitHub:** https://github.com/trevorhobenshield/twitter-api-client  
**Why use it:** The only tool here that supports write actions, Twitter Spaces audio/transcript downloads, and batch queries with higher rate limits.

```bash
pip install -U twitter-api-client
```

**Setup:**
1. Log into x.com in your browser
2. DevTools (F12) → Application → Cookies → `https://x.com`
3. Copy `auth_token` AND `ct0` values

**Key capabilities:**
- **Write:** post tweets, reply, quote, retweet, like, follow, DM, schedule tweets
- **Spaces:** download audio, chat logs, live transcription
- **Media:** download photos/videos/cards from tweets
- **Batch queries:** tweets_by_ids / users_by_ids (much higher rate limits)
- Tweet scheduling with Unix timestamp

---

## 3. TweeterPy — Best for simple, quick lookups

**File:** `tweeterpy_usage.py`  
**GitHub:** https://github.com/iSarabjitDhiman/TweeterPy  
**Why use it:** Simplest API, easy login with username/password, good for quick one-off data pulls without cookie management.

```bash
pip install -U tweeterpy
```

**Key capabilities:**
- Login with username/password (no cookie extraction needed)
- Followers, following, user profiles, tweets, replies, media, likes
- Notifications access
- Simple Python interface

---

## Which tool to use?

| Goal | Use |
|---|---|
| Bulk tweet search at scale | **Scweet** or **twscrape** |
| Scrape followers/following | **twscrape** (multi-account) or **Scweet** |
| Post tweets, send DMs, follow users | **twitter-api-client** |
| Download Spaces audio or transcript | **twitter-api-client** |
| Download tweet media (photos/videos) | **twitter-api-client** |
| Quick profile lookup, no cookie setup | **TweeterPy** |
| Production-grade async scraping | **twscrape** |
