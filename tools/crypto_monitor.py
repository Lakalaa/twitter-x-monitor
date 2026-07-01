"""
crypto_monitor.py — Deep Crypto Intelligence Scraper + AI-style Classifier
Sources (all free, no API key required):
  - CryptoPanic public feed
  - Reddit: r/CryptoCurrency, r/defi, r/ethfinance, r/memecoins,
            r/SatoshiStreetBets, r/CryptoMarkets, r/solana, r/ethereum,
            r/CryptoScams, r/CryptoComplaints (best-effort)
  - CoinGecko trending + categories
  - Alternative.me Fear & Greed Index
  - Coindesk RSS
  - DeFiLlama hacks feed
"""
from __future__ import annotations
import json
import re
import time
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

# ────────────────────────────────────────────────────────────────────────────
# Keyword banks (each list is scored by count of matches)
# ────────────────────────────────────────────────────────────────────────────

_HACK_KW = [
    "exploit", "hacked", "hack", "attack", "breach", "vulnerability", "critical bug",
    "smart contract bug", "reentrancy", "flash loan", "oracle manipulation",
    "price manipulation", "infinite mint", "stolen", "drained", "lost funds",
    "funds at risk", "emergency", "pause", "paused protocol", "suspended",
    "post-mortem", "security incident", "white hat",
]

_RUG_KW = [
    "rug pull", "rugpull", "rug", "exit scam", "honeypot", "honey pot",
    "dev dump", "team dump", "dumped", "abandoned", "soft rug", "slow rug",
    "insider sell", "pre-sale scam", "fake project", "0 liquidity",
    "liquidity removed", "lp removed", "migrated", "migrating to v2",
    "fake audit", "unaudited", "anon team", "doxxed team fled",
]

_STAKING_KW = [
    "staking", "stake", "unstake", "validator", "delegation", "slashing",
    "slash event", "missed reward", "missed epoch", "downtime penalty",
    "jailed validator", "withdrawal delay", "unbonding period",
    "liquid staking", "restaking", "lsd", "lst", "lrt",
    "eigenlayer", "rocketpool", "lido", "frax", "ankr",
    "withdrawal queue", "stake limit", "dvt", "distributed validator",
]

_YIELD_KW = [
    "yield", "apr", "apy", "impermanent loss", "il", "bad debt",
    "insolvency", "depeg", "de-peg", "depegged", "undercollateralized",
    "collateral", "liquidation", "liquidated", "forced liquidation",
    "lending protocol", "borrow", "interest rate", "rate spike",
    "vault exploit", "strategy bug", "yield farm", "farming",
    "autocompound", "compounder", "rebase", "elastic supply",
    "protocol insolvency", "bad debt written off",
]

_MEMECOIN_KW = [
    "memecoin", "meme coin", "meme token", "doge", "shib", "pepe", "bonk",
    "wif", "floki", "dogwifhat", "wojak", "chad", "pump fun", "pumpfun",
    "solana memecoin", "base memecoin", "new coin launch", "fair launch",
    "stealth launch", "presale", "100x", "1000x", "moon", "mooning",
    "dev wallet", "bundled launch", "sniper bot", "bot sniped",
    "telegram call", "alpha call", "ct call", "degen play",
    "market cap", "ath", "new ath", "coin trending", "trending token",
    "low cap", "micro cap", "gem", "hidden gem", "based",
]

_DEFI_KW = [
    "defi", "dex", "amm", "concentrated liquidity", "clmm",
    "uniswap", "curve", "balancer", "pancakeswap", "raydium", "orca",
    "tvl", "total value locked", "liquidity pool", "lp position",
    "bridge exploit", "cross-chain", "layer2", "l2", "rollup",
    "arbitrum", "optimism", "base", "zksync", "polygon",
    "gas spike", "gas war", "front-run", "mev", "sandwich",
    "fee revenue", "protocol revenue", "buyback",
]

_REWARD_KW = [
    "airdrop", "air drop", "reward distribution", "retroactive",
    "claim now", "claim open", "snapshot taken", "eligible",
    "points season", "points program", "campaign launch",
    "incentive", "liquidity mining", "token launch", "tge",
    "vesting unlock", "cliff unlock", "unlock event", "vest",
    "bonus", "referral reward", "trading competition",
]

