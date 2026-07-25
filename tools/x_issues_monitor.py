"""
x_issues_monitor.py
Monitors a wide range of crypto X/Twitter accounts for:
  - User issues: staking stuck, trading failures, locked funds, withdrawal problems
  - Help requests from any user mentioning protocols
  - Trending tokens and price moves
  - DeFi/protocol issues, exploits, outages
  - Yield, locked staking, liquidity problems

Uses direct UserTweets GraphQL (works from datacenter IPs).
SearchTimeline is Cloudflare-blocked on cloud IPs — never use it.

Two output tiers:
  URGENT  — issues, hacks, stuck funds, help requests (low engagement threshold)
  TRENDING — market moves, token news (engagement-sorted)
"""
from __future__ import annotations
import re
from typing import Optional

from x_scraper import fetch_tweets_from_accounts

# ---------------------------------------------------------------------------
# Account list — curated for maximum crypto-issue coverage
# Covers: official protocols, exchanges, DeFi, support accounts,
#         aggregators, alert bots, community figures
# ---------------------------------------------------------------------------

_ACCOUNTS: dict[str, list[str]] = {

    # ── Bitcoin ──────────────────────────────────────────────────────────
    "bitcoin": [
        "saylor", "DocumentingBTC", "BitcoinMagazine", "Bitcoin",
        "jack", "lopp", "nvk", "pierre_rochard",
    ],

    # ── Ethereum core ────────────────────────────────────────────────────
    "ethereum": [
        "ethereum", "VitalikButerin", "TimBeiko", "sassal0x",
        "ultrasoundmoney", "evan_van_ness", "ethstatus",
    ],

    # ── Layer-2 / Scaling ─────────────────────────────────────────────────
    "layer2": [
        "arbitrum", "optimismFND", "0xPolygon", "Starknet",
        "zksync", "base", "Scroll_ZKP", "LineaBuild",
        "MetisDAO", "MantaNetwork",
    ],

    # ── DeFi protocols ────────────────────────────────────────────────────
    "defi": [
        "DefiLlama", "Uniswap", "AaveAave", "MakerDAO",
        "CurveFinance", "compoundfinance", "SushiSwap", "BalancerLabs",
        "dydx", "GMX_IO", "PancakeSwap", "QuickswapDEX",
        "dforce_network", "fraxfinance", "VenusProtocol",
        "BenqiFinance", "AlpacaFinance", "yearnfinance",
    ],

    # ── Staking / Liquid staking ──────────────────────────────────────────
    "staking": [
        "LidoFinance", "RocketPool", "staderlabs", "ankr",
        "frxETH_", "StakeWise", "pStake_",
        "EigenLayer", "ether_fi", "KelpDAO",
    ],

    # ── Bridges & cross-chain ─────────────────────────────────────────────
    "bridges": [
        "StargateFinance", "LayerZero_Core", "MultichainOrg",
        "HopProtocol", "AcrossProtocol", "Connext",
        "SymbiosisFinance", "deBridgeFinance",
    ],

    # ── CEX / Exchanges ───────────────────────────────────────────────────
    "exchanges": [
        "binance", "cz_binance", "coinbase", "Bybit_Official",
        "krakenfx", "OKX", "gate_io", "Bitfinex",
        "crypto_com", "mexc_global", "HTX_Global",
        "BitstampSupport", "CoinbaseSupport",
    ],

    # ── Wallets & infrastructure ──────────────────────────────────────────
    "wallets": [
        "MetaMask", "phantom", "TrustWallet", "RainbowWallet",
        "safe", "WalletConnect", "Ledger", "Trezor",
        "CoinbaseWallet", "AlchemyPlatform", "infura_io",
    ],

    # ── Alt-L1s ───────────────────────────────────────────────────────────
    "altl1": [
        "solana", "SolanaStatus", "SolanaFndn",
        "avalancheavax", "BNBCHAIN", "Polkadot",
        "cosmos", "TronFoundation", "SuiNetwork",
        "aptos_network", "Algorand", "nearprotocol",
        "Cardano", "Ripple", "StellarOrg",
    ],

    # ── Security / Alerts ────────────────────────────────────────────────
    "security": [
        "PeckShieldAlert", "BeosinAlert", "BlockSecTeam",
        "CertiKCommunity", "officer_cia", "SlowMist_Team",
        "hackenclub", "immunefi", "DeFiLlama_Hacks",
        "CryptoSecHub", "AnciliaInc",
    ],

    # ── Market & news aggregators ─────────────────────────────────────────
    "market": [
        "WatcherGuru", "lookonchain", "whale_alert",
        "CryptoCapo_", "inversebrah", "CryptoBull2020",
        "TheBlock__", "CoinDesk", "Cointelegraph",
        "CryptoSlate", "decrypt_co", "rektHQ",
        "DeFiant_", "unchainedcrypto",
    ],

    # ── Community / issues surface early here ─────────────────────────────
    "community": [
        "CryptoWhale", "BitcoinFear", "crypto_birb",
        "0xfoobar", "tayvano_", "bantg", "Dogetoshi",
        "MrBadCrypto", "CryptoMessiah", "cryptomanran",
    ],
}

