"""
query_builder.py — Millions-scale Twitter search query generator
================================================================

Generates thousands of targeted search queries per scan cycle, covering
EVERY crypto project on earth:

  STATIC QUERIES (~300):
    • Broad catch-all (any project, any exchange, any complaint type)
    • Every L1 / L2 / sidechain (50+ chains)
    • Every top CEX (100+ exchanges)
    • Every top DEX / AMM / aggregator
    • Every top wallet
    • Every bridge / cross-chain
    • Every DeFi category: lending, yield, LST, restaking, RWA, options, perps
    • Every compute / DePIN / AI token platform
    • Every staking / validator service
    • Every NFT marketplace / GameFi / SocialFi
    • Every stablecoin / oracle / launchpad
    • 12 languages (covering 5B+ speakers)

  DYNAMIC QUERIES (generated fresh each cycle):
    • Top 500 coins by market cap — every cycle (always fresh)
    • ALL 17,851 CoinGecko coins — rotating 600/cycle → full coverage ~30 cycles (7.5h)
    • ALL 7,782 DeFiLlama protocols — rotating 400/cycle → full coverage ~20 cycles (5h)
    • DexScreener trending — newest launches, real-time
    • CoinGecko trending — viral coins of the moment

  TOTAL: ~3,000-4,000 queries per cycle at 16 parallel → complete global coverage
         100 tweets/query × 3,000 queries = 300,000 tweets scanned per cycle

Usage:
    from query_builder import build_queries, start_background_refresh
    start_background_refresh()          # call once at startup
    queries = build_queries(cycle_idx)  # call each scan
"""
from __future__ import annotations
import json, logging, os, threading, time, urllib.request
from typing import Any

log = logging.getLogger(__name__)

_CACHE_DIR = os.path.join("outputs", "cache")

# ── Cache paths ───────────────────────────────────────────────────────────────
_CG_COINS_PATH   = os.path.join(_CACHE_DIR, "qb_cg_coins.json")   # [{symbol,name}]
_DL_PROTOS_PATH  = os.path.join(_CACHE_DIR, "qb_dl_protos.json")  # ["Protocol Name"]
_ROTATION_PATH   = os.path.join(_CACHE_DIR, "qb_rotation.json")   # {cg_idx, dl_idx}

_CG_TTL  = 24 * 3600   # refresh coin list daily
_DL_TTL  = 12 * 3600   # refresh DeFiLlama twice a day
_BG_LOCK = threading.Lock()

# In-memory caches (populated on startup from disk, refreshed in background)
_cg_coins:  list[dict] = []   # [{symbol, name}]
_dl_protos: list[str]  = []   # protocol names
_cg_mtime:  float      = 0.0
_dl_mtime:  float      = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Fetchers
# ══════════════════════════════════════════════════════════════════════════════