_ONCHAIN_KW = [
    "whale alert", "large transfer", "dormant wallet", "moved",
    "exchange inflow", "exchange outflow", "on-chain signal",
    "smart money", "wallet tracking", "nansen", "arkham",
    "large buy", "large sell", "otc deal", "over the counter",
]

# ────────────────────────────────────────────────────────────────────────────
# Token detection
# ────────────────────────────────────────────────────────────────────────────

_DOLLAR_TOKEN_RE = re.compile(r'\$([A-Z]{2,10})\b')
_UPPER_TOKEN_RE  = re.compile(r'\b([A-Z]{2,8})\b')

_KNOWN_TOKENS = {
    # Layer 1s
    "BTC","ETH","SOL","BNB","ADA","DOT","AVAX","ATOM","NEAR","FTM","HBAR",
    "ALGO","VET","TRX","XRP","TON","SUI","APT","SEI","TIA","INJ",
    # Layer 2s / Infrastructure
    "ARB","OP","MATIC","IMX","STARK","ZK","MANTA","BLAST","SCROLL",
    # DeFi
    "UNI","AAVE","CRV","MKR","SNX","COMP","YFI","SUSHI","BAL","FRAX",
    "DYDX","GMX","GNS","PENDLE","LDO","RPL","EIGEN","ETHFI","OSMO",
    "JUP","PYTH","WEN","DRIFT","ZETA","MANGO",
    # Memecoins
    "DOGE","SHIB","PEPE","BONK","WIF","FLOKI","WOJAK","BRETT","TOSHI",
    "MOCHI","POPCAT","BOOK","MEW","BOME","PONKE","CAT","COQ",
    "PUMP","PUMPFUN","TURBO",
    # Stablecoins
    "USDT","USDC","DAI","FRAX","LUSD","CRVUSD","USDE","PYUSD","TUSD",
    # Yield/Liquid Staking
    "STETH","WSTETH","RETH","SFRXETH","METH","RSETH","EZETH",
    # Other popular
    "LINK","GRT","FIL","SAND","MANA","AXS","CHZ","GALA","IMX",
    "LTC","BCH","ETC","RNDR","AR","OCEAN","FET","AGIX","ROSE",
    "JUNO","OSMO","LUNA","LUNC","USTC",
}

def _extract_tokens(text: str) -> list[str]:
    found = set()
    for m in _DOLLAR_TOKEN_RE.finditer(text):
        found.add(m.group(1).upper())
    for m in _UPPER_TOKEN_RE.finditer(text):
        t = m.group(1).upper()
        if t in _KNOWN_TOKENS:
            found.add(t)
    # Remove common English words that look like tickers
    noise = {"A","I","IT","IS","IN","ON","AT","BE","BY","DO","IF","OF","OR",
              "TO","UP","AS","AN","WE","US","GO","NO","SO","TV","AM","PM",
              "USD","EUR","GBP","ALL","NEW","NOW","THE","FOR","AND","BUT",
              "NOT","GET","SET","PUT","API","URL","GAS","CEX","DEX","DAO",
              "NFT","DLC","P2P","ETF","IPO","ICO","IDO","TGE","TVL","ATH",
              "APR","APY","LTV","LTV","KYC","AML","OTC","CEX","DEX","AMM",
              "MEV","AVG","MAX","MIN","SEC","BTC","ETH"}
    return sorted(found - noise)

# ────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ────────────────────────────────────────────────────────────────────────────

_UA = "Mozilla/5.0 (compatible; CryptoMonitorBot/2.0; +https://github.com)"

def _fetch_json(url: str, timeout: int = 12) -> Optional[dict | list]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                    "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:
        return None

def _fetch_text(url: str, timeout: int = 12) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                    "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return ""

# ────────────────────────────────────────────────────────────────────────────
# AI-style classifier
# ────────────────────────────────────────────────────────────────────────────

_CAT_BANKS = {
    "hack":    _HACK_KW,
    "rug":     _RUG_KW,
    "staking": _STAKING_KW,
    "yield":   _YIELD_KW,
    "memecoin":_MEMECOIN_KW,
    "defi":    _DEFI_KW,
    "reward":  _REWARD_KW,
    "onchain": _ONCHAIN_KW,
}

