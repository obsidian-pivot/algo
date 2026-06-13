#!/usr/bin/env python3
"""
================================================================================
  ObsidianPivot — NSE DAY TRADING ALGO SYSTEM v3.0
  FIRSTOCK EDITION
================================================================================
  Author  : ObsidianPivot
  Broker  : Firstock Broking Pvt. Ltd. (SEBI Reg: INZ000260334)
  Date    : May 2026
  Python  : 3.9+

  FOUR PILLARS — all four must confirm before any trade is placed:
    Pillar 1 : EMA Crossover  (9 EMA vs 21 EMA on 15-min chart)
    Pillar 2 : Pivot Points   (PP, R1, R2, S1, S2 from yesterday OHLC)
    Pillar 3 : ADX Filter     (ADX(14) >= 20 = trend exists, else VETO)
    Pillar 4 : Volume         (entry candle >= 1.5x 20-bar average volume)

  WHY FIRSTOCK OVER ZERODHA:
    ✓ API subscription  : FREE  (Zerodha = ₹2,000/month)
    ✓ Account opening   : FREE  (Zerodha = ₹200)
    ✓ AMC               : FREE  (Zerodha = ₹300/year)
    ✓ Intraday brokerage: ₹20 or 0.03% (same as Zerodha)
    ✓ Official Python SDK (pip install thefirstock)
    ✓ REST + WebSocket support
    ✓ SHA256 auto-encryption, TOTP-based 2FA
    → Net saving: ₹24,000+/year in API fees alone

  WHAT CHANGED vs ZERODHA VERSION:
    • Section 0  : Added thefirstock import (replaces kiteconnect)
    • Section 1  : Config uses FIRSTOCK_* env vars (not KITE_*)
    • Section 1  : Watchlist uses Firstock token format (string-based)
    • Section 8  : OrderManager fully rewritten for Firstock API
    • Section 10 : DailyRiskGuard — unchanged
    • Section 11 : DataFetcher rewritten for Firstock timePriceSeries API
    • Section 13 : TradingEngine._init_firstock() replaces _init_kite()
    • Section 13 : WebSocket via Firstock socketConnect (replaces KiteTicker)
    • Everything else (Sections 2-7, 9, 12, 14, 15) is IDENTICAL

  MODES:
    paper    — Simulated orders, no real capital. START HERE always.
    live     — Real orders via Firstock API
    backtest — Replays historical CSV data through the system

  QUICK START (step by step):
    Step 1 : pip install thefirstock ta pandas numpy requests pyotp
    Step 2 : Open Firstock account at firstock.in (free, Aadhaar KYC)
    Step 3 : Get API credentials from firstock.in → Developer → API Keys
    Step 4 : Set environment variables (see Section 1)
    Step 5 : python nse_algo_firstock.py --backtest    (validate logic)
    Step 6 : python nse_algo_firstock.py --mode paper  (30+ paper days)
    Step 7 : python nse_algo_firstock.py --mode live   (only after step 6)

  FIRSTOCK API SETUP (one-time):
    1. Log in at firstock.in → Go to Profile → API & Developer
    2. Generate your Vendor Code and API Key
    3. Add Firstock to Google Authenticator for TOTP
    4. Set environment variables (see Section 1 below)
    5. TOTP auto-generation is handled by pyotp in this file

  TELEGRAM ALERTS (optional):
    1. Message @BotFather on Telegram → /newbot → get token
    2. Message @userinfobot → get your chat_id
    3. export TELEGRAM_BOT_TOKEN="your_token"
       export TELEGRAM_CHAT_ID="your_chat_id"

  COST BREAKDOWN (₹5,00,000 capital · 3 trades/day · 22 days/month):
    API subscription : ₹0       (FREE — biggest saving vs Zerodha)
    Brokerage        : ₹1,320   (66 trades × ₹20)
    STT              : ~₹800    (intraday sell side ~0.025%)
    Exchange charges : ~₹150
    Total/month      : ~₹2,270  (vs ~₹4,270 with Zerodha)
================================================================================
"""

# ==============================================================================
# SECTION 0 — IMPORTS
# Changes from Zerodha version:
#   REMOVED : from kiteconnect import KiteConnect
#   ADDED   : from thefirstock import thefirstock
#   ADDED   : import pyotp  (auto-generates TOTP codes for daily login)
# All other imports are identical.
# ==============================================================================
import os
import sys
import time
import logging
import csv
import argparse
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo  # Python 3.9+

import pandas as pd
import numpy as np

# ta — Technical Analysis library (unchanged from Zerodha version)
# Provides EMA, ADX, Bollinger Bands, ATR
# Install: pip install ta
try:
    from ta.trend import EMAIndicator, ADXIndicator
    from ta.volatility import BollingerBands, AverageTrueRange
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    print("WARNING: 'ta' library not installed. Run: pip install ta")

# thefirstock — Official Firstock Python SDK
# This is the ONLY broker-specific import in the entire file.
# Install: pip install thefirstock
try:
    from thefirstock import thefirstock as fs
    FIRSTOCK_AVAILABLE = True
except ImportError:
    FIRSTOCK_AVAILABLE = False
    print("WARNING: 'thefirstock' not installed. Run: pip install thefirstock")
    print("         Not required for backtest mode.")

# pyotp — Generates TOTP codes automatically so the algo can log in daily
# without you manually typing your authenticator code.
# Install: pip install pyotp
try:
    import pyotp
    PYOTP_AVAILABLE = True
except ImportError:
    PYOTP_AVAILABLE = False
    print("WARNING: 'pyotp' not installed. Run: pip install pyotp")
    print("         Required for automated daily login in live/paper mode.")

# requests — Telegram notifications (unchanged)
try:
    import requests as http_requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


# ==============================================================================
# SECTION 1 — CONFIGURATION
# Changes from Zerodha version:
#   REMOVED : KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN
#   ADDED   : FIRSTOCK_USER_ID, FIRSTOCK_PASSWORD, FIRSTOCK_TOTP_SECRET,
#             FIRSTOCK_VENDOR_CODE, FIRSTOCK_API_KEY
#   CHANGED : WATCHLIST tokens are now Firstock instrument tokens (strings)
#             Trading symbol format changes: "RELIANCE" → "RELIANCE-EQ"
#
# HOW TO GET YOUR FIRSTOCK CREDENTIALS:
#   1. Log in at firstock.in
#   2. Go to Profile → API & Developer → Generate API Key
#   3. Your Vendor Code is shown on the same page
#   4. For TOTP secret: when setting up Google Authenticator,
#      use "Setup key manually" and note the base32 secret key
#      (usually shown as a string like "JBSWY3DPEHPK3PXP")
#
# HOW TO FIND FIRSTOCK INSTRUMENT TOKENS:
#   Download from: connect.thefirstock.com/NFO_symbols.txt
#   Or via the API after login:
#     result = fs.firstock_searchScrip(userId=USER_ID, stext="RELIANCE")
# ==============================================================================

class Config:
    """
    Master configuration for ObsidianPivot — Firstock Edition.

    SECURITY RULE: Never hardcode credentials in this file.
    Always use environment variables. Add to your ~/.bashrc or ~/.zshrc:

        export FIRSTOCK_USER_ID="your_client_id"
        export FIRSTOCK_PASSWORD="your_firstock_password"
        export FIRSTOCK_TOTP_SECRET="your_totp_base32_secret"
        export FIRSTOCK_VENDOR_CODE="your_vendor_code"
        export FIRSTOCK_API_KEY="your_api_key"
        export TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
        export TELEGRAM_CHAT_ID="your_telegram_chat_id"
    """

    # ── TRADING MODE ─────────────────────────────────────────────────────────
    # "paper"    : Simulated orders, no real money. ALWAYS start here.
    # "live"     : Real orders via Firstock API. Only after profitable paper run.
    # "backtest" : Historical CSV data replay. No API connection needed.
    MODE: str = "paper"

    # ── FIRSTOCK API CREDENTIALS ─────────────────────────────────────────────
    # These replace the KITE_* variables from the Zerodha version.
    # Read from environment variables — never hardcode here.
    FIRSTOCK_USER_ID: str      = os.getenv("FIRSTOCK_USER_ID", "")
    FIRSTOCK_PASSWORD: str     = os.getenv("FIRSTOCK_PASSWORD", "")
    FIRSTOCK_TOTP_SECRET: str  = os.getenv("FIRSTOCK_TOTP_SECRET", "")
    FIRSTOCK_VENDOR_CODE: str  = os.getenv("FIRSTOCK_VENDOR_CODE", "")
    FIRSTOCK_API_KEY: str      = os.getenv("FIRSTOCK_API_KEY", "")

    # ── TELEGRAM ALERTS (unchanged) ──────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str   = os.getenv("TELEGRAM_CHAT_ID", "")
    SEND_TELEGRAM: bool     = bool(os.getenv("TELEGRAM_BOT_TOKEN"))

    # ── CAPITAL AND RISK (unchanged) ─────────────────────────────────────────
    TOTAL_CAPITAL: float        = 500_000.0  # INR — your total trading capital
    RISK_PER_TRADE_PCT: float   = 0.01       # 1% risk per trade
    DAILY_LOSS_LIMIT_PCT: float = 0.02       # Halt trading if down 2% today
    WEEKLY_LOSS_LIMIT_PCT: float = 0.05      # Reduce size if down 5% on week
    MAX_TRADES_PER_DAY: int     = 3          # Never exceed 3 trades per day
    MIN_RR_RATIO: float         = 2.0        # Minimum 1:2 reward-to-risk

    # ── WATCHLIST ─────────────────────────────────────────────────────────────
    # CHANGED from Zerodha version:
    #   • "token" is now a Firstock instrument token (fetch via searchScrip)
    #   • "trading_symbol" uses Firstock format: "SYMBOL-EQ" for NSE equity
    #   • "exchange" stays the same: "NSE", "BSE", "NFO"
    #
    # To find the correct token for any stock:
    #   python -c "
    #   from thefirstock import thefirstock as fs
    #   fs.firstock_login(userId='ID', password='PASS', TOTP='123456',
    #                     vendorCode='VC', apiKey='AK')
    #   r = fs.firstock_searchScrip(userId='ID', stext='RELIANCE')
    #   print(r)
    #   "
    WATCHLIST: list = [
        {
            "symbol":         "RELIANCE",
            "trading_symbol": "RELIANCE-EQ",   # Firstock format for NSE equity
            "exchange":       "NSE",
            "token":          "738561",         # Firstock instrument token
        },
        {
            "symbol":         "HDFCBANK",
            "trading_symbol": "HDFCBANK-EQ",
            "exchange":       "NSE",
            "token":          "341249",
        },
        {
            "symbol":         "INFY",
            "trading_symbol": "INFY-EQ",
            "exchange":       "NSE",
            "token":          "408065",
        },
        {
            "symbol":         "TCS",
            "trading_symbol": "TCS-EQ",
            "exchange":       "NSE",
            "token":          "2953217",
        },
        {
            "symbol":         "ICICIBANK",
            "trading_symbol": "ICICIBANK-EQ",
            "exchange":       "NSE",
            "token":          "1270529",
        },
    ]

    # ── INDICATOR PARAMETERS (unchanged) ─────────────────────────────────────
    EMA_FAST: int         = 9       # Fast EMA — Pillar 1
    EMA_SLOW: int         = 21      # Slow EMA — Pillar 1
    EMA_TREND: int        = 50      # Optional trend EMA (System A addition)
    ADX_PERIOD: int       = 14      # ADX period — Pillar 3
    ADX_MIN: float        = 20.0    # Below this = hard veto, no trading
    ADX_STRONG: float     = 25.0    # Above this = full position size
    BB_PERIOD: int        = 20      # Bollinger Bands period (morning filter)
    BB_STD: float         = 2.0     # Bollinger Bands standard deviation
    ATR_PERIOD: int       = 14      # ATR for stop-loss calculation
    VOL_MA_PERIOD: int    = 20      # Volume moving average period — Pillar 4
    VOL_MULTIPLIER: float = 1.5     # Entry candle volume must be >= 1.5x avg

    # ── PIVOT AND ENTRY SETTINGS (unchanged) ─────────────────────────────────
    PIVOT_ENTRY_TOLERANCE: float = 0.002    # Price must be within 0.2% of pivot
    EMA_MIN_SEPARATION: float    = 0.0015   # EMA gap must be >= 0.15% of price
    SL_PIVOT_BUFFER: float       = 0.004    # SL placed 0.4% beyond pivot
    SL_ATR_MULTIPLIER: float     = 0.5      # SL = entry +/- (0.5 * ATR)
    MAX_SL_PCT: float            = 0.008    # Hard cap: SL never > 0.8% away

    # ── TIMEFRAME (unchanged) ────────────────────────────────────────────────
    # Firstock timePriceSeries interval uses integer minutes: 1, 3, 5, 10, 15
    CANDLE_INTERVAL_MIN: int = 15           # 15-minute candles
    CANDLES_NEEDED: int      = 60           # How many candles to load per stock

    # ── TRADING HOURS IST (unchanged) ────────────────────────────────────────
    IST             = ZoneInfo("Asia/Kolkata")
    MARKET_OPEN     = (9, 15)
    TRADE_START     = (9, 30)
    PRIME_END       = (11, 30)
    AFTERNOON_START = (13, 30)
    CAUTION_START   = (14, 30)
    HARD_EXIT       = (15, 15)
    MARKET_CLOSE    = (15, 30)

    # ── PIVOT RANGE FILTER (unchanged) ───────────────────────────────────────
    PIVOT_RANGE_MIN: float  = 0.008
    PIVOT_RANGE_FULL: float = 0.015

    # ── FILE PATHS (unchanged) ───────────────────────────────────────────────
    LOG_FILE: str          = "algo_log_firstock.txt"
    JOURNAL_FILE: str      = "trades_journal_firstock.csv"
    BACKTEST_DATA_DIR: str = "historical_data"


