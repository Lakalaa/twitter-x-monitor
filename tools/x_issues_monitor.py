"""
x_issues_monitor.py

PRIORITY ORDER (what gets sent to Telegram first):
  1. User complaint replies — random users replying to project/community posts
     (staking stuck, tx failed, can't withdraw, asking for help, etc.)
  2. Official account urgent posts — exploits, outages, hacks from monitored accounts
  3. Official account trending posts — price news, new listings, governance

The key insight: we monitor ECOSYSTEM PROJECTS built on chains — pump.fun,
Raydium, Magic Eden, meme coins, NFT communities, gaming projects, TON apps, etc.
Users complain under THOSE community posts, not under @solana or @ethereum.

New projects launch constantly. Dynamic discovery via CoinGecko trending refreshes
the account pool every 4 hours automatically.
"""
from __future__ import annotations
import json
import re
import time
import logging
import urllib.request
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
#
# FOCUS: ecosystem projects (DEXes, launchpads, NFT markets, gaming, meme coins)
# that have active user communities complaining about issues.
# ─────────────────────────────────────────────────────────────────────────────

_ACCOUNTS: dict[str, list[str]] = {

    # ── Exchange support — users tag these with every complaint ──────────────
    "exchanges": [
        "BinanceHelpDesk", "binance", "CoinbaseSupport", "coinbase",
        "Bybit_CS", "Bybit_Official", "KrakenSupport", "krakenfx",
        "OKXSupport", "OKX", "GateioHelp", "gate_io",
        "HTXGlobal_Help", "HTX_Global", "BitstampSupport",
        "CoinExSupport", "KucoinSupport", "mexc_global",
        "cryptocom_cares", "crypto_com", "gemini", "bitfinex",
        "upbit_official", "BithumbCS",
    ],

    # ── Wallet / infra support ────────────────────────────────────────────
    "wallets": [
        "MetaMask_Support", "MetaMask", "TrustWalletApp", "TrustWallet",
        "LedgerSupport", "Ledger", "phantom", "RainbowWallet",
        "safe", "WalletConnect", "Trezor", "CoinbaseWallet",
        "AlchemyPlatform", "infura_io", "QuickNode", "Rabby_io",
        "exodus", "AtomicWallet", "okx_wallet",
    ],

    # ── Liquid staking / yield — users complain here about locked stake ───
    "staking": [
        "LidoFinance", "RocketPool", "staderlabs", "ankr",
        "EigenLayer", "ether_fi", "KelpDAO", "StakeWise",
        "pStake_", "frxETH_", "MarinadeFinance", "enzyme_finance",
        "jito_sol", "sanctumso", "solayerlabs",
    ],

    # ── Bridges — stuck funds are the #1 complaint ───────────────────────
    "bridges": [
        "StargateFinance", "LayerZero_Core", "HopProtocol",
        "AcrossProtocol", "Connext", "deBridgeFinance",
        "MultichainOrg", "SocketDotTech", "orbiter_finance",
        "wormhole", "portalbridge", "cbridge_celer",
        "synapse_proto", "RenProject",
    ],

    # ── Solana DEX / DeFi — biggest user bases on Solana ─────────────────
    "solana_dex": [
        "RaydiumProtocol", "OrcaProtocol", "JupiterExchange",
        "MeteoraAG", "drift_trade", "KaminoFinance",
        "MangoMarkets", "solendprotocol", "MarinadeFinance",
        "SaberProtocol", "squadsprotocol", "heliuslabs",
        "lifinity_io", "GooseFX_", "ZetaMarkets",
    ],

    # ── pump.fun & Solana launchpads — new tokens launch here daily ───────
    "solana_launch": [
        "pumpdotfun", "moonshot_money", "letscookfi",
        "boop_fun", "believe_app", "launchcoin_",
        "bonkbot_io", "BullX_io", "trojanOnSolana",
    ],

    # ── Solana NFT marketplaces — users complain about listings, royalties ─
    "solana_nft": [
        "MagicEden", "tensor_hq", "SolanaFloor",
        "MonkeDAO", "SolanaMonkeyBiz", "okay_bears",
        "DeGodsNFT", "y00tsNFT", "CoralCubeNFT",
        "hyperspace_xyz",
    ],

    # ── Solana meme & community tokens — huge user communities ───────────
    "solana_meme": [
        "bonk_inu", "dogwifcoin", "book_of_meme",
        "moodengcoin", "goattoken_", "ACTonsolana",
        "fartcoin_sol", "POPCAT_",
    ],

    # ── Solana gaming & lifestyle ─────────────────────────────────────────
    "solana_gaming": [
        "StarAtlas", "StepNofficial", "helium",
        "Hivemapper", "aurory_game", "StarbirdGG",
        "GeneticChain",
    ],

    # ── ETH DeFi — high-value, frequent stuck-tx complaints ───────────────
    "ethereum": [
        "ethereum", "ethstatus", "AaveAave", "Uniswap",
        "MakerDAO", "CurveFinance", "compoundfinance",
        "BalancerLabs", "1inchNetwork", "dYdX",
        "fraxfinance", "ConvexFinance", "iearnfinance",
        "paraswap", "BancorNetwork", "KyberNetwork",
        "pendle_fi", "sparkdotfi",
    ],

    # ── ETH NFT communities — users complain about failed mints, transfers ─
    "eth_nft": [
        "BoredApeYC", "AzukiOfficial", "pudgypenguins",
        "doodles", "proof_xyz", "coolcatsnft",
        "CryptoPunksBot", "CloneXOfficial", "rtfkt",
        "NFTfi", "blur_io",
    ],

    # ── BNB Chain / BSC ecosystem ─────────────────────────────────────────
    "bnb": [
        "BNBCHAIN", "binance", "PancakeSwap", "VenusProtocol",
        "BiswapDEX", "ApeSwapFinance", "alpacafinance",
        "ellipsis_fi", "FourMeme_BNB",
    ],

    # ── Base ecosystem — fast-growing, many new projects ─────────────────
    "base": [
        "base", "BuildOnBase", "jessepollak", "AerodromeFinance",
        "MorphoLabs", "BaseSwap_fi", "moonwell_fi",
        "seamlessprotocol_", "BasePaint_xyz", "friendtech",
        "SyndicateDAO", "virtuals_io",
    ],

    # ── Arbitrum ecosystem ────────────────────────────────────────────────
    "arbitrum": [
        "arbitrum", "GMX_IO", "camelotdex", "pendle_fi",
        "y2kfinance", "PlutusDAO_", "dopex_io",
        "RyskFinance", "SpartacusDAO",
    ],

    # ── Optimism ecosystem ────────────────────────────────────────────────
    "optimism": [
        "optimismFND", "Optimism", "VelodromeFi", "synthetix_io",
        "QiDaoProtocol", "pika_protocol", "lyrafinance",
    ],

    # ── Polygon ecosystem ─────────────────────────────────────────────────
    "polygon": [
        "0xPolygon", "QuickswapDEX", "aavegotchi",
        "SushiSwap", "dfyn_network",
    ],

    # ── Other L2 / ZK — many new users, unfamiliar tech = more complaints ─
    "layer2": [
        "zksync", "Starknet", "Scroll_ZKP", "LineaBuild",
        "MetisDAO", "BlastL2", "modenetwork",
        "taiko_xyz", "manta_network", "ancient8io",
        "eclipsefnd", "MantleBlockchain",
    ],

    # ── Avalanche / Subnets ───────────────────────────────────────────────
    "avalanche": [
        "avalancheavax", "AvaLabs", "BenqiFinance",
        "traderjoe_xyz", "CoreDaoOrg", "dexalot",
        "GoGoPool_",
    ],

    # ── TON ecosystem — exploding user base from Telegram mini-apps ───────
    "ton": [
        "ton_blockchain", "tonkeeper", "notcoin_dog",
        "HamsterKombat_io", "STONfi", "dedust_io",
        "getgems_io", "TonRaffles", "cryptobotFAQ",
        "Blum_crypto", "major", "dogs", "catizen_tg",
    ],

    # ── Ronin / Axie / Sky Mavis ──────────────────────────────────────────
    "ronin": [
        "Ronin_Network", "AxieInfinity", "SkyMavisHQ",
        "ronin_wallet", "roninchain", "katana_dex", "Pixels_",
        "Apeiron_Game", "ZeroRanger_",
    ],

    # ── Cosmos ecosystem ──────────────────────────────────────────────────
    "cosmos": [
        "cosmos", "OsmosisZone", "keplr_wallet", "stride_zone",
        "neutron_org", "dydx", "celestia", "dymension_xyz",
    ],

    # ── XRP / XRPL ───────────────────────────────────────────────────────
    "xrp": [
        "xrpledger", "Ripple", "XRPcommunity", "XRPHealthCheck",
        "xrpl_org", "XUMM_app", "sologenic", "xaman_app",
    ],

    # ── LTC ──────────────────────────────────────────────────────────────
    "litecoin": [
        "LTCFoundation", "litecoin", "LitecoinCore", "SatoshiLite",
    ],

    # ── Major meme coins — huge communities, users frequently complain ────
    "meme": [
        "dogecoin", "Shibtoken", "pepecoineth", "FlokiInu",
        "dogelon", "ShibaInuHodler", "baby_doge",
        "BONK_Coin", "notcoin_dog", "pepe",
    ],

    # ── Multi-chain gaming — users complain about in-game assets/tokens ──
    "gaming": [
        "decentraland", "TheSandboxGame", "Gala_Games",
        "immutable", "Illuvium", "PlantvsUndead_",
        "YGG_DAO", "GuildFi", "MOBOX_Official",
        "monsterGalaxy_", "CrabadaGame",
    ],

    # ── AI / new narrative projects — growing user bases ─────────────────
    "ai_crypto": [
        "bittensor_", "grass_io", "io_net",
        "RenderToken", "akash_network", "FetchAI_",
        "virtuals_io", "ai16zdao", "elizaOS_",
    ],

    # ── Alt-L1 chains ─────────────────────────────────────────────────────
    "altl1": [
        "Polkadot", "SuiNetwork", "aptos_network",
        "nearprotocol", "StellarOrg", "TronFoundation",
        "Cardano", "Algorand", "sei_network",
        "MovementLabsXYZ", "monad_xyz",
    ],

    # ── Security / exploit alert ──────────────────────────────────────────
    "security": [
        "PeckShieldAlert", "BeosinAlert", "BlockSecTeam",
        "CertiKCommunity", "SlowMist_Team", "immunefi",
        "AnciliaInc", "tayvano_", "samczsun", "Mudit__Gupta",
    ],

    # ── News / market ─────────────────────────────────────────────────────
    "market": [
        "WatcherGuru", "lookonchain", "whale_alert",
        "CoinDesk", "Cointelegraph", "rektHQ", "DeFiant_",
        "DefiLlama",
    ],

    # ── Bitcoin ───────────────────────────────────────────────────────────
    "bitcoin": [
        "saylor", "BitcoinMagazine", "Bitcoin", "jack", "lopp",
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Dynamic account discovery via CoinGecko
# Fetches trending/top-volume projects and extracts their Twitter handles.
# Refreshes every 4 hours — captures NEW projects automatically.
# ─────────────────────────────────────────────────────────────────────────────

_DYNAMIC_ACCOUNTS: list[str] = []
_DYNAMIC_LAST_REFRESH: float = 0.0
_DYNAMIC_REFRESH_INTERVAL: float = 4 * 3600  # 4 hours


def _refresh_dynamic_accounts() -> None:
    """
    Pull trending coins from CoinGecko and add their Twitter handles to the
    dynamic pool. This automatically picks up new projects (meme coins, gaming
    tokens, AI narratives) as they trend, without manual list updates.
    """
    global _DYNAMIC_ACCOUNTS, _DYNAMIC_LAST_REFRESH
    now = time.time()
    if now - _DYNAMIC_LAST_REFRESH < _DYNAMIC_REFRESH_INTERVAL:
        return

    handles: list[str] = []
    existing_lower = {a.lower() for accs in _ACCOUNTS.values() for a in accs}

    try:
        req = urllib.request.Request(
            "https://api.coingecko.com/api/v3/search/trending",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())
        slugs = [c["item"]["id"] for c in data.get("coins", [])[:15]]

        for slug in slugs:
            try:
                req2 = urllib.request.Request(
                    f"https://api.coingecko.com/api/v3/coins/{slug}"
                    "?localization=false&tickers=false&market_data=false"
                    "&community_data=false&developer_data=false",
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                )
                with urllib.request.urlopen(req2, timeout=8) as r2:
                    coin = json.loads(r2.read())
                tw = (coin.get("links") or {}).get("twitter_screen_name", "")
                if tw and tw.lower() not in existing_lower and tw not in handles:
                    handles.append(tw)
            except Exception:
                pass
            time.sleep(1.5)

    except Exception as e:
        logging.warning(f"x_issues_monitor: CoinGecko dynamic discovery error: {e}")

    if handles:
        _DYNAMIC_ACCOUNTS = handles
        _DYNAMIC_LAST_REFRESH = now
        logging.info(f"x_issues_monitor: dynamic accounts refreshed — {len(handles)} new: {handles}")

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

# ── TWO-PART COMPLAINT DETECTION ─────────────────────────────────────────────
# A real complaint requires BOTH: personal ownership language AND a problem word.
# This filters OUT market discussion ("Goldman backs crypto bill"), news commentary
# ("what's the chance it passes?"), and general chat — while keeping in tweets
# like "my withdrawal is stuck 3 days" or "I can't unstake my ETH".

# Part 1: personal ownership / first-person context
_HAS_PERSONAL_RE = re.compile(
    r"\b(my|our|i\b|i\'ve|i\'m|i\'d|i have|i had|i sent|i tried|"
    r"we\'ve|we\'re|we have|we had|"
    r"\bme\b|mine|myself|myself|our\s+funds?|our\s+account|our\s+wallet)\b",
    re.IGNORECASE,
)

# Part 2: problem / stuck / distress words
_HAS_PROBLEM_RE = re.compile(
    r"\b("
    # Stuck / pending
    r"stuck|pending|pending\s+for|delayed|not\s+(?:processed|credited|arrived|received|showing)|"
    r"never\s+(?:arrived|received|got|credited)|didn.?t\s+(?:arrive|receive|credit)|"
    # Failed transactions
    r"fail(?:ed)?|revert(?:ed)?|reject(?:ed)?|"
    # Missing funds
    r"missing|lost|gone|disappear(?:ed)?|"
    # Access issues
    r"locked|frozen|frozen\s+out|suspended|blocked|banned|can.?t\s+access|locked\s+out|"
    # Can't do action
    r"can.?t|cannot|couldn.?t|unable\s+to|not\s+(?:able|working)|"
    # Support not responding
    r"no\s+response|zero\s+response|not\s+responding|ignor(?:ed|ing)|no\s+reply|"
    r"been\s+waiting|still\s+waiting|waiting\s+(?:\d+|\w+\s+)\s*(?:day|hour|week)|"
    r"(?:day|hour|week)s?\s+(?:and\s+)?(?:still|no|without)|"
    # Withdrawal / deposit
    r"withdraw(?:al)?|deposit\s+(?:fail|stuck|not)|"
    # Staking problems
    r"unstake|can.?t\s+stake|staking\s+(?:issue|problem|fail|stuck)|"
    # Lost access / recovery
    r"lost\s+access|lost\s+(?:my\s+)?(?:funds?|money|coins?|tokens?|eth|btc|sol|xrp)|"
    # Refund / compensation
    r"refund|compensat(?:e|ion)|reimburs(?:e|ement)|"
    # Specific action failures
    r"wrong\s+(?:address|network|amount|chain)|sent\s+to\s+wrong|"
    r"double\s+(?:charged|deducted)|charged\s+twice|overcharged"
    r")\b",
    re.IGNORECASE,
)

# HELP QUESTION — someone asking how to fix their specific situation
# (even without explicit "stuck/failed" — just "how do I withdraw?" is valid)
_HELP_QUESTION_RE = re.compile(
    r"(?:"
    r"how\s+(?:do|can|to)\s+(?:i|we|one)\s+\w+|"         # how do I / how can I
    r"where\s+(?:is|are)\s+my\s+\w+|"                      # where is my [thing]
    r"why\s+(?:is|isn.?t|won.?t|didn.?t|hasn.?t|haven.?t)\s+my|"  # why isn't my
    r"why\s+(?:is|isn.?t|won.?t)\s+(?:my|the)\s+\w+\s+(?:still|not)|"
    r"anyone\s+(?:know|help|else\s+having)|"                # anyone know/help
    r"(?:please|pls)\s+help\s+(?:me|us)|"                  # please help me
    r"i\s+need\s+(?:help|support|assistance)\s+with|"       # I need help with
    r"urgent(?:ly)?\s+(?:need|require|please)"              # urgently need
    r")",
    re.IGNORECASE,
)

def _is_complaint(text: str) -> bool:
    """
    Returns True if the tweet sounds like a real personal complaint or help request.
    Requires EITHER:
      (a) personal pronoun + a problem word — "my withdrawal is stuck"
      (b) a direct help question about their own situation — "how do I unstake?"
    """
    if _HAS_PERSONAL_RE.search(text) and _HAS_PROBLEM_RE.search(text):
        return True
    if _HELP_QUESTION_RE.search(text):
        return True
    return False

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
    "exchanges":     "🏛️ EXCHANGE",
    "wallets":       "👛 WALLET",
    "staking":       "🔒 STAKING / YIELD",
    "bridges":       "🌉 BRIDGE",
    "solana_dex":    "◎  SOLANA DEX",
    "solana_launch": "🚀 SOLANA LAUNCH (pump.fun etc)",
    "solana_nft":    "🖼️  SOLANA NFT",
    "solana_meme":   "🐶 SOLANA MEME",
    "solana_gaming": "🎮 SOLANA GAMING",
    "ronin":         "🎮 RONIN / AXIE",
    "solana":        "◎  SOLANA",
    "litecoin":      "Ł  LITECOIN",
    "xrp":           "✕  XRP / XRPL",
    "base":          "🔵 BASE",
    "arbitrum":      "🔵 ARBITRUM",
    "optimism":      "🔴 OPTIMISM",
    "polygon":       "🟣 POLYGON",
    "layer2":        "⚡ LAYER-2",
    "bnb":           "🟡 BNB CHAIN",
    "avalanche":     "🔺 AVALANCHE",
    "ethereum":      "⟠  ETHEREUM / DEFI",
    "eth_nft":       "🖼️  ETH NFT",
    "cosmos":        "⚛️  COSMOS",
    "ton":           "💎 TON ECOSYSTEM",
    "meme":          "🐸 MEME COIN",
    "gaming":        "🎮 GAMING",
    "ai_crypto":     "🤖 AI / COMPUTE",
    "altl1":         "🔵 ALT-CHAIN",
    "security":      "🚨 SECURITY",
    "market":        "📊 MARKET",
    "bitcoin":       "₿  BITCOIN",
    "misc":          "🔥 CRYPTO",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_spam(text: str) -> bool:
    return bool(_SPAM_RE.search(text))

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

    # Refresh CoinGecko trending accounts (no-op if < 4 hours since last refresh)
    _refresh_dynamic_accounts()

    session  = _make_session(auth, ct0)
    cache    = _load_user_id_cache()
    cutoff   = time.time() - 48 * 3600

    # ── Step 1: Fetch official/project account tweets ─────────────────────
    # Combine static list + dynamic CoinGecko trending accounts
    all_accounts_to_scan = _ALL_ACCOUNTS + [
        h for h in _DYNAMIC_ACCOUNTS if h.lower() not in _seen_set
    ]

    official_tweets: list[dict] = []  # (tweet dict with extra "source_cat" key)
    global_seen: set[str] = set(seen_ids)

    for screen_name in all_accounts_to_scan:
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
    # Use TweetDetail on ALL fetched official tweets — not sorted by popularity.
    # A tweet with 2 likes from @MetaMask support still has real users replying
    # with issues. We want those low-engagement replies just as much as viral ones.
    # Shuffle so every account gets a fair chance across cycles.
    import random
    reply_sources = list(official_tweets)
    random.shuffle(reply_sources)
    # Cap at 60 to control API usage per cycle (each call = 1 TweetDetail request)
    reply_sources = reply_sources[:60]

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
        # In a reply context: accept ANY complaint/question language.
        # We don't require crypto cashtags or trending keywords — a reply saying
        # "how do I withdraw?" or "my transfer is stuck" is enough.
        if not _is_complaint(text):
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