def classify_item(title: str, body: str = "") -> dict:
    """
    Return: {category, priority (1–5), tokens, subcategory}
    """
    combined = f"{title} {body}".lower()
    scores = {cat: sum(1 for kw in kws if kw in combined)
              for cat, kws in _CAT_BANKS.items()}

    # Determine winning category
    best_cat = max(scores, key=lambda c: scores[c])
    best_score = scores[best_cat]

    # Override: if explicit high-urgency words appear, force hack/rug
    if any(kw in combined for kw in ("exploit", "hack", "stolen", "drained", "rug pull", "rugpull", "exit scam")):
        best_cat = "hack" if scores["hack"] >= scores["rug"] else "rug"
        best_score = max(scores["hack"], scores["rug"])

    if best_score == 0:
        best_cat = "general"

    # Priority based on score + category urgency
    urgency_bonus = {"hack": 2, "rug": 2, "yield": 1, "staking": 1,
                     "memecoin": 0, "defi": 0, "reward": 0, "onchain": 1, "general": 0}
    priority = min(5, max(1, best_score + urgency_bonus.get(best_cat, 0)))

    tokens = _extract_tokens(f"{title} {body[:300]}")
    return {"category": best_cat, "priority": priority, "tokens": tokens}

# ────────────────────────────────────────────────────────────────────────────
# Source scrapers
# ────────────────────────────────────────────────────────────────────────────

def _norm_item(source, title, url, date, clf) -> dict:
    return {"source": source, "title": title[:200], "url": url,
            "date": date, **clf}

# -- CryptoPanic -------------------------------------------------------------

def fetch_cryptopanic(filter_kind: str = "hot", limit: int = 25) -> list[dict]:
    data = _fetch_json(
        f"https://cryptopanic.com/api/v1/posts/?public=true&filter={filter_kind}&limit={limit}"
    )
    if not data or "results" not in data:
        return []
    items = []
    for post in data["results"]:
        title = post.get("title", "")
        body  = post.get("body") or ""
        url_  = post.get("url") or (post.get("source") or {}).get("url", "")
        date  = (post.get("published_at") or "")[:10]
        currencies = [c["code"].upper() for c in (post.get("currencies") or [])]
        clf = classify_item(title, body)
        for c in currencies:
            if c not in clf["tokens"]:
                clf["tokens"].append(c)
        items.append(_norm_item("CryptoPanic", title, url_, date, clf))
    return items

# -- CryptoPanic rising (newer/less filtered) --------------------------------

def fetch_cryptopanic_rising(limit: int = 20) -> list[dict]:
    data = _fetch_json(
        f"https://cryptopanic.com/api/v1/posts/?public=true&filter=rising&limit={limit}"
    )
    if not data or "results" not in data:
        return []
    items = []
    for post in data["results"]:
        title = post.get("title", "")
        body  = post.get("body") or ""
        url_  = post.get("url") or (post.get("source") or {}).get("url", "")
        date  = (post.get("published_at") or "")[:10]
        clf = classify_item(title, body)
        items.append(_norm_item("CryptoPanic·Rising", title, url_, date, clf))
    return items

# -- Reddit ------------------------------------------------------------------

_REDDIT_UA = "CryptoMonitorBot/2.0 (Telegram; bot contact: none)"

_SUBREDDITS = [
    ("CryptoCurrency",      15),
    ("defi",                12),
    ("ethfinance",          10),
    ("memecoins",            8),
    ("SatoshiStreetBets",    8),
    ("CryptoMarkets",        8),
    ("solana",               8),
    ("ethereum",             8),
    ("CryptoScams",          6),
    ("yieldfarming",         6),
    ("NFTsMarketplace",      4),
    ("altcoin",              6),
]