# Flat list, deduplicated, preserving order
_ALL_ACCOUNTS: list[str] = []
_seen_set: set[str] = set()
for _accs in _ACCOUNTS.values():
    for _a in _accs:
        if _a.lower() not in _seen_set:
            _seen_set.add(_a.lower())
            _ALL_ACCOUNTS.append(_a)

# Account → category map
_ACCOUNT_TO_CAT: dict[str, str] = {}
for _cat, _accs in _ACCOUNTS.items():
    for _a in _accs:
        _ACCOUNT_TO_CAT[_a.lower()] = _cat

# ---------------------------------------------------------------------------
# Keyword patterns
# ---------------------------------------------------------------------------

_CASHTAG_RE = re.compile(r"\$[A-Z]{2,10}\b")

# URGENT patterns — any match = always forward, even low engagement
_URGENT_PATTERNS = re.compile(
    r"\b("
    # Help requests
    r"help|need help|anyone help|can.t|cannot|please help|support ticket|"
    r"contacted support|no response|not working|broken|down|outage|"
    # Transaction / fund issues
    r"stuck|pending|failed|revert|reverted|error|can.t withdraw|withdrawal.* fail|"
    r"won.t process|not.* credited|missing fund|lost fund|fund.* gone|"
    r"transaction fail|tx fail|tx.* stuck|stuck.*tx|"
    # Staking / yield issues
    r"can.t unstake|unstaking.* fail|staking.* issue|locked.*stake|stake.* locked|"
    r"validator.*slash|slash.*validator|slash.*penalt|"
    r"yield.*drop|yield.*gone|reward.*miss|reward.*not|apy.*wrong|"
    r"liquid.*issue|liquidity.*drain|"
    # Lock / freeze issues
    r"fund.* lock|lock.*fund|account.*lock|lock.*account|wallet.*block|"
    r"frozen|freeze|suspend|access.*denied|can.t access|"
    # Protocol / exchange issues
    r"exploit|hack|hacked|rug|rug.?pull|vulnerability|vuln|"
    r"emergency|pause|paused|circuit.breaker|"
    r"bug|glitch|wrong price|price.*error|oracle.*fail|liquidat.*wrong|"
    r"withdraw.*disabl|deposit.*disabl|trading.*halt|halt.*trading|"
    # Bridge issues
    r"bridge.*stuck|stuck.*bridge|bridge.*fail|cross.chain.*fail|"
    r"relayer|message.*fail|transfer.*stuck|"
    # Wallet / gas
    r"gas.*spike|gas.*too high|gas.*error|nonce.*error|"
    r"wallet.*drain|approval.*issue|infinite approval|"
    r"phish|scam alert|address poison"
    r")\b",
    re.IGNORECASE,
)

# TRENDING patterns — token/market news, lower priority
_TRENDING_PATTERNS = re.compile(
    r"\b("
    r"bitcoin|ethereum|solana|bnb|crypto|defi|nft|token|blockchain|"
    r"altcoin|staking|yield|protocol|layer2|l2|rollup|"
    r"dex|cex|wallet|listing|launch|upgrade|fork|halving|etf|"
    r"btc|eth|sol|usdt|usdc|matic|avax|dot|ada|xrp|doge|shib|pepe|"
    r"trending|ath|all.time.high|pump|breakout|bull|bear|"
    r"airdrop|snapshot|governance|vote|proposal"
    r")\b",
    re.IGNORECASE,
)

