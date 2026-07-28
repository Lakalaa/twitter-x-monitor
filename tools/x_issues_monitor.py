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
import os
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
        "NumeraiOfficial", "SingularityNET",
    ],

    # ── Trading bots — users lose money when bots break, stop, mis-trade ─
    "trading_bots": [
        "3commas_io", "pionex", "bitsgap", "Cryptohopper",
        "CornixIO", "WunderTrading", "TradeSanta_io",
        "MaestroSniper", "TrojanOnSolana", "BananaGunBot",
        "bonkbot_io", "BullX_io", "Photon_sol",
        "hype_terminal", "SigmaBot_sol",
    ],

    # ── On/off ramps — card declines, failed deposits, KYC issues ────────
    "onramps": [
        "MoonPay", "Transak", "RampNetwork", "Banxa",
        "SimplexCC", "Onramper", "Alchemy_api",
        "LifiProtocol", "ChangeNOW_io", "SimpleSwap_io",
        "FixedFloat", "StealthEX_io",
    ],

    # ── Portfolio / tracking tools — sync failures, wrong balances ────────
    "portfolio": [
        "zerion", "DeBankDeFi", "Zapper_fi", "CoinStats",
        "Delta_App", "rotki", "ApeBoard", "Pulsar_Finance",
        "NFTbank_ai", "OKX",
    ],

    # ── NFT marketplaces / tools — failed mints, stuck listings ──────────
    "nft_tools": [
        "opensea", "blur_io", "NFTfi", "paraspace_xyz",
        "nftperp", "NFTScan_Official", "traitsniper",
        "icy_tools", "nansen_ai", "Reservoir0x",
    ],

    # ── DeFi tools / aggregators — users lose funds in complex txns ───────
    "defi_tools": [
        "DeFiSaver", "InstaDApp", "CowProtocol",
        "Furucombo", "AlphaFinanceLab", "IndexCoop",
        "xdotfinance", "EnzymeFinance",
    ],

    # ── Crypto payments / merchants — stuck payments, wrong amounts ───────
    "payments": [
        "BitPay", "NOWPayments_net", "CoinPayments",
        "BTCPayServer", "Utrust", "TripleA_io",
        "Coinbase_Commerce", "SpherePay",
    ],

    # ── Launchpads — users miss IDOs, refunds stuck, tokens not received ─
    "launchpads": [
        "Polkastarter", "DaoMaker", "trustpad_io",
        "PinkSale_Finance", "GemPad_io", "seedify_fund",
        "GameFi_Official", "StartFi_io", "DAO5_io",
    ],

    # ── Copy trading / social trading — signal issues, trade failures ─────
    "copy_trading": [
        "eToro", "CopyTradingApp", "Bitget_Global",
        "OKXApps", "Bybit_Official", "MEXC_Global",
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

    # ── Centralized earn / savings / lending ──────────────────────────────
    "cex_earn": [
        "Nexo", "swissborg", "ledn_io", "hodlnaut",
        "CelsiusNetwork", "BlockFi", "vauld_official",
        "CoinLoanCom", "YouHodler", "abra_official",
        "Matrixport_official", "WhaleFin_", "BabelFinance",
        "CoinRabbit_io", "FinBlox", "Haru_Invest",
    ],

    # ── Regional / smaller CEXes ──────────────────────────────────────────
    "cex_regional": [
        "WazirXIndia", "CoinDCX", "ZebPay", "BitBnS",
        "Bitkub_official", "TokoCrypto", "Coinstore_official",
        "coincheck_jp", "bitFlyerUSA", "CoinoneOfficial",
        "Luno", "YellowCardFin", "Paxful", "bitso",
        "CoinJarSupport", "BTCMarkets_com",
        "LocalBitcoins", "Remitano", "HodlHodl",
        "RobinhoodApp", "CashApp", "Bittrex", "Poloniex",
        "HitBTC", "WhiteBIT", "LATOKENcom",
        "XT_com", "CoinW_official", "BitGet_Support",
    ],

    # ── Yield farming aggregators ─────────────────────────────────────────
    "earn_yield": [
        "beefyfinance", "HarvestFinance", "BadgerDAO",
        "IdleFinance", "StakeDAO_", "PickleFinance",
        "vesperfinance", "Gamma_Strategies", "ArrakisFinance",
        "RangeProtocol", "ACryptoS", "AutoFarmNetwork",
        "YieldYak", "Adamant_Finance", "sommfinance",
        "AlphaVentureDAO", "concentratorDAO",
    ],

    # ── More lending / money markets ──────────────────────────────────────
    "lending": [
        "RadiantCapital", "WePiggy", "HundredFinance",
        "NotionalFinance", "ExactlyProtocol",
        "FringeFinance", "MELD_network", "StrikeFinance",
        "flux_finance", "UwULend", "tapioca_dao",
        "SonneFinance", "moonwell_fi", "PolixFinance",
        "BenqiFinance", "ironbankofficial",
    ],

    # ── Options / structured products ─────────────────────────────────────
    "options_defi": [
        "PremiaFinance", "opyn_", "HegicProtocol",
        "PanopticProtocol", "Thetanuts_fi", "Rysk_Finance",
        "Dopex_io", "PsyFinance", "StrikeFinance",
        "BufferFinance", "antfarm_finance",
    ],

    # ── More NFT projects (Ethereum) ──────────────────────────────────────
    "nft_eth2": [
        "worldofwomennft", "moonbirds", "VeeFriends",
        "Meebits", "artblocks_io", "nounsdao",
        "InvisibleFriends", "MutantCatsNFT", "HAPEPrime",
        "CyberKongz", "Wolf_Game_NFT", "goblintown",
        "DigiDaigaku", "degods", "y00tsNFT",
        "OnChainMonkey", "NFTBoxOfficial", "ThinkingApes",
        "CloneX_Support", "rtfkt",
    ],

    # ── NFT on other chains ───────────────────────────────────────────────
    "nft_multichain": [
        "NBATopShot", "Sorare", "AlienWorldsio",
        "Splinterlands", "GodsUnchained", "GuildOfGuardians",
        "PegaxyOfficial", "PlanetIX_io", "CometGame_io",
        "ZedRun", "F1DeltaTime", "PolygonNFT",
        "FamousFoxFed", "DripHaus", "SolanaNFT_",
        "GeneticChain", "Solarians_", "aurory_game",
    ],

    # ── More launchpads ───────────────────────────────────────────────────
    "launchpad2": [
        "redkite_pad", "kommunitas_io", "EnjinStarter",
        "TrustSwap", "Decubate_io", "MintedLab",
        "TierBot_io", "BybitLaunchpad", "HuobiIncubator",
        "OKXVentures", "KuCoinLabs", "MexcLaunchpad",
        "Bounce_Finance", "CopperLaunch", "fjord_foundry",
    ],

    # ── Cross-chain infra ─────────────────────────────────────────────────
    "cross_chain": [
        "axelarcore", "PythNetwork", "ChainPort_io",
        "Allbridge_io", "MayanFinance", "LiFi_io",
        "SymbiosisFinance", "XYFinance_", "RangoExchange",
        "OKU_trade", "Via_Protocol", "unizen_io",
    ],

    # ── DEX aggregators ───────────────────────────────────────────────────
    "dex_agg": [
        "1inchNetwork", "paraswap", "CowProtocol",
        "KyberNetwork", "OdosProtocol", "hashflownetwork",
        "DecentrEx", "SwapSpaceTeam", "ChangeNOW_io",
        "StealthEX_io", "FixedFloat", "exolix_exchange",
        "simpleswap_io", "changehero_io",
    ],

    # ── More TON ecosystem ────────────────────────────────────────────────
    "ton2": [
        "storm_trade", "evaa_protocol", "TonFi_io",
        "TonkeeperWallet", "TonWhales", "tonstakers",
        "Gatto_TON", "PunkCity_TON", "tgbtc_official",
        "MyTonWallet", "TonTech_", "openleague_ton",
    ],

    # ── More Solana ecosystem ─────────────────────────────────────────────
    "solana2": [
        "parcl", "Realms_DAOs", "sns_dao",
        "famousfoxes", "DripHaus", "tensor_hq",
        "Solend", "switchboardxyz", "pyth_network",
        "Clockworkxyz", "GenesysGo", "SolanaFM",
        "HelloMoon_io", "step_finance",
    ],

    # ── P2P / OTC / payments apps ─────────────────────────────────────────
    "p2p_otc": [
        "Paxful", "LocalBitcoins", "Remitano",
        "HodlHodl", "AgoraDEX", "LocalCryptos",
        "Bisq_Network", "RoboSats_",
    ],

    # ── Crypto debit cards ────────────────────────────────────────────────
    "crypto_cards": [
        "crypto_com", "wirexapp", "crypterium_",
        "cryptopayapp", "AdvancedCash_", "paybis",
        "Monolith_web3", "FoldApp", "LoligoSystems",
        "baanx_com", "PaybisCard",
    ],

    # ── More DeFi (misc protocols with large user bases) ─────────────────
    "defi_misc": [
        "reflexerfinance", "liquity_eth", "inverse_finance",
        "TrueUSD", "USTprotocol", "TribeDAO",
        "OlympusDAO", "KlimaDAO_official", "TempusFinance",
        "ribbonfinance", "polynomialfi", "friktion_labs",
        "CratD2C", "YAMFinance", "AlchemixFi",
    ],

    # ── More Arbitrum / OP / Base ecosystem ──────────────────────────────
    "arb_op_base2": [
        "RdpxV2", "traderjoexyz", "AbracadabraDefi",
        "SperaxUSD", "JonesDAOfi", "UmamiFinance",
        "HMXorg", "GMXBluebird", "AcalaNetwork",
        "cap_finance", "rage_trade", "premia_blue",
    ],

    # ── Cosmos / IBC ecosystem (expanded) ────────────────────────────────
    "cosmos2": [
        "QuicksilverZone", "umee_", "CrescentHub_",
        "Persistence_one", "mars_protocol",
        "levanaprotocol", "WhiteWhale_fi",
        "Kujira_Zone", "MilkyWay_Zone", "ICAprotocol",
        "CoreumNetwork", "DYMension", "Composable_Fin",
    ],

    # ── Sui / Aptos ecosystem ─────────────────────────────────────────────
    "sui_aptos": [
        "SuiNetwork", "MystenLabs", "cetus_protocol",
        "turbosfinance", "navi_protocol", "scallop_io",
        "bucket_protocol", "aftermath_fi", "KriyaDEX",
        "aptos_network", "ThalaLabs", "PancakeSwap",
        "liquidswapDEX", "AptosLend", "MomentumSafe",
    ],

    # ── Polygon / zkEVM deeper ────────────────────────────────────────────
    "polygon2": [
        "0xPolygon", "0xPolygonDev", "UniswapProtocol",
        "SushiSwap", "QuickswapDEX", "balancerlabs",
        "OceanProtocol", "GnosisSafe", "polymarket",
        "Superfluid_HQ", "FXDX_Exchange", "MMFinance_",
        "ArborFinance", "MetaStreet_xyz",
    ],

    # ── More meme coins (all chains) ──────────────────────────────────────
    "meme2": [
        "wojak_coin", "LadysToken", "DegenToken_",
        "Coq_Inu", "MogCoin_", "HarryPotterObama",
        "OrdiToken", "SATS_token", "ShibariumTech",
        "ChinaToken_", "catcoin_official",
        "ToshiOnBase", "SonicToken_", "anon_base",
    ],

    # ── NFT infra / tools ─────────────────────────────────────────────────
    "nft_infra": [
        "opensea", "blur_io", "x2y2io",
        "foundation", "SuperRare", "RaribleDotCom",
        "KnownOrigin_io", "Async_Art", "NiftyGateway",
        "ObjktCom", "Kalamint", "FxHashOfficial",
        "NiftyDrops", "nftperp", "NFTfi",
    ],

    # ── Insurance / risk protocols ────────────────────────────────────────
    "insurance": [
        "NexusMutual", "InsurAce_io", "UnslashedFin",
        "UnoRe_io", "CoverProtocol", "RiskHarbor",
        "inSure_DeFi", "TidalFinance_",
    ],

    # ── Identity / DAO / governance tools ────────────────────────────────
    "dao_infra": [
        "Snapshot_labs", "SafeGlobal", "JuiceboxETH",
        "tally_xyz", "aragon", "DAOhaus",
        "collab_land", "coordinape_org", "GitcoinDAO",
        "KarmaHQ_xyz", "rabbithole_gg",
    ],

    # ── More exchange support accounts ────────────────────────────────────
    "exchange_support2": [
        "HuobiGlobal", "HTXGlobal_Help", "BitfinexSupport",
        "Bittrex_Support", "poloniex", "HitBTC_Support",
        "Bitpanda_CS", "Bitvavo_", "NamiExchange",
        "CoinexSupport", "BTSE_official", "IndodaxCom",
        "VCC_Exchange", "ZoomexHQ", "WOO_X_Official",
    ],

    # ── More wallets ──────────────────────────────────────────────────────
    "wallets3": [
        "imTokenOfficial", "AlphaWallet", "Status_im",
        "MyCrypto", "MycEtherWallet", "trusteeWallet",
        "SteelWallet", "CoolWallet", "NGrave_io",
        "Foundation_App", "PasskeysWallet", "tokenPocket",
        "Coin98_Wallet", "BraveWallet",
    ],

    # ── Perp / derivatives DEXes — users lose $ on liquidations/errors ───
    "perp_dex": [
        "HyperliquidX", "driftprotocol", "dYdX", "GainsNetwork",
        "LevelFinance_", "MuxProtocol", "VertexProtocol",
        "KwentaEN", "SynFuturesDefi", "orderly_network",
        "aevo_xyz", "RabbitXdex", "ParadigmFi",
        "JuicedMarkets", "Vela_Exchange",
    ],

    # ── RWA / on-chain credit — stuck redemptions, yield issues ──────────
    "rwa": [
        "maplefinance", "GoldfinchFi", "centrifuge",
        "OndoFinance", "MtnProtocol", "usualmoney",
        "Superstate_co", "OpenEden_io", "TProtocol_fi",
        "backed_fi", "AngleMoney", "TrueFi_io",
    ],

    # ── More ETH DeFi — lending, yield, options ───────────────────────────
    "eth_defi": [
        "EthenaLabs", "PrismaFinance", "MorphoLabs",
        "PendleFinance", "convex_finance", "iearnfinance",
        "EulerFinance", "SiloFinance", "FluidProtocol",
        "ionicmoney", "gearboxprotocol", "term_finance",
        "AladdinDAO", "ClearpoolFin", "AjnaProtocol",
    ],

    # ── Meme coins (ETH/Base/multi-chain) ─────────────────────────────────
    "meme_eth": [
        "pepecoineth", "base_brett", "mog_coin",
        "TurboToadToken", "MemeCoinETH", "SaitamaToken",
        "ShibaInu_ETH", "elongate_official", "dogelon",
        "HarryPotterObama", "COQinuToken",
    ],

    # ── More gaming tokens — P2E, guilds, metaverse ───────────────────────
    "gaming2": [
        "Beam_gg", "MythicalGames", "PlayCarv",
        "TreasureDAO", "HeroesOfMavia", "ParallelNFT",
        "PixelmonNFT", "BigTimeStudios", "XBorgApp",
        "GuildFi", "YGG_DAO", "PlanetMojo_",
        "EV_io_game", "SuperverseNFT",
    ],

    # ── SocialFi / DeSo ───────────────────────────────────────────────────
    "socialfi": [
        "farcaster", "lens_protocol", "jokerace_",
        "orb_club", "phaver_app", "cyberconnect",
        "SocialLayerApp", "DSCVR_one", "Chingari_app",
    ],

    # ── Restaking / EigenLayer ecosystem ──────────────────────────────────
    "restaking": [
        "EigenLayer", "ether_fi", "KelpDAO",
        "puffer_finance", "RenzoProtocol", "swell_l2",
        "YieldNestFi", "InceptionLRT", "bedrock_defi",
        "omni_network", "AltLayerOfficial",
    ],

    # ── More wallets / infra ──────────────────────────────────────────────
    "wallets2": [
        "BackpackApp", "FrontierApp", "OneinchNetwork",
        "EnkryptWallet", "frame_eth", "ambire_wallet",
        "okx_wallet", "safepal_io", "gridplus_io",
        "KeystoneHQ", "CoolBitXHQ", "neon_wallet",
    ],

    # ── Emerging CEXes — smaller exchanges, more support complaints ───────
    "cex2": [
        "Phemex_official", "BingXOfficial", "AscendEX_Global",
        "LBank_Exchange", "BitgetWallet", "DigiFinexGlobal",
        "CoinWOfficial", "XT_com", "ProBit_Exchange",
        "Deepcoin_Com", "BitMart_Official", "IndodaxCom",
    ],

    # ── Stablecoin issuers — depegs, redemption issues ────────────────────
    "stablecoins": [
        "Tether_to", "circle", "MakerDAO",
        "fraxfinance", "DeFi_Franc", "liqualityio",
        "crvUSD_curve", "DEUSDX", "USDe_ethena",
    ],

    # ── zkEVM / new L2 ecosystems ─────────────────────────────────────────
    "zkevm": [
        "0xPolygonDev", "zksync", "Starknet",
        "Scroll_ZKP", "LineaBuild", "taiko_xyz",
        "ConsenSysMesh", "HorizenLabs", "KakarotZkEvm",
        "PolygonMiden",
    ],

    # ── Solana DeFi (deeper) — lending, options, perps ────────────────────
    "solana_defi": [
        "KaminoFinance", "MarginFi_", "solend_official",
        "ZetaMarkets", "NullStrike_", "PsyFinance",
        "HxroNetwork", "FluxBeamDEX", "symmetry_fi",
        "RatexSolana",
    ],

    # ── Ordinals / BTC L2 ────────────────────────────────────────────────
    "btc_l2": [
        "Ordinals", "OrdinalBTC", "inscribeNow_",
        "stacks", "merlinchain_", "BounceBit_io",
        "BedrockDiamond", "SolvProtocol", "babylon_chain",
        "corn_chain",
    ],

    # ── Derivatives / margin CEXes ────────────────────────────────────────
    "cex_derivatives": [
        "DeribitExchange", "BitMEX", "WOO_X_Official",
        "bitget_official", "MEXC_Official", "BingXOfficial",
        "CoinexExchange", "BitFuturesXYZ", "nominex_",
        "BybitDerivatives", "BinanceFutures",
        "CoinbasePrimeHQ", "KrakenFutures",
    ],

    # ── More gaming (expanded) ────────────────────────────────────────────
    "gaming3": [
        "ShrapnelGame", "WildcardNFT", "OffTheGridGame",
        "MojoMelee_", "DeadropNFT", "HeroesChained",
        "MetalCore_Game", "CryptoCavemen", "SynCityGame",
        "OthersideHQ", "niftyisland", "readyplayerme",
        "GalaxisGG", "ElmonteNFT", "OverworldNFT",
        "WindRangersGame", "FaralandGame",
    ],

    # ── More AI / DePIN ──────────────────────────────────────────────────
    "ai_depin": [
        "OceanProtocol", "RitualNet", "GensynAI",
        "Gensyn_io", "NumeraiTrader", "FetchAI_",
        "worldcoin", "TaoNetwork_", "NousResearch",
        "hyperbolic_labs", "inferencelabs_",
        "NovitaAI", "prime_intellect", "gizatechxyz",
        "allora_network",
    ],

    # ── More TON mini-apps / games ────────────────────────────────────────
    "ton3": [
        "pixeltap_io", "MatchQuest_io", "bumsapp_",
        "HamsterKombat_io", "Goats_game", "JettonDogs",
        "MajorTON", "xempire_io", "tonkombat_io",
        "Cats_HQ", "TapSwap_io", "PAWS_TON",
        "FleekTON", "OKX_TON",
    ],

    # ── More meme / viral tokens ──────────────────────────────────────────
    "meme3": [
        "apecoinsol", "gigacoinsol", "goatseus",
        "ai16zdao", "skibidi_sol", "babydogecoin",
        "Myro_sol", "PointlessToken", "TrumpMemeToken",
        "peanuttheswc", "chillguyonsol", "lockin_sol",
        "motherofmemes", "gigachadToken", "agent_sol",
    ],

    # ── Newer Solana protocols ────────────────────────────────────────────
    "solana3": [
        "sanctumso", "solayerlabs", "squadsprotocol",
        "NeonEVM", "eclipsefnd", "solaxy_io",
        "SarosFinance", "cykuraprotocol", "CrateProtocol",
        "HandleFi", "SolBlaze", "stabble_io",
        "loopscale_xyz",
    ],

    # ── Newer Base / OP / ARB protocols ──────────────────────────────────
    "base_op_arb3": [
        "extra_finance_", "Superform_xyz", "AIPX_xyz",
        "OvernightDeFi", "dysonfinance_", "toros_finance",
        "PonderFinance", "RocketX_exchange", "PancakeSwap",
        "SharkswapXYZ", "Swapr_eth", "MaverickProtocol",
        "SyncSwapDEX", "SpaceFiProtocol",
    ],

    # ── More NFT / metaverse ──────────────────────────────────────────────
    "nft3": [
        "OthersideHQ", "yugalabs", "AkutarNFT",
        "Nakamigos", "KanpaiPandas", "CryptoadzNFT",
        "Anonymice_NFT", "SappySeals", "DegenToadz",
        "ParallelNFT", "GalacticGeckoNFT",
        "FamousPixelFox", "Shinsei_Galverse",
        "HuxleyComics", "Creepz_IO",
    ],

    # ── Hyperliquid ecosystem ─────────────────────────────────────────────
    "hyperliquid": [
        "HyperliquidX", "HypurrFi", "hypurr_fun",
        "feFormance", "HyperEVM_", "pvp_trade",
        "inceptionLRT", "kiloex_official",
    ],

    # ── More on/off ramps ─────────────────────────────────────────────────
    "onramps2": [
        "AlchemyPay", "guardarian_", "itez_com",
        "BtcDirect", "Paybis", "mercuryo_io",
        "Xanpool", "banxa", "FluidFi_xyz",
        "Calypso_Protocol", "payfare",
    ],

    # ── NFT lending / perps ───────────────────────────────────────────────
    "nft_finance": [
        "NFTfi", "benddaodoteth", "X2Y2Financial",
        "JPEG_d", "nftperp", "floordao_xyz",
        "GoblinSax_", "zhartaTechFi", "unlockd_finance",
        "paraspace_xyz", "Cyan_Finance",
    ],

    # ── More DeFi protocols ───────────────────────────────────────────────
    "defi2": [
        "TempleDAO_", "OlympusDAO", "AlchemixFi",
        "reflexerfinance", "liquity_eth", "inverse_finance",
        "ribbonfinance", "STRIPSFinance", "volmexfinance",
        "unshethXYZ", "DineroXYZ", "pirexeth",
        "YieldNestFi", "Napier_Finance",
    ],

    # ── Liquid restaking (all) ────────────────────────────────────────────
    "restaking2": [
        "puffer_finance", "RenzoProtocol", "swell_l2",
        "YieldNestFi", "InceptionLRT", "bedrock_defi",
        "StaderLabs", "ClayStack_", "bifrost_finance",
        "ether_fi", "KelpDAO", "stakestone_io",
        "symbiotic_fi", "mellow_defi",
    ],

    # ── Prediction markets ────────────────────────────────────────────────
    "prediction": [
        "polymarket", "Augur", "GnosisDAO",
        "azuro_protocol", "overtime_markets",
        "sx_network", "BetSwirl", "Ostium_Labs",
    ],

    # ── Decentralized storage / data ──────────────────────────────────────
    "infra_data": [
        "Filecoin", "arweave", "StorjProject",
        "ThreeFoldio", "sia_hq", "hoprnet",
        "DeFiChain", "NuCypher", "tezos",
        "Kadena_io", "DeSo_Protocol",
    ],

    # ─────────────────────────────────────────────────────────────────────
    # REGIONAL ACCOUNTS — currently underserved crypto communities
    # These markets have huge complaint volumes that aren't caught by the
    # English-focused static list above.
    # ─────────────────────────────────────────────────────────────────────

    # ── India — one of the largest retail crypto user bases globally ──────
    "india": [
        "WazirXIndia", "CoinDCX", "ZebpayIndia", "CoinSwitchKuber",
        "unocoin", "mudrexapp", "GoSats_", "KoinX_in",
        "buyucoin", "BitBNS", "PeepalCo", "giottus_io",
        "CoinDCXSupport", "WazirXSupport",
    ],

    # ── Latin America — explosive crypto adoption, many exchange issues ───
    "latam": [
        "Bitso", "mercadobitcoin", "ripiocity", "LemonCashApp",
        "CriptoYa", "australbtc", "SatoshiTango", "belo_app",
        "CryptoBandidos", "BinanceLATAM", "cryptomkt",
        "masonfinancial_", "Blain_Crypto", "OKXLatam",
    ],

    # ── Africa — fastest growing crypto market, high complaint volume ─────
    "africa": [
        "YellowCardFin", "Luno", "Quidax", "chipper_cash",
        "bitnob_io", "bitmama_", "paxful", "Remitano",
        "CashRamp_io", "KotaniPay", "fuze_africa",
        "BinanceAfrica", "KuCoinAfrica", "leatherback_io",
    ],

    # ── Korea — world's highest per-capita crypto trading volume ──────────
    "korea": [
        "upbit_official", "BithumbCS", "KorbitOfficial",
        "CoinoneOfficial", "gopax_kor", "nfino_official",
        "BithumbGlobal", "upbitofficialcs",
    ],

    # ── Southeast Asia — Indonesia, Thailand, Philippines, Singapore ──────
    "sea": [
        "IndodaxCom", "Bitkub_official", "coinhako", "tokocrypto",
        "Tokenize_asia", "CoinPH_io", "BitazoCom",
        "BinanceTH", "KuCoinSEA", "GeminiSEA",
        "bybit_sg", "OKXSea",
    ],

    # ── Turkey — very large and active crypto market ──────────────────────
    "turkey": [
        "BtcTurk", "Paribu_com", "Bitexen_com", "icryptex",
        "BinanceTR", "KoinfineTR", "bitcashin_io",
        "OKXTurkey", "KuCoinTR",
    ],

    # ── CIS / Eastern Europe — Russia, Ukraine, Kazakhstan ───────────────
    "cis_eeurope": [
        "ExmoOfficial", "WhiteBIT", "kunaexchange",
        "BittrueOfficial", "CEX_io", "CryptoCom_CIS",
        "CoinPaymentsUA", "Binance_ua",
    ],

    # ── Middle East — UAE, Saudi, Kuwait, Bahrain ─────────────────────────
    "middle_east": [
        "Rain_HQ", "BitOasis_com", "CoinMENA_io", "BitcoinMENA",
        "Pyypl", "OKXMiddleEast", "BinanceMENA",
        "CryptoComMENA",
    ],

    # ── More global CEX support — users mention these everywhere ──────────
    "global_cex2": [
        "BinanceCS", "BybitSupport", "OKXSupport",
        "BitgetOfficial", "CoinExSupport", "XT_Official",
        "DigiFinex_", "LBank_Official", "BingXOfficial",
        "BiconomyExchange", "P2PBinance", "BinanceFiat",
    ],

    # ── More DeFi lending / yield — where users get stuck ─────────────────
    "lending2": [
        "AaveAave", "compoundfinance", "EulerFinance",
        "SiloFinance", "FluidProtocol", "ionicmoney",
        "gearboxprotocol", "term_finance", "AjnaProtocol",
        "exaprotocol", "TarotFinance", "curvance_fi",
        "zeroland_fi", "loopring_org",
    ],

    # ── Cross-chain / intent protocols — new tech, many user errors ───────
    "intent_xchain": [
        "CowProtocol", "UniswapX", "1inchNetwork",
        "openocean_", "deBridgeFinance", "SocketDotTech",
        "LifiProtocol", "TransferTo_io", "OdosProtocol",
        "SynapseProtocol", "RelayProtocol",
    ],

    # ── NFT gaming / web3 games with active complaint communities ─────────
    "nft_gaming2": [
        "HeroesOfMavia", "ParallelNFT", "PixelmonNFT",
        "BigTimeStudios", "SuperverseNFT", "BeamOnXyz",
        "MythicalGames", "WildLifeStudios", "EVIOGame",
        "UltimaOnline_", "MonkeyLeagueio", "CrabadaGame",
    ],

    # ── Stablecoins / issuers — depegs cause massive complaint spikes ─────
    "stablecoins": [
        "Tether_to", "circlepay", "PaxosGlobal",
        "TrueUSD", "FraxFinance", "MakerDAO",
        "curves", "ColonyNetwork", "crvUSDFi",
        "LUSDFi", "EthenaLabs", "USDCOfficial",
    ],

    # ── Solana ecosystem deeper — more projects, more users ───────────────
    "solana3": [
        "SolanaFM", "solscan_io", "SolanaFloor",
        "heliuslabs", "triton_one", "jito_labs",
        "hellomoon_io", "SolanaNews_", "solscanio",
        "MEV_sol", "Solana_Daily", "SolfareOfficial",
    ],

    # ── Base / OP / Arb ecosystem — growing fast ──────────────────────────
    "base_op_arb3": [
        "basescan", "arbiscan", "OptimismScan",
        "AerodromeFinance", "VelodromeFi", "camelotdex",
        "GrandBaseXYZ", "BaseSwap_fi", "RocketPoolETH",
        "ShadowOnBase", "IteraDAO_",
    ],

    # ── NFT collectibles with high trading and complaint volume ───────────
    "nft3": [
        "lilpudgys", "BoredApeYC", "pudgypenguins",
        "AzukiOfficial", "milady", "MadLads_",
        "FrogsTribe", "tensorians_", "DeGodsNFT",
        "y00tsNFT", "okay_bears", "SMB_Gen3",
    ],

    # ── HyperLiquid — fast growing, many liquidation complaints ──────────
    "hyperliquid": [
        "HyperliquidX", "HL_Liquidations", "hlperps_",
        "HyperLiquidBot", "SentioXYZ",
    ],

    # ── More on/off ramps — failed card charges, KYC stuck ───────────────
    "onramps2": [
        "MoonPay", "Transak", "RampNetwork",
        "AlchemyPay", "ChangeNOW_io", "SimpleSwap_io",
        "FixedFloat", "StealthEX_io", "SwapZone_io",
        "LetsExchange_", "exolixcom",
    ],

    # ── NFT finance — loans, fractionalization ────────────────────────────
    "nft_finance": [
        "NFTfi", "benddaodoteth", "X2Y2Financial",
        "JPEG_d", "nftperp", "floordao_xyz",
        "GoblinSax_", "zhartaTechFi", "unlockd_finance",
        "paraspace_xyz", "Cyan_Finance",
    ],

    # ── More DeFi protocols ───────────────────────────────────────────────
    "defi2": [
        "TempleDAO_", "OlympusDAO", "AlchemixFi",
        "reflexerfinance", "liquity_eth", "inverse_finance",
        "ribbonfinance", "STRIPSFinance", "volmexfinance",
        "unshethXYZ", "DineroXYZ", "pirexeth",
        "YieldNestFi", "Napier_Finance",
    ],

    # ── Liquid restaking (all) ────────────────────────────────────────────
    "restaking2": [
        "puffer_finance", "RenzoProtocol", "swell_l2",
        "YieldNestFi", "InceptionLRT", "bedrock_defi",
        "StaderLabs", "ClayStack_", "bifrost_finance",
        "ether_fi", "KelpDAO", "stakestone_io",
        "symbiotic_fi", "mellow_defi",
    ],

    # ── Prediction markets ────────────────────────────────────────────────
    "prediction": [
        "polymarket", "Augur", "GnosisDAO",
        "azuro_protocol", "overtime_markets",
        "sx_network", "BetSwirl", "Ostium_Labs",
    ],

    # ── Decentralized storage / data ──────────────────────────────────────
    "infra_data": [
        "Filecoin", "arweave", "StorjProject",
        "ThreeFoldio", "sia_hq", "hoprnet",
        "DeFiChain", "NuCypher", "tezos",
        "Kadena_io", "DeSo_Protocol",
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Dynamic account discovery via CoinGecko
# Fetches trending/top-volume projects and extracts their Twitter handles.
# Refreshes every 4 hours — captures NEW projects automatically.
# ─────────────────────────────────────────────────────────────────────────────

_DYNAMIC_ACCOUNTS: list[str] = []
_DYNAMIC_LAST_REFRESH: float = 0.0
_DYNAMIC_REFRESH_INTERVAL: float = 2 * 3600  # 2 hours — pick up new viral projects faster

# CoinGecko categories to pull top coins from (each returns up to 250 coins)
_COINGECKO_CATEGORIES = [
    "decentralized-exchange",
    "meme-token",
    "gaming",
    "non-fungible-tokens-nft",
    "layer-2",
    "decentralized-finance-defi",
    "play-to-earn",
    "artificial-intelligence",
    "real-world-assets-rwa",
    "centralized-exchange-token-cex",
    "perpetuals",
    "liquid-staking-tokens",
    "restaking",
    "ton-ecosystem",
    "solana-ecosystem",
    "base-ecosystem",
    "arbitrum-ecosystem",
    "new-cryptocurrencies",
]

# ── Rotation state persistence ────────────────────────────────────────────────
# _ROTATION_INDEX resets to 0 on every restart without persistence, causing the
# scanner to always hit the same first 18-24 categories (exchanges, wallets,
# staking…) and never reach the other 40+ categories. We persist to disk so
# rotation continues exactly where it left off after any deploy/restart.
_ROTATION_STATE_PATH = os.path.join(os.path.dirname(__file__), "x_rotation_state.json")


def _load_rotation_state() -> tuple[int, set]:
    """Load (rotation_index, recently_scanned_set) from disk."""
    try:
        if os.path.exists(_ROTATION_STATE_PATH):
            d = json.load(open(_ROTATION_STATE_PATH))
            return d.get("idx", 0), set(d.get("recently_scanned", []))
    except Exception:
        pass
    return 0, set()


def _save_rotation_state(idx: int, recently: set) -> None:
    """Persist rotation index + last 400 scanned account handles."""
    try:
        recently_list = list(recently)[-400:]
        with open(_ROTATION_STATE_PATH, "w") as f:
            json.dump({"idx": idx, "recently_scanned": recently_list}, f)
    except Exception:
        pass


_ROTATION_INDEX: int
_RECENTLY_SCANNED: set
_ROTATION_INDEX, _RECENTLY_SCANNED = _load_rotation_state()

# Last scan diagnostics — readable from app.py after each fetch_issues() call
_LAST_SCAN_STATS: dict = {}
_ROTATION_BATCH: int = 50   # accounts per 5-min cycle — 50×UserTweets = 150/15min, well under ~500/15min limit


def _cg_get(url: str, timeout: int = 10) -> dict:
    """Single CoinGecko GET with error propagation."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _fetch_twitter_handle(slug: str) -> str:
    """Return twitter_screen_name for a CoinGecko coin slug, or ''."""
    try:
        coin = _cg_get(
            f"https://api.coingecko.com/api/v3/coins/{slug}"
            "?localization=false&tickers=false&market_data=false"
            "&community_data=false&developer_data=false"
        )
        return (coin.get("links") or {}).get("twitter_screen_name", "") or ""
    except Exception:
        return ""


def _refresh_dynamic_accounts() -> None:
    """
    Pull Twitter handles from CoinGecko across multiple sources:
      1. Trending search (15 coins — real-time)
      2. Top coins per ecosystem/category (6 categories × 8 coins each)
    Refreshes every 4 hours — automatically discovers new / viral projects.
    """
    global _DYNAMIC_ACCOUNTS, _DYNAMIC_LAST_REFRESH
    now = time.time()
    if now - _DYNAMIC_LAST_REFRESH < _DYNAMIC_REFRESH_INTERVAL:
        return

    handles: list[str] = []
    existing_lower = {a.lower() for accs in _ACCOUNTS.values() for a in accs}
    existing_lower.update(h.lower() for h in _DYNAMIC_ACCOUNTS)

    def _add(tw: str) -> None:
        if tw and tw.lower() not in existing_lower and tw not in handles:
            handles.append(tw)
            existing_lower.add(tw.lower())

    # ── Source 1: Trending ────────────────────────────────────────────────
    try:
        data = _cg_get("https://api.coingecko.com/api/v3/search/trending")
        slugs = [c["item"]["id"] for c in data.get("coins", [])[:15]]
        for slug in slugs:
            _add(_fetch_twitter_handle(slug))
            time.sleep(2.5)
    except Exception as e:
        logging.warning(f"x_issues_monitor: CoinGecko trending error: {e}")

    # ── Source 2: Top coins from rotating categories ───────────────────────
    # Pick 6 categories per refresh (rotate through all on each 4h cycle)
    import random
    cats = random.sample(_COINGECKO_CATEGORIES, min(6, len(_COINGECKO_CATEGORIES)))
    for cat in cats:
        try:
            url = (
                f"https://api.coingecko.com/api/v3/coins/markets"
                f"?vs_currency=usd&category={cat}&order=volume_desc"
                f"&per_page=20&page=1&sparkline=false"
            )
            coins = _cg_get(url)
            slugs = [c["id"] for c in coins[:8]]
            for slug in slugs:
                _add(_fetch_twitter_handle(slug))
                time.sleep(2.5)
        except Exception as e:
            logging.warning(f"x_issues_monitor: CoinGecko category '{cat}' error: {e}")
        time.sleep(3)

    # ── Source 3: Top 24h gainers (new viral tokens) ───────────────────────
    try:
        gainers = _cg_get(
            "https://api.coingecko.com/api/v3/coins/markets"
            "?vs_currency=usd&order=gecko_desc&per_page=10&page=1"
            "&sparkline=false&price_change_percentage=24h"
        )
        for c in gainers[:6]:
            _add(_fetch_twitter_handle(c["id"]))
            time.sleep(2.5)
    except Exception as e:
        logging.warning(f"x_issues_monitor: CoinGecko gainers error: {e}")

    if handles:
        _DYNAMIC_ACCOUNTS = handles
        logging.info(
            f"x_issues_monitor: dynamic discovery — {len(handles)} new handles "
            f"from CoinGecko: {handles[:10]}{'...' if len(handles)>10 else ''}"
        )

    _DYNAMIC_LAST_REFRESH = now


# ─────────────────────────────────────────────────────────────────────────────
# Background CoinGecko enrichment — ALL ~17,851 coins, market-cap priority
#
# Strategy:
#   Phase 1 (fast, ~3 min): Fetch all coin IDs sorted by market cap using
#     /coins/markets pages 1-72 (250 per page). Saves the ranked ID list.
#   Phase 2 (slow, ~3 hrs): Fetch /coins/{id} for each to get twitter_screen_name.
#     Rate: 1 call / 2.5 sec = 24/min (safely under the 30/min free limit).
#     Saves handles as they are discovered — Render restarts resume from cache.
#   Refresh: weekly (7 days) — handles rarely change.
#
# This gives us every project that CoinGecko knows about, ranked by importance,
# added automatically with zero manual curation.
# ─────────────────────────────────────────────────────────────────────────────

_BG_CACHE_PATH    = os.path.join("outputs", "cache", "cg_full_handles.json")
_BG_SLUGS_PATH    = os.path.join("outputs", "cache", "cg_full_slugs.json")
_BG_ENRICHED: list[str] = []
_BG_LAST_RUN: float = 0.0
_BG_INTERVAL: float = 7 * 24 * 3600   # refresh weekly


def _load_bg_cache() -> list[str]:
    try:
        if os.path.exists(_BG_CACHE_PATH):
            with open(_BG_CACHE_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_bg_cache(handles: list[str]) -> None:
    os.makedirs(os.path.dirname(_BG_CACHE_PATH), exist_ok=True)
    with open(_BG_CACHE_PATH, "w") as f:
        json.dump(handles, f)


def _load_slug_list() -> list[str]:
    try:
        if os.path.exists(_BG_SLUGS_PATH):
            with open(_BG_SLUGS_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_slug_list(slugs: list[str]) -> None:
    os.makedirs(os.path.dirname(_BG_SLUGS_PATH), exist_ok=True)
    with open(_BG_SLUGS_PATH, "w") as f:
        json.dump(slugs, f)


def _run_bg_enrichment() -> None:
    """
    Background thread: scrape Twitter handles for ALL CoinGecko coins (~17,851),
    ordered by market cap so the most important projects are enriched first.

    Phase 1 — build ranked slug list (~3 min, runs once or on stale):
      GET /coins/markets pages 1-72, 250 coins/page, sorted by market cap desc.
      Saved to cg_full_slugs.json. Skipped on subsequent runs if file exists.

    Phase 2 — fetch handles (background, ~3-4 hours):
      GET /coins/{slug} for each → links.twitter_screen_name.
      Rate: 1 req / 2.5 s. Appends to cg_full_handles.json every 50 coins so
      restarts pick up where they left off.

    The result (_BG_ENRICHED) is used inside fetch_issues() as additional
    accounts to rotate into every scan batch.
    """
    global _BG_ENRICHED, _BG_LAST_RUN
    import threading as _threading

    def _worker():
        global _BG_ENRICHED, _BG_LAST_RUN
        time.sleep(45)   # let module finish loading first
        while True:
            now = time.time()
            if now - _BG_LAST_RUN < _BG_INTERVAL:
                time.sleep(600)
                continue

            logging.info("x_issues_monitor: bg enrichment — starting full CoinGecko scrape")
            existing_lower = {a.lower() for a in _ALL_ACCOUNTS}
            existing_lower.update(h.lower() for h in _DEFILLAMA_ACCOUNTS)
            existing_lower.update(h.lower() for h in _BG_ENRICHED)
            collected: list[str] = list(_BG_ENRICHED)

            # ── Phase 1: build ranked slug list ───────────────────────────
            slugs = _load_slug_list()
            if not slugs:
                logging.info("x_issues_monitor: bg enrichment phase1 — fetching all coin IDs")
                for page in range(1, 75):   # pages 1-74 covers ~18,500 coins
                    try:
                        url = (
                            "https://api.coingecko.com/api/v3/coins/markets"
                            f"?vs_currency=usd&order=market_cap_desc"
                            f"&per_page=250&page={page}&sparkline=false"
                        )
                        data = _cg_get(url, timeout=20)
                        if not data:
                            break
                        slugs.extend(c["id"] for c in data if "id" in c)
                        time.sleep(2.5)     # 24 req/min, safe for free tier
                    except Exception as e:
                        logging.warning(f"x_issues_monitor: bg phase1 page {page}: {e}")
                        time.sleep(10)
                _save_slug_list(slugs)
                logging.info(f"x_issues_monitor: bg enrichment phase1 done — {len(slugs)} slugs")

            # ── Phase 2: fetch handles ─────────────────────────────────────
            done_lower = {h.lower() for h in collected}
            todo = [s for s in slugs if s not in done_lower]
            logging.info(
                f"x_issues_monitor: bg enrichment phase2 — "
                f"{len(todo)} coins to fetch ({len(slugs)-len(todo)} already done)"
            )
            for i, slug in enumerate(todo):
                tw = _fetch_twitter_handle(slug)
                if tw and tw.lower() not in existing_lower:
                    collected.append(tw)
                    existing_lower.add(tw.lower())
                    done_lower.add(tw.lower())
                # Save progress every 50 coins and update live variable
                if i % 50 == 0 and i > 0:
                    _BG_ENRICHED = list(collected)
                    _save_bg_cache(collected)
                    logging.info(
                        f"x_issues_monitor: bg enrichment {i}/{len(todo)} "
                        f"({len(collected)} handles so far)"
                    )
                time.sleep(2.5)

            _BG_ENRICHED = collected
            _save_bg_cache(collected)
            _BG_LAST_RUN = time.time()
            logging.info(
                f"x_issues_monitor: bg enrichment complete — "
                f"{len(collected):,} handles from {len(slugs):,} CoinGecko coins"
            )

    t = _threading.Thread(target=_worker, daemon=True, name="cg-bg-enrichment")
    t.start()


# Load cached handles immediately on import (no API call)
_BG_ENRICHED = _load_bg_cache()
_run_bg_enrichment()

# ─────────────────────────────────────────────────────────────────────────────
# DeFiLlama protocol enrichment — 7,782 protocols, ONE API call, no key needed
# Fetches all DeFiLlama protocols and extracts Twitter handles for any protocol
# NOT already in our static list. Adds ~6,800 additional project accounts and
# refreshes every 12 hours automatically. Free, no rate limits.
# ─────────────────────────────────────────────────────────────────────────────
_DEFILLAMA_CACHE_PATH = os.path.join("outputs", "cache", "defillama_handles.json")
_DEFILLAMA_ACCOUNTS: list[str] = []
_DEFILLAMA_LAST_REFRESH: float = 0.0
_DEFILLAMA_REFRESH_INTERVAL: float = 12 * 3600   # every 12 hours


def _load_defillama_cache() -> list[str]:
    try:
        if os.path.exists(_DEFILLAMA_CACHE_PATH):
            with open(_DEFILLAMA_CACHE_PATH) as _f:
                return json.load(_f)
    except Exception:
        pass
    return []


def _save_defillama_cache(handles: list[str]) -> None:
    os.makedirs(os.path.dirname(_DEFILLAMA_CACHE_PATH), exist_ok=True)
    with open(_DEFILLAMA_CACHE_PATH, "w") as _f:
        json.dump(handles, _f)


def _refresh_defillama_accounts() -> None:
    """
    Fetch all DeFiLlama protocols (~8,000) and extract Twitter handles not already
    in our static _ALL_ACCOUNTS list. One API call, no auth, no rate limit.
    Results become additional rotation accounts in fetch_issues().
    Persists to disk so Render restarts don't re-fetch immediately.
    """
    global _DEFILLAMA_ACCOUNTS, _DEFILLAMA_LAST_REFRESH
    now = time.time()
    if now - _DEFILLAMA_LAST_REFRESH < _DEFILLAMA_REFRESH_INTERVAL:
        return
    try:
        req = urllib.request.Request(
            "https://api.llama.fi/protocols",
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as _r:
            data = json.loads(_r.read())
        existing = {a.lower() for a in _ALL_ACCOUNTS}
        handles: list[str] = []
        seen_new: set[str] = set()
        for p in data:
            tw = (p.get("twitter") or "").strip().lstrip("@").strip()
            if tw and 3 <= len(tw) <= 50 and tw.lower() not in existing and tw.lower() not in seen_new:
                handles.append(tw)
                seen_new.add(tw.lower())
        _DEFILLAMA_ACCOUNTS = handles
        _DEFILLAMA_LAST_REFRESH = now
        _save_defillama_cache(handles)
        logging.info(f"x_issues_monitor: DeFiLlama — {len(handles)} additional protocol handles discovered")
    except Exception as e:
        logging.warning(f"x_issues_monitor: DeFiLlama refresh error: {e}")


# Load DeFiLlama cache from disk on startup (instant — no API call needed)
_DEFILLAMA_ACCOUNTS = _load_defillama_cache()

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
    # Failed transactions / actions
    r"fail(?:ed)?|revert(?:ed)?|reject(?:ed)?|"
    # Missing funds
    r"missing|lost|gone|disappear(?:ed)?|vanish(?:ed)?|"
    # Access issues
    r"locked|frozen|suspended|blocked|banned|can.?t\s+access|locked\s+out|"
    # Can't do action
    r"can.?t|cannot|couldn.?t|unable\s+to|not\s+(?:able|working)|"
    # Support not responding
    r"no\s+response|not\s+responding|ignor(?:ed|ing)|no\s+reply|"
    r"been\s+waiting|still\s+waiting|waiting\s+(?:\d+|\w+\s+)\s*(?:day|hour|week)|"
    r"(?:day|hour|week)s?\s+(?:and\s+)?(?:still|no|without)|"
    # Withdrawal / deposit / transfer
    r"withdraw(?:al)?|deposit\s+(?:fail|stuck|not)|transfer\s+(?:fail|stuck)|"
    # Staking / unstaking
    r"unstake|can.?t\s+stake|staking\s+(?:issue|problem|fail|stuck)|"
    # Lost access / funds
    r"lost\s+access|lost\s+(?:my\s+)?(?:funds?|money|coins?|tokens?|eth|btc|sol|xrp)|"
    # Refund / compensation
    r"refund|compensat(?:e|ion)|reimburs(?:e|ement)|"
    # Wrong network / address
    r"wrong\s+(?:address|network|amount|chain)|sent\s+to\s+wrong|"
    r"double\s+(?:charged|deducted)|charged\s+twice|overcharged|"
    # Trading bot specific
    r"bot\s+(?:stopped|crashed|not\s+working|failed|disconnected|broke)|"
    r"api\s+(?:key|error|disconnect|invalid|expired)|"
    r"trade\s+(?:closed|failed|not\s+executed|missed)|order\s+(?:failed|rejected|stuck)|"
    r"signal\s+(?:not|failed|missed)|my\s+(?:bot|signal|trade|position)|"
    # KYC / verification
    r"kyc\s+(?:failed|rejected|stuck|pending)|verification\s+(?:failed|rejected|pending)|"
    r"identity\s+(?:rejected|failed|not\s+verified)|"
    # Card / payment
    r"card\s+(?:was\s+)?(?:declined|failed|charged|rejected)|declined\b|"
    r"payment\s+(?:failed|declined|stuck)|"
    r"charged\s+(?:but|without)|money\s+(?:deducted|taken|gone)|"
    # Mint / NFT / launchpad
    r"mint\s+(?:failed|stuck|not|didn.?t)|didn.?t\s+receive\s+(?:my|the)\s+(?:nft|token)|"
    r"ido\s+(?:allocation|not\s+received|refund)|launchpad\s+(?:issue|stuck|failed)|"
    r"airdrop\s+(?:not|didn.?t|missing|stuck)|didn.?t\s+get\s+(?:my\s+)?airdrop|"
    # Gas / fees
    r"gas\s+(?:too\s+high|stuck|failed|used\s+up)|fees?\s+(?:too\s+high|stuck|charged)|"
    # Swap / exchange specific
    r"swap\s+(?:failed|stuck|wrong)|bridge\s+(?:stuck|failed|lost)|"
    r"exchange\s+(?:rate\s+wrong|failed|stuck)|"
    # Account problems
    r"account\s+(?:banned|suspended|frozen|hacked|compromised|not\s+accessible)"
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
    Requires ONE OF:
      (a) personal pronoun + a problem word — "my withdrawal is stuck"
      (b) a direct help question about their own situation — "how do I unstake?"
      (c) a multilingual complaint phrase (Spanish/PT/TR/ID/RU/KO/ZH/JA/AR)
    """
    if _HAS_PERSONAL_RE.search(text) and _HAS_PROBLEM_RE.search(text):
        return True
    if _HELP_QUESTION_RE.search(text):
        return True
    if _MULTILINGUAL_COMPLAINT_RE.search(text):
        return True
    return False


def _is_reply_complaint(text: str) -> bool:
    """
    Check for a real user complaint in a reply under an official crypto account post.
    The reply context itself already implies personal stake, so we're less strict than
    the top-level _is_complaint, but we still require real issue signals — not just
    any negative word.

    Accepts:
      (a) personal pronoun + problem word  (e.g. "my withdrawal is stuck")
      (b) direct help question             (e.g. "how do I unstake?")
      (c) strong standalone crypto phrase  (e.g. "withdrawal stuck 3 days", "tx failed",
          "funds missing", "account suspended", "support not responding")
    Uses module-level _STRONG_REPLY_RE (compiled once, not on every call).
    """
    if _is_complaint(text):
        return True
    if _STRONG_REPLY_RE.search(text):
        return True
    if _MULTILINGUAL_COMPLAINT_RE.search(text):
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

# ── STRONG standalone reply patterns ─────────────────────────────────────────
# Compiled ONCE at module level (was previously compiled inside _is_reply_complaint
# on every single call — hundreds of recompiles per cycle).
# These fire even without a personal pronoun because the crypto context itself
# implies personal stake (the user is replying under an official project post).
_STRONG_REPLY_RE = re.compile(
    r"\b("
    # Stuck/pending with time indicator
    r"(?:stuck|pending)\s+(?:for\s+)?\d+\s+(?:day|hour|week)|"
    r"(?:still|been)\s+(?:waiting|pending)\s+(?:for\s+)?\d+|"
    r"(?:still|been)\s+waiting\s+(?:for\s+)?(?:a\s+)?(?:day|hour|week|month)|"
    r"(?:\d+\s+(?:day|hour|week)s?\s+(?:and\s+)?(?:still|no|without))|"
    # Withdrawal / deposit specific
    r"withdraw(?:al)?\s+(?:stuck|failed|pending|not\s+(?:received|credited|processed|working|showing|arrived))|"
    r"deposit\s+(?:stuck|failed|pending|not\s+(?:received|credited|processed|showing|arrived))|"
    # Funds missing / gone
    r"funds?\s+(?:gone|missing|lost|stuck|not\s+(?:received|arrived|credited|showing))|"
    r"money\s+(?:gone|missing|lost|stuck|not\s+(?:received|arrived|credited))|"
    r"(?:eth|btc|sol|bnb|usdt|usdc|tokens?|coins?)\s+(?:gone|missing|lost|stuck|not\s+received|never\s+arrived)|"
    # Account issues
    r"account\s+(?:banned|suspended|frozen|hacked|compromised|locked)|"
    # Transaction issues
    r"tx\s+(?:failed|reverted|stuck|dropped|not\s+(?:processed|confirmed))|"
    r"transaction\s+(?:failed|reverted|stuck|not\s+(?:processed|confirmed|received))|"
    # Can't do action — concise forms users actually write
    r"can.?t\s+(?:withdraw|deposit|access|stake|unstake|swap|bridge|login|sign\s*in|connect)|"
    r"unable\s+to\s+(?:withdraw|deposit|access|stake|unstake|swap|bridge|login|connect)|"
    r"not\s+able\s+to\s+(?:withdraw|deposit|access|stake|unstake|swap)|"
    # Bridge / swap
    r"bridge\s+(?:stuck|failed|not\s+(?:working|processed|arrived))|"
    r"swap\s+(?:failed|stuck|not\s+(?:working|completed))|"
    # KYC / verification
    r"kyc\s+(?:rejected|failed|stuck|not\s+(?:approved|processed|verified))|"
    r"verification\s+(?:rejected|failed|stuck|not\s+(?:approved|processed))|"
    # Support not responding
    r"no\s+response\s+(?:from\s+)?(?:support|team|customer\s+service)|"
    r"support\s+(?:not\s+responding|ignoring\s+me|never\s+replies?|not\s+helpful)|"
    r"ticket\s+(?:ignored|no\s+response|still\s+open|unanswered)|"
    # Order / trade issues
    r"order\s+(?:failed|rejected|stuck|not\s+(?:filled|executed|processed))|"
    r"trade\s+(?:failed|not\s+executed|stuck)|"
    # Exchange-specific
    r"withdrawal\s+(?:request|fee|limit|issue)|"
    r"deposit\s+(?:not\s+showing|missing|disappeared)|"
    r"login\s+(?:failed|not\s+working|issue|problem)|"
    r"2fa\s+(?:not\s+working|issue|locked)|"
    r"password\s+reset\s+(?:not\s+working|issue)|"
    # Short complaint markers
    r"this\s+is\s+(?:a\s+)?(?:scam|fraud|rug\s*pull)|"
    # Additional patterns users commonly write
    r"lost\s+(?:all\s+)?(?:my\s+)?(?:funds?|money|tokens?|coins?|eth|btc|sol|bnb|usdt)|"
    r"never\s+(?:received|credited|arrived|showed\s+up)|"
    r"(?:hours?|days?)\s+(?:later|and)\s+(?:still|nothing|no\s+response)|"
    r"(?:sent|transferred)\s+(?:but|and)\s+(?:never|not)\s+(?:received|arrived|credited)|"
    r"where\s+(?:are|is)\s+my\s+(?:funds?|money|tokens?|coins?|withdrawal|deposit)|"
    r"getting\s+(?:scammed|rugged|ignored)|"
    r"(?:no|zero)\s+(?:support|response|help)\s+from"
    r")\b",
    re.IGNORECASE,
)

# ── Multilingual complaint patterns ──────────────────────────────────────────
# Covers the major non-English crypto user bases: Spanish, Portuguese, Turkish,
# Indonesian, Russian, Korean, Chinese, Japanese, Arabic.
# These are compiled once at module level for performance.
_MULTILINGUAL_COMPLAINT_RE = re.compile(
    r"("
    # ── Spanish (massive LatAm/Spain crypto community) ────────────────────
    r"no\s+puedo\s+retirar|retiro\s+(?:bloqueado|fallido|pendiente|atascado)|"
    r"fondos?\s+(?:bloqueados?|congelados?|perdidos?|desaparecidos?)|"
    r"transacc?i[oó]n\s+(?:fallida|no\s+procesada|rechazada|fallida)|"
    r"cuenta\s+(?:suspendida|bloqueada|congelada|baneada)|"
    r"no\s+recibi[oó]\s+(?:mis?\s+)?(?:fondos?|tokens?|monedas?)|"
    r"deposito\s+no\s+lleg[oó]|saldo\s+no\s+(?:aparece|actualiz)|"
    r"soporte\s+no\s+responde|sin\s+respuesta\s+(?:del?\s+)?soporte|"
    r"me\s+(?:robaron|estafaron|hackearon|bloquearon\s+la\s+cuenta)|"
    r"no\s+(?:puedo\s+acceder|me\s+deja\s+entrar)\s+a\s+mi\s+cuenta|"
    # ── Portuguese (Brazil — huge crypto market) ──────────────────────────
    r"n[aã]o\s+consigo\s+sacar|saque\s+(?:bloqueado|falhou|pendente|preso)|"
    r"fundos?\s+(?:bloqueados?|perdidos?|sumiu|desapareceu)|"
    r"transaç[aã]o\s+(?:falhou|recusada|n[aã]o\s+processada)|"
    r"conta\s+(?:suspensa|bloqueada|congelada|banida)|"
    r"n[aã]o\s+recebi\s+(?:o\s+)?(?:depósito|saque|token|fundos?)|"
    r"dep[oó]sito\s+n[aã]o\s+(?:chegou|caiu|apareceu)|"
    r"suporte\s+n[aã]o\s+(?:responde|resposta)|me\s+(?:roubaram|golpearam|hackearam)|"
    r"meu\s+dinheiro\s+(?:sumiu|desapareceu|n[aã]o\s+chegou)|"
    # ── Turkish (very large and active crypto market) ─────────────────────
    r"para\s+[cç]ekemiyorum|[cç]ekim\s+(?:tak[iı]l[iı]|ba[sş]ar[iı]s[iı]z|bekliyor)|"
    r"hesab[iı]m\s+(?:donduruldu|ask[iı]ya\s+al[iı]nd[iı]|engellendi)|"
    r"i[sş]lem\s+ba[sş]ar[iı]s[iı]z|paran?\s+(?:kayboldu|gitti|bulunam[iı]yor)|"
    r"destek\s+cevap\s+vermiyor|m[üu][şs]teri\s+hizmetleri\s+(?:yok|cevap\s+vermiyor)|"
    r"param[iı]\s+[cç]ekemiyorum|yat[iı]r[iı]m(?:ım)?\s+gelmedi|"
    # ── Indonesian (large crypto population, growing rapidly) ────────────
    r"tidak\s+bisa\s+(?:withdraw|tarik\s+dana)|penarikan\s+(?:gagal|pending|terganjal)|"
    r"dana\s+(?:tidak\s+masuk|hilang|raib|belum\s+masuk)|"
    r"transaksi\s+(?:gagal|bermasalah|error)|akun\s+(?:diblokir|ditangguhkan|dibekukan)|"
    r"deposit\s+tidak\s+masuk|saldo\s+(?:tidak\s+muncul|belum\s+bertambah)|"
    r"cs\s+tidak\s+(?:merespons|membalas)|support\s+tidak\s+(?:membalas|membantu)|"
    r"uang\s+(?:hilang|tidak\s+masuk|raib)|"
    # ── Russian (CIS countries have very active crypto users) ─────────────
    r"не\s+могу\s+вывести|вывод\s+(?:застрял|заблокирован|не\s+прошёл|завис)|"
    r"средства\s+(?:заблокированы|пропали|не\s+поступили|исчезли)|"
    r"транзакция\s+(?:не\s+прошла|застряла|отклонена|зависла)|"
    r"аккаунт\s+(?:заблокирован|заморожен|приостановлен|взломан)|"
    r"поддержка\s+не\s+отвечает|служба\s+поддержки\s+(?:молчит|не\s+реагирует)|"
    r"деньги\s+(?:пропали|не\s+пришли|украли)|меня\s+взломали|"
    r"вывести\s+нельзя|депозит\s+не\s+зачислен|"
    # ── Korean (highest per-capita crypto trading in the world) ───────────
    r"출금이\s*(?:안돼|안됩니다|실패|안됨|막혔|오류|불가)|"
    r"입금이\s*(?:안됐|안됩니다|안왔|실패|오류)|"
    r"계정이\s*(?:정지|동결|잠겼|차단|비활성화)|"
    r"자금이\s*(?:없어졌|사라졌|증발|묶였|분실)|"
    r"거래\s*(?:실패|오류|안됨)|고객센터\s*(?:응답없음|답없음|연락안됨)|"
    r"코인이\s*(?:사라졌|없어졌|분실)|지갑\s*(?:오류|해킹|접속불가)|"
    # ── Chinese Simplified (enormous crypto user base) ─────────────────────
    r"提[币幣]失败|出金失败|提现失败|无法提[币幣]|无法提现|"
    r"账户(?:被封|冻结|封禁|被锁|被停用)|"
    r"转账失败|充值未到账|资金丢失|钱包被盗|币不见了|"
    r"提[币幣]卡住|出金卡住|客服不回|联系不上客服|联系不到|"
    r"无法登录|账号被封|提币受限|出入金失败|"
    # ── Japanese ──────────────────────────────────────────────────────────
    r"出金できない|引き出せない|取引失敗|"
    r"資金が消えた|アカウントが凍結|サポートが応答しない|"
    r"送金失敗|入金されない|ウォレットが開けない|"
    # ── Arabic (Middle East + North Africa crypto market) ─────────────────
    r"لا\s+أستطيع\s+السحب|السحب\s+معلق|الأموال\s+مفقودة|"
    r"حسابي\s+محظور|لم\s+أستلم|الدعم\s+لا\s+يرد|"
    r"فشل\s+التحويل|الرصيد\s+مجمد|تم\s+اختراق\s+حسابي"
    r")",
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
    "trading_bots":  "🤖 TRADING BOT",
    "onramps":       "💳 ON/OFF RAMP",
    "portfolio":     "📊 PORTFOLIO TOOL",
    "nft_tools":     "🖼️  NFT TOOL",
    "defi_tools":    "⚙️  DEFI TOOL",
    "payments":      "💸 CRYPTO PAYMENT",
    "launchpads":    "🚀 LAUNCHPAD",
    "copy_trading":  "📋 COPY TRADING",
    "altl1":         "🔵 ALT-CHAIN",
    "security":      "🚨 SECURITY",
    "market":        "📊 MARKET",
    "bitcoin":       "₿  BITCOIN",
    "misc":          "🔥 CRYPTO",
    # Regional
    "india":         "🇮🇳 INDIA CRYPTO",
    "latam":         "🌎 LATAM CRYPTO",
    "africa":        "🌍 AFRICA CRYPTO",
    "korea":         "🇰🇷 KOREA CRYPTO",
    "sea":           "🌏 SEA CRYPTO",
    "turkey":        "🇹🇷 TURKEY CRYPTO",
    "cis_eeurope":   "🌐 CIS/E-EUROPE CRYPTO",
    "middle_east":   "🌙 MIDDLE EAST CRYPTO",
    "global_cex2":   "🏛️ EXCHANGE",
    "lending2":      "💸 LENDING",
    "intent_xchain": "⚡ CROSS-CHAIN",
    "nft_gaming2":   "🎮 GAMING / NFT",
    "stablecoins":   "💵 STABLECOIN",
    "solana3":       "◎  SOLANA",
    "base_op_arb3":  "🔵 L2",
    "nft3":          "🖼️  NFT",
    "hyperliquid":   "📈 HYPERLIQUID",
    "onramps2":      "💳 ON/OFF RAMP",
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
    per_account: int = 40,
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

    Step 0 (NEW): SearchTimeline keyword search via residential proxy.
      Searches ALL of Twitter for complaint keywords — not just reply threads
      under monitored accounts. Massively expands worldwide coverage.
    """
    seen_ids = seen_ids or set()

    global _ROTATION_INDEX, _RECENTLY_SCANNED

    auth, ct0 = _load_creds()
    if not auth or not ct0:
        logging.warning("x_issues_monitor: no credentials")
        return []

    # Refresh CoinGecko discovery (no-op if < refresh interval)
    _refresh_dynamic_accounts()

    # Refresh DeFiLlama protocol list (7,782 handles, one API call every 12h)
    _refresh_defillama_accounts()

    session  = _make_session(auth, ct0)
    cache    = _load_user_id_cache()
    # 36-hour cutoff so we catch complaints posted on slowly-replied support threads.
    # Dedup via seen_ids prevents re-sending anything already sent.
    cutoff   = time.time() - 36 * 3600

    # ── Step 1: Build account pool + rotate batch ─────────────────────────
    # DESIGN:
    #   Tier 1 (ALWAYS): 16 highest-signal accounts scanned every cycle
    #     — BinanceHelpDesk, Coinbase, MetaMask etc. have hundreds of complaints/day
    #     — Their IDs are hardcoded in _KNOWN_USER_IDS so no UserByScreenName calls
    #   Tier 2 (ROTATING): 24 accounts, one randomly chosen per category
    #     — _ROTATION_INDEX persists to disk → never resets on restart
    #     — random.choice() per category gives intra-category variety
    #     — recently_scanned set avoids repeating same account 2 cycles in a row
    #   Total: ~40 accounts per cycle, all within UserTweets rate limit (~300/15min)
    import random as _rand

    # Priority accounts — guaranteed every cycle, IDs hardcoded in x_scraper.py
    _PRIORITY_ALWAYS = [
        # Exchange support — highest complaint volume
        "BinanceHelpDesk", "CoinbaseSupport", "KrakenSupport", "Bybit_CS",
        "OKXSupport", "HTXGlobal_Help",
        # Wallet support
        "MetaMask_Support", "TrustWalletApp", "LedgerSupport", "phantom",
        # DeFi + L2 flagships
        "AaveAave", "JupiterExchange", "arbitrum", "base",
        # Security monitors (live exploit/hack feed)
        "PeckShieldAlert", "BlockSecTeam",
    ]
    # Rotating slots: pick 24 accounts from ALL categories this cycle.
    # 16 priority + 32 rotating = 48 total. With 15-min interval each account
    # set stays safely under Twitter's ~300 UserTweets calls per 15-min window.
    _ROTATE_SLOTS = 32

    dynamic_new   = [h for h in _DYNAMIC_ACCOUNTS   if h.lower() not in _seen_set]
    bg_new        = [h for h in _BG_ENRICHED         if h.lower() not in _seen_set]
    defillama_new = [h for h in _DEFILLAMA_ACCOUNTS  if h.lower() not in _seen_set]

    # Build batch: priority first, then rotating category picks
    batch_set: set[str] = {a.lower() for a in _PRIORITY_ALWAYS}
    batch: list[str] = list(_PRIORITY_ALWAYS)

    # Rotate through all categories. Each cycle we advance the starting index by
    # _ROTATE_SLOTS so every category appears roughly once every ≈3 cycles (45 min).
    # Within each category we use random.choice() to pick a different account each
    # time, preferring accounts NOT recently scanned for maximum variety.
    cat_list = list(_ACCOUNTS.items())
    num_cats = len(cat_list)
    added = 0
    for step in range(num_cats):
        if added >= _ROTATE_SLOTS:
            break
        cat_idx = (_ROTATION_INDEX * _ROTATE_SLOTS + step) % num_cats
        cat, cat_accounts = cat_list[cat_idx]
        if not cat_accounts:
            continue
        # Prefer accounts not scanned in the last 2 cycles (~30 min)
        fresh = [a for a in cat_accounts if a.lower() not in _RECENTLY_SCANNED]
        candidates = fresh if fresh else cat_accounts
        pick = _rand.choice(candidates)
        if pick.lower() not in batch_set:
            batch_set.add(pick.lower())
            batch.append(pick)
            added += 1

    # Fold in CoinGecko + DeFiLlama discovered accounts
    # DeFiLlama: 4 rotating picks from ~6,800 protocol handles not in static list
    for h in (dynamic_new + bg_new)[:6]:
        if h.lower() not in batch_set:
            batch_set.add(h.lower())
            batch.append(h)
    # Rotate through DeFiLlama accounts: pick 4 per cycle offset by rotation index
    dl_offset = (_ROTATION_INDEX * 4) % max(len(defillama_new), 1)
    for h in (defillama_new[dl_offset:dl_offset+4] or defillama_new[:4]):
        if h.lower() not in batch_set:
            batch_set.add(h.lower())
            batch.append(h)

    # Advance rotation and persist so restarts don't repeat same categories
    _ROTATION_INDEX += 1
    # Track what was just scanned so next cycle picks different accounts
    _RECENTLY_SCANNED = (_RECENTLY_SCANNED | {a.lower() for a in batch})
    # Keep only last 2 cycles worth to avoid blocking too many accounts
    if len(_RECENTLY_SCANNED) > len(batch) * 2 + 50:
        _RECENTLY_SCANNED = set(list(_RECENTLY_SCANNED)[-len(batch)*2:])
    _save_rotation_state(_ROTATION_INDEX, _RECENTLY_SCANNED)

    _rand.shuffle(batch)
    all_accounts_to_scan = batch
    pool_size = len(_ALL_ACCOUNTS) + len(dynamic_new) + len(bg_new) + len(defillama_new)

    logging.info(
        f"x_issues_monitor: scanning {len(all_accounts_to_scan)} accounts "
        f"(pool={pool_size:,}, static={len(_ALL_ACCOUNTS)}, defillama={len(defillama_new):,}, "
        f"dynamic={len(dynamic_new)}, rotation_idx={_ROTATION_INDEX}, "
        f"fresh_candidates={len(batch) - len(_PRIORITY_ALWAYS)})"
    )

    # ── Step 0: Global keyword search via residential proxy ───────────────
    # Uses SearchTimeline (blocked from GCP IPs) via Webshare residential proxy
    # to search ALL of Twitter for complaint keywords — not just monitored accounts.
    # This catches complaints from ANY user about ANY crypto project worldwide.
    # Each query returns 20 tweets; we rotate through queries each cycle.
    # ── 300+ search queries across every chain, exchange, category, language ──
    # Structured in tiers so the most impactful queries run every cycle:
    #   Tier 1 (broad)  — catch ANY crypto complaint regardless of project
    #   Tier 2 (chain)  — specific L1/L2 complaints
    #   Tier 3 (project) — top CEX/DEX/wallet/bridge/DeFi complaints
    #   Tier 4 (category) — NFT, gaming, stablecoin, oracle, RWA, etc.
    #   Tier 5 (language) — 9 non-English languages
    _SEARCH_QUERIES = [
        # ── Tier 1: Broad — catch ANY project ──────────────────────────────
        '(withdrawal OR withdraw) (stuck OR failed OR blocked OR pending) crypto -is:retweet min_faves:1',
        '(lost OR missing OR stolen) (funds OR tokens OR crypto) -scam -is:retweet min_faves:1',
        '(wallet OR account) (hacked OR compromised OR drained) crypto -is:retweet min_faves:1',
        '(transaction OR tx) failed crypto (help OR support) -is:retweet min_faves:1',
        '(swap OR bridge) failed (crypto OR defi OR tokens) -is:retweet min_faves:1',
        '(deposit OR withdrawal) (not received OR not showing OR missing) crypto -is:retweet min_faves:1',
        '"customer support" (no response OR ignored OR useless) crypto -is:retweet min_faves:1',
        '(seed phrase OR private key) (stolen OR leaked OR compromised) -is:retweet min_faves:1',
        '(smart contract OR protocol) (exploit OR hack OR vulnerability) crypto -is:retweet',
        '(liquidated OR liquidation) (defi OR crypto OR position) (unfair OR wrong OR bug) -is:retweet',
        '(airdrop OR tokens) (not received OR not sent OR missing) -is:retweet min_faves:1',
        '(KYC OR verification) (rejected OR failed OR blocked) crypto exchange -is:retweet min_faves:1',
        'crypto exchange (down OR offline OR maintenance) (funds OR withdrawal) -is:retweet min_faves:1',
        '(gas fees OR gas fee) (stuck OR failed OR too high) ethereum -is:retweet min_faves:2',
        '(rug pull OR rugpull OR rugged) crypto (lost OR funds OR money) -is:retweet min_faves:2',
        '(phishing OR fake site OR impersonator) crypto (stole OR drained) -is:retweet min_faves:1',
        # ── Tier 2: L1 / L2 chain-specific ────────────────────────────────
        '(withdrawal OR transaction) stuck OR failed ethereum -is:retweet min_faves:1',
        '(withdrawal OR transaction) stuck OR failed solana -is:retweet min_faves:1',
        '(withdrawal OR transaction) stuck OR failed "BNB" OR "binance smart chain" -is:retweet',
        '(withdrawal OR transaction) failed polygon OR matic -is:retweet min_faves:1',
        '(bridge OR transaction) stuck arbitrum -is:retweet min_faves:1',
        '(bridge OR transaction) stuck optimism OR "OP mainnet" -is:retweet min_faves:1',
        '(bridge OR transaction) stuck base -is:retweet min_faves:1',
        '(transaction OR withdrawal) failed avalanche OR avax -is:retweet min_faves:1',
        '(transaction OR withdrawal) failed tron OR TRC20 -is:retweet min_faves:1',
        '(transaction OR bridge) failed "TON" OR "the open network" -is:retweet min_faves:1',
        '(transaction OR withdrawal) failed near protocol -is:retweet min_faves:1',
        '(transaction OR staking) failed cosmos OR atom OR osmosis -is:retweet min_faves:1',
        '(transaction OR bridge) failed starknet -is:retweet min_faves:1',
        '(transaction OR bridge) failed zkSync -is:retweet min_faves:1',
        '(transaction OR bridge) failed scroll -is:retweet min_faves:1',
        '(transaction OR bridge) failed blast (crypto OR L2) -is:retweet min_faves:1',
        '(transaction OR staking) failed cardano OR ADA -is:retweet min_faves:1',
        '(transaction OR bridge) failed sui (crypto OR blockchain) -is:retweet min_faves:1',
        '(transaction OR bridge) failed aptos -is:retweet min_faves:1',
        '(transaction OR staking) failed polkadot OR DOT -is:retweet min_faves:1',
        '(transaction OR staking) failed fantom OR FTM -is:retweet min_faves:1',
        '(transaction OR staking) failed "injective" OR INJ -is:retweet min_faves:1',
        # ── Tier 3A: CEX-specific ──────────────────────────────────────────
        '@Binance (withdrawal OR account OR funds) (stuck OR blocked OR missing OR frozen) -is:retweet',
        '@binance_cs OR @BinanceHelpDesk (problem OR issue OR help) -is:retweet min_faves:1',
        '@coinbase (withdrawal OR account OR funds) (stuck OR blocked OR missing OR frozen) -is:retweet',
        '@CoinbaseSupport (problem OR issue OR not working) -is:retweet min_faves:1',
        '@krakensupport OR @kraken (withdrawal OR funds) (stuck OR blocked OR missing) -is:retweet',
        '@OKX OR @OKXSupport (withdrawal OR funds OR account) (blocked OR missing OR frozen) -is:retweet',
        '@Bybit_Official OR @Bybit_CS (withdrawal OR account OR funds) (stuck OR blocked) -is:retweet',
        '@KuCoin_Shares OR @KuCoinUpdates (withdrawal OR funds) (stuck OR blocked) -is:retweet',
        '@gate_io (withdrawal OR funds) (stuck OR blocked OR missing) -is:retweet min_faves:1',
        '@HuobiGlobal OR @HTX_Global (withdrawal OR funds) (stuck OR blocked) -is:retweet',
        '@bitfinex (withdrawal OR funds) (stuck OR blocked OR missing) -is:retweet min_faves:1',
        '@BitgetWallet OR @bitgetglobal (withdrawal OR funds) (stuck OR blocked) -is:retweet',
        '@mexc_official (withdrawal OR funds) (stuck OR blocked) -is:retweet min_faves:1',
        '@CryptoComOfficial (withdrawal OR funds) (stuck OR blocked OR missing) -is:retweet',
        '@WazirX OR @ZebPay (withdrawal OR funds) (stuck OR blocked) -is:retweet min_faves:1',
        '@coinswitch_kuber OR @CoinDCX (withdrawal OR funds) stuck OR blocked -is:retweet',
        # ── Tier 3B: Wallet-specific ──────────────────────────────────────
        '@MetaMask (transaction OR gas OR funds) (failed OR stuck OR wrong) -is:retweet min_faves:1',
        '@TrustWallet (transaction OR funds OR NFT) (failed OR missing OR stuck) -is:retweet',
        '@phantom (transaction OR NFT OR funds) failed OR stuck -is:retweet min_faves:1',
        '@LedgerHQ OR @LedgerSupport (device OR transaction OR funds) (issue OR failed OR stuck) -is:retweet',
        '@Trezor (device OR transaction OR funds) (issue OR failed OR stuck) -is:retweet min_faves:1',
        '@CoinbaseWallet (transaction OR funds) (failed OR stuck OR missing) -is:retweet',
        '@rabby_io OR @rainbow_me (transaction OR funds) failed OR stuck -is:retweet min_faves:1',
        '@safe (multisig OR transaction) (failed OR stuck OR issue) -is:retweet min_faves:1',
        # ── Tier 3C: DeFi — DEX / bridge / lending ───────────────────────
        '@Uniswap (swap OR liquidity OR funds) (failed OR stuck OR issue) -is:retweet min_faves:1',
        '@1inch (swap OR transaction) failed OR stuck -is:retweet min_faves:1',
        '@AaveAave (liquidation OR borrow OR position) (wrong OR failed OR issue) -is:retweet',
        '@compoundfinance (liquidation OR borrow) (wrong OR failed) -is:retweet min_faves:1',
        '@MakerDAO OR @MakerDAO (vault OR liquidation OR DAI) (failed OR issue OR wrong) -is:retweet',
        '@LidoFinance (staking OR withdrawal OR stETH) (failed OR stuck OR issue) -is:retweet',
        '@CurveFinance (swap OR pool OR funds) (failed OR stuck OR issue) -is:retweet min_faves:1',
        '@LayerZero_Core OR @Arbitrum bridge (stuck OR failed OR lost) -is:retweet min_faves:1',
        '@wormhole (bridge OR tokens) (stuck OR lost OR failed) -is:retweet min_faves:1',
        '@across_protocol (bridge OR tokens) (stuck OR lost OR failed) -is:retweet min_faves:1',
        '@HyperliquidX (trade OR position OR liquidation) (wrong OR failed OR issue) -is:retweet',
        '@dydxprotocol (trade OR liquidation OR withdrawal) (wrong OR failed) -is:retweet min_faves:1',
        '@JupiterExchange (swap OR tokens) (failed OR stuck OR missing) -is:retweet min_faves:1',
        '@RaydiumProtocol (swap OR pool OR liquidity) (failed OR stuck) -is:retweet min_faves:1',
        '@SushiSwap (swap OR funds) failed OR stuck -is:retweet min_faves:1',
        '@PancakeSwap (swap OR funds) failed OR stuck -is:retweet min_faves:1',
        # ── Tier 3D: Staking / LST / yield ───────────────────────────────
        '(staking withdrawal OR unstaking) (stuck OR delayed OR failed) crypto -is:retweet min_faves:1',
        '(liquid staking OR stETH OR rETH OR wstETH) (issue OR bug OR withdrawal) -is:retweet min_faves:1',
        '@EtherFi_io OR @swell_l2 (withdrawal OR staking) (failed OR stuck) -is:retweet',
        '@RocketPool (withdrawal OR node OR rETH) (failed OR stuck OR issue) -is:retweet min_faves:1',
        '(restaking OR EigenLayer) (slash OR issue OR failed OR stuck) -is:retweet min_faves:1',
        # ── Tier 4A: NFT / gaming ─────────────────────────────────────────
        '(NFT OR nfts) (stolen OR hacked OR missing OR drained) wallet -is:retweet min_faves:1',
        '@opensea (NFT OR listing OR offer) (missing OR stolen OR failed) -is:retweet min_faves:1',
        '@Blur_io (NFT OR bid OR trade) (failed OR issue OR missing) -is:retweet min_faves:1',
        '@MagicEden (NFT OR listing OR trade) (failed OR missing OR stolen) -is:retweet',
        '(web3 game OR play to earn OR GameFi) (tokens OR rewards) (missing OR stuck OR not sent) -is:retweet min_faves:1',
        '@AxieInfinity OR @axie (rewards OR tokens OR SLP) (missing OR stuck OR failed) -is:retweet',
        '@StepN_official (GST OR GMT OR rewards) (missing OR stuck OR failed) -is:retweet',
        # ── Tier 4B: Stablecoin / RWA / oracle ───────────────────────────
        '(USDT OR USDC OR DAI OR BUSD) (frozen OR blacklisted OR not transferring) -is:retweet min_faves:1',
        '@Tether_to OR @Circle (USDT OR USDC) (frozen OR issue OR blacklisted) -is:retweet min_faves:1',
        '(tokenized stocks OR RWA OR real world asset) (issue OR failed OR stuck) crypto -is:retweet min_faves:1',
        '@chainlink (oracle OR price feed OR data) (wrong OR failed OR issue) -is:retweet min_faves:1',
        # ── Tier 4C: Memecoins / launchpads / airdrops ───────────────────
        '(memecoin OR meme coin) (rug pull OR rugpull OR scam OR stolen) -is:retweet min_faves:2',
        '(pump.fun OR launchpad) (rug OR scam OR stolen OR drained) -is:retweet min_faves:1',
        '(airdrop OR claim) (not working OR failed OR not received OR scam) crypto -is:retweet min_faves:1',
        '(presale OR ICO OR IDO) (rug OR scam OR funds not returned) -is:retweet min_faves:2',
        '(PEPE OR WIF OR BONK OR SHIB) (stolen OR drained OR rug) -is:retweet min_faves:2',
        # ── Tier 4D: Perps / options / derivatives ────────────────────────
        '(perpetual OR perp OR futures) (liquidated OR wrong price OR forced close) crypto -is:retweet min_faves:1',
        '(options OR expiry) (lost OR wrong OR issue) crypto trading -is:retweet min_faves:1',
        '(copy trading OR social trading) (lost OR issue OR stopped) crypto -is:retweet min_faves:1',
        # ── Tier 5: Non-English — 9 languages × multiple complaint types ──
        # Spanish (400M speakers — massive LatAm crypto market)
        '(retiro OR retirar) (bloqueado OR fallido OR atascado) (cripto OR crypto OR exchange) -is:retweet',
        '(fondos OR tokens) (perdidos OR robados OR desaparecidos) cripto -is:retweet min_faves:1',
        '(billetera OR wallet) (hackeada OR comprometida OR vaciada) cripto -is:retweet min_faves:1',
        '(intercambio OR exchange) (caído OR sin respuesta OR bloqueado) cripto -is:retweet min_faves:1',
        '@Binance OR @coinbase (problema OR error OR bloqueado) retiro -is:retweet min_faves:1',
        'estafa crypto (perdí OR robaron OR desapareció) -is:retweet min_faves:1',
        # Portuguese (220M speakers — Brazil is top-5 crypto market)
        '(saque OR retirada) (bloqueado OR falhou OR travado) (cripto OR crypto) -is:retweet',
        '(fundos OR tokens) (perdidos OR roubados OR sumiu) cripto -is:retweet min_faves:1',
        '(carteira OR wallet) (hackeada OR invadida OR drenada) cripto -is:retweet min_faves:1',
        '(golpe OR scam) crypto (perdi OR roubaram OR sumiu) -is:retweet min_faves:1',
        'corretora (crypto OR cripto) (fora do ar OR bloqueada OR suporte) -is:retweet min_faves:1',
        # Korean (South Korea — one of highest crypto adoption rates)
        '출금 (오류 OR 실패 OR 막힘 OR 안됨) -is:retweet min_faves:1',
        '코인 (해킹 OR 도난 OR 분실) -is:retweet min_faves:1',
        '(거래소 OR 지갑) (오류 OR 먹통 OR 점검) -is:retweet min_faves:1',
        '스테이킹 (오류 OR 실패 OR 지연) -is:retweet min_faves:1',
        'NFT (도난 OR 사기 OR 오류) -is:retweet min_faves:1',
        # Chinese (1.4B potential — despite bans, offshore trading massive)
        '提币 (失败 OR 卡住 OR 不到账) -is:retweet',
        '(账户 OR 钱包) (被盗 OR 被封 OR 冻结) 加密货币 -is:retweet',
        '(交易所 OR 合约) (跑路 OR 骗局 OR 出问题) -is:retweet min_faves:1',
        '(空投 OR airdrop) (没收到 OR 失败 OR 骗局) -is:retweet min_faves:1',
        # Turkish (growing crypto market, high inflation driving adoption)
        'para çekemiyorum (kripto OR borsa OR exchange) -is:retweet',
        '(cüzdan OR hesap) (hacklendi OR çalındı OR donduruldu) kripto -is:retweet',
        '(borsa OR exchange) (dolandırıcılık OR hata OR çöktü) kripto -is:retweet min_faves:1',
        'kripto (kayıp OR çalıntı OR dolandırıcı) -is:retweet min_faves:1',
        # Russian (CIS region — large crypto usage under sanctions)
        'вывод (застрял OR заблокирован OR не пришел) крипто -is:retweet',
        '(кошелек OR биржа) (взломан OR заморожен OR недоступна) крипто -is:retweet',
        '(токены OR монеты) (украли OR потерял OR не пришли) крипто -is:retweet min_faves:1',
        'мошенники (крипто OR биткоин OR токены) -is:retweet min_faves:1',
        # Indonesian (SE Asia — massive crypto adoption)
        '(penarikan OR withdraw) (gagal OR ditahan OR tidak masuk) kripto -is:retweet',
        '(dompet OR wallet) (kena hack OR diretas OR dibobol) kripto -is:retweet',
        'penipuan kripto (uang OR token OR dana) hilang -is:retweet min_faves:1',
        # Hindi (India — world's largest crypto user base by number)
        'क्रिप्टो (निकासी OR विड्रॉल) (फंसा OR विफल OR अटका) -is:retweet min_faves:1',
        'क्रिप्टो (धोखाधड़ी OR हैक OR स्कैम) -is:retweet min_faves:1',
        # Vietnamese (SE Asia — fast-growing crypto market)
        '(rút tiền OR withdraw) (thất bại OR bị chặn) crypto -is:retweet min_faves:1',
        '(ví OR wallet) (bị hack OR mất tiền) crypto -is:retweet min_faves:1',
        # Arabic (Gulf states — massive crypto wealth)
        'السحب (معلق OR فاشل OR محجوب) كريبتو -is:retweet',
        '(محفظة OR حساب) (اختراق OR سرقة OR تجميد) كريبتو -is:retweet',
        'احتيال كريبتو (أموال OR رموز) -is:retweet min_faves:1',
        # Japanese
        '(出金 OR 引き出し) (失敗 OR できない OR 詰まった) 暗号資産 -is:retweet',
        '(ウォレット OR 取引所) (ハック OR 不正アクセス OR 凍結) 暗号 -is:retweet min_faves:1',
        # ── Tier 6: Emerging threats & new project categories ──────────────
        '(AI agent OR AI crypto OR AI token) (rug OR scam OR failed) -is:retweet min_faves:2',
        '(DePIN OR decentralized physical) (issue OR failed OR rug) -is:retweet min_faves:1',
        '(SocialFi OR friend.tech OR social token) (rug OR drained OR failed) -is:retweet min_faves:1',
        '(prediction market OR Polymarket) (funds OR withdrawal) (issue OR failed) -is:retweet min_faves:1',
        '(perpetual DEX OR GMX OR Gains) (liquidation OR position OR issue) wrong -is:retweet min_faves:1',
        '(cross-chain OR multichain) bridge (stuck OR lost OR failed) -is:retweet min_faves:1',
        '(LST OR liquid staking token) (issue OR de-peg OR failed) -is:retweet min_faves:1',
        'crypto (tax OR IRS OR HMRC) (locked OR issue OR wrong) exchange -is:retweet min_faves:1',
        '"account frozen" exchange (crypto OR bitcoin OR coins) -is:retweet min_faves:1',
        '"funds not received" crypto exchange -is:retweet min_faves:1',
        '"transaction pending" (hours OR days) crypto (stuck OR help) -is:retweet min_faves:1',
        '"wrong address" crypto (sent OR transferred) (help OR recovery) -is:retweet min_faves:1',
    ]

    search_complaints: list[dict] = []
    try:
        from proxy_pool import make_proxied_session
        from x_scraper import search_keyword_complaints as _search_kw, BEARER as _BEARER
        proxy_sess = make_proxied_session(auth, ct0, _BEARER)
        if proxy_sess:
            import threading as _sth

            # Pick 20 queries per cycle, rotating through all 300+ over time
            # Full rotation every ~15 cycles (~3-4 hours at 15-min cycle interval)
            q_offset = (_ROTATION_INDEX - 1) * 20
            selected_queries = [_SEARCH_QUERIES[(q_offset + i) % len(_SEARCH_QUERIES)]
                                for i in range(20)]

            # Run searches in parallel batches of 4 to maximise throughput
            # (4 threads × 5 batches = 20 queries; each thread sleeps 1s between calls)
            _search_lock = _sth.Lock()
            _search_buf: list[dict] = []

            def _run_search_batch(queries: list[str]) -> None:
                for q in queries:
                    try:
                        results = _search_kw(q, proxy_sess, count=20)
                        with _search_lock:
                            for t in results:
                                tid = t.get("id", "")
                                if tid and tid not in seen_ids:
                                    t["source_cat"] = "search"
                                    t["search_query"] = q
                                    _search_buf.append(t)
                                    seen_ids.add(tid)
                    except Exception as _qe:
                        logging.debug(f"x_issues_monitor: search query error: {_qe}")
                    time.sleep(1.2)

            # Split 20 queries into 4 threads of 5 each
            _batch_size = 5
            _threads = []
            for _bi in range(0, len(selected_queries), _batch_size):
                _t = _sth.Thread(
                    target=_run_search_batch,
                    args=(selected_queries[_bi:_bi + _batch_size],),
                    daemon=True
                )
                _threads.append(_t)
                _t.start()
            for _t in _threads:
                _t.join(timeout=90)   # max 90s for all searches

            search_complaints = _search_buf
            logging.info(
                f"x_issues_monitor: step0 — {len(search_complaints)} tweets via "
                f"{len(selected_queries)} queries ({len(_SEARCH_QUERIES)} total in pool)"
            )
    except Exception as _se:
        logging.warning(f"x_issues_monitor: step0 search error: {_se}")

    official_tweets: list[dict] = []
    global_seen: set[str] = set(seen_ids)
    ids_resolved = 0
    ids_failed   = 0

    for screen_name in all_accounts_to_scan:
        uid = get_user_id(screen_name, session, cache)
        if not uid:
            ids_failed += 1
            continue
        ids_resolved += 1
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

    logging.info(
        f"x_issues_monitor: step1 — {ids_resolved} IDs resolved, "
        f"{ids_failed} failed (429/cache miss), {len(official_tweets)} recent tweets found"
    )

    # ── Step 2: Fetch reply threads ────────────────────────────────────────
    # Use TweetDetail on ALL fetched official tweets — not sorted by popularity.
    # A tweet with 2 likes from @MetaMask support still has real users replying
    # with issues. We want those low-engagement replies just as much as viral ones.
    # Shuffle so every account gets a fair chance across cycles.
    import random
    reply_sources = list(official_tweets)
    random.shuffle(reply_sources)
    # Cap at 120 to control API usage per cycle (each call = 1 TweetDetail request).
    # Raising from 60→120 doubles the reply thread coverage per cycle, catching
    # more complaints from low-engagement posts that still have real user replies.
    reply_sources = reply_sources[:120]

    user_reply_tweets: list[dict] = []  # replies from random community users
    total_raw_replies = 0

    for src in reply_sources:
        src_id  = src.get("id", "")
        src_cat = src.get("source_cat", "misc")
        src_user = src.get("user", "")
        if not src_id:
            continue
        replies = fetch_tweet_replies(src_id, session, max_age_hours=36)
        total_raw_replies += len(replies)
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

    logging.info(
        f"x_issues_monitor: step2 — checked {len(reply_sources)} reply threads, "
        f"{total_raw_replies} raw replies, {len(user_reply_tweets)} unique non-self replies"
    )

    # ── Step 3: Classify ──────────────────────────────────────────────────
    global _LAST_SCAN_STATS

    bucket_a: list[dict] = []  # user complaint replies + search results ← PRIORITY
    bucket_b: list[dict] = []  # official urgent
    bucket_c: list[dict] = []  # official trending

    # --- Process Step 0 search results (direct keyword-matched complaints) ---
    # These come from SearchTimeline across ALL of Twitter — highest coverage.
    # Apply same spam/complaint filters but no source/category caps (diverse sources).
    _search_source_count: dict[str, int] = {}
    for t in search_complaints:
        text = t.get("text", "")
        tid  = t.get("id", "")
        if not text or tid in seen_ids:
            continue
        if _is_spam(text):
            continue
        # For search results, require either the multilingual pattern OR standard complaint
        if not (_is_reply_complaint(text) or _MULTILINGUAL_COMPLAINT_RE.search(text)):
            continue
        src = t.get("user", "").lower()
        # Limit per-user to 1 from search results to ensure variety
        if _search_source_count.get(src, 0) >= 1:
            continue
        _search_source_count[src] = _search_source_count.get(src, 0) + 1
        seen_ids.add(tid)
        bucket_a.append({
            "type":          "user_complaint",
            "category":      "search",
            "reply_to_user": "",
            "tweet_id":      tid,
            "text":          text[:500],
            "url":           t.get("url", ""),
            "date":          t.get("date", ""),
            "user":          t.get("user", ""),
            "likes":         t.get("likes", 0),
            "retweets":      t.get("retweets", 0),
            "tokens":        extract_tokens(text),
            "urgent":        _is_complaint(text),
            "search_query":  t.get("search_query", ""),
        })

    # --- Process user reply tweets (Bucket A) ---
    # Two-level cap to enforce variety across ALL projects:
    #   Per-source cap:   max 2 per official account → stops @Binance flooding
    #   Per-category cap: max 2 per category (exchanges/wallets/DeFi/bridges/etc.)
    #                     so every category gets a turn even across the same cycle
    _per_source_count: dict[str, int] = {}
    _per_cat_count:    dict[str, int] = {}
    _PER_SOURCE_CAP = 2   # up to 2 complaints per official account per cycle
    _PER_CAT_CAP    = 2   # up to 2 complaints per category per cycle (variety enforced across 100+ categories)

    for t in user_reply_tweets:
        text = t.get("text", "")
        tid  = t.get("id", "")
        if not text or tid in seen_ids:
            continue
        if _is_spam(text):
            continue
        if not _is_reply_complaint(text):
            continue
        src = t.get("reply_to_user", "").lower()
        cat = t.get("reply_to_cat", t.get("source_cat", "misc"))
        if _per_source_count.get(src, 0) >= _PER_SOURCE_CAP:
            continue
        if _per_cat_count.get(cat, 0) >= _PER_CAT_CAP:
            continue
        _per_source_count[src] = _per_source_count.get(src, 0) + 1
        _per_cat_count[cat]    = _per_cat_count.get(cat, 0) + 1
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

    # Expose per-account fetch statuses for debugging
    try:
        from x_scraper import _LAST_FETCH_STATUS as _fetch_debug
        fetch_debug_sample = dict(list(_fetch_debug.items())[:20])
    except Exception:
        fetch_debug_sample = {}

    _LAST_SCAN_STATS.update({
        "batch_size":      len(all_accounts_to_scan),
        "pool_size":       pool_size,
        "defillama_pool":  len(defillama_new),
        "ids_resolved":    ids_resolved,
        "ids_failed":      ids_failed,
        "search_tweets":   len(search_complaints),
        "official_tweets": len(official_tweets),
        "reply_threads":   len(reply_sources),
        "raw_replies":     total_raw_replies,
        "unique_replies": len(user_reply_tweets),
        "bucket_a":       len(bucket_a),
        "bucket_b":       len(bucket_b),
        "bucket_c":       len(bucket_c),
        "fetch_debug":    fetch_debug_sample,
    })

    return bucket_a + bucket_b + bucket_c


# ─────────────────────────────────────────────────────────────────────────────
# Async wrapper
# ─────────────────────────────────────────────────────────────────────────────

async def afetch_issues(
    scraper=None, categories=None,
    seen_ids: Optional[set] = None,
    per_query_count: int = 40,
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
