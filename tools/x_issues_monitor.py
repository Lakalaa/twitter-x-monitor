"""
x_issues_monitor.py

TWO-LAYER scraping approach:
  Layer 1 — Account tweets: fetch top-level posts from 200+ monitored accounts
             (protocols, exchanges, wallets, security, communities, all requested networks)
  Layer 2 — Reply threads: for support/community tweets that attract user complaints,
             fetch the reply thread via TweetDetail to capture real user messages
             from ANY account — not just official ones.

Networks covered: ETH, BTC, Solana, BNB, Base, Polygon, Arbitrum, Optimism,
  Ronin/Axie, LTC, XRP/XRPL, Cosmos, Avalanche subnets, zkSync, Starknet,
  TON, NEAR, Aptos, Sui, Algorand, Stellar, Cardano, Tron, Fantom, Harmony

Issue types caught:
  URGENT  — stuck tx, can't withdraw, locked funds, staking fails, bridge stuck,
             wallet drained, exploit/hack, oracle fail, gas error, any "help" request
  TRENDING — price moves, token news, new listings, governance, airdrops
"""
from __future__ import annotations
import re
import time
import logging
from typing import Optional

from x_scraper import (
    fetch_tweets_from_accounts,
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
# Account registry
# ─────────────────────────────────────────────────────────────────────────────

_ACCOUNTS: dict[str, list[str]] = {

    # ── Bitcoin ──────────────────────────────────────────────────────────────
    "bitcoin": [
        "saylor", "Strategy", "DocumentingBTC", "BitcoinMagazine", "Bitcoin",
        "jack", "lopp", "nvk", "pierre_rochard", "BTCFoundation",
        "bitstamp", "BitcoinCorePR",
    ],

    # ── Ethereum ─────────────────────────────────────────────────────────────
    "ethereum": [
        "ethereum", "VitalikButerin", "TimBeiko", "sassal0x",
        "ultrasoundmoney", "evan_van_ness", "ethstatus",
        "EF_ESP", "ethdotorg",
    ],

    # ── Base (Coinbase L2) ────────────────────────────────────────────────────
    "base": [
        "base", "BuildOnBase", "jessepollak", "basename_app",
        "BaseSwap_fi", "AerodromeFinance", "MorphoLabs",
    ],

    # ── Arbitrum ─────────────────────────────────────────────────────────────
    "arbitrum": [
        "arbitrum", "ArbitrumDAO", "OffchainLabs", "GMX_IO",
        "camelotdex", "TridentDAO",
    ],

    # ── Optimism ─────────────────────────────────────────────────────────────
    "optimism": [
        "optimismFND", "Optimism", "VelodromeFi",
        "synthetix_io",
    ],

    # ── Polygon ──────────────────────────────────────────────────────────────
    "polygon": [
        "0xPolygon", "sandeepnailwal", "jdkanani",
        "QuickswapDEX", "SUSHI",
    ],

    # ── zkSync / Starknet / other L2 ─────────────────────────────────────────
    "layer2": [
        "zksync", "Starknet", "Scroll_ZKP", "LineaBuild",
        "MetisDAO", "MantaNetwork", "BlastL2", "modenetwork",
        "xai_games",
    ],

    # ── Ronin / Axie / Sky Mavis ──────────────────────────────────────────────
    "ronin": [
        "Ronin_Network", "AxieInfinity", "SkyMavisHQ",
        "ronin_wallet", "katana_dex", "roninchain",
        "Pixels_", "YGG",
    ],

    # ── Solana ───────────────────────────────────────────────────────────────
    "solana": [
        "solana", "SolanaStatus", "SolanaFndn", "solana_devs",
        "phantom", "JupiterExchange", "solendprotocol",
        "MangoMarkets", "RaydiumProtocol", "OrcaProtocol",
        "drift_trade", "MarinadeFinance",
    ],

    # ── LTC (Litecoin) ───────────────────────────────────────────────────────
    "litecoin": [
        "LTCFoundation", "litecoin", "SatoshiLite",
        "LitecoinCore", "loshan1", "Twitchy_Fingers",
    ],

    # ── XRP / XRPL ───────────────────────────────────────────────────────────
    "xrp": [
        "Ripple", "xrpledger", "BradGarlinghouse", "JoelKatz",
        "XRPcommunity", "XRP_Productions", "XRPHealthCheck",
        "xrpl_org", "XUMM_app", "sologenic",
    ],

    # ── BNB / BSC ────────────────────────────────────────────────────────────
    "bnb": [
        "BNBCHAIN", "cz_binance", "binance", "PancakeSwap",
        "VenusProtocol", "AlpacaFinance", "BabydogeCoin",
    ],

    # ── Avalanche / Subnets ───────────────────────────────────────────────────
    "avalanche": [
        "avalancheavax", "AvaLabs", "BlizzardFund",
        "BenqiFinance", "traderjoe_xyz", "GMX_IO",
        "CoreDaoOrg", "dexalot",
    ],

    # ── Cosmos ecosystem ─────────────────────────────────────────────────────
    "cosmos": [
        "cosmos", "cosmoshub", "ibc_protocol",
        "OsmosisZone", "keplr_wallet", "stride_zone",
        "neutron_org", "celestia",
    ],

    # ── TON ──────────────────────────────────────────────────────────────────
    "ton": [
        "ton_blockchain", "TonCoin_official", "toncenter",
        "tonkeeper", "getgems_io",
    ],

    # ── Other alt-L1 ─────────────────────────────────────────────────────────
    "altl1": [
        "Polkadot", "TronFoundation", "SuiNetwork", "aptos_network",
        "Algorand", "nearprotocol", "Cardano", "StellarOrg",
        "FantomFDN", "harmonyprotocol", "elrondnetwork",
    ],

    # ── DeFi protocols ───────────────────────────────────────────────────────
    "defi": [
        "DefiLlama", "Uniswap", "AaveAave", "MakerDAO",
        "CurveFinance", "compoundfinance", "SushiSwap", "BalancerLabs",
        "dydx", "fraxfinance", "yearnfinance",
        "dforce_network", "SymbiosisFinance",
    ],

    # ── Liquid staking / yield ───────────────────────────────────────────────
    "staking": [
        "LidoFinance", "RocketPool", "staderlabs", "ankr",
        "EigenLayer", "ether_fi", "KelpDAO", "StakeWise",
        "pStake_", "frxETH_", "enzyme_finance",
    ],

    # ── Bridges ──────────────────────────────────────────────────────────────
    "bridges": [
        "StargateFinance", "LayerZero_Core", "HopProtocol",
        "AcrossProtocol", "Connext", "deBridgeFinance",
        "MultichainOrg", "SocketDotTech", "orbiter_finance",
        "symbiosis_fi",
    ],

    # ── CEX & support handles ─────────────────────────────────────────────────
    "exchanges": [
        "binance", "BinanceHelpDesk", "coinbase", "CoinbaseSupport",
        "Bybit_Official", "Bybit_CS", "krakenfx", "KrakenSupport",
        "OKX", "OKXSupport", "gate_io", "GateioHelp",
        "Bitfinex", "BitfinexSupport", "crypto_com", "cryptocom_cares",
        "mexc_global", "HTX_Global", "HTXGlobal_Help",
        "BitstampSupport", "CoinExSupport", "KucoinSupport",
    ],

    # ── Wallets & infra ──────────────────────────────────────────────────────
    "wallets": [
        "MetaMask", "MetaMask_Support", "phantom",
        "TrustWallet", "TrustWalletApp", "RainbowWallet",
        "safe", "WalletConnect", "Ledger", "LedgerSupport",
        "Trezor", "CoinbaseWallet", "AlchemyPlatform",
        "infura_io", "QuickNode", "Rabby_io",
    ],

    # ── Security / exploit alerts ────────────────────────────────────────────
    "security": [
        "PeckShieldAlert", "BeosinAlert", "BlockSecTeam",
        "CertiKCommunity", "officer_cia", "SlowMist_Team",
        "hackenclub", "immunefi", "AnciliaInc",
        "CryptoSecHub", "tayvano_", "0xfoobar",
        "Mudit__Gupta", "samczsun",
    ],

    # ── Market / news aggregators ────────────────────────────────────────────
    "market": [
        "WatcherGuru", "lookonchain", "whale_alert",
        "CryptoCapo_", "TheBlock__", "CoinDesk",
        "Cointelegraph", "decrypt_co", "rektHQ",
        "DeFiant_", "unchainedcrypto", "CryptoSlate",
        "CryptoNewsIO", "coincodecap",
    ],

    # ── Community & issue aggregators ────────────────────────────────────────
    "community": [
        "CryptoWhale", "bantg", "MrBadCrypto",
        "cryptomanran", "Excellion", "BitcoinFear",
        "crypto_birb", "GordonGoner", "Cobie",
        "DeFiGod1", "Route2FI",
    ],
}

# Flat deduped account list
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

# Accounts whose reply threads are most likely to contain user complaints.
# We will call TweetDetail on their recent tweets to surface replies from any user.
_REPLY_SCRAPE_ACCOUNTS = {
    # Exchange support — users tag these with complaints
    "binancehelpdesk", "coinbasesupport", "bybit_cs", "krakensupport",
    "okxsupport", "gateiohelp", "htxglobal_help", "bitstampsupport",
    "coinexsupport", "kucoinsupport", "cryptocom_cares",
    # Wallet support
    "metamask_support", "trustwalletapp", "ledgersupport",
    # Protocol accounts where users complain in replies
    "metamask", "lido finance", "rocketpool", "eigenlayer",
    "lidofinance", "aaveaave", "uniswap",
    # Network status accounts
    "solanastatus", "ethstatus", "ronin_network",
    # Security — users report hacks/losses in replies
    "peckshieldalert", "blockSecteam", "immunefi",
}

# ─────────────────────────────────────────────────────────────────────────────
# Keyword patterns
# ─────────────────────────────────────────────────────────────────────────────

_CASHTAG_RE = re.compile(r"\$[A-Z]{2,10}\b")

# URGENT — always forward regardless of likes count
_URGENT_RE = re.compile(
    r"\b("
    # Generic distress
    r"help|need help|please help|can.?t|cannot|anyone know|anyone help|"
    r"support ticket|no response|not working|broken|down|outage|"
    # Transaction issues
    r"stuck|pending forever|failed|revert|reverted|transaction fail|tx fail|"
    r"tx.?stuck|stuck.?tx|won.?t process|not.?credited|missing fund|lost fund|"
    r"fund.?gone|funds gone|can.?t withdraw|withdrawal.?fail|deposit.?fail|"
    r"withdraw.?stuck|deposit.?stuck|"
    # Staking / yield
    r"can.?t unstake|unstaking.?fail|staking.?issue|staking.?stuck|"
    r"locked.?stake|stake.?lock|validator.?slash|slash|penalt|"
    r"yield.?drop|yield.?gone|reward.?miss|reward.?not|apy.?wrong|"
    r"not receiving reward|reward.?delay|"
    # Locked / frozen funds
    r"fund.?lock|lock.?fund|account.?lock|lock.?account|wallet.?block|"
    r"frozen|freeze|suspend|access.?denied|can.?t access|"
    r"asset.?lock|balance.?wrong|balance.?missing|"
    # Protocol / exchange issues
    r"exploit|hack|hacked|rug|rug.?pull|vulnerability|vuln|"
    r"emergency|paused|circuit.?breaker|"
    r"bug|glitch|wrong price|price.?error|oracle.?fail|liquidat|"
    r"withdraw.?disabl|deposit.?disabl|trading.?halt|maintenance|"
    r"error code|getting error|error message|"
    # Bridge issues
    r"bridge.?stuck|bridge.?fail|cross.?chain.?fail|relayer|"
    r"message.?fail|transfer.?stuck|bridging.?issue|"
    # Wallet / gas / approval
    r"gas.?spike|gas.?too.?high|gas.?error|nonce.?error|"
    r"wallet.?drain|approval.?issue|infinite.?approval|"
    r"seed.?phrase|private.?key.?stolen|phish|address.?poison|"
    r"scam.?alert|warn.?everyone|"
    # Specific network issues
    r"ronin.?issue|ronin.?down|ronin.?stuck|"
    r"ltc.?issue|litecoin.?stuck|"
    r"xrp.?issue|xrpl.?error|ripple.?issue|"
    r"solana.?down|solana.?outage|sol.?issue|"
    r"base.?issue|base.?down|base.?stuck|"
    r"subnet.?issue|subnet.?down"
    r")\b",
    re.IGNORECASE,
)

# TRENDING — crypto news / market moves
_TRENDING_RE = re.compile(
    r"\b("
    r"bitcoin|ethereum|solana|bnb|crypto|defi|nft|token|blockchain|"
    r"ronin|litecoin|ltc|xrp|xrpl|ripple|cardano|ada|"
    r"altcoin|staking|yield|protocol|layer2|l2|rollup|subnet|"
    r"dex|cex|wallet|listing|launch|upgrade|fork|halving|etf|"
    r"btc|eth|sol|usdt|usdc|matic|avax|dot|doge|shib|pepe|"
    r"trending|ath|all.time.high|pump|breakout|bull|bear|"
    r"airdrop|snapshot|governance|vote|proposal|whale|"
    r"just.in|breaking|alert|just.announced"
    r")\b",
    re.IGNORECASE,
)

# Spam — hard exclude
_SPAM_RE = re.compile(
    r"\b("
    r"giveaway|give away|free.?token|token.?free|claim your|"
    r"100x guaranteed|follow.?win|retweet.?win|rt.?win|"
    r"dm for.?profit|signal.?group|vip.?signal|"
    r"ngl\.link|t\.me/\+|join.?channel|referral.?code|"
    r"contact.?admin|contact.?support.?dm|"
    r"get back your|recover your|fund.?recover"
    r")\b",
    re.IGNORECASE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Category headers
# ─────────────────────────────────────────────────────────────────────────────

_CAT_HEADER = {
    "bitcoin":   "₿  BITCOIN",
    "ethereum":  "⟠  ETHEREUM",
    "base":      "🔵 BASE",
    "arbitrum":  "🔵 ARBITRUM",
    "optimism":  "🔴 OPTIMISM",
    "polygon":   "🟣 POLYGON",
    "layer2":    "⚡ LAYER-2",
    "ronin":     "🎮 RONIN / AXIE",
    "solana":    "◎  SOLANA",
    "litecoin":  "Ł  LITECOIN",
    "xrp":       "✕  XRP / XRPL",
    "bnb":       "🟡 BNB CHAIN",
    "avalanche": "🔺 AVALANCHE",
    "cosmos":    "⚛️  COSMOS",
    "ton":       "💎 TON",
    "altl1":     "🔵 ALT-CHAIN",
    "defi":      "🏦 DEFI",
    "staking":   "🔒 STAKING / YIELD",
    "bridges":   "🌉 BRIDGE",
    "exchanges": "🏛️ EXCHANGE",
    "wallets":   "👛 WALLET",
    "security":  "🚨 SECURITY",
    "market":    "📊 MARKET",
    "community": "💬 COMMUNITY",
    "misc":      "🔥 CRYPTO",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_spam(text: str) -> bool:
    return bool(_SPAM_RE.search(text))


def _is_urgent(text: str) -> bool:
    return bool(_URGENT_RE.search(text))


def _is_trending(text: str) -> bool:
    return bool(_CASHTAG_RE.search(text) or _TRENDING_RE.search(text))


def extract_tokens(text: str) -> list[str]:
    seen, out = set(), []
    for t in _CASHTAG_RE.findall(text):
        if t.upper() not in seen:
            seen.add(t.upper())
            out.append(t)
    return out


def _guess_category(user: str) -> str:
    return _ACCOUNT_TO_CAT.get(user.lower(), "misc")


def _make_entry(tw: dict, urgent: bool, is_reply: bool = False) -> dict:
    user = tw.get("user", "")
    tid  = tw.get("id", "")
    return {
        "category": _guess_category(user),
        "tweet_id": tid,
        "text":     tw.get("text", "")[:500],
        "url":      tw.get("url", "") or (f"https://x.com/{user}/status/{tid}" if tid else ""),
        "date":     tw.get("date", ""),
        "user":     user,
        "likes":    tw.get("likes", 0),
        "retweets": tw.get("retweets", 0),
        "tokens":   extract_tokens(tw.get("text", "")),
        "urgent":   urgent,
        "is_reply": is_reply,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main fetch
# ─────────────────────────────────────────────────────────────────────────────

def fetch_issues(
    seen_ids: Optional[set] = None,
    per_account: int = 20,
) -> list[dict]:
    """
    Two-layer scrape:
      Layer 1 — top-level tweets from all monitored accounts
      Layer 2 — reply threads on support/community tweets (captures ANY user's complaint)

    Returns: urgent items first (no engagement floor), then trending sorted by engagement.
    """
    seen_ids = seen_ids or set()

    # ── Layer 1: account tweets ───────────────────────────────────────────────
    auth, ct0 = _load_creds()
    if not auth or not ct0:
        logging.warning("x_issues_monitor: no credentials")
        return []

    session = _make_session(auth, ct0)
    cache   = _load_user_id_cache()

    raw_tweets: list[dict] = []
    global_seen: set[str]  = set(seen_ids)

    for screen_name in _ALL_ACCOUNTS:
        uid = get_user_id(screen_name, session, cache)
        if not uid:
            continue
        tweets = fetch_user_tweets(uid, screen_name, session, count=per_account)
        cutoff = time.time() - 48 * 3600
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
            global_seen.add(tid)
            raw_tweets.append(t)
        time.sleep(0.4)

    # ── Layer 2: reply threads on support/community tweets ───────────────────
    # Pick tweets from accounts whose threads attract user complaints, then
    # fetch replies — these are real user messages from ANY account on X.
    reply_source_tweets = [
        t for t in raw_tweets
        if t.get("user", "").lower() in _REPLY_SCRAPE_ACCOUNTS
        or _is_urgent(t.get("text", ""))  # also expand threads on urgent posts
    ]
    # Limit to avoid hammering the API — take the 15 most recent
    reply_source_tweets = sorted(
        reply_source_tweets,
        key=lambda t: _parse_twitter_date(t.get("date", "")),
        reverse=True,
    )[:15]

    for src in reply_source_tweets:
        src_id = src.get("id", "")
        if not src_id:
            continue
        replies = fetch_tweet_replies(src_id, session, max_age_hours=48)
        for r in replies:
            rid = r.get("id", "")
            if not rid or rid in global_seen:
                continue
            if not r.get("user"):
                continue
            global_seen.add(rid)
            raw_tweets.append(r)
        time.sleep(0.4)

    _save_user_id_cache(cache)

    # ── Classify ──────────────────────────────────────────────────────────────
    urgent:   list[dict] = []
    trending: list[dict] = []

    for tw in raw_tweets:
        text = tw.get("text", "")
        tid  = tw.get("id", "")
        if not text or tid in seen_ids:
            continue
        if _is_spam(text):
            continue

        is_reply = tw.get("is_reply", False)
        if _is_urgent(text):
            urgent.append(_make_entry(tw, urgent=True, is_reply=is_reply))
        elif _is_trending(text):
            trending.append(_make_entry(tw, urgent=False, is_reply=is_reply))

    # Sort: urgent by date (newest first), trending by engagement
    urgent.sort(  key=lambda x: _parse_twitter_date(x.get("date", "")), reverse=True)
    trending.sort(key=lambda x: x["likes"] + x["retweets"], reverse=True)

    return urgent + trending


# ─────────────────────────────────────────────────────────────────────────────
# Async wrapper (backward compat)
# ─────────────────────────────────────────────────────────────────────────────

async def afetch_issues(
    scraper=None,
    categories=None,
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
    cat      = item.get("category", "misc")
    urgent   = item.get("urgent", False)
    is_reply = item.get("is_reply", False)
    header   = _CAT_HEADER.get(cat, "🔥 CRYPTO")
    text     = item.get("text", "")
    url      = item.get("url", "")
    date     = item.get("date", "")
    user     = item.get("user", "")
    tokens   = item.get("tokens", [])
    likes    = item.get("likes", 0)
    rts      = item.get("retweets", 0)

    if urgent:
        prefix = "🆘"
    elif is_reply:
        prefix = "💬"
    else:
        prefix = "📌"

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = [f"{prefix} <b>{header}</b>"]

    tok_line = " ".join(f"<b>{esc(t)}</b>" for t in tokens[:6])
    if tok_line:
        lines.append(tok_line)

    if is_reply:
        lines.append("↩️ <i>user reply</i>")

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