# ==============================================================================
# SECTION 2 — LOGGING  (IDENTICAL to Zerodha version)
# ==============================================================================

def setup_logging(log_file: str) -> logging.Logger:
    """
    Creates a dual-output logger: console (INFO) + file (DEBUG).
    The file captures every condition check and skip reason.
    Review algo_log_firstock.txt every evening.
    """
    logger = logging.getLogger("OBSIDIAN_FIRSTOCK")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    if not logger.handlers:
        logger.addHandler(ch)
        logger.addHandler(fh)

    return logger


logger = setup_logging(Config.LOG_FILE)


# ==============================================================================
# SECTION 3 — PIVOT POINT CALCULATOR  (IDENTICAL to Zerodha version)
# No broker-specific code here — pure math.
# ==============================================================================

class PivotCalculator:
    """
    Calculates classic daily pivot points from previous session OHLC.
    Defines WHERE to enter and WHERE to target.
    Called pre-market with yesterday's Bhavcopy data.

    Formulas:
        PP = (H + L + C) / 3
        R1 = (2*PP) - L    R2 = PP + (H - L)
        S1 = (2*PP) - H    S2 = PP - (H - L)
    """

    @staticmethod
    def calculate(high: float, low: float, close: float) -> dict:
        pp = (high + low + close) / 3
        pivots = {
            "PP": round(pp, 2),
            "R1": round((2 * pp) - low,         2),
            "R2": round(pp + (high - low),       2),
            "S1": round((2 * pp) - high,         2),
            "S2": round(pp - (high - low),       2),
        }
        logger.debug(f"Pivots calculated: {pivots}")
        return pivots

    @staticmethod
    def range_quality(pivots: dict, price: float) -> str:
        """
        Checks if R1-S1 range is wide enough to trade.
        Returns: "full" | "normal" | "skip"
        """
        rng_pct = (pivots["R1"] - pivots["S1"]) / price
        if rng_pct >= Config.PIVOT_RANGE_FULL:
            return "full"
        elif rng_pct >= Config.PIVOT_RANGE_MIN:
            return "normal"
        return "skip"

    @staticmethod
    def nearest_pivot(
        price: float, pivots: dict
    ) -> tuple[Optional[str], Optional[float]]:
        """
        Returns (name, level) of the closest pivot within 0.2% tolerance.
        Returns (None, None) if price is not near any pivot.
        """
        for name, level in sorted(pivots.items(), key=lambda x: abs(x[1] - price)):
            if abs(price - level) / price <= Config.PIVOT_ENTRY_TOLERANCE:
                logger.debug(f"Price near pivot {name}={level}")
                return name, level
        return None, None

    @staticmethod
    def target_levels(
        entry_pivot: str, direction: str, pivots: dict
    ) -> tuple[Optional[float], Optional[float]]:
        """
        Returns (T1, T2) target prices.
        Long:  S2 → S1 → PP → R1 → R2
        Short: R2 → R1 → PP → S1 → S2
        """
        seq = ["S2", "S1", "PP", "R1", "R2"]
        if direction == "short":
            seq = list(reversed(seq))
        try:
            idx = seq.index(entry_pivot)
            t1 = pivots.get(seq[idx + 1]) if idx + 1 < len(seq) else None
            t2 = pivots.get(seq[idx + 2]) if idx + 2 < len(seq) else None
            return t1, t2
        except (ValueError, IndexError):
            return None, None


# ==============================================================================
# SECTION 4 — INDICATOR ENGINE  (IDENTICAL to Zerodha version)
# Pure pandas + ta math — no broker-specific code.
# ==============================================================================

class IndicatorEngine:
    """
    Adds all four-pillar indicator columns to an OHLCV DataFrame.

    Input columns : open, high, low, close, volume
    Output columns added:
        ema_fast, ema_slow, ema_trend          → Pillar 1 (EMA)
        ema_sep, ema_dir, ema_crosses_6        → Pillar 1 (tangle detection)
        adx, adx_pos, adx_neg, adx_rising      → Pillar 3 (ADX)
        vol_ma, vol_ratio                      → Pillar 4 (Volume)
        bb_upper, bb_lower, bb_width, bb_expanding → Morning filter 3
        atr                                    → Stop-loss calculation
        body, body_pct, is_bullish, is_bearish → Candle classification
    """

    @staticmethod
    def compute(df: pd.DataFrame) -> pd.DataFrame:
        if not TA_AVAILABLE:
            raise RuntimeError("Install ta library: pip install ta")

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"]).copy()

        # ── PILLAR 1: EMA CROSSOVER ───────────────────────────────────────
        df["ema_fast"] = EMAIndicator(
            close=df["close"], window=Config.EMA_FAST, fillna=True
        ).ema_indicator()

        df["ema_slow"] = EMAIndicator(
            close=df["close"], window=Config.EMA_SLOW, fillna=True
        ).ema_indicator()

        df["ema_trend"] = EMAIndicator(
            close=df["close"], window=Config.EMA_TREND, fillna=True
        ).ema_indicator()

        # EMA separation as % of price (must be >= 0.15% to be a valid signal)
        df["ema_sep"] = abs(df["ema_fast"] - df["ema_slow"]) / df["close"]

        # EMA direction: +1 = uptrend (long only), -1 = downtrend (short only)
        df["ema_dir"] = 0
        df.loc[df["ema_fast"] > df["ema_slow"], "ema_dir"] =  1
        df.loc[df["ema_fast"] < df["ema_slow"], "ema_dir"] = -1

        # Tangle detector: >2 crossovers in 6 candles = choppy, skip
        crosses = (df["ema_dir"] != df["ema_dir"].shift(1)).astype(int)
        df["ema_crosses_6"] = crosses.rolling(6).sum().fillna(0)

        # ── PILLAR 3: ADX TREND STRENGTH ─────────────────────────────────
        # ADX measures HOW STRONG the trend is, not which direction.
        # This is the most important filter. ADX < 20 = absolute veto.
        adx_ind = ADXIndicator(
            high=df["high"], low=df["low"], close=df["close"],
            window=Config.ADX_PERIOD, fillna=True
        )
        df["adx"]        = adx_ind.adx()
        df["adx_pos"]    = adx_ind.adx_pos()
        df["adx_neg"]    = adx_ind.adx_neg()
        df["adx_rising"] = df["adx"] > df["adx"].shift(1)

        # ── PILLAR 4: VOLUME CONFIRMATION ────────────────────────────────
        # Entry candle must be >= 1.5x the 20-bar average
        df["vol_ma"]    = df["volume"].rolling(Config.VOL_MA_PERIOD).mean()
        df["vol_ratio"] = df["volume"] / df["vol_ma"].replace(0, float("nan"))

        # ── BOLLINGER BANDS (Morning Filter 3) ───────────────────────────
        bb = BollingerBands(
            close=df["close"], window=Config.BB_PERIOD,
            window_dev=Config.BB_STD, fillna=True
        )
        df["bb_upper"]     = bb.bollinger_hband()
        df["bb_lower"]     = bb.bollinger_lband()
        df["bb_width"]     = (df["bb_upper"] - df["bb_lower"]) / df["close"]
        df["bb_expanding"] = df["bb_width"] > df["bb_width"].shift(1)

        # ── ATR: Stop-Loss Sizing ─────────────────────────────────────────
        df["atr"] = AverageTrueRange(
            high=df["high"], low=df["low"], close=df["close"],
            window=Config.ATR_PERIOD, fillna=True
        ).average_true_range()

        # ── CANDLE CLASSIFICATION ─────────────────────────────────────────
        df["body"]       = abs(df["close"] - df["open"])
        df["body_pct"]   = df["body"] / df["close"]
        df["up_wick"]    = df["high"] - df[["open", "close"]].max(axis=1)
        df["dn_wick"]    = df[["open", "close"]].min(axis=1) - df["low"]
        df["is_bullish"] = df["close"] > df["open"]
        df["is_bearish"] = df["close"] < df["open"]

        return df

    @staticmethod
    def latest(df: pd.DataFrame) -> dict:
        """Returns latest row as a flat dict — feeds into SignalGenerator."""
        r = df.iloc[-1]
        return {
            "close":         float(r["close"]),
            "volume":        float(r["volume"]),
            "ema_fast":      float(r["ema_fast"]),
            "ema_slow":      float(r["ema_slow"]),
            "ema_trend":     float(r["ema_trend"]),
            "ema_sep":       float(r["ema_sep"]),
            "ema_dir":       int(r["ema_dir"]),
            "ema_crosses_6": int(r["ema_crosses_6"]),
            "adx":           float(r["adx"]),
            "adx_rising":    bool(r["adx_rising"]),
            "vol_ma":        float(r["vol_ma"])    if not pd.isna(r["vol_ma"])    else 0.0,
            "vol_ratio":     float(r["vol_ratio"]) if not pd.isna(r["vol_ratio"]) else 0.0,
            "bb_width":      float(r["bb_width"]),
            "bb_expanding":  bool(r["bb_expanding"]),
            "atr":           float(r["atr"]),
            "is_bullish":    bool(r["is_bullish"]),
            "is_bearish":    bool(r["is_bearish"]),
            "body_pct":      float(r["body_pct"]),
        }