def fetch_reddit_sub(subreddit: str, limit: int = 15) -> list[dict]:
    data = _fetch_json(
        f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}",
    )
    if not data:
        return []
    posts = (data.get("data") or {}).get("children", [])
    items = []
    for p in posts:
        pd    = p.get("data", {})
        title = pd.get("title", "")
        body  = pd.get("selftext", "") or ""
        link  = "https://reddit.com" + (pd.get("permalink") or "")
        ts    = pd.get("created_utc", 0)
        date  = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        score = pd.get("score", 0)
        clf   = classify_item(title, body[:600])
        clf["priority"] = max(clf["priority"], min(3, score // 300 + 1))
        items.append(_norm_item(f"r/{subreddit}", title, link, date, clf))
    return items

def fetch_all_reddit(limit_per_sub: int = 12) -> list[dict]:
    items = []
    for sub, lim in _SUBREDDITS:
        try:
            items += fetch_reddit_sub(sub, min(lim, limit_per_sub))
        except Exception:
            pass
    return items

# -- DeFiLlama hacks ---------------------------------------------------------

def fetch_defillama_hacks(limit: int = 10) -> list[dict]:
    """Fetch recent hacks/exploits from DeFiLlama."""
    data = _fetch_json("https://defillama.com/api/hacks")
    if not isinstance(data, list):
        # Try alternate endpoint
        data = _fetch_json("https://defi-explorers.vercel.app/api/hacks")
    if not isinstance(data, list):
        return []
    items = []
    for h in sorted(data, key=lambda x: x.get("date", 0), reverse=True)[:limit]:
        name   = h.get("name") or h.get("project", "Unknown project")
        amount = h.get("amount") or h.get("fundsLost", 0)
        method = h.get("technique") or h.get("classification", "exploit")
        date   = h.get("date", "")
        if isinstance(date, (int, float)) and date > 1e9:
            date = datetime.fromtimestamp(date, tz=timezone.utc).strftime("%Y-%m-%d")
        amount_str = f"${amount:,.0f}" if amount else "unknown amount"
        title = f"🚨 {name} exploited — {amount_str} lost via {method}"
        clf = {"category": "hack", "priority": 5, "tokens": _extract_tokens(name)}
        items.append(_norm_item("DeFiLlama Hacks", title, "https://defillama.com/hacks", str(date), clf))
    return items

# -- CoinGecko trending + movers --------------------------------------------

def fetch_coingecko_trending() -> list[dict]:
    data = _fetch_json("https://api.coingecko.com/api/v3/search/trending")
    if not data:
        return []
    items = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for c in (data.get("coins") or [])[:8]:
        item   = c.get("item", {})
        sym    = item.get("symbol", "").upper()
        name   = item.get("name", "")
        rank   = item.get("market_cap_rank") or "?"
        score  = item.get("score", 0)
        pri    = 3 if score < 3 else 4
        clf    = {"category": "memecoin" if sym in _KNOWN_TOKENS and
                  sym in {"DOGE","SHIB","PEPE","BONK","WIF","FLOKI","BRETT","TOSHI","MOCHI","POPCAT","TURBO"}
                  else "trending", "priority": pri, "tokens": [sym] if sym else []}
        title  = f"🔥 {name} ({sym}) trending — rank #{rank}"
        url    = f"https://www.coingecko.com/en/coins/{item.get('id','')}"
        items.append(_norm_item("CoinGecko Trending", title, url, today, clf))
    return items

def fetch_coingecko_categories() -> list[dict]:
    """Top-moving DeFi/memecoin categories from CoinGecko."""
    data = _fetch_json(
        "https://api.coingecko.com/api/v3/coins/categories?order=market_cap_change_24h_desc"
    )
    if not isinstance(data, list):
        return []
    items = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    interesting_cats = [
        "meme-token","decentralized-finance-defi","liquid-staking-tokens",
        "restaking","real-world-assets-rwa","yield-farming","layer-2",
    ]
    for cat in data[:20]:
        cat_id = cat.get("id", "")
        if not any(ic in cat_id for ic in interesting_cats):
            continue
        name   = cat.get("name", "")
        chg    = cat.get("market_cap_change_24h") or 0
        vol    = cat.get("volume_24h") or 0
        arrow  = "📈" if chg >= 0 else "📉"
        title  = f"{arrow} {name} category: {chg:+.1f}% 24h, ${vol:,.0f} volume"
        clf    = classify_item(name, "")
        clf["priority"] = max(clf["priority"], 2 if abs(chg) > 5 else 1)
        items.append(_norm_item("CoinGecko Categories", title,
                                f"https://www.coingecko.com/en/categories/{cat_id}",
                                today, clf))
        if len(items) >= 5:
            break
    return items

# -- Coindesk RSS -----------------------------------------------------------

def fetch_coindesk_rss(limit: int = 10) -> list[dict]:
    text = _fetch_text("https://www.coindesk.com/arc/outboundfeeds/rss/")
    if not text:
        return []
    try:
        root  = ET.fromstring(text)
        items = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            desc  = (item.findtext("description") or "").strip()
            pub   = (item.findtext("pubDate") or "")[:16]
            if not title:
                continue
            clf = classify_item(title, desc)
            items.append(_norm_item("CoinDesk", title, link, today, clf))
            if len(items) >= limit:
                break
        return items
    except Exception:
        return []

# -- Fear & Greed Index -----------------------------------------------------

def fetch_fear_greed() -> Optional[dict]:
    data = _fetch_json("https://api.alternative.me/fng/?limit=1")
    if not data:
        return None
    d = (data.get("data") or [{}])[0]
    return {
        "value": int(d.get("value", 0)),
        "label": d.get("value_classification", "Unknown"),
    }

# ────────────────────────────────────────────────────────────────────────────
# Master fetch + de-dup + sort
# ────────────────────────────────────────────────────────────────────────────

def fetch_all(
    *,
    min_priority: int = 1,
    category_filter: Optional[str] = None,
    limit_per_source: int = 15,
) -> dict:
    items: list[dict] = []

    items += fetch_cryptopanic("hot",   limit_per_source)
    items += fetch_cryptopanic_rising(  limit_per_source)
    items += fetch_all_reddit(          limit_per_source)
    items += fetch_coingecko_trending()
    items += fetch_coingecko_categories()
    items += fetch_coindesk_rss(        limit_per_source)
    items += fetch_defillama_hacks(     8)

    # De-duplicate by normalised title slug
    seen: set[str] = set()
    unique: list[dict] = []
    for it in items:
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(it)

    # Filter
    if category_filter and category_filter != "all":
        unique = [i for i in unique if i["category"] == category_filter]
    unique = [i for i in unique if i["priority"] >= min_priority]

    # Sort: priority desc, date desc
    unique.sort(key=lambda x: (x["priority"], x["date"]), reverse=True)

    return {
        "items": unique,
        "fear_greed": fetch_fear_greed(),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

# ────────────────────────────────────────────────────────────────────────────
# Telegram formatter
# ────────────────────────────────────────────────────────────────────────────

_CAT_EMOJI = {
    "hack":    "🚨",
    "rug":     "💀",
    "staking": "🥩",
    "yield":   "💰",
    "memecoin":"🐸",
    "defi":    "⚗️",
    "reward":  "🎁",
    "onchain": "🔗",
    "trending":"🔥",
    "general": "📰",
}

_CAT_LABEL = {
    "hack":    "HACKS & EXPLOITS",
    "rug":     "RUG PULLS & SCAMS",
    "staking": "STAKING ISSUES",
    "yield":   "YIELD / DeFi ISSUES",
    "memecoin":"MEMECOIN NEWS",
    "defi":    "DeFi GENERAL",
    "reward":  "REWARDS & AIRDROPS",
    "onchain": "ON-CHAIN SIGNALS",
    "trending":"TRENDING TOKENS",
    "general": "GENERAL CRYPTO",
}

_PRIORITY_ICON = {1: "▪️", 2: "🔵", 3: "🟡", 4: "🟠", 5: "🔴"}

_CAT_ORDER = ["hack","rug","staking","yield","memecoin","defi",
              "reward","onchain","trending","general"]


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_for_telegram(
    items: list[dict],
    fear_greed: Optional[dict],
    fetched_at: str,
    header: str,
    max_items: int = 25,
) -> list[str]:
    lines: list[str] = [header]
    lines.append(f"🕐 {fetched_at}")
    if fear_greed:
        v   = fear_greed["value"]
        lbl = fear_greed["label"]
        bar = "🟢" if v >= 60 else ("🟡" if v >= 40 else "🔴")
        lines.append(f"{bar} Fear &amp; Greed: <b>{v}/100</b> — {lbl}")
    lines.append("")

    by_cat: dict[str, list[dict]] = {}
    for it in items[:max_items]:
        by_cat.setdefault(it["category"], []).append(it)

    for cat in _CAT_ORDER:
        group = by_cat.get(cat, [])
        if not group:
            continue
        emoji = _CAT_EMOJI.get(cat, "📌")
        label = _CAT_LABEL.get(cat, cat.upper())
        lines.append(f"{emoji} <b>{label}</b>")
        for it in group:
            pri   = _PRIORITY_ICON.get(it["priority"], "▪️")
            title = _esc(it["title"][:160])
            url   = it.get("url", "")
            tok   = (" <i>" + " ".join(f"${t}" for t in it["tokens"][:5]) + "</i>") if it["tokens"] else ""
            src   = _esc(it.get("source", ""))
            date  = it.get("date", "")
            if url:
                lines.append(f"{pri} <a href='{url}'>{title}</a>{tok}")
            else:
                lines.append(f"{pri} {title}{tok}")
            lines.append(f"    └ {src} · {date}")
        lines.append("")

    if not by_cat:
        lines.append("No items matched the current filter.")

    # Chunk into ≤4000-char messages
    chunks: list[str] = []
    current = ""
    for line in lines:
        addition = line + "\n"
        if len(current) + len(addition) > 4000:
            if current:
                chunks.append(current.rstrip())
            current = addition
        else:
            current += addition
    if current.strip():
        chunks.append(current.rstrip())

    return chunks or ["No crypto news found."]


# ────────────────────────────────────────────────────────────────────────────
# Public entry point
# ────────────────────────────────────────────────────────────────────────────

_HEADERS = {
    None:       "📡 <b>Crypto Intelligence Report</b>",
    "all":      "📡 <b>Crypto Intelligence Report</b>",
    "hack":     "🚨 <b>Hacks, Exploits &amp; Security Incidents</b>",
    "rug":      "💀 <b>Rug Pulls &amp; Scams</b>",
    "staking":  "🥩 <b>Staking Issues &amp; Validator Incidents</b>",
    "yield":    "💰 <b>Yield &amp; DeFi Issues</b>",
    "memecoin": "🐸 <b>Memecoin News &amp; Launches</b>",
    "defi":     "⚗️ <b>DeFi News</b>",
    "reward":   "🎁 <b>Rewards, Airdrops &amp; Campaigns</b>",
    "onchain":  "🔗 <b>On-Chain Signals &amp; Whale Moves</b>",
    "trending": "🔥 <b>Trending Tokens</b>",
    "complaint":"🚨 <b>Crypto Alerts — Hacks, Scams &amp; Issues</b>",
}


def build_digest(
    category_filter: Optional[str] = None,
    min_priority: int = 1,
    max_items: int = 25,
) -> tuple[list[str], int]:
    """
    Fetch + classify + format.
    category_filter: None/"all"/"hack"/"rug"/"staking"/"yield"/"memecoin"/
                     "defi"/"reward"/"onchain"/"trending"/"complaint"
    Returns (message_chunks, item_count).
    """
    # Map legacy/alias filter names
    _alias = {
        "complaint": "hack", "complaints": "hack", "alert": "hack",
        "alerts": "hack", "scam": "rug", "scams": "rug",
        "rewards": "reward", "airdrops": "reward",
        "meme": "memecoin", "memecoins": "memecoin",
        "stake": "staking", "staking": "staking",
        "yields": "yield", "defi": "defi",
        "onchain": "onchain", "whale": "onchain",
        "trending": "trending",
    }
    cat = _alias.get(category_filter or "", category_filter)
    if cat == "all":
        cat = None

    data = fetch_all(
        min_priority=min_priority,
        category_filter=cat,
    )
    header = _HEADERS.get(cat, _HEADERS[None])
    chunks = format_for_telegram(
        data["items"], data["fear_greed"], data["fetched_at"],
        header=header, max_items=max_items,
    )
    return chunks, len(data["items"])