# Noise/spam filter — exclude these patterns entirely
_SPAM_PATTERNS = re.compile(
    r"\b("
    r"giveaway|give away|free.*token|token.*free|claim your|"
    r"100x guaranteed|follow.*win|retweet.*win|rt.*win|"
    r"dm for.*profit|signal.*group|vip.*signal|"
    r"ngl\.link|t\.me/\+|join.*channel|referral.*code"
    r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Category display headers
# ---------------------------------------------------------------------------

_CAT_HEADER = {
    "bitcoin":   "₿  BITCOIN",
    "ethereum":  "⟠  ETHEREUM",
    "layer2":    "⚡ LAYER-2",
    "defi":      "🏦 DEFI",
    "staking":   "🔒 STAKING / YIELD",
    "bridges":   "🌉 BRIDGE",
    "exchanges": "🏛️ EXCHANGE",
    "wallets":   "👛 WALLET",
    "altl1":     "🔵 ALT-CHAIN",
    "security":  "🚨 SECURITY",
    "market":    "📊 MARKET",
    "community": "💬 COMMUNITY",
    "misc":      "🔥 CRYPTO",
}

_URGENT_PREFIX = "🆘"
_ISSUE_PREFIX  = "⚠️"


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _is_spam(text: str) -> bool:
    return bool(_SPAM_PATTERNS.search(text))


def _is_urgent(text: str) -> bool:
    return bool(_URGENT_PATTERNS.search(text))


def _is_trending(text: str) -> bool:
    return bool(_CASHTAG_RE.search(text) or _TRENDING_PATTERNS.search(text))


def extract_tokens(text: str) -> list[str]:
    seen, out = set(), []
    for t in _CASHTAG_RE.findall(text):
        if t.upper() not in seen:
            seen.add(t.upper())
            out.append(t)
    return out


def _guess_category(tweet: dict) -> str:
    return _ACCOUNT_TO_CAT.get(tweet.get("user", "").lower(), "misc")


def fetch_issues(
    seen_ids: Optional[set] = None,
    per_account: int = 20,
) -> list[dict]:
    """
    Fetch tweets from all monitored accounts.
    Returns two tiers:
      - urgent: any tweet matching issue/help keywords (low engagement OK)
      - trending: crypto-relevant tweets sorted by engagement
    Merged list: urgent first, then trending (deduped).
    """
    seen_ids = seen_ids or set()
    raw = fetch_tweets_from_accounts(_ALL_ACCOUNTS, tweets_per_account=per_account, max_age_hours=48)

    urgent:   list[dict] = []
    trending: list[dict] = []
    used_ids: set[str]   = set()

    for tw in raw:
        tid  = tw.get("id", "")
        text = tw.get("text", "")
        if not tid or tid in seen_ids or not text:
            continue
        if _is_spam(text):
            continue

        tokens = extract_tokens(text)
        cat    = _guess_category(tw)
        entry  = {
            "category": cat,
            "tweet_id": tid,
            "text":     text[:500],
            "url":      tw.get("url", ""),
            "date":     tw.get("date", ""),
            "user":     tw.get("user", ""),
            "likes":    tw.get("likes", 0),
            "retweets": tw.get("retweets", 0),
            "tokens":   tokens,
            "urgent":   False,
        }

        if _is_urgent(text):
            entry["urgent"] = True
            urgent.append(entry)
            used_ids.add(tid)
        elif _is_trending(text):
            trending.append(entry)
            used_ids.add(tid)
        # else: skip (not relevant)

    # Sort each tier
    # Urgent: recency-biased (issues matter NOW) — sort by date desc, then engagement
    urgent.sort(key=lambda x: (x["likes"] + x["retweets"]), reverse=True)
    # Trending: highest engagement first
    trending.sort(key=lambda x: (x["likes"] + x["retweets"]), reverse=True)

    return urgent + trending


async def afetch_issues(
    scraper=None,
    categories=None,
    seen_ids: Optional[set] = None,
    per_query_count: int = 20,
) -> list[dict]:
    """Async wrapper — scraper arg kept for backward compat."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: fetch_issues(seen_ids=seen_ids, per_account=per_query_count)
    )


def format_issue_for_telegram(item: dict) -> str:
    cat    = item.get("category", "misc")
    urgent = item.get("urgent", False)
    header = _CAT_HEADER.get(cat, "🔥 CRYPTO")
    prefix = _URGENT_PREFIX if urgent else _ISSUE_PREFIX if _is_urgent(item.get("text","")) else "📌"
    text   = item.get("text", "")
    url    = item.get("url", "")
    date   = item.get("date", "")
    user   = item.get("user", "")
    tokens = item.get("tokens", [])
    likes  = item.get("likes", 0)
    rts    = item.get("retweets", 0)

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    tok_line = " ".join(f"<b>{esc(t)}</b>" for t in tokens[:6])

    lines = [
        f"{prefix} <b>{header}</b>",
    ]
    if tok_line:
        lines.append(tok_line)
    if user:
        lines.append(f'👤 <a href="https://x.com/{user}">@{esc(user)}</a>')
    if url:
        lines.append(f'<a href="{url}">{esc(text[:400])}</a>')
    else:
        lines.append(esc(text[:400]))
    lines.append(f"❤️ {likes:,}  🔁 {rts:,}")
    if date:
        lines.append(f"<i>🕐 {date}</i>")

    return "\n".join(lines)