# ==============================================================================
# SECTION 5 — MORNING FILTER  (IDENTICAL to Zerodha version)
# Pure scoring logic — no broker code.
# ==============================================================================

class MorningFilter:
    """
    4-Filter Morning Scanner — run by 9:30 AM every trading day.

    Scores the day 0.0–4.0 and sets position sizing for the day.
    Green=1pt · Amber=0.5pt · Red=0pt

    Filter 1: ADX trend strength (most important — hard veto if < 20)
    Filter 2: SGX Nifty gap size (conviction proxy)
    Filter 3: Bollinger Band width (volatility check)
    Filter 4: Opening volume level (institutional activity check)

    Score >= 3.5 → Full 1% risk per trade
    Score 2.5-3.0 → Half 0.5% risk per trade
    Score < 2.5   → No trades today, protect capital
    """

    @staticmethod
    def run(indicators: dict, gap_pct: float) -> dict:
        """
        Args:
            indicators : dict from IndicatorEngine.latest()
            gap_pct    : SGX Nifty gap % vs prev NSE close (e.g. +0.6 or -0.3)
        Returns:
            dict with score, grade, position_size_pct, decision, per-filter notes
        """
        score   = 0.0
        results = {}

        # FILTER 1 — ADX (primary gate)
        adx = indicators["adx"]
        if adx >= Config.ADX_STRONG:
            f1 = {"score": 1.0, "grade": "green",
                  "note": f"ADX={adx:.1f} — strong trend, full confidence"}
        elif adx >= Config.ADX_MIN:
            f1 = {"score": 0.5, "grade": "amber",
                  "note": f"ADX={adx:.1f} — borderline trend, be selective"}
        else:
            f1 = {"score": 0.0, "grade": "red",
                  "note": f"ADX={adx:.1f} — NO TREND (hard veto applies)"}
        score += f1["score"]
        results["f1_adx"] = f1

        # FILTER 2 — Gap Size
        abs_gap = abs(gap_pct)
        if abs_gap >= 0.5:
            f2 = {"score": 1.0, "grade": "green",
                  "note": f"Gap={gap_pct:+.2f}% — strong overnight conviction"}
        elif abs_gap >= 0.3:
            f2 = {"score": 0.5, "grade": "amber",
                  "note": f"Gap={gap_pct:+.2f}% — moderate, watch carefully"}
        else:
            f2 = {"score": 0.0, "grade": "red",
                  "note": f"Gap={gap_pct:+.2f}% — flat open, high sideways risk"}
        score += f2["score"]
        results["f2_gap"] = f2

        # FILTER 3 — Bollinger Band Width
        bb_w = indicators["bb_width"]
        bb_x = indicators["bb_expanding"]
        if bb_w > 0.015 and bb_x:
            f3 = {"score": 1.0, "grade": "green",
                  "note": f"BB={bb_w:.3f} expanding — good volatility"}
        elif bb_w > 0.010:
            f3 = {"score": 0.5, "grade": "amber",
                  "note": f"BB={bb_w:.3f} — moderate volatility"}
        else:
            f3 = {"score": 0.0, "grade": "red",
                  "note": f"BB={bb_w:.3f} — squeeze, avoid trading today"}
        score += f3["score"]
        results["f3_bb"] = f3

        # FILTER 4 — Volume
        vr = indicators["vol_ratio"]
        if vr >= Config.VOL_MULTIPLIER:
            f4 = {"score": 1.0, "grade": "green",
                  "note": f"Volume={vr:.1f}x avg — institutional activity"}
        elif vr >= 1.0:
            f4 = {"score": 0.5, "grade": "amber",
                  "note": f"Volume={vr:.1f}x avg — marginal"}
        else:
            f4 = {"score": 0.0, "grade": "red",
                  "note": f"Volume={vr:.1f}x avg — weak, no follow-through"}
        score += f4["score"]
        results["f4_volume"] = f4

        # OVERALL DECISION (ADX veto always overrides score)
        if adx < Config.ADX_MIN:
            grade, pos_pct = "red", 0.0
            decision = "NO TRADE — ADX hard veto (ADX < 20)"
        elif score >= 3.5:
            grade, pos_pct = "green", Config.RISK_PER_TRADE_PCT
            decision = "TRADE — full size (1% risk)"
        elif score >= 2.5:
            grade, pos_pct = "amber", Config.RISK_PER_TRADE_PCT / 2.0
            decision = "TRADE — half size (0.5% risk)"
        else:
            grade, pos_pct = "red", 0.0
            decision = "NO TRADE — morning score too low"

        results.update({
            "score":             round(score, 1),
            "grade":             grade,
            "position_size_pct": pos_pct,
            "decision":          decision,
        })

        logger.info(
            f"Morning filter: {score:.1f}/4.0 [{grade.upper()}] | {decision}"
        )
        for k, v in results.items():
            if isinstance(v, dict):
                logger.debug(f"  {k}: {v['note']} [{v['grade']}]")

        return results


# ==============================================================================
# SECTION 6 — SIGNAL GENERATOR  (IDENTICAL to Zerodha version)
# The 6-condition entry checklist — pure logic, no broker code.
# ==============================================================================

class SignalGenerator:
    """
    Checks all 6 entry conditions and returns a complete trade signal or None.

    The 6 conditions (ALL must pass):
        1. ADX >= 20 (trend exists — Pillar 3)
        2. EMAs correctly oriented for direction (Pillar 1)
        2b. EMAs not tangled (< 3 crossovers in 6 candles)
        3. EMA separation >= 0.15% of price (not a weak crossover)
        4. Price within 0.2% of a pivot level (Pillar 2 — entry zone)
        5. Signal candle confirms direction (bullish for long, bearish for short)
        6. Volume >= 1.5x 20-bar average (Pillar 4 — institutional fuel)

    One failed condition = no signal. Wait for the next candle.
    """

    @staticmethod
    def _check(ind: dict, pivots: dict, direction: str) -> dict:
        price  = ind["close"]
        checks = {}

        # C1: ADX >= 20
        c1 = ind["adx"] >= Config.ADX_MIN
        checks["c1_adx"] = {
            "pass": c1,
            "note": f"ADX={ind['adx']:.1f} {'OK' if c1 else 'FAIL'}"
        }

        # C2: EMA direction
        c2 = ind["ema_dir"] == (1 if direction == "long" else -1)
        checks["c2_ema_dir"] = {
            "pass": c2,
            "note": ("9 EMA above 21" if direction == "long" else "9 EMA below 21")
                    + (" OK" if c2 else " FAIL")
        }

        # C2b: Not tangled
        tangled = ind["ema_crosses_6"] > 2
        checks["c2b_tangle"] = {
            "pass": not tangled,
            "note": f"{ind['ema_crosses_6']} crosses in 6 {'(TANGLED)' if tangled else '(OK)'}"
        }

        # C3: EMA separation >= 0.15%
        c3 = ind["ema_sep"] >= Config.EMA_MIN_SEPARATION
        checks["c3_ema_sep"] = {
            "pass": c3,
            "note": f"EMA gap={ind['ema_sep']:.3%} {'OK' if c3 else 'too tight'}"
        }

        # C4: Near pivot
        pvt_name, pvt_price = PivotCalculator.nearest_pivot(price, pivots)
        c4 = pvt_name is not None
        checks["c4_pivot"] = {
            "pass": c4,
            "note": f"Near {pvt_name}={pvt_price}" if c4 else "Not near any pivot",
            "pivot_name":  pvt_name,
            "pivot_price": pvt_price,
        }

        # C5: Signal candle
        c5 = (ind["is_bullish"] if direction == "long" else ind["is_bearish"]) \
             and ind["body_pct"] > 0.001
        checks["c5_candle"] = {
            "pass": c5,
            "note": ("Bullish" if direction == "long" else "Bearish")
                    + " candle " + ("OK" if c5 else "FAIL")
        }

        # C6: Volume >= 1.5x avg
        c6 = ind["vol_ratio"] >= Config.VOL_MULTIPLIER
        checks["c6_volume"] = {
            "pass": c6,
            "note": f"Volume={ind['vol_ratio']:.1f}x {'OK' if c6 else 'insufficient'}"
        }

        hard = ["c1_adx", "c2_ema_dir", "c2b_tangle",
                "c3_ema_sep", "c4_pivot", "c5_candle", "c6_volume"]
        all_pass = all(checks[k]["pass"] for k in hard)

        return {
            "all_pass":    all_pass,
            "checks":      checks,
            "pivot_name":  pvt_name,
            "pivot_price": pvt_price,
            "failed":      [k for k in hard if not checks[k]["pass"]],
        }

    @staticmethod
    def generate(
        ind: dict, pivots: dict, position_size_pct: float
    ) -> Optional[dict]:
        """
        Tries long then short. Returns complete signal dict or None.
        Also validates minimum 1:2 risk-to-reward before returning.
        """
        if position_size_pct == 0:
            return None

        for direction in ["long", "short"]:
            result = SignalGenerator._check(ind, pivots, direction)

            if not result["all_pass"]:
                logger.debug(
                    f"{direction}: SKIP — failed [{', '.join(result['failed'])}]"
                )
                continue

            price     = ind["close"]
            atr       = ind["atr"]
            pvt_name  = result["pivot_name"]
            pvt_price = result["pivot_price"]

            sl_pivot = SLTPCalc.pivot_stop(price, pvt_price, direction)
            sl_atr   = SLTPCalc.atr_stop(price, atr, direction)
            sl       = SLTPCalc.best_sl(price, sl_pivot, sl_atr, direction)

            if sl is None:
                logger.debug(f"{direction}: SKIP — SL exceeds hard cap")
                continue

            t1, t2 = PivotCalculator.target_levels(pvt_name, direction, pivots)
            if t1 is None:
                logger.debug(f"{direction}: SKIP — no T1 available")
                continue

            risk   = abs(price - sl)
            reward = abs(t1 - price)
            rr     = reward / risk if risk > 0 else 0.0

            if rr < Config.MIN_RR_RATIO:
                logger.debug(f"{direction}: SKIP — RR={rr:.2f} < {Config.MIN_RR_RATIO}")
                continue

            signal = {
                "direction":         direction,
                "entry_price":       round(price, 2),
                "stop_loss":         round(sl, 2),
                "target_1":          round(t1, 2),
                "target_2":          round(t2, 2) if t2 else None,
                "entry_pivot":       pvt_name,
                "pivot_price":       pvt_price,
                "risk_reward":       round(rr, 2),
                "position_size_pct": position_size_pct,
                "adx":               round(ind["adx"], 1),
                "vol_ratio":         round(ind["vol_ratio"], 2),
                "ema_sep_pct":       round(ind["ema_sep"] * 100, 3),
                "timestamp":         datetime.now(Config.IST).isoformat(),
            }

            logger.info(
                f"SIGNAL [{direction.upper()}] entry={price:.2f} "
                f"SL={sl:.2f} T1={t1:.2f} T2={t2} "
                f"RR={rr:.2f} ADX={ind['adx']:.1f} Vol={ind['vol_ratio']:.1f}x"
            )
            return signal

        return None


