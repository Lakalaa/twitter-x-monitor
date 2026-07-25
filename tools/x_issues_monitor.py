"""
x_issues_monitor.py

PRIORITY ORDER (what gets sent to Telegram first):
  1. User complaint replies — random users replying to official posts with issues
     (staking stuck, tx failed, can't withdraw, asking for help, etc.)
  2. Official account urgent posts — exploits, outages, hacks from monitored accounts
  3. Official account trending posts — price news, new listings, governance

"User complaint replies" are fetched via TweetDetail on the most-engaged official
tweets. These replies come from ANY user on X — the regular community members
tagging support and complaining about issues. This is the primary signal.

Networks: ETH, BTC, Solana, BNB, Base, Polygon, Arbitrum, Optimism, Ronin/Axie,
          LTC, XRP/XRPL, Cosmos, Avalanche, zkSync, Starknet, TON, NEAR, Sui,
          Aptos, Algorand, Stellar, Cardano, Tron, Fantom
"""
from __future__ import annotations
import re
import time
import logging
from typing import Optional

from x_scraper import (
    fetch_tweet_replies,
    _make_session,
    _load_creds,
    _load_user_id_cache,
    _save_user_id_cache,
    get_user_id,
    fetch_user_tweets,
    _parse_twitter_date,
)

# ─────────────────────────────────────────────────────────────────────────────
# Accounts to monitor — only used as reply-scrape SOURCES
# We read their tweets so we can then fetch the replies underneath them.
# The replies (from any random user) are what we actually care about most.
# ─────────────────────────────────────────────────────────────────────────────

