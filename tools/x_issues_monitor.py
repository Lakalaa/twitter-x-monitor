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

# Account rotation state — scan a rotating batch each cycle instead of all at once
_ROTATION_INDEX: int = 0

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
# Background CoinGecko enrichment — top 500 coins by market cap
# Runs once in a background thread on startup, refreshes every 24 hours.
# Slowly fetches each coin's Twitter handle (1 call / 3 sec) and saves to disk.
# This gives us 300-500 additional project accounts automatically, covering
# thousands of coins we'd never manually list.
# ─────────────────────────────────────────────────────────────────────────────

_BG_CACHE_PATH = os.path.join("outputs", "cache", "cg_top500_handles.json")
_BG_ENRICHED: list[str] = []
_BG_LAST_RUN: float = 0.0
_BG_INTERVAL: float = 24 * 3600

def _load_bg_cache() -> list[str]:
    """Load previously discovered handles from disk."""
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

def _run_bg_enrichment() -> None:
    """
    Background thread: fetch top 500 coins by market cap from CoinGecko,
    resolve each to its Twitter handle, persist to disk.
    Runs once every 24 hours. Sleeps 3s between each detail call to respect
    CoinGecko free-tier rate limits (~20 req/min).
    """
    global _BG_ENRICHED, _BG_LAST_RUN
    import threading as _threading

    def _worker():
        global _BG_ENRICHED, _BG_LAST_RUN
        time.sleep(30)  # wait for _ALL_ACCOUNTS to be fully populated at module level
        while True:
            now = time.time()
            if now - _BG_LAST_RUN < _BG_INTERVAL:
                time.sleep(300)  # check every 5 min
                continue

            logging.info("x_issues_monitor: starting background CoinGecko top-500 enrichment")
            existing_lower = {a.lower() for a in _ALL_ACCOUNTS}
            existing_lower.update(h.lower() for h in _BG_ENRICHED)
            collected: list[str] = list(_BG_ENRICHED)  # keep existing

            # Fetch top 500 by market cap across 2 pages
            slugs: list[str] = []
            for page in (1, 2):
                try:
                    url = (
                        f"https://api.coingecko.com/api/v3/coins/markets"
                        f"?vs_currency=usd&order=market_cap_desc"
                        f"&per_page=250&page={page}&sparkline=false"
                    )
                    data = _cg_get(url, timeout=15)
                    slugs.extend(c["id"] for c in data)
                    time.sleep(5)
                except Exception as e:
                    logging.warning(f"x_issues_monitor: bg enrichment page {page} error: {e}")

            logging.info(f"x_issues_monitor: bg enrichment fetching handles for {len(slugs)} coins")

            for slug in slugs:
                tw = _fetch_twitter_handle(slug)
                if tw and tw.lower() not in existing_lower:
                    collected.append(tw)
                    existing_lower.add(tw.lower())
                time.sleep(3)  # respect free-tier rate limit

            _BG_ENRICHED = collected
            _save_bg_cache(collected)
            _BG_LAST_RUN = time.time()
            logging.info(
                f"x_issues_monitor: bg enrichment done — "
                f"{len(collected)} total handles cached to disk"
            )

    t = _threading.Thread(target=_worker, daemon=True, name="cg-bg-enrichment")
    t.start()