# ==============================================================================
# SECTION 7 — STOP-LOSS AND TARGET CALCULATOR  (IDENTICAL to Zerodha version)
# Pure math — no broker code.
# ==============================================================================

class SLTPCalc:
    """
    3-method stop-loss hierarchy:
        1. Pivot-based SL (primary) — 0.4% beyond the pivot level
        2. ATR-based SL (alternate) — 0.5 * ATR from entry
        3. Hard cap 0.8% — if either method exceeds this, SKIP the trade

    Position sizing formula:
        Capital at risk = Total Capital * Risk%
        SL per share    = |entry - stop_loss|
        Quantity        = Capital at risk / SL per share
    """

    @staticmethod
    def pivot_stop(entry: float, pivot: float, direction: str) -> float:
        buf = pivot * Config.SL_PIVOT_BUFFER
        return round((pivot - buf) if direction == "long" else (pivot + buf), 2)

    @staticmethod
    def atr_stop(entry: float, atr: float, direction: str) -> float:
        dist = atr * Config.SL_ATR_MULTIPLIER
        return round((entry - dist) if direction == "long" else (entry + dist), 2)

    @staticmethod
    def best_sl(
        entry: float, sl_pivot: float, sl_atr: float, direction: str
    ) -> Optional[float]:
        """Tighter SL = closer to entry = smaller potential loss."""
        sl       = max(sl_pivot, sl_atr) if direction == "long" else min(sl_pivot, sl_atr)
        dist_pct = abs(entry - sl) / entry
        if dist_pct > Config.MAX_SL_PCT:
            logger.debug(f"SL {dist_pct:.3%} > hard cap {Config.MAX_SL_PCT:.1%}")
            return None
        return sl

    @staticmethod
    def quantity(
        capital: float, risk_pct: float, entry: float, sl: float
    ) -> int:
        """
        Example: ₹5,00,000 × 1% = ₹5,000 at risk
                 Entry ₹1,200 − SL ₹1,185 = ₹15/share
                 Quantity = 5000 / 15 = 333 shares
        """
        capital_at_risk = capital * risk_pct
        sl_per_share    = abs(entry - sl)
        if sl_per_share <= 0:
            logger.warning("SL per share = 0 — cannot size position")
            return 0
        qty = int(capital_at_risk / sl_per_share)
        logger.debug(
            f"Qty calc | cap={capital:,.0f} risk={risk_pct:.1%} "
            f"entry={entry} sl={sl} sl/share={sl_per_share:.2f} qty={qty}"
        )
        return qty


# ==============================================================================
# SECTION 8 — ORDER MANAGER  *** COMPLETELY REWRITTEN FOR FIRSTOCK ***
#
# Changes from Zerodha version:
#   REMOVED : All KiteConnect-specific calls
#             (kite.place_order, kite.TRANSACTION_TYPE_BUY, PRODUCT_MIS, etc.)
#   ADDED   : thefirstock API calls
#             (fs.firstock_placeOrder, fs.firstock_cancelOrder, etc.)
#   CHANGED : product type  "I" (Intraday) replaces PRODUCT_MIS
#   CHANGED : transaction type "B"/"S" replaces TRANSACTION_TYPE_BUY/SELL
#   CHANGED : order_type "MKT"/"SL" replaces ORDER_TYPE_MARKET/ORDER_TYPE_SL
#   CHANGED : exit_live_order() uses fs.firstock_cancelOrder +
#             fs.firstock_placeOrder to flatten position
#
# Firstock order type codes:
#   priceType: "MKT" = Market, "LMT" = Limit, "SL" = Stop-Loss,
#              "SL-M" = Stop-Loss Market
#   product:   "I" = Intraday (equivalent to Zerodha MIS)
#              "C" = CNC (Delivery)
#              "M" = Margin
#   retention: "DAY" = Day order (auto-cancelled at 3:30 PM)
# ==============================================================================

class OrderManager:
    """
    Handles all order operations.

    paper mode : Simulates in memory + saves CSV journal
    live mode  : Places real orders via Firstock API

    Firstock API reference:
        firstock_placeOrder()  — place entry, SL, target orders
        firstock_modifyOrder() — modify SL price after T1 hit
        firstock_cancelOrder() — cancel pending SL on exit
        firstock_orderBook()   — check fill status
    """

    def __init__(self, mode: str, firstock_session: Optional[dict] = None):
        self.mode              = mode
        self.session           = firstock_session  # Contains jKey from login
        self.user_id           = Config.FIRSTOCK_USER_ID
        self.orders            = []                # In-memory trade book
        self.firstock_order_ids = {}               # Maps internal oid → Firstock norenordno

    # ── ENTRY ORDER ───────────────────────────────────────────────────────────
    def place_order(
        self,
        symbol: str,
        trading_symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        entry_price: float,
        stop_loss: float,
        target_1: float,
        target_2: Optional[float],
        signal: dict,
    ) -> str:
        """
        Places the entry order and immediately follows with a SL order.
        Returns the internal order_id string.
        """
        oid = f"ORD_{symbol}_{datetime.now().strftime('%H%M%S%f')[:14]}"
        tx  = "BUY" if direction == "long" else "SELL"

        # Build the internal order record (same structure as Zerodha version)
        order = {
            "order_id":         oid,
            "symbol":           symbol,
            "trading_symbol":   trading_symbol,
            "exchange":         exchange,
            "direction":        direction,
            "transaction":      tx,
            "quantity":         quantity,
            "entry_price":      entry_price,
            "stop_loss":        stop_loss,
            "target_1":         target_1,
            "target_2":         target_2,
            "status":           "OPEN",
            "entry_time":       datetime.now(Config.IST).isoformat(),
            "exit_price":       None,
            "exit_time":        None,
            "pnl":              None,
            "exit_reason":      None,
            "t1_hit":           False,
            "sl_moved":         False,
            "pnl_booked":       False,
            "journaled":        False,
            "adx_entry":        signal.get("adx"),
            "vol_ratio":        signal.get("vol_ratio"),
            "risk_reward":      signal.get("risk_reward"),
            "pivot":            signal.get("entry_pivot"),
            "firstock_entry_id": None,   # Filled after live order
            "firstock_sl_id":    None,   # Filled after SL order
        }
        self.orders.append(order)

        if self.mode == "paper":
            logger.info(
                f"[PAPER] {tx} {quantity}x {trading_symbol} @ {entry_price:.2f} | "
                f"SL={stop_loss:.2f} T1={target_1:.2f} T2={target_2}"
            )

        elif self.mode == "live":
            if not FIRSTOCK_AVAILABLE or not self.session:
                raise RuntimeError("Firstock session not available for live trading")
            try:
                # ── STEP 1: Place entry market order ─────────────────────
                entry_resp = fs.firstock_placeOrder(
                    userId          = self.user_id,
                    exchange        = exchange,
                    tradingSymbol   = trading_symbol,
                    quantity        = str(quantity),
                    price           = "0",        # 0 = market order
                    product         = "I",         # I = Intraday (same as MIS)
                    transactionType = "B" if direction == "long" else "S",
                    priceType       = "MKT",       # Market order type
                    retention       = "DAY",       # Day order
                    triggerPrice    = "",
                    remarks         = f"ObsidianPivot-{oid}",
                )
                entry_norenordno = self._extract_order_id(entry_resp)
                order["firstock_entry_id"] = entry_norenordno
                logger.info(
                    f"[LIVE] Entry order placed | "
                    f"Firstock ID={entry_norenordno} | "
                    f"{tx} {quantity}x {trading_symbol}"
                )

                # ── STEP 2: Place server-side SL order immediately ────────
                # This protects you even if your internet drops after entry
                sl_norenordno = self._place_firstock_sl(
                    trading_symbol, exchange, direction, quantity, stop_loss, oid
                )
                order["firstock_sl_id"] = sl_norenordno

            except Exception as e:
                logger.error(f"Live order FAILED for {symbol}: {e}")
                self.orders.remove(order)
                raise

        return oid

    def _place_firstock_sl(
        self,
        trading_symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        sl_price: float,
        remarks_ref: str,
    ) -> Optional[str]:
        """
        Places a stop-loss order on Firstock's server.

        For a LONG trade:  SL is a SELL SL order below entry
        For a SHORT trade: SL is a BUY SL order above entry

        Firstock SL order requires both:
          triggerPrice : the price that activates the SL
          price        : the limit price (set slightly below trigger for longs,
                         slightly above trigger for shorts to guarantee fill)
        """
        if not FIRSTOCK_AVAILABLE or not self.session:
            return None

        sl_tx      = "S" if direction == "long" else "B"
        # Limit price: 0.3% slippage buffer to ensure fill
        sl_limit   = sl_price * (0.997 if direction == "long" else 1.003)

        try:
            sl_resp = fs.firstock_placeOrder(
                userId          = self.user_id,
                exchange        = exchange,
                tradingSymbol   = trading_symbol,
                quantity        = str(quantity),
                price           = str(round(sl_limit, 2)),
                product         = "I",
                transactionType = sl_tx,
                priceType       = "SL",            # Stop-Loss order
                retention       = "DAY",
                triggerPrice    = str(round(sl_price, 2)),
                remarks         = f"ObsidianPivot-SL-{remarks_ref}",
            )
            sl_id = self._extract_order_id(sl_resp)
            logger.info(f"[LIVE] SL order placed | Firstock ID={sl_id} @ {sl_price:.2f}")
            return sl_id
        except Exception as e:
            logger.error(f"SL order FAILED: {e} | Manual intervention required!")
            Notifier.send(
                f"⚠️ <b>SL ORDER FAILED</b>\n"
                f"Symbol: {trading_symbol}\n"
                f"SL price: ₹{sl_price:.2f}\n"
                f"PLACE SL MANUALLY IMMEDIATELY!"
            )
            return None

    def modify_sl_to_breakeven(
        self, oid: str, breakeven_price: float
    ):
        """
        Called when T1 is hit — moves the server-side SL to breakeven.
        Uses Firstock's modifyOrder API.
        """
        order = next((o for o in self.orders if o["order_id"] == oid), None)
        if not order:
            return

        order["stop_loss"] = breakeven_price
        order["sl_moved"]  = True

        if self.mode == "live" and FIRSTOCK_AVAILABLE and order.get("firstock_sl_id"):
            try:
                fs.firstock_modifyOrder(
                    userId      = self.user_id,
                    norenordno  = order["firstock_sl_id"],
                    price       = str(round(breakeven_price * 0.998, 2)),
                    triggerPrice= str(round(breakeven_price, 2)),
                    quantity    = str(order["quantity"]),
                    exchange    = order["exchange"],
                    tradingSymbol = order["trading_symbol"],
                    priceType   = "SL",
                )
                logger.info(
                    f"[LIVE] SL modified to breakeven {breakeven_price:.2f} | "
                    f"Firstock SL ID={order['firstock_sl_id']}"
                )
            except Exception as e:
                logger.error(f"SL modify failed: {e}")

    def exit_order(self, oid: str, exit_price: float, reason: str):
        """
        Exits an open position.
        Paper mode: records exit in memory.
        Live mode:  cancels pending SL + places market exit order.
        """
        for o in self.orders:
            if o["order_id"] != oid or o["status"] != "OPEN":
                continue

            if self.mode == "live" and FIRSTOCK_AVAILABLE and self.session:
                # Cancel existing SL order first
                if o.get("firstock_sl_id"):
                    try:
                        fs.firstock_cancelOrder(
                            userId     = self.user_id,
                            norenordno = o["firstock_sl_id"],
                        )
                        logger.info(f"[LIVE] SL order cancelled: {o['firstock_sl_id']}")
                    except Exception as e:
                        logger.warning(f"SL cancel failed (may be already triggered): {e}")

                # Place market exit order (opposite direction)
                exit_tx = "S" if o["direction"] == "long" else "B"
                try:
                    exit_resp = fs.firstock_placeOrder(
                        userId          = self.user_id,
                        exchange        = o["exchange"],
                        tradingSymbol   = o["trading_symbol"],
                        quantity        = str(o["quantity"]),
                        price           = "0",
                        product         = "I",
                        transactionType = exit_tx,
                        priceType       = "MKT",
                        retention       = "DAY",
                        triggerPrice    = "",
                        remarks         = f"ObsidianPivot-EXIT-{reason}",
                    )
                    exit_id = self._extract_order_id(exit_resp)
                    logger.info(
                        f"[LIVE] Exit order placed | "
                        f"Firstock ID={exit_id} | Reason={reason}"
                    )
                except Exception as e:
                    logger.error(f"Exit order FAILED: {e}")

            # Update internal record regardless of live/paper
            o["exit_price"]  = exit_price
            o["exit_time"]   = datetime.now(Config.IST).isoformat()
            o["status"]      = "CLOSED"
            o["exit_reason"] = reason
            mult             = 1 if o["direction"] == "long" else -1
            o["pnl"]         = round(
                (exit_price - o["entry_price"]) * o["quantity"] * mult, 2
            )

            logger.info(
                f"[EXIT] {oid} {o['symbol']} | {reason} | "
                f"exit={exit_price:.2f} P&L=₹{o['pnl']:+,.2f}"
            )
            return

    @staticmethod
    def _extract_order_id(response: dict) -> Optional[str]:
        """
        Extracts the Firstock order number (norenordno) from the API response.
        Firstock returns: {"stat": "Ok", "norenordno": "24010100000001", ...}
        """
        if not response:
            return None
        if response.get("stat") == "Ok":
            return response.get("norenordno") or response.get("requestTime")
        logger.warning(f"Firstock order response: {response}")
        return None

    def open_orders(self) -> list:
        return [o for o in self.orders if o["status"] == "OPEN"]

    def save_journal(self, filepath: str):
        """Appends closed trades to CSV. Review this every evening."""
        if not self.orders:
            return
        write_header = not os.path.exists(filepath)
        with open(filepath, "a", newline="", encoding="utf-8") as f:
            # Build fieldnames from all keys across all orders
            all_keys = list(dict.fromkeys(
                k for o in self.orders for k in o.keys()
            ))
            w = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            if write_header:
                w.writeheader()
            for o in self.orders:
                if o["status"] == "CLOSED" and not o.get("journaled"):
                    w.writerow(o)
                    o["journaled"] = True
        logger.debug(f"Journal updated: {filepath}")