_ACCOUNTS: dict[str, list[str]] = {

    # ── Exchange support — users tag these with every complaint ───────────────
    "exchanges": [
        "BinanceHelpDesk", "binance", "CoinbaseSupport", "coinbase",
        "Bybit_CS", "Bybit_Official", "KrakenSupport", "krakenfx",
        "OKXSupport", "OKX", "GateioHelp", "gate_io",
        "HTXGlobal_Help", "HTX_Global", "BitstampSupport",
        "CoinExSupport", "KucoinSupport", "mexc_global",
        "cryptocom_cares", "crypto_com",
    ],

    # ── Wallet / infra support ─────────────────────────────────────────────
    "wallets": [
        "MetaMask_Support", "MetaMask", "TrustWalletApp", "TrustWallet",
        "LedgerSupport", "Ledger", "phantom", "RainbowWallet",
        "safe", "WalletConnect", "Trezor", "CoinbaseWallet",
        "AlchemyPlatform", "infura_io", "QuickNode", "Rabby_io",
    ],

    # ── Liquid staking / yield — users complain here about locked stake ────
    "staking": [
        "LidoFinance", "RocketPool", "staderlabs", "ankr",
        "EigenLayer", "ether_fi", "KelpDAO", "StakeWise",
        "pStake_", "frxETH_", "MarinadeFinance", "enzyme_finance",
    ],

    # ── Bridges — common source of stuck funds complaints ─────────────────
    "bridges": [
        "StargateFinance", "LayerZero_Core", "HopProtocol",
        "AcrossProtocol", "Connext", "deBridgeFinance",
        "MultichainOrg", "SocketDotTech", "orbiter_finance",
    ],

    # ── Ronin / Axie / Sky Mavis ───────────────────────────────────────────
    "ronin": [
        "Ronin_Network", "AxieInfinity", "SkyMavisHQ",
        "ronin_wallet", "roninchain", "katana_dex", "Pixels_",
    ],

    # ── Solana ecosystem ──────────────────────────────────────────────────
    "solana": [
        "solana", "SolanaStatus", "phantom",
        "JupiterExchange", "solendprotocol", "MangoMarkets",
        "RaydiumProtocol", "OrcaProtocol", "drift_trade",
    ],

    # ── LTC ────────────────────────────────────────────────────────────────
    "litecoin": [
        "LTCFoundation", "litecoin", "LitecoinCore", "SatoshiLite",
    ],

    # ── XRP / XRPL ────────────────────────────────────────────────────────
    "xrp": [
        "xrpledger", "Ripple", "XRPcommunity", "XRPHealthCheck",
        "xrpl_org", "XUMM_app", "sologenic",
    ],

    # ── Base ──────────────────────────────────────────────────────────────
    "base": [
        "base", "BuildOnBase", "jessepollak", "AerodromeFinance",
        "MorphoLabs", "BaseSwap_fi",
    ],

    # ── Arbitrum ──────────────────────────────────────────────────────────
    "arbitrum": [
        "arbitrum", "GMX_IO", "camelotdex",
    ],

    # ── Optimism ──────────────────────────────────────────────────────────
    "optimism": [
        "optimismFND", "Optimism", "VelodromeFi", "synthetix_io",
    ],

    # ── Polygon ───────────────────────────────────────────────────────────
    "polygon": [
        "0xPolygon", "QuickswapDEX",
    ],

    # ── Other L2 / ZK ─────────────────────────────────────────────────────
    "layer2": [
        "zksync", "Starknet", "Scroll_ZKP", "LineaBuild",
        "MetisDAO", "BlastL2", "modenetwork",
    ],

    # ── BNB / BSC ─────────────────────────────────────────────────────────
    "bnb": [
        "BNBCHAIN", "binance", "PancakeSwap", "VenusProtocol",
    ],

    # ── Avalanche / Subnets ───────────────────────────────────────────────
    "avalanche": [
        "avalancheavax", "AvaLabs", "BenqiFinance",
        "traderjoe_xyz", "CoreDaoOrg", "dexalot",
    ],

    # ── Ethereum / DeFi ───────────────────────────────────────────────────
    "ethereum": [
        "ethereum", "ethstatus", "AaveAave", "Uniswap",
        "MakerDAO", "CurveFinance", "compoundfinance",
    ],

    # ── Cosmos ────────────────────────────────────────────────────────────
    "cosmos": [
        "cosmos", "OsmosisZone", "keplr_wallet", "stride_zone",
        "neutron_org",
    ],

    # ── TON ───────────────────────────────────────────────────────────────
    "ton": [
        "ton_blockchain", "tonkeeper",
    ],

    # ── Other alt-L1 ──────────────────────────────────────────────────────
    "altl1": [
        "Polkadot", "SuiNetwork", "aptos_network",
        "nearprotocol", "StellarOrg", "TronFoundation",
        "Cardano", "Algorand",
    ],

    # ── Security / exploit alert ───────────────────────────────────────────
    "security": [
        "PeckShieldAlert", "BeosinAlert", "BlockSecTeam",
        "CertiKCommunity", "SlowMist_Team", "immunefi",
        "AnciliaInc", "tayvano_", "samczsun", "Mudit__Gupta",
    ],

    # ── News / market — for trending posts ────────────────────────────────
    "market": [
        "WatcherGuru", "lookonchain", "whale_alert",
        "CoinDesk", "Cointelegraph", "rektHQ", "DeFiant_",
        "DefiLlama",
    ],

    # ── Bitcoin ────────────────────────────────────────────────────────────
    "bitcoin": [
        "saylor", "BitcoinMagazine", "Bitcoin", "jack", "lopp",
    ],
}

# Flat deduped list
_ALL_ACCOUNTS: list[str] = []
_seen_set: set[str] = set()
for _accs in _ACCOUNTS.values():
    for _a in _accs:
        if _a.lower() not in _seen_set:
            _seen_set.add(_a.lower())
            _ALL_ACCOUNTS.append(_a)

# Account → category
_ACCOUNT_TO_CAT: dict[str, str] = {}
for _cat, _accs in _ACCOUNTS.items():
    for _a in _accs:
        _ACCOUNT_TO_CAT[_a.lower()] = _cat

# ─────────────────────────────────────────────────────────────────────────────
# Keyword patterns
# ─────────────────────────────────────────────────────────────────────────────

_CASHTAG_RE = re.compile(r"\$[A-Z]{2,10}\b")