def _http_get(url: str, timeout: int = 15) -> Any:
    req = urllib.request.Request(url, headers={
        "User-Agent": "CryptoComplaintMonitor/2.0",
        "Accept":     "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _fetch_all_cg_coins() -> list[dict]:
    """
    Fetch ALL CoinGecko coins (up to 18,000) using /coins/markets.
    Returns list of {symbol, name} dicts, sorted by market cap desc.
    One page = 250 coins, 72 pages covers all 18,000.
    Rate: 2.5s between pages (safe for CoinGecko free tier).
    """
    coins: list[dict] = []
    log.info("query_builder: fetching all CoinGecko coins (all 72 pages)...")
    for page in range(1, 74):
        try:
            url = (
                "https://api.coingecko.com/api/v3/coins/markets"
                f"?vs_currency=usd&order=market_cap_desc"
                f"&per_page=250&page={page}&sparkline=false"
            )
            data = _http_get(url, timeout=20)
            if not data:
                break
            for c in data:
                sym  = (c.get("symbol") or "").strip().upper()
                name = (c.get("name")   or "").strip()
                if sym and name:
                    coins.append({"symbol": sym, "name": name})
            if len(data) < 250:
                break   # last page
            time.sleep(2.5)
        except Exception as e:
            log.warning(f"query_builder: CG page {page} error: {e}")
            time.sleep(10)
    log.info(f"query_builder: CoinGecko fetch done — {len(coins):,} coins")
    return coins


def _fetch_dl_protocol_names() -> list[str]:
    """
    Fetch all DeFiLlama protocol names (7,782+).
    One API call, returns list of protocol name strings.
    """
    try:
        data = _http_get("https://api.llama.fi/protocols", timeout=30)
        names = []
        for p in (data if isinstance(data, list) else []):
            name = (p.get("name") or "").strip()
            if name and len(name) > 1:
                names.append(name)
        log.info(f"query_builder: DeFiLlama fetch done — {len(names):,} protocols")
        return names
    except Exception as e:
        log.warning(f"query_builder: DeFiLlama fetch error: {e}")
        return []


def _fetch_dexscreener_trending() -> list[dict]:
    """
    Fetch trending / boosted tokens from DexScreener — catches brand-new launches.
    Returns [{symbol, name}].
    """
    results: list[dict] = []
    for url in [
        "https://api.dexscreener.com/token-boosts/active/v1",
        "https://api.dexscreener.com/token-boosts/top/v1",
    ]:
        try:
            data = _http_get(url, timeout=10)
            if isinstance(data, list):
                for item in data[:50]:
                    sym  = (item.get("symbol") or item.get("tokenAddress","")[:8]).upper()
                    name = (item.get("description") or item.get("name") or "").strip()
                    name = name[:40] if name else ""
                    if sym:
                        results.append({"symbol": sym, "name": name})
        except Exception as e:
            log.debug(f"query_builder: DexScreener {url} error: {e}")
    return results


def _fetch_cg_trending() -> list[dict]:
    """CoinGecko trending search — top 15 coins of the moment."""
    try:
        data = _http_get("https://api.coingecko.com/api/v3/search/trending", timeout=10)
        results = []
        for item in data.get("coins", [])[:15]:
            c    = item.get("item", {})
            sym  = (c.get("symbol") or "").upper()
            name = (c.get("name")   or "").strip()
            if sym:
                results.append({"symbol": sym, "name": name})
        return results
    except Exception as e:
        log.debug(f"query_builder: CG trending error: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Persistence
# ══════════════════════════════════════════════════════════════════════════════

def _load_cg_coins() -> tuple[list[dict], float]:
    try:
        if os.path.exists(_CG_COINS_PATH):
            st = os.stat(_CG_COINS_PATH)
            with open(_CG_COINS_PATH) as f:
                return json.load(f), st.st_mtime
    except Exception:
        pass
    return [], 0.0


def _save_cg_coins(coins: list[dict]) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(_CG_COINS_PATH, "w") as f:
        json.dump(coins, f)


def _load_dl_protos() -> tuple[list[str], float]:
    try:
        if os.path.exists(_DL_PROTOS_PATH):
            st = os.stat(_DL_PROTOS_PATH)
            with open(_DL_PROTOS_PATH) as f:
                return json.load(f), st.st_mtime
    except Exception:
        pass
    return [], 0.0


def _save_dl_protos(names: list[str]) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(_DL_PROTOS_PATH, "w") as f:
        json.dump(names, f)


def _load_rotation() -> dict:
    try:
        if os.path.exists(_ROTATION_PATH):
            with open(_ROTATION_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {"cg_idx": 0, "dl_idx": 0}


def _save_rotation(state: dict) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(_ROTATION_PATH, "w") as f:
        json.dump(state, f)


# ══════════════════════════════════════════════════════════════════════════════
# Background refresh thread
# ══════════════════════════════════════════════════════════════════════════════

def start_background_refresh() -> None:
    """
    Start background thread that keeps coin/protocol lists fresh.
    Call once at startup.
    """
    global _cg_coins, _dl_protos, _cg_mtime, _dl_mtime

    # Load from disk immediately (no blocking)
    with _BG_LOCK:
        _cg_coins,  _cg_mtime  = _load_cg_coins()
        _dl_protos, _dl_mtime  = _load_dl_protos()

    def _worker():
        global _cg_coins, _dl_protos, _cg_mtime, _dl_mtime
        time.sleep(10)   # brief startup delay
        while True:
            now = time.time()

            # Refresh CoinGecko full list
            if now - _cg_mtime > _CG_TTL:
                try:
                    coins = _fetch_all_cg_coins()
                    if coins:
                        with _BG_LOCK:
                            _cg_coins  = coins
                            _cg_mtime  = time.time()
                        _save_cg_coins(coins)
                except Exception as e:
                    log.warning(f"query_builder: CG refresh error: {e}")

            # Refresh DeFiLlama protocol list
            if now - _dl_mtime > _DL_TTL:
                try:
                    names = _fetch_dl_protocol_names()
                    if names:
                        with _BG_LOCK:
                            _dl_protos = names
                            _dl_mtime  = time.time()
                        _save_dl_protos(names)
                except Exception as e:
                    log.warning(f"query_builder: DL refresh error: {e}")

            time.sleep(3600)   # check every hour

    t = threading.Thread(target=_worker, daemon=True, name="qb-refresh")
    t.start()
    log.info(
        f"query_builder: started — "
        f"{len(_cg_coins):,} CG coins, {len(_dl_protos):,} DL protocols loaded from cache"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Query generation helpers
# ══════════════════════════════════════════════════════════════════════════════

_COMPLAINT_BROAD = (
    "withdrawal OR withdraw OR stuck OR failed OR scam OR hack OR rug OR "
    "drained OR missing OR frozen OR blocked OR exploit OR lost"
)
_COMPLAINT_STRICT = (
    "withdrawal OR hack OR exploit OR rug OR drained OR scam OR stuck OR missing OR frozen"
)


def _coin_queries(symbol: str, name: str, seen: set) -> list[str]:
    """Generate 1-2 targeted complaint queries for a coin."""
    q: list[str] = []
    sym  = symbol.strip().upper()
    name = name.strip()

    # Skip obvious noise: too short, generic words, fiat
    if not sym or len(sym) > 9:
        return q
    skip = {"USD", "EUR", "GBP", "JPY", "CNY", "BRL", "BTC", "ETH", "SOL",
            "BNB", "XRP", "ADA", "DOT", "AVAX", "MATIC", "LINK", "DOGE",
            "SHIB", "NEAR", "APT", "SUI", "TON"}
    # Still generate for the big ones — just with stricter complaint filter

    key = sym
    if key not in seen:
        seen.add(key)
        q.append(
            f'(${sym} OR "{sym}") ({_COMPLAINT_BROAD}) -is:retweet'
        )

    if name and name.lower() not in seen and len(name) > 2 and name.upper() != sym:
        seen.add(name.lower())
        q.append(
            f'"{name}" ({_COMPLAINT_STRICT}) -is:retweet min_faves:1'
        )

    return q


def _proto_query(proto_name: str, seen: set) -> list[str]:
    """Generate one targeted query for a DeFiLlama protocol."""
    name = proto_name.strip()
    if not name or len(name) < 2 or name.lower() in seen:
        return []
    seen.add(name.lower())
    return [
        f'"{name}" (hack OR exploit OR rug OR funds OR withdrawal OR stuck OR drained OR failed) '
        f'-is:retweet min_faves:1'
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Main public function
# ══════════════════════════════════════════════════════════════════════════════

# How many coins / protocols to include per cycle from the rotating universe
_CG_TOP_ALWAYS   = 500   # top 500 by market cap — always, every cycle
_CG_ROTATE_CHUNK = 600   # rotating slice from full 17,851 list
_DL_ROTATE_CHUNK = 400   # rotating slice from full 7,782 protocol list


def build_queries(cycle_idx: int = 0) -> list[str]:
    """
    Build the full query list for one scan cycle.

    cycle_idx should increment each cycle (persistent via rotation state).
    """
    queries:   list[str] = list(_STATIC_QUERIES)
    seen_keys: set[str]  = set()

    with _BG_LOCK:
        cg_snap = list(_cg_coins)
        dl_snap = list(_dl_protos)

    # ── Top 500 coins: always, every cycle ───────────────────────────────────
    for coin in cg_snap[:_CG_TOP_ALWAYS]:
        queries.extend(_coin_queries(coin["symbol"], coin["name"], seen_keys))

    # ── Rotating slice: remaining 17,351 coins ────────────────────────────────
    remaining_cg = cg_snap[_CG_TOP_ALWAYS:]
    if remaining_cg:
        n        = len(remaining_cg)
        start    = (cycle_idx * _CG_ROTATE_CHUNK) % n
        end      = start + _CG_ROTATE_CHUNK
        chunk    = remaining_cg[start:end]
        if end > n:                          # wrap around
            chunk += remaining_cg[:end - n]
        for coin in chunk:
            queries.extend(_coin_queries(coin["symbol"], coin["name"], seen_keys))

    # ── Rotating slice: all DeFiLlama protocols ───────────────────────────────
    if dl_snap:
        n     = len(dl_snap)
        start = (cycle_idx * _DL_ROTATE_CHUNK) % n
        end   = start + _DL_ROTATE_CHUNK
        chunk = dl_snap[start:end]
        if end > n:
            chunk += dl_snap[:end - n]
        for name in chunk:
            queries.extend(_proto_query(name, seen_keys))

    # ── DexScreener trending (brand new launches) ─────────────────────────────
    try:
        for coin in _fetch_dexscreener_trending():
            queries.extend(_coin_queries(coin["symbol"], coin["name"], seen_keys))
    except Exception:
        pass

    # ── CoinGecko trending (viral right now) ──────────────────────────────────
    try:
        for coin in _fetch_cg_trending():
            queries.extend(_coin_queries(coin["symbol"], coin["name"], seen_keys))
    except Exception:
        pass

    log.info(
        f"query_builder: cycle={cycle_idx} — {len(queries):,} queries "
        f"({len(_STATIC_QUERIES)} static + {len(queries)-len(_STATIC_QUERIES):,} dynamic | "
        f"cg={len(cg_snap):,} dl={len(dl_snap):,})"
    )

    # Advance and persist rotation state
    try:
        state = _load_rotation()
        state["cg_idx"] = cycle_idx + 1
        state["dl_idx"] = cycle_idx + 1
        _save_rotation(state)
    except Exception:
        pass

    return queries


# ══════════════════════════════════════════════════════════════════════════════
# STATIC QUERY BANK
# Covers ALL crypto categories: exchanges, wallets, DeFi, staking, yield,
# compute/DePIN, AI tokens, bridges, NFT, gaming, stablecoins, oracles,
# RWA, launchpads, new chains, all languages. ~300 queries.
# ══════════════════════════════════════════════════════════════════════════════

_STATIC_QUERIES: list[str] = [

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 1 — ULTRA-BROAD: catch ANY project, ANY complaint
    # ─────────────────────────────────────────────────────────────────────────
    '(withdrawal OR withdraw) (stuck OR failed OR blocked OR pending OR delayed) crypto -is:retweet min_faves:1',
    '(lost OR missing OR stolen) (funds OR tokens OR coins OR crypto) -scam -is:retweet min_faves:1',
    '(wallet OR account) (hacked OR compromised OR drained OR emptied) crypto -is:retweet min_faves:1',
    '(transaction OR tx) (failed OR stuck OR rejected OR dropped) crypto (help OR support OR please) -is:retweet min_faves:1',
    '(swap OR bridge OR transfer) (failed OR stuck OR lost) (crypto OR defi OR token) -is:retweet min_faves:1',
    '(deposit OR withdrawal) (not received OR not showing OR missing OR disappeared) crypto -is:retweet min_faves:1',
    '"customer support" (no response OR ignored OR useless OR scam) crypto -is:retweet min_faves:1',
    '(seed phrase OR private key OR recovery phrase) (stolen OR leaked OR compromised OR phished) -is:retweet min_faves:1',
    '(smart contract OR protocol) (exploit OR hack OR vulnerability OR bug) crypto -is:retweet',
    '(liquidated OR liquidation) (defi OR crypto OR position) (unfair OR wrong OR bug OR manipulation) -is:retweet',
    '(KYC OR verification) (rejected OR failed OR blocked OR stuck) crypto exchange -is:retweet min_faves:1',
    'crypto exchange (down OR offline OR maintenance OR not working) (funds OR withdrawal OR deposit) -is:retweet',
    '(rug pull OR rugpull OR rugged) crypto (lost OR funds OR money OR tokens) -is:retweet min_faves:2',
    '(phishing OR fake site OR impersonator OR spoofing) crypto (stole OR drained OR stolen) -is:retweet min_faves:1',
    '"account frozen" exchange (crypto OR bitcoin OR coins OR funds) -is:retweet min_faves:1',
    '"funds not received" crypto exchange -is:retweet min_faves:1',
    '"transaction pending" (hours OR days OR weeks) crypto (stuck OR help OR still) -is:retweet min_faves:1',
    '"wrong address" crypto (sent OR transferred) (help OR recovery OR lost) -is:retweet min_faves:1',
    'crypto scam (lost OR stolen OR drained OR funds) (help OR police OR report) -is:retweet min_faves:2',
    '(coins OR tokens) not showing (wallet OR account OR balance) crypto -is:retweet min_faves:1',
    'crypto (withdrawal OR deposit) (fee OR fees) (excessive OR wrong OR charged) -is:retweet min_faves:1',
    '"exit scam" crypto project -is:retweet min_faves:2',
    '(airdrop OR tokens) (not received OR not sent OR missing OR failed OR scam) -is:retweet min_faves:1',
    '"private sale" OR "presale" crypto (rug OR scam OR funds not returned OR disappeared) -is:retweet min_faves:2',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 2 — ALL L1 / L2 / CHAINS (50+ chains)
    # ─────────────────────────────────────────────────────────────────────────
    # Major L1s
    '(withdrawal OR transaction) (stuck OR failed) ethereum OR ETH -is:retweet min_faves:1',
    '(withdrawal OR transaction) (stuck OR failed) solana OR SOL -is:retweet min_faves:1',
    '(withdrawal OR transaction) (stuck OR failed) "BNB chain" OR "BSC" OR "binance smart chain" -is:retweet',
    '(transaction OR withdrawal) (failed OR stuck) polygon OR MATIC OR POL -is:retweet min_faves:1',
    '(transaction OR withdrawal) (failed OR stuck) avalanche OR AVAX -is:retweet min_faves:1',
    '(transaction OR withdrawal) (failed OR stuck) tron OR TRX OR TRC20 -is:retweet min_faves:1',
    '(transaction OR staking) (failed OR stuck) cardano OR ADA -is:retweet min_faves:1',
    '(transaction OR staking) (failed OR stuck) polkadot OR DOT OR parachain -is:retweet min_faves:1',
    '(transaction OR staking) (failed OR stuck) cosmos OR ATOM OR osmosis -is:retweet min_faves:1',
    '(transaction OR staking) (failed OR stuck) fantom OR FTM -is:retweet min_faves:1',
    '(transaction OR bridge) (failed OR stuck) "TON" OR "the open network" OR telegram -is:retweet min_faves:1',
    '(transaction OR withdrawal) (failed OR stuck) near protocol OR NEAR -is:retweet min_faves:1',
    '(transaction OR staking) (failed OR stuck) "injective" OR INJ -is:retweet min_faves:1',
    '(transaction OR bridge) (failed OR stuck) sui blockchain OR SUI -is:retweet min_faves:1',
    '(transaction OR bridge) (failed OR stuck) aptos OR APT -is:retweet min_faves:1',
    '(transaction OR bridge) (failed OR stuck) algorand OR ALGO -is:retweet min_faves:1',
    '(transaction OR bridge) (failed OR stuck) hedera OR HBAR -is:retweet min_faves:1',
    '(transaction OR staking) (failed OR stuck) chainlink OR LINK -is:retweet min_faves:1',
    '(transaction OR bridge) (failed OR stuck) flow blockchain OR FLOW -is:retweet min_faves:1',
    '(transaction OR bridge) (failed OR stuck) stellar OR XLM -is:retweet min_faves:1',
    '(transaction OR bridge) (failed OR stuck) ripple OR XRP -is:retweet min_faves:1',
    '(transaction OR bridge) (failed OR stuck) litecoin OR LTC -is:retweet min_faves:1',
    '(transaction OR staking) (failed OR stuck) kaspa OR KAS -is:retweet min_faves:1',
    '(transaction OR bridge) (failed OR stuck) multiversx OR EGLD -is:retweet min_faves:1',
    # Ethereum L2s
    '(bridge OR transaction) (stuck OR failed OR lost) arbitrum -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed OR lost) optimism OR "OP mainnet" -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed OR lost) "base" (L2 OR chain OR network OR coinbase) -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed OR lost) starknet -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed OR lost) zkSync -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed OR lost) scroll (L2 OR chain OR crypto) -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed OR lost) blast (L2 OR chain OR crypto) -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed OR lost) linea -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed OR lost) mantle (crypto OR L2 OR chain) -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed OR lost) manta (network OR pacific OR L2) -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed OR lost) "polygon zkEVM" -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed) monad (crypto OR blockchain) -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed) berachain OR BERA -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed) sei network OR SEI -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed) "eclipse" (solana OR L2 OR chain) -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed) "taiko" blockchain -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed) "celo" (blockchain OR chain) -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed) "sonic" (chain OR blockchain OR FTM) -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed) "hyperliquid" (L1 OR chain) -is:retweet min_faves:1',
    '(bridge OR transaction) (stuck OR failed) "megaeth" OR "mega eth" -is:retweet min_faves:1',
    'bitcoin (transaction OR withdraw) (stuck OR failed OR replaced OR double spend) -is:retweet min_faves:1',
    '(lightning network OR LN) (payment OR channel) (failed OR stuck OR lost) -is:retweet min_faves:1',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 3A — CENTRALIZED EXCHANGES (100+ CEXes)
    # ─────────────────────────────────────────────────────────────────────────
    '@Binance OR @BinanceUS (withdrawal OR account OR funds) (stuck OR blocked OR missing OR frozen) -is:retweet',
    '@binance_cs OR @BinanceHelpDesk (problem OR issue OR help OR failed) -is:retweet min_faves:1',
    '@coinbase (withdrawal OR account OR funds) (stuck OR blocked OR missing OR frozen) -is:retweet',
    '@CoinbaseSupport (problem OR issue OR not working OR failed) -is:retweet min_faves:1',
    '@krakensupport OR @kraken (withdrawal OR funds OR account) (stuck OR blocked OR missing) -is:retweet',
    '@OKX OR @OKXSupport (withdrawal OR funds OR account) (blocked OR missing OR frozen OR failed) -is:retweet',
    '@Bybit_Official OR @Bybit_CS (withdrawal OR account OR funds) (stuck OR blocked OR missing) -is:retweet',
    '@KuCoin_Shares OR @kucoincom (withdrawal OR funds OR account) (stuck OR blocked OR missing) -is:retweet',
    '@gate_io OR @GateioUser (withdrawal OR funds OR account) (stuck OR blocked OR missing) -is:retweet',
    '@HTX_Global OR @HuobiGlobal (withdrawal OR funds OR account) (stuck OR blocked) -is:retweet',
    '@bitfinex (withdrawal OR funds OR account) (stuck OR blocked OR missing) -is:retweet min_faves:1',
    '@BitgetWallet OR @bitgetglobal (withdrawal OR funds OR account) (stuck OR blocked) -is:retweet',
    '@mexc_official OR @MEXC_Global (withdrawal OR funds OR account) (stuck OR blocked) -is:retweet',
    '@CryptoComOfficial (withdrawal OR funds OR account) (stuck OR blocked OR missing) -is:retweet',
    '@WazirX (withdrawal OR funds OR account) (stuck OR blocked OR hacked OR frozen) -is:retweet',
    '@CoinDCX OR @ZebPay (withdrawal OR funds OR account) stuck OR blocked -is:retweet',
    '@Gemini (withdrawal OR funds OR account) (stuck OR blocked OR frozen) -is:retweet min_faves:1',
    '@bitstamp (withdrawal OR funds OR account) (stuck OR blocked OR issue) -is:retweet min_faves:1',
    '@BitMart (withdrawal OR funds OR account) (stuck OR blocked OR hacked) -is:retweet',
    '@phemex_official (withdrawal OR funds) (stuck OR blocked OR missing) -is:retweet',
    '@BTCEX_official OR @xt_com (withdrawal OR funds) (stuck OR blocked OR missing) -is:retweet',
    '@bingxofficial (withdrawal OR funds) (stuck OR blocked) -is:retweet',
    '@lbank_official (withdrawal OR funds) (stuck OR blocked) -is:retweet',
    '@DigiFinex OR @AscendEX_Global (withdrawal OR funds) (stuck OR blocked) -is:retweet',
    '@poloniex (withdrawal OR funds OR account) (stuck OR blocked OR hacked) -is:retweet',
    '@bittrex (withdrawal OR funds OR account) (stuck OR failed) -is:retweet',
    '@Upbit_Global (withdrawal OR funds OR account) (stuck OR blocked) -is:retweet',
    '@Bithumb_Korea (withdrawal OR funds OR account) (stuck OR blocked) -is:retweet',
    '@CoinoneOfficial OR @Korbit (withdrawal OR funds) stuck OR blocked -is:retweet',
    '@IndodaxOfficial (withdrawal OR funds) (stuck OR blocked OR missing) -is:retweet',
    '@tokocrypto (withdrawal OR funds) (stuck OR blocked) -is:retweet',
    '@CoinSwitchApp OR @coinswitch_kuber (withdrawal OR funds) stuck OR blocked -is:retweet',
    '"Bitpanda" OR "Coinjar" (withdrawal OR funds) (stuck OR blocked OR issue) -is:retweet min_faves:1',
    '"Robinhood crypto" (withdrawal OR funds OR issue) (stuck OR blocked OR missing) -is:retweet min_faves:1',
    '"eToro crypto" (withdrawal OR funds) (stuck OR blocked OR issue) -is:retweet min_faves:1',
    '"Revolut crypto" (withdrawal OR funds) (stuck OR blocked OR failed) -is:retweet min_faves:1',
    '"PayPal crypto" (withdrawal OR funds OR send) (stuck OR blocked OR failed) -is:retweet min_faves:1',
    '"Venmo crypto" (withdrawal OR funds) (stuck OR blocked OR issue) -is:retweet min_faves:1',
    '"CashApp bitcoin" (withdrawal OR funds OR buy OR sell) (stuck OR blocked OR issue) -is:retweet min_faves:1',
    '@BackpackExch (withdrawal OR funds OR account) (stuck OR blocked OR issue) -is:retweet',
    '@deribit (withdrawal OR margin OR option) (stuck OR blocked OR issue) -is:retweet min_faves:1',
    '@BitMEX (withdrawal OR margin OR liquidation) (stuck OR blocked OR wrong) -is:retweet min_faves:1',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 3B — WALLETS (all major + hardware)
    # ─────────────────────────────────────────────────────────────────────────
    '@MetaMask OR @MetaMask_Support (transaction OR gas OR funds OR NFT) (failed OR stuck OR wrong OR missing) -is:retweet min_faves:1',
    '@TrustWallet OR @TrustWalletApp (transaction OR funds OR NFT) (failed OR missing OR stuck OR drained) -is:retweet',
    '@phantom (transaction OR NFT OR funds OR drained) (failed OR stuck OR wrong OR missing) -is:retweet min_faves:1',
    '@LedgerHQ OR @LedgerSupport (device OR transaction OR funds OR firmware) (issue OR failed OR stuck OR bricked) -is:retweet',
    '@Trezor (device OR transaction OR funds OR recovery) (issue OR failed OR stuck OR bricked) -is:retweet min_faves:1',
    '@CoinbaseWallet (transaction OR funds OR NFT) (failed OR stuck OR missing OR drained) -is:retweet',
    '@rabby_io OR @rainbow_me OR @frame_eth (transaction OR funds) (failed OR stuck OR issue) -is:retweet',
    '@safe (multisig OR transaction OR funds) (failed OR stuck OR issue OR locked) -is:retweet min_faves:1',
    '@ExodusWallet (transaction OR funds OR crypto) (failed OR stuck OR missing) -is:retweet min_faves:1',
    '@mytonwallet OR @tonkeeper (transaction OR funds OR TON) (failed OR stuck OR issue) -is:retweet',
    '@solflare OR @backpackapp (transaction OR funds OR NFT) (failed OR stuck OR issue) -is:retweet',
    '@Zerion OR @DeBankDeFi (portfolio OR funds OR transaction) (wrong OR missing OR stuck) -is:retweet',
    '@OKXWallet (transaction OR funds) (failed OR stuck OR missing) -is:retweet',
    '@BitKeep OR @BitgetWallet (transaction OR funds) (failed OR stuck OR missing) -is:retweet',
    '"hardware wallet" (lost OR bricked OR corrupted OR forgot PIN OR recovery) crypto -is:retweet min_faves:1',
    '"seed phrase" (lost OR stolen OR wrong OR compromised OR phished) wallet crypto -is:retweet min_faves:1',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 3C — DEX / AMM / AGGREGATORS
    # ─────────────────────────────────────────────────────────────────────────
    '@Uniswap (swap OR liquidity OR position OR funds) (failed OR stuck OR issue OR wrong) -is:retweet min_faves:1',
    '@SushiSwap (swap OR funds OR liquidity) (failed OR stuck OR missing) -is:retweet min_faves:1',
    '@PancakeSwap (swap OR funds OR liquidity) (failed OR stuck OR missing) -is:retweet',
    '@CurveFinance (swap OR pool OR funds OR CRV) (failed OR stuck OR issue OR exploit) -is:retweet min_faves:1',
    '@BalancerLabs (swap OR pool OR funds OR BAL) (failed OR stuck OR issue) -is:retweet min_faves:1',
    '@1inch (swap OR transaction OR funds) (failed OR stuck OR issue) -is:retweet min_faves:1',
    '@paraswap OR @kybernetwork (swap OR transaction) (failed OR stuck) -is:retweet',
    '@JupiterExchange (swap OR tokens OR funds) (failed OR stuck OR missing) -is:retweet min_faves:1',
    '@RaydiumProtocol (swap OR pool OR liquidity OR funds) (failed OR stuck OR drained) -is:retweet',
    '@OrcaWhirlpool OR @meteora_ag (swap OR pool OR liquidity) (failed OR stuck OR issue) -is:retweet',
    '@dydxprotocol (trade OR liquidation OR withdrawal OR perp) (wrong OR failed OR issue) -is:retweet min_faves:1',
    '@HyperliquidX (trade OR position OR liquidation OR withdrawal) (wrong OR failed OR issue) -is:retweet',
    '@GMX_IO OR @gains_network (trade OR liquidation OR GLP OR position) (wrong OR failed) -is:retweet min_faves:1',
    '@VenusProtocol OR @ApeXProtocolCo (swap OR funds OR liquidation) (failed OR stuck) -is:retweet',
    '@TraderJoe_xyz (swap OR pool OR funds) (failed OR stuck OR missing) -is:retweet',
    'DEX (swap OR liquidity) (failed OR stuck OR front-run OR sandwiched) crypto -is:retweet min_faves:1',
    '(MEV OR sandwich attack OR front-run) (loss OR stolen OR drained) defi -is:retweet min_faves:2',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 3D — BRIDGES / CROSS-CHAIN
    # ─────────────────────────────────────────────────────────────────────────
    '@LayerZero_Core OR @StargateFinance (bridge OR tokens OR stuck) (failed OR lost OR issue) -is:retweet min_faves:1',
    '@wormhole (bridge OR tokens OR nft) (stuck OR lost OR failed OR exploit) -is:retweet min_faves:1',
    '@across_protocol OR @Connext (bridge OR tokens) (stuck OR lost OR failed) -is:retweet min_faves:1',
    '@Hop_Protocol OR @Orbiter_Finance (bridge OR tokens) (stuck OR lost OR failed) -is:retweet',
    '@MultichainOrg OR @Synapse_Protocol (bridge OR tokens OR chain) (stuck OR lost OR failed OR exploit) -is:retweet min_faves:1',
    '@Ronin_Network OR @Axie (bridge OR tokens OR RON OR AXS) (stuck OR lost OR hacked) -is:retweet',
    '@debridgeio OR @Li_Finance (bridge OR swap) (stuck OR failed OR lost) -is:retweet',
    '(cross-chain bridge OR multichain bridge) (stuck OR lost OR failed OR exploit) -is:retweet min_faves:1',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 4A — LENDING / BORROWING / MONEY MARKETS
    # ─────────────────────────────────────────────────────────────────────────
    '@AaveAave (liquidation OR borrow OR supply OR position OR aToken) (wrong OR failed OR issue OR bug) -is:retweet',
    '@compoundfinance (liquidation OR borrow OR supply) (wrong OR failed OR issue) -is:retweet min_faves:1',
    '@MakerDAO OR @sparkdotfi (vault OR liquidation OR DAI OR collateral) (failed OR issue OR wrong) -is:retweet',
    '@VenusProtocol (borrow OR liquidation OR supply) (wrong OR failed OR bug) -is:retweet',
    '@BenqiFinance OR @ironbank OR @morpho_labs (borrow OR liquidation) (wrong OR failed) -is:retweet',
    '(money market OR lending protocol) (liquidation OR borrow OR collateral) (bug OR wrong OR unfair) -is:retweet min_faves:1',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 4B — STAKING / LIQUID STAKING / RESTAKING (yield)
    # ─────────────────────────────────────────────────────────────────────────
    '(staking withdrawal OR unstaking) (stuck OR delayed OR failed OR not received) -is:retweet min_faves:1',
    '(liquid staking OR stETH OR rETH OR wstETH OR cbETH) (issue OR bug OR withdrawal OR de-peg) -is:retweet min_faves:1',
    '@LidoFinance (staking OR withdrawal OR stETH OR validator) (failed OR stuck OR issue) -is:retweet',
    '@RocketPool (withdrawal OR node OR rETH OR minipool) (failed OR stuck OR issue) -is:retweet min_faves:1',
    '@EtherFi_io OR @swell_l2 OR @stakewise_io (withdrawal OR staking OR stETH) (failed OR stuck) -is:retweet',
    '@fraxfinance OR @staderprotocol (staking OR withdrawal OR frxETH) (failed OR stuck OR issue) -is:retweet',
    '(restaking OR EigenLayer OR AVS) (slash OR slashing OR issue OR failed OR stuck) -is:retweet min_faves:1',
    '@KelpDAO OR @renzo_protocol OR @PufferFinance (restaking OR withdrawal OR stuck) -is:retweet',
    '(validator OR node operator) (slashed OR offline OR jailed OR missing rewards) -is:retweet min_faves:1',
    '@Everstake OR @p2pvalidator (staking OR rewards OR validator) (issue OR missing OR failed) -is:retweet',
    '(ETH staking OR BNB staking OR SOL staking) (rewards OR withdrawal) (delayed OR missing OR failed) -is:retweet min_faves:1',
    '@jito_sol OR @marinade_finance (staking OR mSOL OR JitoSOL OR rewards) (failed OR stuck OR issue) -is:retweet',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 4C — YIELD FARMING / VAULTS / STRATEGIES
    # ─────────────────────────────────────────────────────────────────────────
    '@yearnfinance (vault OR strategy OR yvault OR yield) (failed OR stuck OR exploit OR bug) -is:retweet min_faves:1',
    '@ConvexFinance (rewards OR cvxCRV OR claim OR boost) (failed OR missing OR stuck) -is:retweet',
    '@PendleFinance (yield OR PT OR YT OR maturity) (failed OR stuck OR issue) -is:retweet',
    '@OriginProtocol OR @sommfinance (vault OR yield OR OUSD) (failed OR exploit OR issue) -is:retweet',
    '@beefy_finance (vault OR APY OR harvest OR strategy) (failed OR stuck OR rug) -is:retweet',
    '@alpacafinance OR @autofarm_network (vault OR yield OR harvest) (failed OR stuck OR exploit) -is:retweet',
    '(yield farming OR liquidity mining) (rewards OR harvest) (missing OR failed OR stopped) -is:retweet min_faves:1',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 4D — COMPUTE / DePIN / AI TOKENS
    # ─────────────────────────────────────────────────────────────────────────
    '@akashnet_ OR @AKT (compute OR GPU OR deployment OR tokens) (failed OR stuck OR issue OR rug) -is:retweet',
    '@rendernetwork OR RNDR OR Render (render job OR payment OR GPU OR tokens) (failed OR stuck OR issue) -is:retweet min_faves:1',
    '@opentensor OR @bittensor_ OR TAO (mining OR staking OR rewards OR neurons) (failed OR issue OR rug) -is:retweet',
    '@ionet_official (GPU OR compute OR task OR tokens) (failed OR stuck OR issue OR scam) -is:retweet',
    '@NosanaCI OR @hotspotty (mining OR tokens OR rewards) (failed OR stuck OR missing) -is:retweet',
    '@Grass_io OR @nodepay_ai (node OR points OR rewards OR network) (failed OR stuck OR scam) -is:retweet',
    '@FilecoinProject OR @holochain (storage OR retrieval OR tokens) (failed OR stuck OR issue) -is:retweet',
    '@StorjProject OR @ArweaveEco (storage OR tokens OR rewards) (failed OR stuck OR issue) -is:retweet',
    '(DePIN OR decentralized physical infrastructure) (tokens OR rewards OR node) (failed OR rug OR stuck) -is:retweet min_faves:1',
    '(AI token OR AI agent) (rug pull OR scam OR failed OR drained) -is:retweet min_faves:2',
    '@virtuals_io OR @ai16z OR @ElizaOS (tokens OR agent OR rewards) (failed OR rug OR stuck) -is:retweet min_faves:1',
    '(GPU mining OR CPU mining) (pool OR rewards) (not paid OR missing OR stopped) -is:retweet min_faves:1',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 4E — OPTIONS / PERPS / DERIVATIVES / COPY-TRADING
    # ─────────────────────────────────────────────────────────────────────────
    '(perpetual OR perp OR futures) (liquidated OR wrong price OR forced close OR bad fill) crypto -is:retweet min_faves:1',
    '(options OR expiry OR strike) (lost OR wrong OR issue OR liquidated) crypto trading -is:retweet min_faves:1',
    '(copy trading OR social trading OR lead trader) (lost OR issue OR stopped OR misleading) crypto -is:retweet min_faves:1',
    '(funding rate OR basis) (wrong OR excessive OR manipulation) perp futures crypto -is:retweet min_faves:1',
    '(leverage OR margin) (liquidated OR wicked OR stop hunt OR manipulation) crypto -is:retweet min_faves:1',
    '@deribit OR @BitMEX OR @BybitDerivatives (option OR expiry OR settlement) wrong OR issue -is:retweet min_faves:1',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 4F — NFT MARKETPLACES / GAMING / SOCIALFI
    # ─────────────────────────────────────────────────────────────────────────
    '(NFT OR nfts) (stolen OR hacked OR missing OR drained OR phished) wallet -is:retweet min_faves:1',
    '@opensea (NFT OR listing OR offer OR royalty) (missing OR stolen OR failed OR removed) -is:retweet min_faves:1',
    '@Blur_io (NFT OR bid OR trade OR listing) (failed OR issue OR missing) -is:retweet min_faves:1',
    '@MagicEden (NFT OR listing OR trade OR offer) (failed OR missing OR stolen) -is:retweet',
    '@tensor_hq (NFT OR trade OR listing) (failed OR missing OR issue) -is:retweet',
    '(web3 game OR play to earn OR P2E OR GameFi OR blockchain game) (tokens OR rewards OR NFT) (missing OR stuck OR not sent OR rug) -is:retweet min_faves:1',
    '@AxieInfinity OR @axie (rewards OR tokens OR SLP OR AXS) (missing OR stuck OR failed OR rug) -is:retweet',
    '@StepN_official (GST OR GMT OR rewards OR shoes) (missing OR stuck OR failed) -is:retweet',
    '@GalaGames OR @illuviumio (tokens OR NFT OR rewards) (missing OR failed OR rug) -is:retweet',
    '(SocialFi OR friend.tech OR Stars Arena OR social token) (rug OR drained OR failed OR exit scam) -is:retweet min_faves:1',
    '(Pump.fun OR pumpfun OR launchpad) (rug OR scam OR drained OR dev dumped) -is:retweet min_faves:1',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 4G — STABLECOINS / ORACLES / RWA / INSURANCE
    # ─────────────────────────────────────────────────────────────────────────
    '(USDT OR USDC OR DAI OR BUSD OR FDUSD OR TUSD) (frozen OR blacklisted OR not transferring OR seized) -is:retweet min_faves:1',
    '@Tether_to OR @Circle (USDT OR USDC) (frozen OR issue OR blacklisted OR seized) -is:retweet min_faves:1',
    '(algo stablecoin OR algorithmic stablecoin OR UST OR depeg OR de-peg) (issue OR collapsed OR lost) -is:retweet min_faves:2',
    '@chainlink OR @pyth_network (oracle OR price feed OR data) (wrong OR failed OR issue OR exploit) -is:retweet min_faves:1',
    '(tokenized stocks OR RWA OR real world asset) (issue OR failed OR stuck OR delist) crypto -is:retweet min_faves:1',
    '@ondofinance OR @MapleFinance (RWA OR tokenized OR yield) (failed OR issue OR withdrawal) -is:retweet',
    '(DeFi insurance OR Nexus Mutual OR InsurAce) (claim OR payout OR denied OR failed) -is:retweet min_faves:1',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 4H — LAUNCHPADS / IDO / PRESALES / NEW PROJECTS
    # ─────────────────────────────────────────────────────────────────────────
    '(memecoin OR meme coin) (rug pull OR rugpull OR scam OR stolen OR drained) -is:retweet min_faves:2',
    '(ICO OR IDO OR IEO OR MEME launch) (rug OR scam OR funds not returned OR disappeared) -is:retweet min_faves:2',
    '@CoinList OR @PolkaStarter OR @DaoMaker (IDO OR allocation OR tokens) (missing OR failed OR scam) -is:retweet',
    '(PEPE OR WIF OR BONK OR SHIB OR DOGE OR FLOKI) (scam OR drained OR rug OR stolen) -is:retweet min_faves:2',
    '(new coin OR new token) launch (rug OR scam OR honeypot OR drained) -is:retweet min_faves:2',
    '(honeypot OR can\'t sell OR liquidity removed) crypto token -is:retweet min_faves:1',
    '(dev wallet OR team wallet) (dumped OR selling OR drained) project crypto -is:retweet min_faves:2',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 5 — 12 LANGUAGES (covering 6B+ speakers)
    # ─────────────────────────────────────────────────────────────────────────
    # Spanish (500M speakers — LatAm + Spain, massive crypto market)
    '(retiro OR retirar) (bloqueado OR fallido OR atascado OR pendiente) (cripto OR crypto OR exchange) -is:retweet',
    '(fondos OR tokens OR monedas) (perdidos OR robados OR desaparecidos OR vaciados) cripto -is:retweet min_faves:1',
    '(billetera OR wallet) (hackeada OR comprometida OR vaciada OR drenada) cripto -is:retweet min_faves:1',
    '(intercambio OR exchange) (caído OR sin respuesta OR bloqueado OR estafa) cripto -is:retweet min_faves:1',
    'estafa crypto (perdí OR robaron OR desapareció OR me vaciaron) -is:retweet min_faves:1',
    '(rug pull OR rugpull OR estafa) cripto proyecto -is:retweet min_faves:2',
    '(staking OR apuesta) (fallo OR fallido OR no recibido OR atascado) cripto -is:retweet',
    # Portuguese (250M — Brazil is top-3 crypto country)
    '(saque OR retirada) (bloqueado OR falhou OR travado OR pendente) (cripto OR crypto) -is:retweet',
    '(fundos OR tokens OR moedas) (perdidos OR roubados OR sumiu OR drenados) cripto -is:retweet min_faves:1',
    '(carteira OR wallet) (hackeada OR invadida OR drenada OR comprometida) cripto -is:retweet min_faves:1',
    '(golpe OR scam OR fraude) crypto (perdi OR roubaram OR sumiu OR desapareceu) -is:retweet min_faves:1',
    'corretora (crypto OR cripto) (fora do ar OR bloqueada OR sem suporte OR roubou) -is:retweet min_faves:1',
    '(rug pull OR golpe) cripto token projeto -is:retweet min_faves:2',
    # Korean (South Korea — highest per-capita crypto trading)
    '출금 (오류 OR 실패 OR 막힘 OR 안됨 OR 지연) -is:retweet min_faves:1',
    '코인 (해킹 OR 도난 OR 분실 OR 사기) -is:retweet min_faves:1',
    '(거래소 OR 지갑 OR 스테이킹) (오류 OR 먹통 OR 점검 OR 사기) -is:retweet min_faves:1',
    'NFT (도난 OR 사기 OR 오류 OR 사라짐) -is:retweet min_faves:1',
    '(러그풀 OR 사기코인 OR 스캠) 가상화폐 -is:retweet min_faves:1',
    # Chinese (1.4B — offshore trading massive despite restrictions)
    '提币 (失败 OR 卡住 OR 不到账 OR 被拒) -is:retweet',
    '(账户 OR 钱包) (被盗 OR 被封 OR 冻结 OR 清空) 加密货币 -is:retweet',
    '(交易所 OR 合约 OR 项目) (跑路 OR 骗局 OR 出问题 OR 黑平台) -is:retweet min_faves:1',
    '(空投 OR 质押 OR 流动性) (没收到 OR 失败 OR 骗局 OR 问题) -is:retweet min_faves:1',
    '(DeFi OR 链上) (被盗 OR 被黑 OR 漏洞 OR 闪电贷) -is:retweet min_faves:1',
    # Turkish (high crypto adoption due to inflation)
    'para çekemiyorum (kripto OR borsa OR exchange) -is:retweet',
    '(cüzdan OR hesap) (hacklendi OR çalındı OR donduruldu OR boşaltıldı) kripto -is:retweet',
    '(borsa OR exchange OR proje) (dolandırıcılık OR hata OR çöktü OR kapandı) kripto -is:retweet min_faves:1',
    '(kripto OR coin) (kayıp OR çalıntı OR dolandırıcı OR rug) -is:retweet min_faves:1',
    # Russian (CIS region — crypto under sanctions, very active)
    'вывод (застрял OR заблокирован OR не пришел OR отклонен) крипто -is:retweet',
    '(кошелек OR биржа OR протокол) (взломан OR заморожен OR недоступна OR украли) крипто -is:retweet',
    '(токены OR монеты OR средства) (украли OR потерял OR не пришли OR застряли) крипто -is:retweet min_faves:1',
    '(мошенники OR скам OR rug pull) (крипто OR биткоин OR токены OR проект) -is:retweet min_faves:1',
    # Indonesian (SE Asia — massive youth crypto adoption)
    '(penarikan OR withdraw) (gagal OR ditahan OR tidak masuk OR diblokir) kripto -is:retweet',
    '(dompet OR wallet) (kena hack OR diretas OR dibobol OR dikuras) kripto -is:retweet',
    'penipuan kripto (uang OR token OR dana) (hilang OR dicuri OR kabur) -is:retweet min_faves:1',
    '(rug pull OR scam) token kripto proyek -is:retweet min_faves:1',
    # Hindi (India — world's largest crypto user base by count)
    'क्रिप्टो (निकासी OR विड्रॉल) (फंसा OR विफल OR अटका OR ब्लॉक) -is:retweet min_faves:1',
    'क्रिप्टो (धोखाधड़ी OR हैक OR स्कैम OR चोरी) -is:retweet min_faves:1',
    '(एक्सचेंज OR वॉलेट) (बंद OR फ्रीज OR हैक) क्रिप्टो -is:retweet min_faves:1',
    # Vietnamese (fast-growing, one of highest crypto ownership rates)
    '(rút tiền OR withdraw) (thất bại OR bị chặn OR không về) crypto -is:retweet min_faves:1',
    '(ví OR wallet) (bị hack OR mất tiền OR bị drain) crypto -is:retweet min_faves:1',
    '(lừa đảo OR rug pull OR scam) crypto dự án -is:retweet min_faves:1',
    # Arabic (Gulf states — immense crypto wealth)
    'السحب (معلق OR فاشل OR محجوب OR مرفوض) كريبتو -is:retweet',
    '(محفظة OR حساب OR بورصة) (اختراق OR سرقة OR تجميد OR نصب) كريبتو -is:retweet',
    '(احتيال OR نصب OR rug pull) كريبتو (أموال OR رموز OR مشروع) -is:retweet min_faves:1',
    # Japanese (large institutional and retail crypto market)
    '(出金 OR 引き出し) (失敗 OR できない OR 詰まった OR 拒否) 暗号資産 -is:retweet',
    '(ウォレット OR 取引所) (ハック OR 不正アクセス OR 凍結 OR 詐欺) 暗号 -is:retweet min_faves:1',
    '(詐欺 OR ラグプル OR スキャム) 仮想通貨 プロジェクト -is:retweet min_faves:1',
    # French (Europe + francophone Africa — huge DeFi audience)
    '(retrait OR virement) (bloqué OR échoué OR en attente) crypto -is:retweet min_faves:1',
    '(fonds OR tokens) (perdus OR volés OR disparus) crypto -is:retweet min_faves:1',
    '(arnaque OR escroquerie OR rug pull) crypto projet -is:retweet min_faves:1',
    # German (Europe — large trading + DeFi population)
    '(Auszahlung OR Abhebung) (blockiert OR fehlgeschlagen OR steckt fest) Krypto -is:retweet min_faves:1',
    '(Wallet OR Konto) (gehackt OR leer geräumt OR gesperrt) Krypto -is:retweet min_faves:1',
    '(Betrug OR Rug Pull OR Scam) Krypto Projekt -is:retweet min_faves:1',

    # ─────────────────────────────────────────────────────────────────────────
    # TIER 6 — EMERGING CATEGORIES (2025-2026 hot sectors)
    # ─────────────────────────────────────────────────────────────────────────
    '(AI agent OR AI crypto OR autonomous agent OR agent token) (rug OR scam OR failed OR drained) -is:retweet min_faves:2',
    '(SocialFi OR social finance OR creator token) (rug OR drained OR failed OR exit scam) -is:retweet min_faves:1',
    '(prediction market OR Polymarket OR Manifold) (funds OR withdrawal OR issue) (failed OR stuck) -is:retweet min_faves:1',
    '(cross-chain OR multichain OR OmniChain) bridge (stuck OR lost OR failed OR exploit) -is:retweet min_faves:1',
    '(RWA tokenization OR tokenized bonds OR tokenized real estate) (issue OR failed OR stuck) crypto -is:retweet min_faves:1',
    'crypto (tax OR IRS OR HMRC OR ATO) (locked OR issue OR wrong) exchange -is:retweet min_faves:1',
    '(DEX aggregator OR router OR pathfinder) (slippage OR failed OR wrong price OR drained) -is:retweet min_faves:1',
    '(DAO OR governance) (funds OR treasury) (drained OR exploited OR hacked OR vote manipulation) -is:retweet min_faves:2',
    '(flash loan OR flash loan attack) exploit OR hack defi protocol -is:retweet min_faves:2',
    '(re-entrancy OR oracle manipulation OR price manipulation) exploit defi -is:retweet min_faves:2',
    '(account abstraction OR smart wallet OR ERC-4337) (issue OR failed OR drained) -is:retweet min_faves:1',
    '(inscriptions OR Ordinals OR BRC-20 OR runes) (failed OR stuck OR scam OR drained) -is:retweet min_faves:1',
    '(meme coin launchpad OR bonding curve) (rug OR dump OR drained OR dev sold) -is:retweet min_faves:1',
    '(airdrop farming OR points program OR season 2) (cancelled OR not paying OR rug OR worthless) -is:retweet min_faves:2',
    '(LST OR LRT OR liquid restaking token) (issue OR de-peg OR failed OR exploit) -is:retweet min_faves:1',
    '(ZK proof OR ZK rollup OR validity proof) (bug OR exploit OR incorrect) -is:retweet min_faves:1',
    '(node sale OR validator sale OR infrastructure sale) (rug OR scam OR no delivery) crypto -is:retweet min_faves:2',
]