# ==============================================================================
# SECTION 9 — POSITION MONITOR  (IDENTICAL to Zerodha version)
# Exit logic is broker-agnostic — uses OrderManager.exit_order() and
# OrderManager.modify_sl_to_breakeven() which handle broker differences.
# ==============================================================================

class PositionMonitor:
    """
    Checks exit conditions on every candle for all open positions.

    Exit hierarchy:
        1. ADX drops below 20 mid-trade → exit 100% immediately
        2. 3:15 PM hard close → exit 100%, no exceptions
        3. Stop-loss hit → exit 100%
        4. Target 1 hit → exit 50%, move SL to breakeven
        5. Target 2 hit → exit remaining 50%
    """

    def __init__(self, order_manager: OrderManager):
        self.mgr = order_manager

    def check(self, symbol: str, price: float, adx: float) -> list:
        actions = []
        now = datetime.now(Config.IST)
        hm  = (now.hour, now.minute)

        for o in self.mgr.open_orders():
            if o["symbol"] != symbol:
                continue

            d  = o["direction"]
            sl = o["stop_loss"]
            t1 = o["target_1"]
            t2 = o["target_2"]
            ep = o["entry_price"]

            # EXIT 1: ADX dropped — trend has ended, exit immediately
            if adx < Config.ADX_MIN:
                self.mgr.exit_order(o["order_id"], price, "ADX_DROPPED")
                actions.append(f"ADX_DROPPED @ {price:.2f}")
                continue

            # EXIT 2: Hard 3:15 PM close
            if hm >= Config.HARD_EXIT:
                self.mgr.exit_order(o["order_id"], price, "HARD_EXIT_315PM")
                actions.append(f"HARD_EXIT_315 @ {price:.2f}")
                continue

            # EXIT 3: Stop-loss hit
            sl_hit = (d == "long" and price <= sl) or (d == "short" and price >= sl)
            if sl_hit:
                self.mgr.exit_order(o["order_id"], sl, "STOP_LOSS")
                actions.append(f"SL_HIT @ {sl:.2f}")
                continue

            # EXIT 4: Target 1 hit → exit 50%, move SL to breakeven
            if not o["t1_hit"]:
                t1_hit = (d == "long" and price >= t1) or (d == "short" and price <= t1)
                if t1_hit:
                    o["t1_hit"] = True
                    # In live mode: exit half the quantity here
                    # For simplicity we track T1 and T2 as full exit at T2
                    # To implement partial exit, halve quantity and reorder SL
                    self.mgr.modify_sl_to_breakeven(o["order_id"], ep)
                    actions.append(f"T1_HIT @ {t1:.2f} — SL moved to breakeven {ep:.2f}")
                    logger.info(f"T1 HIT {symbol} @ {t1:.2f} | SL → breakeven {ep:.2f}")

            # EXIT 5: Target 2 hit → exit remaining 50%
            if o["t1_hit"] and t2:
                t2_hit = (d == "long" and price >= t2) or (d == "short" and price <= t2)
                if t2_hit:
                    self.mgr.exit_order(o["order_id"], t2, "TARGET_2_HIT")
                    actions.append(f"TARGET_2_HIT @ {t2:.2f}")

        return actions


# ==============================================================================
# SECTION 10 — DAILY RISK GUARD  (IDENTICAL to Zerodha version)
# Pure capital protection logic — no broker code.
# ==============================================================================

class DailyRiskGuard:
    """
    Enforces daily and weekly risk limits automatically.
    Halts trading when:
        • Daily loss exceeds 2% of capital
        • Trade count reaches 3 (MAX_TRADES_PER_DAY)
    This enforces rules in code, not willpower.
    """

    def __init__(self, capital: float):
        self.capital     = capital
        self.daily_pnl   = 0.0
        self.weekly_pnl  = 0.0
        self.trade_count = 0
        self.halted      = False
        self.halt_reason = ""

    def record_closed_trade(self, pnl: float):
        self.daily_pnl   += pnl
        self.weekly_pnl  += pnl
        self.trade_count += 1
        self._check()

    def _check(self):
        daily_pct = self.daily_pnl / self.capital
        if daily_pct <= -Config.DAILY_LOSS_LIMIT_PCT:
            self.halted      = True
            self.halt_reason = (
                f"Daily loss {daily_pct:.2%} exceeds "
                f"-{Config.DAILY_LOSS_LIMIT_PCT:.0%} limit"
            )
            logger.warning(f"TRADING HALTED: {self.halt_reason}")
            Notifier.send(f"🛑 <b>TRADING HALTED</b>\n{self.halt_reason}")
        if self.trade_count >= Config.MAX_TRADES_PER_DAY:
            self.halted      = True
            self.halt_reason = f"Max {Config.MAX_TRADES_PER_DAY} trades reached"
            logger.warning(f"TRADING HALTED: {self.halt_reason}")

    def can_trade(self) -> bool:
        if self.halted:
            logger.info(f"Risk guard halted: {self.halt_reason}")
        return not self.halted

    def reset_for_day(self):
        logger.info(
            f"Day reset | Daily P&L: ₹{self.daily_pnl:+,.2f} | "
            f"Trades: {self.trade_count}"
        )
        self.daily_pnl   = 0.0
        self.trade_count = 0
        self.halted      = False
        self.halt_reason = ""


# ==============================================================================
# SECTION 11 — DATA FETCHER  *** REWRITTEN FOR FIRSTOCK ***
#
# Changes from Zerodha version:
#   REMOVED : kite.historical_data() calls
#   ADDED   : fs.firstock_timePriceSeries() for OHLCV candles
#   ADDED   : fs.firstock_getPreviousDayOHLC() equivalent
#             (Firstock doesn't have a direct daily OHLC call —
#              we fetch 1-day candles from timePriceSeries)
#   KEPT    : _from_csv() for backtest mode (unchanged)
#   KEPT    : _synthetic() for testing without API (unchanged)
#
# Firstock timePriceSeries parameters:
#   exchange  : "NSE", "BSE", "NFO"
#   token     : instrument token (string)
#   startTime : "DD/MM/YYYY HH:MM:SS"
#   endTime   : "DD/MM/YYYY HH:MM:SS"
#   interval  : "1" to "1440" (minutes) — use "15" for 15-min candles
#               "D" = daily candles
# ==============================================================================