# USER COMPLAINT — any of these in a reply = this is a user reporting a problem.
# Intentionally broad: we want to catch any complaint, even vague ones.
_COMPLAINT_RE = re.compile(
    r"\b("
    # Distress / help requests
    r"help|please help|need help|anyone help|can.?t|cannot|not working|"
    r"not able|unable|no response|still waiting|waiting for|support ticket|"
    r"contacted|reached out|no reply|ignoring|"
    # Transaction / fund issues
    r"stuck|pending|failed|revert|reverted|not.?credited|missing|lost|gone|"
    r"can.?t withdraw|withdrawal|deposit.?fail|not.?received|not.?showing|"
    r"didn.?t arrive|never arrived|"
    # Staking / yield
    r"unstake|unstaking|staking.?issue|locked|stake.?lock|"
    r"yield|apy|reward|not.?earning|earning.?nothing|"
    r"validator|slash|penalt|"
    # Locked / frozen
    r"frozen|freeze|suspend|account.?lock|lock|access|"
    r"can.?t access|blocked|banned|"
    # Money / fund issues
    r"fund|balance|money|amount|token|coin|"
    r"wrong amount|wrong balance|overcharged|charged.?twice|"
    # Bridge / cross-chain
    r"bridge|bridging|cross.?chain|transfer|"
    # Wallet / gas
    r"gas|nonce|approval|signature|"
    r"wallet|address|"
    # Error language
    r"error|bug|glitch|issue|problem|broken|"
    r"why.?is|what.?happened|what.?is.?going|"
    # General complaint language
    r"horrible|terrible|worst|scam|rip.?off|"
    r"refund|compensation|"
    # Network-specific issue language
    r"ronin|litecoin|ltc|xrp|xrpl|solana|sol|base|subnet|"
    r"arbitrum|optimism|polygon|zksync|bnb|bsc"
    r")\b",
    re.IGNORECASE,
)

# OFFICIAL URGENT — for official account posts only (exploits, outages, hacks)
_OFFICIAL_URGENT_RE = re.compile(
    r"\b("
    r"exploit|hack|hacked|rug|rug.?pull|vulnerability|vuln|"
    r"emergency|paused|circuit.?breaker|incident|outage|down|"
    r"warning|alert|caution|critical|"
    r"drained|stolen|breach|"
    r"maintenance|degraded|investigating"
    r")\b",
    re.IGNORECASE,
)

# TRENDING — for official account posts (news, price, listings)
_TRENDING_RE = re.compile(
    r"\b("
    r"bitcoin|ethereum|solana|bnb|crypto|defi|nft|token|blockchain|"
    r"ronin|litecoin|ltc|xrp|xrpl|"
    r"staking|yield|protocol|layer2|l2|rollup|subnet|"
    r"dex|listing|launch|upgrade|fork|halving|etf|"
    r"btc|eth|sol|usdt|usdc|matic|avax|xrp|doge|"
    r"trending|ath|pump|breakout|bull|bear|"
    r"airdrop|governance|vote|whale|"
    r"just.in|breaking|alert|just.announced"
    r")\b",
    re.IGNORECASE,
)