# Load any previously cached handles immediately on import
_BG_ENRICHED = _load_bg_cache()
_run_bg_enrichment()

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
    Requires EITHER:
      (a) personal pronoun + a problem word — "my withdrawal is stuck"
      (b) a direct help question about their own situation — "how do I unstake?"
    """
    if _HAS_PERSONAL_RE.search(text) and _HAS_PROBLEM_RE.search(text):
        return True
    if _HELP_QUESTION_RE.search(text):
        return True
    return False


def _is_reply_complaint(text: str) -> bool:
    """
    Relaxed check for reply context (someone replying under an official crypto account post).
    The reply context itself implies personal stake — we only need a problem signal, not a pronoun.
    Accepts:
      (a) personal pronoun + problem word (strict match from _is_complaint)
      (b) help question pattern
      (c) problem word alone — "withdrawal stuck", "bridge not working", "failed transaction"
    This catches real complaints like "still pending 3 days", "tx reverted", "can't unstake"
    that don't use "I/my" explicitly.
    """
    if _is_complaint(text):
        return True
    # In reply context: problem keyword alone is enough
    if _HAS_PROBLEM_RE.search(text):
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

    global _ROTATION_INDEX

    auth, ct0 = _load_creds()
    if not auth or not ct0:
        logging.warning("x_issues_monitor: no credentials")
        return []

    # Refresh CoinGecko discovery (no-op if < 4 hours since last refresh)
    _refresh_dynamic_accounts()

    session  = _make_session(auth, ct0)
    cache    = _load_user_id_cache()
    # 24-hour cutoff: fetch tweets from past 24h so we catch accounts that tweet
    # infrequently. Dedup via seen_ids prevents re-sending old complaints.
    cutoff   = time.time() - 24 * 3600

    # ── Step 1: Build account pool + rotate batch ─────────────────────────
    # Always include the highest-complaint accounts in every cycle so we surface
    # issues from the biggest platforms every 5 minutes, not just once per ~100 min.
    # Remaining slots rotate through the full pool to cover smaller projects too.
    _PRIORITY_ALWAYS = [
        # CEX support (highest complaint volume)
        "BinanceHelpDesk", "binance", "CoinbaseSupport", "coinbase",
        "KrakenSupport", "krakenfx", "Bybit_CS", "Bybit_Official",
        "OKXSupport", "OKX", "cryptocom_cares", "crypto_com",
        # Wallet support
        "MetaMask_Support", "MetaMask", "TrustWalletApp", "LedgerSupport",
        "phantom", "RabbyWallet",
        # Top DeFi / DEX
        "Uniswap", "AaveAave", "JupiterExchange", "GMX_IO",
        # Top L1 / L2
        "arbitrum", "optimismFND", "0xPolygon", "base",
        # Bridges & aggregators
        "StargateFinance", "LayerZero_Core", "across_protocol",
    ]
    priority_set = {a.lower() for a in _PRIORITY_ALWAYS}

    import random as _rand
    dynamic_new = [h for h in _DYNAMIC_ACCOUNTS if h.lower() not in _seen_set]
    bg_new      = [h for h in _BG_ENRICHED     if h.lower() not in _seen_set]
    full_pool   = _ALL_ACCOUNTS + dynamic_new + bg_new

    # Rotating pool = everything NOT in the priority always-on set
    rotating_pool = [h for h in full_pool if h.lower() not in priority_set]

    # Fill: always-on accounts first, then rotating slots
    rotate_slots = max(0, _ROTATION_BATCH - len(_PRIORITY_ALWAYS))
    pool_size    = len(rotating_pool)
    start        = (_ROTATION_INDEX * rotate_slots) % max(pool_size, 1)
    rotating_batch = rotating_pool[start : start + rotate_slots]
    if len(rotating_batch) < rotate_slots:
        rotating_batch += rotating_pool[: rotate_slots - len(rotating_batch)]
    _ROTATION_INDEX += 1

    batch = list(_PRIORITY_ALWAYS) + rotating_batch
    _rand.shuffle(batch)
    all_accounts_to_scan = batch

    logging.info(
        f"x_issues_monitor: scanning {len(all_accounts_to_scan)} accounts "
        f"(pool={pool_size}, dynamic={len(dynamic_new)}, "
        f"rotation_idx={_ROTATION_INDEX})"
    )

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
    # Cap at 60 to control API usage per cycle (each call = 1 TweetDetail request)
    reply_sources = reply_sources[:60]

    user_reply_tweets: list[dict] = []  # replies from random community users
    total_raw_replies = 0

    for src in reply_sources:
        src_id  = src.get("id", "")
        src_cat = src.get("source_cat", "misc")
        src_user = src.get("user", "")
        if not src_id:
            continue
        replies = fetch_tweet_replies(src_id, session, max_age_hours=24)
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
        # In a reply context: relaxed check — problem word alone is enough.
        # We don't require personal pronouns since replying to an official post
        # already signals personal context. "withdrawal stuck", "tx failed" etc.
        if not _is_reply_complaint(text):
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

    _LAST_SCAN_STATS.update({
        "batch_size":     len(all_accounts_to_scan),
        "ids_resolved":   ids_resolved,
        "ids_failed":     ids_failed,
        "official_tweets": len(official_tweets),
        "reply_threads":  len(reply_sources),
        "raw_replies":    total_raw_replies,
        "unique_replies": len(user_reply_tweets),
        "bucket_a":       len(bucket_a),
        "bucket_b":       len(bucket_b),
        "bucket_c":       len(bucket_c),
    })

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