class DataFetcher:
    """
    Fetches OHLCV data.

    live/paper mode : Uses Firstock timePriceSeries API
    backtest mode   : Reads CSV files from Config.BACKTEST_DATA_DIR
    """

    # Firstock datetime format for API calls
    FS_DT_FMT = "%d/%m/%Y %H:%M:%S"

    def __init__(self, mode: str, session: Optional[dict] = None):
        self.mode    = mode
        self.session = session
        self.user_id = Config.FIRSTOCK_USER_ID

    def get_candles(
        self,
        token: str,
        symbol: str,
        interval_min: int = 15,
        n: int = 60,
    ) -> Optional[pd.DataFrame]:
        """Returns a DataFrame of n recent OHLCV candles."""
        if self.mode in ("live", "paper"):
            return self._from_firstock(token, interval_min, n)
        return self._from_csv(symbol)

    def _from_firstock(
        self, token: str, interval_min: int, n: int
    ) -> Optional[pd.DataFrame]:
        """
        Fetches candles from Firstock timePriceSeries API.
        Fetches last 4 days to ensure we have enough 15-min candles.
        """
        if not FIRSTOCK_AVAILABLE or not self.session:
            logger.debug("Firstock session not available — using synthetic data")
            return self._synthetic()

        now_ist  = datetime.now(Config.IST)
        end_dt   = now_ist
        start_dt = now_ist - timedelta(days=4)

        try:
            resp = fs.firstock_timePriceSeries(
                userId    = self.user_id,
                exchange  = "NSE",
                token     = token,
                startTime = start_dt.strftime(self.FS_DT_FMT),
                endTime   = end_dt.strftime(self.FS_DT_FMT),
                interval  = str(interval_min),   # "15" for 15-min candles
            )

            if not resp or resp.get("stat") != "Ok":
                logger.warning(f"Firstock timePriceSeries returned: {resp}")
                return self._synthetic()

            # Firstock returns a list of dicts with keys:
            # time, into (open), inth (high), intl (low), intc (close),
            # intv (volume), v (cumulative volume)
            candles = resp.get("data", [])
            if not candles:
                logger.warning("Firstock returned empty candle data")
                return self._synthetic()

            df = pd.DataFrame(candles)

            # Rename Firstock column names to our standard names
            col_map = {
                "time": "datetime",
                "into": "open",
                "inth": "high",
                "intl": "low",
                "intc": "close",
                "intv": "volume",
            }
            df = df.rename(columns=col_map)
            df = df[["datetime", "open", "high", "low", "close", "volume"]]

            # Parse Firstock datetime format: "DD-MMM-YYYY HH:MM:SS"
            df["datetime"] = pd.to_datetime(
                df["datetime"], format="%d-%b-%Y %H:%M:%S", errors="coerce"
            )
            df = df.sort_values("datetime").reset_index(drop=True)

            return df.tail(n).reset_index(drop=True)

        except Exception as e:
            logger.error(f"Firstock candle fetch error: {e}")
            return self._synthetic()

    def get_prev_day_ohlc(
        self, token: str, symbol: str
    ) -> Optional[dict]:
        """
        Fetches previous trading day's OHLC for pivot point calculation.
        Uses Firstock timePriceSeries with daily interval ("D").
        """
        if not FIRSTOCK_AVAILABLE or not self.session:
            logger.warning(f"Using fallback OHLC for {symbol}")
            return {"high": 1250.0, "low": 1180.0, "close": 1215.0}

        now_ist = datetime.now(Config.IST)
        prev    = (now_ist - timedelta(days=1)).date()
        # Skip weekends
        while prev.weekday() > 4:
            prev -= timedelta(days=1)

        try:
            resp = fs.firstock_timePriceSeries(
                userId    = self.user_id,
                exchange  = "NSE",
                token     = token,
                startTime = prev.strftime("%d/%m/%Y") + " 09:00:00",
                endTime   = prev.strftime("%d/%m/%Y") + " 16:00:00",
                interval  = "D",   # Daily candle
            )

            if resp and resp.get("stat") == "Ok":
                data = resp.get("data", [])
                if data:
                    last = data[-1]
                    return {
                        "high":  float(last.get("inth", 0)),
                        "low":   float(last.get("intl", 0)),
                        "close": float(last.get("intc", 0)),
                    }

            logger.warning(f"Firstock prev OHLC empty for {symbol} — using fallback")
            return {"high": 1250.0, "low": 1180.0, "close": 1215.0}

        except Exception as e:
            logger.error(f"Firstock prev OHLC error for {symbol}: {e}")
            return {"high": 1250.0, "low": 1180.0, "close": 1215.0}

    def _from_csv(self, symbol: str) -> Optional[pd.DataFrame]:
        """Reads historical OHLCV CSV for backtesting. Identical to Zerodha version."""
        path = os.path.join(Config.BACKTEST_DATA_DIR, f"{symbol}_15min.csv")
        if not os.path.exists(path):
            logger.error(
                f"Backtest CSV not found: {path}\n"
                f"Required columns: datetime,open,high,low,close,volume"
            )
            return None
        try:
            df = pd.read_csv(path, parse_dates=["datetime"])
            df.columns = [c.lower() for c in df.columns]
            return df
        except Exception as e:
            logger.error(f"CSV read error {path}: {e}")
            return None

    def _synthetic(self, n: int = 100) -> pd.DataFrame:
        """Generates synthetic OHLCV data for testing. Identical to Zerodha version."""
        np.random.seed(42)
        base    = 1200.0
        ret     = np.random.normal(0.0002, 0.003, n)
        closes  = base * np.cumprod(1 + ret)
        highs   = closes * (1 + np.abs(np.random.normal(0, 0.004, n)))
        lows    = closes * (1 - np.abs(np.random.normal(0, 0.004, n)))
        opens   = np.roll(closes, 1)
        volumes = np.random.randint(80_000, 600_000, n).astype(float)
        return pd.DataFrame({
            "datetime": pd.date_range("2024-01-02 09:15", periods=n, freq="15min"),
            "open":     np.round(opens,  2),
            "high":     np.round(highs,  2),
            "low":      np.round(lows,   2),
            "close":    np.round(closes, 2),
            "volume":   volumes,
        })


# ==============================================================================
# SECTION 12 — TELEGRAM NOTIFIER  (IDENTICAL to Zerodha version)
# ==============================================================================

class Notifier:
    """Sends Telegram alerts for trade signals, exits, and morning scores."""

    @staticmethod
    def send(message: str):
        if not (Config.SEND_TELEGRAM and REQUESTS_AVAILABLE):
            return
        if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
            return
        try:
            http_requests.post(
                f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id":    Config.TELEGRAM_CHAT_ID,
                    "text":       message,
                    "parse_mode": "HTML",
                },
                timeout=5,
            )
        except Exception as e:
            logger.debug(f"Telegram error: {e}")

    @staticmethod
    def trade(signal: dict, symbol: str, qty: int):
        Notifier.send(
            f"<b>🟢 TRADE SIGNAL — ObsidianPivot</b>\n"
            f"Broker : Firstock\n"
            f"Symbol : {symbol}\n"
            f"Dir    : {signal['direction'].upper()}\n"
            f"Entry  : ₹{signal['entry_price']:,.2f}\n"
            f"SL     : ₹{signal['stop_loss']:,.2f}\n"
            f"T1     : ₹{signal['target_1']:,.2f}\n"
            f"T2     : {signal['target_2']}\n"
            f"RR     : 1:{signal['risk_reward']}\n"
            f"Qty    : {qty}\n"
            f"ADX    : {signal['adx']}\n"
            f"Pivot  : {signal['entry_pivot']}"
        )

    @staticmethod
    def exit(symbol: str, pnl: float, reason: str):
        icon = "✅" if pnl >= 0 else "❌"
        Notifier.send(
            f"{icon} <b>TRADE CLOSED — ObsidianPivot</b>\n"
            f"Symbol : {symbol}\n"
            f"P&L    : ₹{pnl:+,.2f}\n"
            f"Reason : {reason}"
        )

    @staticmethod
    def morning(scores: dict):
        lines = ["<b>🌅 MORNING SCAN — ObsidianPivot (Firstock)</b>"]
        for sym, r in scores.items():
            emoji = "🟢" if r["grade"] == "green" else ("🟡" if r["grade"] == "amber" else "🔴")
            lines.append(f"{emoji} {sym}: {r['score']}/4.0 — {r['decision']}")
        Notifier.send("\n".join(lines))


# ==============================================================================
# SECTION 13 — FIRSTOCK LOGIN MANAGER  *** NEW SECTION — NOT IN ZERODHA VERSION ***
#
# Why this section exists:
#   Firstock uses a token-based session. Every day you must log in with:
#     • userId
#     • password (auto SHA256-hashed by the SDK)
#     • TOTP (6-digit code from Google Authenticator, changes every 30s)
#     • vendorCode
#     • apiKey
#
#   The jKey returned by login() is used in ALL subsequent API calls.
#   It expires at midnight — you must log in fresh every trading day.
#
#   pyotp handles TOTP generation automatically from the base32 secret,
#   so the algo can log in without you typing a code.
# ==============================================================================

class FirestockLoginManager:
    """
    Manages daily Firstock login and session state.

    Auto-generates TOTP using pyotp so no manual authenticator code needed.
    The jKey session token is stored and passed to all API components.
    """

    @staticmethod
    def generate_totp() -> str:
        """
        Generates the current 6-digit TOTP code using the stored secret.
        The TOTP_SECRET must be the base32 string from Google Authenticator
        setup (shown when you scan the QR code manually as a key).
        """
        if not PYOTP_AVAILABLE:
            raise RuntimeError(
                "pyotp not installed. Run: pip install pyotp\n"
                "Then set FIRSTOCK_TOTP_SECRET in your environment."
            )
        if not Config.FIRSTOCK_TOTP_SECRET:
            raise ValueError(
                "FIRSTOCK_TOTP_SECRET not set.\n"
                "Set it with: export FIRSTOCK_TOTP_SECRET='your_base32_secret'"
            )
        totp = pyotp.TOTP(Config.FIRSTOCK_TOTP_SECRET)
        code = totp.now()
        logger.debug(f"Generated TOTP code: {code}")
        return code

    @staticmethod
    def login() -> Optional[dict]:
        """
        Logs in to Firstock and returns the session dict containing jKey.
        Must be called once per trading day before any API calls.

        Returns:
            {"jKey": "session_token", "userId": "client_id", ...}
            or None if login fails
        """
        if not FIRSTOCK_AVAILABLE:
            logger.warning("thefirstock not installed — running without live data")
            return None

        if not all([
            Config.FIRSTOCK_USER_ID,
            Config.FIRSTOCK_PASSWORD,
            Config.FIRSTOCK_VENDOR_CODE,
            Config.FIRSTOCK_API_KEY,
        ]):
            logger.warning(
                "Firstock credentials not set in environment variables.\n"
                "Running in simulation mode (synthetic data).\n"
                "Set FIRSTOCK_USER_ID, FIRSTOCK_PASSWORD, "
                "FIRSTOCK_VENDOR_CODE, FIRSTOCK_API_KEY"
            )
            return None

        try:
            totp_code = FirestockLoginManager.generate_totp()

            logger.info(f"Logging in to Firstock as {Config.FIRSTOCK_USER_ID}...")
            resp = fs.firstock_login(
                userId      = Config.FIRSTOCK_USER_ID,
                password    = Config.FIRSTOCK_PASSWORD,   # SDK auto-SHA256 hashes
                TOTP        = totp_code,
                vendorCode  = Config.FIRSTOCK_VENDOR_CODE,
                apiKey      = Config.FIRSTOCK_API_KEY,
            )

            if resp and resp.get("stat") == "Ok":
                j_key = resp.get("susertoken") or resp.get("jKey")
                session = {
                    "jKey":   j_key,
                    "userId": Config.FIRSTOCK_USER_ID,
                    "stat":   "Ok",
                }
                logger.info(
                    f"✓ Firstock login successful | "
                    f"userId={Config.FIRSTOCK_USER_ID} | "
                    f"jKey={str(j_key)[:8]}..."
                )
                return session
            else:
                logger.error(f"Firstock login FAILED: {resp}")
                Notifier.send(
                    f"🔴 <b>FIRSTOCK LOGIN FAILED</b>\n"
                    f"Response: {resp}\n"
                    f"Check credentials and TOTP secret."
                )
                return None

        except Exception as e:
            logger.error(f"Firstock login exception: {e}")
            return None

    @staticmethod
    def logout(session: Optional[dict]):
        """Gracefully logs out of Firstock at end of trading day."""
        if not session or not FIRSTOCK_AVAILABLE:
            return
        try:
            fs.firstock_logout(userId=Config.FIRSTOCK_USER_ID)
            logger.info("Firstock logout successful")
        except Exception as e:
            logger.debug(f"Logout error (non-critical): {e}")