# Spam — always exclude
_SPAM_RE = re.compile(
    r"\b("
    r"giveaway|give away|free.?token|claim your|"
    r"100x guaranteed|follow.?win|retweet.?win|rt.?win|"
    r"dm for.?profit|signal.?group|vip.?signal|"
    r"t\.me/\+|join.?channel|referral.?code|"
    r"get back your|recover your|fund.?recover|"
    r"contact.?recovery|recovery.?agent|"
    r"i lost.* and got it back|i was scammed.* and recovered"
    r")\b",
    re.IGNORECASE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Category headers
# ─────────────────────────────────────────────────────────────────────────────

_CAT_HEADER = {
    "exchanges": "🏛️ EXCHANGE",
    "wallets":   "👛 WALLET",
    "staking":   "🔒 STAKING / YIELD",
    "bridges":   "🌉 BRIDGE",
    "ronin":     "🎮 RONIN / AXIE",
    "solana":    "◎  SOLANA",
    "litecoin":  "Ł  LITECOIN",
    "xrp":       "✕  XRP / XRPL",
    "base":      "🔵 BASE",
    "arbitrum":  "🔵 ARBITRUM",
    "optimism":  "🔴 OPTIMISM",
    "polygon":   "🟣 POLYGON",
    "layer2":    "⚡ LAYER-2",
    "bnb":       "🟡 BNB CHAIN",
    "avalanche": "🔺 AVALANCHE",
    "ethereum":  "⟠  ETHEREUM / DEFI",
    "cosmos":    "⚛️  COSMOS",
    "ton":       "💎 TON",
    "altl1":     "🔵 ALT-CHAIN",
    "security":  "🚨 SECURITY",
    "market":    "📊 MARKET",
    "bitcoin":   "₿  BITCOIN",
    "misc":      "🔥 CRYPTO",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_spam(text: str) -> bool:
    return bool(_SPAM_RE.search(text))

def _is_complaint(text: str) -> bool:
    return bool(_COMPLAINT_RE.search(text))

def _is_official_urgent(text: str) -> bool:
    return bool(_OFFICIAL_URGENT_RE.search(text))

def _is_trending(text: str) -> bool:
    return bool(_CASHTAG_RE.search(text) or _TRENDING_RE.search(text))

def extract_tokens(text: str) -> list[str]:
    seen, out = set(), []
    for t in _CASHTAG_RE.findall(text):
        if t.upper() not in seen:
            seen.add(t.upper())
            out.append(t)
    return out

def _cat_from_user(user: str) -> str:
    return _ACCOUNT_TO_CAT.get(user.lower(), "misc")

def _cat_from_parent(parent_user: str) -> str:
    """Derive category from the account being replied to."""
    return _ACCOUNT_TO_CAT.get(parent_user.lower(), "misc")


# ─────────────────────────────────────────────────────────────────────────────
# Main fetch — three buckets, user complaints always first
# ─────────────────────────────────────────────────────────────────────────────

def fetch_issues(
    seen_ids: Optional[set] = None,
    per_account: int = 20,
) -> list[dict]:
    """
    Fetch and classify crypto content from X.

    Returns THREE buckets merged in priority order:
      Bucket A — user_complaint replies (highest priority)
                 Any user replying under official posts with complaint language.
                 These are the community members asking for help / reporting issues.
      Bucket B — official account urgent posts (second)
                 Exploits, outages, hacks from monitored accounts.
      Bucket C — official account trending posts (last)
                 Price news, listings, governance, market moves.
    """
    seen_ids = seen_ids or set()

    auth, ct0 = _load_creds()
    if not auth or not ct0:
        logging.warning("x_issues_monitor: no credentials")
        return []

    session  = _make_session(auth, ct0)
    cache    = _load_user_id_cache()
    cutoff   = time.time() - 48 * 3600

    # ── Step 1: Fetch official account tweets ─────────────────────────────
    official_tweets: list[dict] = []  # (tweet dict with extra "source_cat" key)
    global_seen: set[str] = set(seen_ids)

    for screen_name in _ALL_ACCOUNTS:
        uid = get_user_id(screen_name, session, cache)
        if not uid:
            continue
        tweets = fetch_user_tweets(uid, screen_name, session, count=per_account)
        for t in tweets:
            tid = t.get("id", "")
            if not tid or tid in global_seen:
                continue
            ts = _parse_twitter_date(t.get("date", ""))
            if ts and ts < cutoff:
                continue
            if not t.get("user"):
                t["user"] = screen_name
            t["url"] = f"https://x.com/{t['user']}/status/{tid}"
            t["source_cat"] = _cat_from_user(t["user"])
            global_seen.add(tid)
            official_tweets.append(t)
        time.sleep(0.4)

    # ── Step 2: Fetch reply threads ────────────────────────────────────────
    # For each official tweet, use TweetDetail to get replies from ANY user.
    # Sort by most replies/engagement first (those attract the most user complaints).
    # Cap at 40 source tweets to control API usage.
    reply_sources = sorted(
        official_tweets,
        key=lambda t: t.get("likes", 0) + t.get("retweets", 0),
        reverse=True,
    )[:40]

    user_reply_tweets: list[dict] = []  # replies from random community users

    for src in reply_sources:
        src_id  = src.get("id", "")
        src_cat = src.get("source_cat", "misc")
        src_user = src.get("user", "")
        if not src_id:
            continue
        replies = fetch_tweet_replies(src_id, session, max_age_hours=48)
        for r in replies:
            rid = r.get("id", "")
            if not rid or rid in global_seen:
                continue
            r_user = r.get("user", "")
            if not r_user:
                continue
            # Skip if the reply is from the same official account (self-replies)
            if r_user.lower() == src_user.lower():
                continue
            global_seen.add(rid)
            # Tag with context: who they're replying to
            r["reply_to_user"] = src_user
            r["reply_to_cat"]  = src_cat
            r["url"]           = f"https://x.com/{r_user}/status/{rid}"
            user_reply_tweets.append(r)
        time.sleep(0.4)

    _save_user_id_cache(cache)

    # ── Step 3: Classify ──────────────────────────────────────────────────

    bucket_a: list[dict] = []  # user complaint replies  ← PRIORITY
    bucket_b: list[dict] = []  # official urgent
    bucket_c: list[dict] = []  # official trending

    # --- Process user reply tweets (Bucket A) ---
    for t in user_reply_tweets:
        text = t.get("text", "")
        tid  = t.get("id", "")
        if not text or tid in seen_ids:
            continue
        if _is_spam(text):
            continue
        # Accept: any complaint language OR any crypto-relevant content
        # (in a reply context, even light mentions are worth surfacing)
        if not (_is_complaint(text) or _is_trending(text) or _CASHTAG_RE.search(text)):
            continue
        bucket_a.append({
            "type":          "user_complaint",
            "category":      t.get("reply_to_cat", "misc"),
            "reply_to_user": t.get("reply_to_user", ""),
            "tweet_id":      tid,
            "text":          text[:500],
            "url":           t.get("url", ""),
            "date":          t.get("date", ""),
            "user":          t.get("user", ""),
            "likes":         t.get("likes", 0),
            "retweets":      t.get("retweets", 0),
            "tokens":        extract_tokens(text),
            "urgent":        _is_complaint(text),
        })

    # --- Process official account tweets (Buckets B + C) ---
    for t in official_tweets:
        text = t.get("text", "")
        tid  = t.get("id", "")
        if not text or tid in seen_ids:
            continue
        if _is_spam(text):
            continue
        entry = {
            "type":          "official",
            "category":      t.get("source_cat", "misc"),
            "reply_to_user": "",
            "tweet_id":      tid,
            "text":          text[:500],
            "url":           t.get("url", ""),
            "date":          t.get("date", ""),
            "user":          t.get("user", ""),
            "likes":         t.get("likes", 0),
            "retweets":      t.get("retweets", 0),
            "tokens":        extract_tokens(text),
            "urgent":        _is_official_urgent(text),
        }
        if _is_official_urgent(text):
            bucket_b.append(entry)
        elif _is_trending(text):
            bucket_c.append(entry)

    # Sort each bucket
    # A: complaint replies — most recent first (issues are time-sensitive)
    bucket_a.sort(key=lambda x: _parse_twitter_date(x.get("date", "")), reverse=True)
    # B: official urgent — by engagement
    bucket_b.sort(key=lambda x: x["likes"] + x["retweets"], reverse=True)
    # C: official trending — by engagement
    bucket_c.sort(key=lambda x: x["likes"] + x["retweets"], reverse=True)

    return bucket_a + bucket_b + bucket_c


# ─────────────────────────────────────────────────────────────────────────────
# Async wrapper
# ─────────────────────────────────────────────────────────────────────────────

async def afetch_issues(
    scraper=None, categories=None,
    seen_ids: Optional[set] = None,
    per_query_count: int = 20,
) -> list[dict]:
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: fetch_issues(seen_ids=seen_ids, per_account=per_query_count)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Telegram formatting
# ─────────────────────────────────────────────────────────────────────────────

def format_issue_for_telegram(item: dict) -> str:
    itype      = item.get("type", "official")
    cat        = item.get("category", "misc")
    urgent     = item.get("urgent", False)
    reply_to   = item.get("reply_to_user", "")
    header     = _CAT_HEADER.get(cat, "🔥 CRYPTO")
    text       = item.get("text", "")
    url        = item.get("url", "")
    date       = item.get("date", "")
    user       = item.get("user", "")
    tokens     = item.get("tokens", [])
    likes      = item.get("likes", 0)
    rts        = item.get("retweets", 0)

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Choose prefix based on type and urgency
    if itype == "user_complaint":
        prefix = "🆘" if urgent else "💬"
    elif urgent:
        prefix = "⚠️"
    else:
        prefix = "📌"

    lines = [f"{prefix} <b>{header}</b>"]

    tok_line = " ".join(f"<b>{esc(t)}</b>" for t in tokens[:5])
    if tok_line:
        lines.append(tok_line)

    # For user complaint replies, show who they're replying to (context)
    if itype == "user_complaint" and reply_to:
        lines.append(f'↩️ replying to <a href="https://x.com/{reply_to}">@{esc(reply_to)}</a>')

    if user:
        lines.append(f'👤 <a href="https://x.com/{user}">@{esc(user)}</a>')

    tweet_text = esc(text[:400])
    if url:
        lines.append(f'<a href="{url}">{tweet_text}</a>')
    else:
        lines.append(tweet_text)

    lines.append(f"❤️ {likes:,}  🔁 {rts:,}")
    if date:
        lines.append(f"<i>🕐 {date}</i>")

    return "\n".join(lines)