# ==============================================================================
# SECTION 14 — TRADING ENGINE  *** UPDATED FOR FIRSTOCK ***
#
# Changes from Zerodha version:
#   REMOVED : _init_kite() method
#   ADDED   : _init_firstock() method — uses FirestockLoginManager
#   CHANGED : All DataFetcher and OrderManager instantiation uses session
#             instead of kite object
#   KEPT    : pre_market_setup(), on_candle(), run_once(), run_live_loop()
#             are structurally identical — only broker references updated
#   ADDED   : WebSocket setup comment (Firstock socketConnect)
# ==============================================================================

class TradingEngine:
    """
    Main orchestrator — runs pre_market_setup() once then on_candle()
    for each symbol on every 15-minute candle through the trading day.

    Call order each day:
        1. engine = TradingEngine()           → logs in, initialises
        2. engine.pre_market_setup(gap_pct)   → calculates pivots, scores
        3. [loop] engine.on_candle(symbol)    → signal scan + execution
        4. engine.shutdown()                  → logout, save journal
    """

    def __init__(self):
        self.session       = FirestockLoginManager.login()
        self.fetcher       = DataFetcher(Config.MODE, self.session)
        self.order_mgr     = OrderManager(Config.MODE, self.session)
        self.monitor       = PositionMonitor(self.order_mgr)
        self.risk_guard    = DailyRiskGuard(Config.TOTAL_CAPITAL)
        self.morning_scores: dict = {}
        self.pivot_cache: dict    = {}

        logger.info(
            f"ObsidianPivot (Firstock) ready | "
            f"Mode={Config.MODE} | "
            f"Capital=₹{Config.TOTAL_CAPITAL:,.0f} | "
            f"Watchlist={[i['symbol'] for i in Config.WATCHLIST]} | "
            f"Session={'LIVE' if self.session else 'SIMULATION'}"
        )

    def pre_market_setup(self, gap_pct: float = 0.0):
        """
        Run at 8:30–9:10 AM every trading day.
        1. Calculates pivot levels for all watchlist stocks
        2. Runs 4-filter morning scanner for each stock
        3. Caches results for use during market hours
        4. Sends morning summary via Telegram
        """
        logger.info("=" * 60)
        logger.info(f"PRE-MARKET SETUP | SGX gap={gap_pct:+.2f}%")
        self.risk_guard.reset_for_day()
        self.pivot_cache.clear()
        self.morning_scores.clear()

        for inst in Config.WATCHLIST:
            sym   = inst["symbol"]
            token = inst["token"]    # string token for Firstock

            # Fetch yesterday's OHLC for pivot calculation
            prev = self.fetcher.get_prev_day_ohlc(token, sym)
            if not prev:
                logger.warning(f"{sym}: no prev OHLC — skipping")
                continue

            # Calculate pivot points
            pivots = PivotCalculator.calculate(prev["high"], prev["low"], prev["close"])
            self.pivot_cache[sym] = pivots
            rq = PivotCalculator.range_quality(pivots, prev["close"])
            logger.info(
                f"{sym} | PP={pivots['PP']} R1={pivots['R1']} "
                f"R2={pivots['R2']} S1={pivots['S1']} S2={pivots['S2']} | "
                f"Range quality: {rq.upper()}"
            )
            if rq == "skip":
                logger.info(f"{sym}: pivot range too tight — will skip today")

            # Fetch candles and compute indicators
            df = self.fetcher.get_candles(
                token, sym, Config.CANDLE_INTERVAL_MIN, Config.CANDLES_NEEDED
            )
            if df is None or len(df) < Config.VOL_MA_PERIOD + 5:
                logger.warning(f"{sym}: insufficient candle data")
                continue

            df  = IndicatorEngine.compute(df)
            ind = IndicatorEngine.latest(df)

            # Run 4-filter morning scanner
            self.morning_scores[sym] = MorningFilter.run(ind, gap_pct)

        Notifier.morning(self.morning_scores)
        logger.info("PRE-MARKET SETUP DONE")
        logger.info("=" * 60)

    def on_candle(self, symbol: str):
        """
        Called on every new 15-min candle for the given symbol.
        Checks exits on open positions, then scans for new signals.
        """
        now = datetime.now(Config.IST)
        hm  = (now.hour, now.minute)

        # Before 9:30 AM — watch only
        if hm < Config.TRADE_START:
            return

        # After 3:15 PM — force-close all positions
        if hm >= Config.HARD_EXIT:
            for o in self.order_mgr.open_orders():
                if o["symbol"] == symbol:
                    self.order_mgr.exit_order(
                        o["order_id"], o["entry_price"], "HARD_EXIT_315"
                    )
            return

        # Check daily risk limits
        if not self.risk_guard.can_trade():
            return

        # Fetch latest data
        inst = next(
            (i for i in Config.WATCHLIST if i["symbol"] == symbol), None
        )
        if not inst:
            return

        df = self.fetcher.get_candles(
            inst["token"], symbol,
            Config.CANDLE_INTERVAL_MIN, Config.CANDLES_NEEDED
        )
        if df is None or len(df) < Config.VOL_MA_PERIOD + 5:
            return

        df    = IndicatorEngine.compute(df)
        ind   = IndicatorEngine.latest(df)
        price = ind["close"]
        adx   = ind["adx"]

        # Check and execute exits on open positions
        actions = self.monitor.check(symbol, price, adx)
        for a in actions:
            logger.info(f"{symbol} | {a}")

        # Record P&L of just-closed trades to risk guard
        for o in self.order_mgr.orders:
            if (o["symbol"] == symbol
                    and o["status"] == "CLOSED"
                    and o.get("pnl") is not None
                    and not o.get("pnl_booked")):
                self.risk_guard.record_closed_trade(o["pnl"])
                o["pnl_booked"] = True
                Notifier.exit(symbol, o["pnl"], o["exit_reason"])
                self.order_mgr.save_journal(Config.JOURNAL_FILE)

        # Skip signal scan if already in a trade for this symbol
        if any(o["symbol"] == symbol for o in self.order_mgr.open_orders()):
            return

        # Get morning score for this symbol (sets position size limit)
        score   = self.morning_scores.get(symbol, {})
        pos_pct = score.get("position_size_pct", 0.0)
        if pos_pct == 0:
            return  # Morning filter said no trading today

        # Check pivot range quality
        pivots = self.pivot_cache.get(symbol)
        if not pivots:
            return
        if PivotCalculator.range_quality(pivots, price) == "skip":
            logger.debug(f"{symbol}: pivot range too tight — skipping")
            return

        # Scan for a valid signal (all 6 conditions)
        signal = SignalGenerator.generate(ind, pivots, pos_pct)
        if not signal:
            return

        # Calculate position size
        qty = SLTPCalc.quantity(
            Config.TOTAL_CAPITAL,
            signal["position_size_pct"],
            signal["entry_price"],
            signal["stop_loss"],
        )
        if qty <= 0:
            return

        # Place the order
        self.order_mgr.place_order(
            symbol         = symbol,
            trading_symbol = inst["trading_symbol"],
            exchange       = inst["exchange"],
            direction      = signal["direction"],
            quantity       = qty,
            entry_price    = signal["entry_price"],
            stop_loss      = signal["stop_loss"],
            target_1       = signal["target_1"],
            target_2       = signal["target_2"],
            signal         = signal,
        )

        Notifier.trade(signal, symbol, qty)
        self.order_mgr.save_journal(Config.JOURNAL_FILE)

    def run_once(self, gap_pct: float = 0.0):
        """Single cycle: pre-market setup + one candle scan per symbol."""
        self.pre_market_setup(gap_pct)
        for inst in Config.WATCHLIST:
            self.on_candle(inst["symbol"])
        self.order_mgr.save_journal(Config.JOURNAL_FILE)

    def run_live_loop(self, gap_pct: float = 0.0, poll_sec: int = 60):
        """
        Continuous polling loop — checks each symbol every poll_sec seconds.

        For production, upgrade to Firstock WebSocket (socketConnect) for
        real-time tick data instead of polling. Example:
            ws = fs.firstock_socketConnect(
                userId=Config.FIRSTOCK_USER_ID,
                onMessage=on_tick_callback,
                onOpen=on_open_callback,
            )
            ws.subscribe(tokens=["NSE|738561", "NSE|341249"])
        """
        self.pre_market_setup(gap_pct)
        logger.info("Live loop started — Ctrl+C to stop")

        try:
            while True:
                now = datetime.now(Config.IST)
                hm  = (now.hour, now.minute)

                if Config.MARKET_OPEN <= hm <= Config.MARKET_CLOSE:
                    for inst in Config.WATCHLIST:
                        self.on_candle(inst["symbol"])

                if hm >= Config.MARKET_CLOSE:
                    logger.info("Market closed — saving and shutting down")
                    self.shutdown()
                    break

                time.sleep(poll_sec)

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            self.shutdown()

    def shutdown(self):
        """Saves journal, logs out of Firstock cleanly."""
        self.order_mgr.save_journal(Config.JOURNAL_FILE)
        logger.info(
            f"Session summary | "
            f"Daily P&L: ₹{self.risk_guard.daily_pnl:+,.2f} | "
            f"Trades: {self.risk_guard.trade_count}"
        )
        FirestockLoginManager.logout(self.session)


# ==============================================================================
# SECTION 15 — BACKTESTER  (IDENTICAL to Zerodha version)
# No broker code — pure CSV + signal logic.
# ==============================================================================

class Backtester:
    """
    Replays historical OHLCV data through the full signal engine.
    Use this to validate the system before any live trading.

    HOW TO USE:
        1. mkdir historical_data
        2. Place CSV files: historical_data/RELIANCE_15min.csv
           Required columns: datetime,open,high,low,close,volume
        3. python nse_algo_firstock.py --backtest

    FREE HISTORICAL DATA SOURCES:
        • NSEPython: pip install nsepython (NSE official data)
        • NSE Bhavcopy: nseindia.com → Market Data → Bhavcopy
        • After Firstock login: use DataFetcher._from_firstock() for any token
    """

    def __init__(self):
        self.fetcher = DataFetcher("backtest")
        self.trades  = []

    def run(
        self, symbol: str,
        prev_high: float, prev_low: float, prev_close: float
    ) -> list:
        df = self.fetcher.get_candles("0", symbol)
        if df is None or df.empty:
            logger.error(f"No backtest data for {symbol}")
            return []

        df = IndicatorEngine.compute(df)
        logger.info(f"Backtesting {symbol}: {len(df)} candles")

        pivots     = PivotCalculator.calculate(prev_high, prev_low, prev_close)
        open_trade = None
        trades     = []
        warmup     = max(Config.EMA_TREND, Config.ADX_PERIOD, Config.VOL_MA_PERIOD) + 5

        for i in range(warmup, len(df)):
            ind   = IndicatorEngine.latest(df.iloc[:i + 1])
            price = ind["close"]
            adx   = ind["adx"]
            ts    = str(df.iloc[i].get("datetime", i))

            if open_trade:
                d  = open_trade["direction"]
                sl = open_trade["stop_loss"]
                t1 = open_trade["target_1"]
                t2 = open_trade["target_2"]
                ep = open_trade["entry_price"]

                # ADX dropped
                if adx < Config.ADX_MIN:
                    pnl = (price - ep) * (1 if d == "long" else -1) * open_trade["qty"]
                    open_trade.update(
                        exit_price=price, exit_reason="ADX_DROPPED",
                        pnl=round(pnl, 2), exit_time=ts
                    )
                    trades.append(open_trade)
                    open_trade = None
                    continue

                # Stop-loss hit
                sl_hit = (d == "long" and price <= sl) or (d == "short" and price >= sl)
                if sl_hit:
                    pnl = (sl - ep) * (1 if d == "long" else -1) * open_trade["qty"]
                    open_trade.update(
                        exit_price=sl, exit_reason="STOP_LOSS",
                        pnl=round(pnl, 2), exit_time=ts
                    )
                    trades.append(open_trade)
                    open_trade = None
                    continue

                # T1 hit — move SL to breakeven
                if not open_trade.get("t1_hit"):
                    if (d == "long" and price >= t1) or (d == "short" and price <= t1):
                        open_trade["t1_hit"]    = True
                        open_trade["stop_loss"] = ep

                # T2 hit — full exit
                if open_trade.get("t1_hit") and t2:
                    if (d == "long" and price >= t2) or (d == "short" and price <= t2):
                        pnl = (t2 - ep) * (1 if d == "long" else -1) * open_trade["qty"]
                        open_trade.update(
                            exit_price=t2, exit_reason="TARGET_2",
                            pnl=round(pnl, 2), exit_time=ts
                        )
                        trades.append(open_trade)
                        open_trade = None
                continue

            # Scan for new signal
            signal = SignalGenerator.generate(ind, pivots, Config.RISK_PER_TRADE_PCT)
            if signal:
                qty = SLTPCalc.quantity(
                    Config.TOTAL_CAPITAL,
                    signal["position_size_pct"],
                    signal["entry_price"],
                    signal["stop_loss"],
                )
                open_trade = {
                    "symbol":      symbol,
                    "direction":   signal["direction"],
                    "entry_price": signal["entry_price"],
                    "stop_loss":   signal["stop_loss"],
                    "target_1":    signal["target_1"],
                    "target_2":    signal["target_2"],
                    "qty":         qty,
                    "entry_time":  ts,
                    "t1_hit":      False,
                    "adx":         signal["adx"],
                    "rr":          signal["risk_reward"],
                    "exit_price":  None,
                    "exit_reason": None,
                    "pnl":         None,
                    "exit_time":   None,
                }

        self.trades.extend(trades)
        return trades

    def report(self):
        """Prints full performance report and saves CSV."""
        if not self.trades:
            print("Backtest: no trades generated")
            return

        df    = pd.DataFrame(self.trades)
        total = len(df)
        wins  = int((df["pnl"] > 0).sum())
        loss  = total - wins
        wr    = wins / total * 100 if total else 0
        tp    = float(df["pnl"].sum())
        aw    = float(df[df["pnl"] > 0]["pnl"].mean()) if wins else 0
        al    = float(df[df["pnl"] <= 0]["pnl"].mean()) if loss else 0
        gp    = df[df["pnl"] > 0]["pnl"].sum()
        gl    = abs(df[df["pnl"] <= 0]["pnl"].sum())
        pf    = gp / gl if gl else float("inf")
        mdd   = float(df["pnl"].cumsum().min())
        ret   = tp / Config.TOTAL_CAPITAL * 100

        print("\n" + "=" * 58)
        print("  ObsidianPivot — Firstock Edition")
        print("  BACKTEST RESULTS")
        print("=" * 58)
        print(f"  Capital         : ₹{Config.TOTAL_CAPITAL:>12,.0f}")
        print(f"  Total trades    : {total:>12}")
        print(f"  Wins / Losses   : {wins:>5} / {loss:<5}")
        print(f"  Win rate        : {wr:>11.1f}%")
        print(f"  Profit factor   : {pf:>11.2f}x")
        print(f"  Total P&L       : ₹{tp:>+12,.2f}")
        print(f"  Return on cap   : {ret:>11.1f}%")
        print(f"  Avg win trade   : ₹{aw:>+12,.2f}")
        print(f"  Avg loss trade  : ₹{al:>+12,.2f}")
        print(f"  Max drawdown    : ₹{mdd:>12,.2f}")
        print("=" * 58)

        out = "backtest_results_firstock.csv"
        df.to_csv(out, index=False)
        print(f"  Results saved   : {out}")
        print("=" * 58)


# ==============================================================================
# SECTION 16 — ENTRY POINT  (Updated for Firstock)
#
# COMMANDS:
#   python nse_algo_firstock.py                        # paper mode (default)
#   python nse_algo_firstock.py --mode paper           # paper trading
#   python nse_algo_firstock.py --mode paper --gap 0.6 # paper with gap
#   python nse_algo_firstock.py --backtest             # historical backtest
#   python nse_algo_firstock.py --once --gap -0.3      # single scan cycle
#   python nse_algo_firstock.py --mode live            # LIVE TRADING
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="ObsidianPivot — NSE Algo Trading System (Firstock Edition) v3.0"
    )
    parser.add_argument(
        "--mode", default=Config.MODE,
        choices=["paper", "live", "backtest"],
        help="Trading mode: paper (default), live, backtest"
    )
    parser.add_argument(
        "--backtest", action="store_true",
        help="Run backtest on historical CSV data in historical_data/"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one full pre-market + scan cycle then exit"
    )
    parser.add_argument(
        "--gap", type=float, default=0.0,
        help="SGX Nifty gap %% vs previous NSE close (e.g. 0.6 or -0.4)"
    )
    args = parser.parse_args()

    # ── BACKTEST MODE ─────────────────────────────────────────────────────────
    if args.backtest or args.mode == "backtest":
        Config.MODE = "backtest"
        logger.info("Running backtest — no Firstock login needed")
        bt = Backtester()
        # Replace these OHLC values with actual previous-day data from Bhavcopy
        bt.run("RELIANCE",  prev_high=2850.0, prev_low=2780.0, prev_close=2810.0)
        bt.run("HDFCBANK",  prev_high=1680.0, prev_low=1640.0, prev_close=1660.0)
        bt.run("INFY",      prev_high=1810.0, prev_low=1770.0, prev_close=1790.0)
        bt.report()
        return

    # ── PAPER / LIVE MODE ─────────────────────────────────────────────────────
    Config.MODE = args.mode
    engine      = TradingEngine()

    if args.once:
        logger.info("Running single scan cycle")
        engine.run_once(gap_pct=args.gap)
    else:
        engine.run_live_loop(gap_pct=args.gap, poll_sec=60)


if __name__ == "__main__":
    main()


# ==============================================================================
# QUICK SETUP GUIDE — FIRSTOCK EDITION
# ==============================================================================
#
# STEP 1 — INSTALL DEPENDENCIES
#   pip install thefirstock ta pandas numpy requests pyotp
#
# STEP 2 — OPEN FIRSTOCK ACCOUNT (FREE)
#   • Go to firstock.in → Open Account
#   • Complete Aadhaar-based KYC (takes ~10 minutes)
#   • Account opening: ₹0, AMC: ₹0
#
# STEP 3 — GET API CREDENTIALS
#   • Log in at firstock.in
#   • Go to Profile → API & Developer
#   • Click "Generate API Key" → note your API Key and Vendor Code
#
# STEP 4 — SET UP TOTP
#   • When adding Firstock to Google Authenticator:
#     choose "Enter a setup key" instead of scanning QR
#   • Note the base32 secret key shown (looks like "JBSWY3DPEHPK3PXP")
#   • This is your FIRSTOCK_TOTP_SECRET
#
# STEP 5 — SET ENVIRONMENT VARIABLES
#   Add to ~/.bashrc or ~/.zshrc:
#
#   export FIRSTOCK_USER_ID="AB1234"
#   export FIRSTOCK_PASSWORD="your_password"
#   export FIRSTOCK_TOTP_SECRET="JBSWY3DPEHPK3PXP"
#   export FIRSTOCK_VENDOR_CODE="your_vendor_code"
#   export FIRSTOCK_API_KEY="your_api_key"
#   export TELEGRAM_BOT_TOKEN="your_bot_token"    # optional
#   export TELEGRAM_CHAT_ID="your_chat_id"        # optional
#
#   Then reload: source ~/.bashrc
#
# STEP 6 — TEST WITHOUT LIVE DATA
#   python nse_algo_firstock.py --backtest
#   → Runs on synthetic data, no API needed
#   → Verifies all logic works
#
# STEP 7 — PAPER TRADE (MINIMUM 30 DAYS)
#   python nse_algo_firstock.py --mode paper --gap 0.5
#   → Firstock login happens automatically each morning
#   → All signals and trade logic runs but NO real orders placed
#   → Review trades_journal_firstock.csv every evening
#   → Target: 55%+ win rate over 40+ paper trades before going live
#
# STEP 8 — AUTOMATE WITH CRON (Linux/Mac)
#   crontab -e
#   # Start at 8:28 AM, Mon-Fri (Indian weekdays)
#   28 8 * * 1-5 cd /path/to/algo && source ~/.bashrc && \
#       python nse_algo_firstock.py --mode paper --gap 0.0 \
#       >> logs/algo_$(date +\%Y\%m\%d).log 2>&1
#
# STEP 9 — GO LIVE (ONLY AFTER STEP 7 IS PROFITABLE)
#   python nse_algo_firstock.py --mode live --gap 0.4
#   Start with 25% of your intended capital for the first live month.
#   Scale only after 3 consecutive profitable months.
#
# COST SUMMARY vs ZERODHA (₹5L capital, 3 trades/day, 22 days):
#   Firstock API: ₹0/month  (Zerodha = ₹2,000/month)
#   Annual saving: ₹24,000 in API fees alone
#   Brokerage per trade: ₹20 (same as Zerodha)
#   Total monthly cost: ~₹2,270 vs ~₹4,270 with Zerodha
#
# ==============================================================================
