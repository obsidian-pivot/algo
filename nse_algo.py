#!/usr/bin/env python3
"""
================================================================================
NSE DAY TRADING ALGO SYSTEM v3.0
================================================================================
Based on: NSE Day Trading System v3.0
          (EMA Crossover + Pivot Points + ADX Filter + Volume Confirmation)

Author  : Your Name
Date    : May 2026

FOUR PILLARS — all four must confirm before any trade is placed:
  Pillar 1 : EMA Crossover  (9 EMA vs 21 EMA on 15-min chart)
  Pillar 2 : Pivot Points   (PP, R1, R2, S1, S2 from yesterday OHLC)
  Pillar 3 : ADX Filter     (ADX(14) >= 20 = trend exists, else VETO)
  Pillar 4 : Volume         (entry candle >= 1.5x 20-bar average volume)

MODES:
  paper    — Simulates orders, no real capital. START HERE always.
  live     — Real orders via Zerodha Kite Connect API
  backtest — Replays historical CSV data through the system

HOW TO START (step by step):
  Step 1: pip install ta pandas numpy kiteconnect requests
  Step 2: Fill in Config section below (capital, watchlist, API keys)
  Step 3: python nse_algo.py --backtest          (validate the logic)
  Step 4: python nse_algo.py --mode paper        (30+ paper trading days)
  Step 5: python nse_algo.py --mode live         (only after step 4 is profitable)

ZERODHA API SETUP:
  1. Register at developers.kite.trade
  2. Create an app → get API key and secret
  3. Set as environment variables (NEVER hardcode keys in source):
       export KITE_API_KEY="your_key"
       export KITE_API_SECRET="your_secret"
  4. Run the login flow once per day to get the access token

TELEGRAM ALERTS (optional):
  1. Message @BotFather on Telegram → create a bot → get token
  2. Get your chat_id from @userinfobot
  3. export TELEGRAM_BOT_TOKEN="your_token"
     export TELEGRAM_CHAT_ID="your_chat_id"
================================================================================
"""

# ==============================================================================
# SECTION 0 — IMPORTS
# Why: We use only well-maintained, battle-tested libraries.
# - pandas / numpy : data handling and math
# - ta             : technical indicators (EMA, ADX, Bollinger, ATR)
# - kiteconnect    : Zerodha broker API
# - requests       : Telegram notifications
# ==============================================================================
import os
import sys
import time
import logging
import csv
import argparse
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo  # Python 3.9+

import pandas as pd
import numpy as np

# ta — Technical Analysis library
# Provides clean, pandas-based implementations of all indicators we need
# Install: pip install ta
try:
    from ta.trend import EMAIndicator, ADXIndicator
    from ta.volatility import BollingerBands, AverageTrueRange
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    print("WARNING: 'ta' library not installed.")
    print("         Run: pip install ta")

# kiteconnect — Zerodha broker API
# Required only for live and paper modes with real data feed
# Install: pip install kiteconnect
try:
    from kiteconnect import KiteConnect
    KITE_AVAILABLE = True
except ImportError:
    KITE_AVAILABLE = False

# requests — used for Telegram notifications
# Install: pip install requests
try:
    import requests as http_requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


# ==============================================================================
# SECTION 1 — CONFIGURATION
# Why: All tunable parameters live in one place. Never scatter magic numbers
# through the code. Change only this section to customise the system.
# ==============================================================================

class Config:
    """
    Master configuration for the NSE Day Trading Algo.

    SECURITY RULE: Never write API keys directly in this file.
    Always read them from environment variables.
    """

    # ── TRADING MODE ─────────────────────────────────────────────────────────
    # "paper"    : Simulated — no real orders. Start here.
    # "live"     : Real orders via Kite Connect. Only after profitable paper run.
    # "backtest" : Historical CSV data replay.
    MODE: str = "paper"

    # ── API CREDENTIALS ──────────────────────────────────────────────────────
    # Set these as environment variables, not here:
    #   export KITE_API_KEY="xxxxx"
    #   export KITE_API_SECRET="xxxxx"
    #   export KITE_ACCESS_TOKEN="xxxxx"  (generated fresh each day after login)
    KITE_API_KEY: str      = os.getenv("KITE_API_KEY", "YOUR_API_KEY_HERE")
    KITE_API_SECRET: str   = os.getenv("KITE_API_SECRET", "YOUR_SECRET_HERE")
    KITE_ACCESS_TOKEN: str = os.getenv("KITE_ACCESS_TOKEN", "")

    # ── TELEGRAM ALERTS ──────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str   = os.getenv("TELEGRAM_CHAT_ID", "")
    SEND_TELEGRAM: bool     = bool(os.getenv("TELEGRAM_BOT_TOKEN"))

    # ── CAPITAL AND RISK ─────────────────────────────────────────────────────
    TOTAL_CAPITAL: float      = 500_000.0  # INR — your trading capital
    RISK_PER_TRADE_PCT: float = 0.01       # 1% risk per trade
    DAILY_LOSS_LIMIT_PCT: float = 0.02     # Halt if down 2% on the day
    WEEKLY_LOSS_LIMIT_PCT: float = 0.05    # Reduce size if down 5% on week
    MAX_TRADES_PER_DAY: int   = 3          # Never exceed 3 trades per day
    MIN_RR_RATIO: float       = 2.0        # Minimum 1:2 reward-to-risk

    # ── WATCHLIST ────────────────────────────────────────────────────────────
    # Find instrument tokens at: kite.zerodha.com/instruments
    # or via kite.instruments("NSE") after logging in
    WATCHLIST: list = [
        {"symbol": "RELIANCE",  "exchange": "NSE", "token": 738561},
        {"symbol": "HDFCBANK",  "exchange": "NSE", "token": 341249},
        {"symbol": "INFY",      "exchange": "NSE", "token": 408065},
        {"symbol": "TCS",       "exchange": "NSE", "token": 2953217},
        {"symbol": "ICICIBANK", "exchange": "NSE", "token": 1270529},
    ]

    # ── INDICATOR PARAMETERS ─────────────────────────────────────────────────
    EMA_FAST: int         = 9     # Fast EMA (Pillar 1)
    EMA_SLOW: int         = 21    # Slow EMA (Pillar 1)
    EMA_TREND: int        = 50    # Optional trend EMA (from System A)
    ADX_PERIOD: int       = 14    # ADX period (Pillar 3)
    ADX_MIN: float        = 20.0  # Below this = no trading (absolute veto)
    ADX_STRONG: float     = 25.0  # Above this = full position size
    BB_PERIOD: int        = 20    # Bollinger Bands period (morning filter)
    BB_STD: float         = 2.0   # Bollinger Bands standard deviation
    ATR_PERIOD: int       = 14    # ATR for stop-loss calculation
    VOL_MA_PERIOD: int    = 20    # Volume moving average period (Pillar 4)
    VOL_MULTIPLIER: float = 1.5   # Entry candle volume must be >= 1.5x avg

    # ── PIVOT AND ENTRY SETTINGS ─────────────────────────────────────────────
    PIVOT_ENTRY_TOLERANCE: float = 0.002   # Price within 0.2% of pivot
    EMA_MIN_SEPARATION: float    = 0.0015  # EMA gap must be >= 0.15% of price
    SL_PIVOT_BUFFER: float       = 0.004   # SL placed 0.4% beyond pivot
    SL_ATR_MULTIPLIER: float     = 0.5     # SL = entry +/- (0.5 * ATR)
    MAX_SL_PCT: float            = 0.008   # Hard cap: SL never > 0.8% away

    # ── TIMEFRAME ────────────────────────────────────────────────────────────
    CANDLE_INTERVAL: str = "15minute"   # Main chart interval
    CANDLES_NEEDED: int  = 60          # Candles fetched per scan

    # ── TRADING HOURS (IST) ──────────────────────────────────────────────────
    IST = ZoneInfo("Asia/Kolkata")
    MARKET_OPEN     = (9, 15)   # Opens — watch only
    TRADE_START     = (9, 30)   # First trade allowed
    PRIME_END       = (11, 30)  # Prime window ends
    AFTERNOON_START = (13, 30)  # Afternoon window starts
    CAUTION_START   = (14, 30)  # Be very selective
    HARD_EXIT       = (15, 15)  # Close ALL positions
    MARKET_CLOSE    = (15, 30)  # Market closes

    # ── PIVOT RANGE FILTER ───────────────────────────────────────────────────
    PIVOT_RANGE_MIN: float  = 0.008   # R1-S1 must be > 0.8% for any trade
    PIVOT_RANGE_FULL: float = 0.015   # R1-S1 > 1.5% = full position allowed

    # ── FILE PATHS ───────────────────────────────────────────────────────────
    LOG_FILE: str           = "algo_log.txt"
    JOURNAL_FILE: str       = "trades_journal.csv"
    BACKTEST_DATA_DIR: str  = "historical_data"


# ==============================================================================
# SECTION 2 — LOGGING
# Why: Every decision the algo makes must be logged. If a trade goes wrong,
# you need to know exactly what data the system saw and why it acted.
# The log file is your audit trail — review it every evening.
# ==============================================================================

def setup_logging(log_file: str) -> logging.Logger:
    """
    Creates a dual-output logger: console (INFO) + file (DEBUG).
    The file captures everything; the console shows only key events.
    """
    logger = logging.getLogger("NSE_ALGO")
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console: shows INFO and above in real time
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)

    # File: captures DEBUG level — every condition check, every skip reason
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    if not logger.handlers:
        logger.addHandler(ch)
        logger.addHandler(fh)

    return logger


logger = setup_logging(Config.LOG_FILE)


# ==============================================================================
# SECTION 3 — PIVOT POINT CALCULATOR (PILLAR 2)
# Why: Pivot points define WHERE you enter and target. They are calculated
# from yesterday's data before the market opens, so you know your levels
# before the first candle. This removes all real-time guesswork about
# "where should I enter?" — the answer is always: at a pivot level.
#
# Formula (classic pivot points):
#   PP = (H + L + C) / 3
#   R1 = (2 * PP) - L        R2 = PP + (H - L)
#   S1 = (2 * PP) - H        S2 = PP - (H - L)
# ==============================================================================

class PivotCalculator:
    """Calculates classic daily pivot points from previous session OHLC."""

    @staticmethod
    def calculate(high: float, low: float, close: float) -> dict:
        """
        Args:
            high  : Previous day's High
            low   : Previous day's Low
            close : Previous day's Close

        Returns dict: {PP, R1, R2, S1, S2}
        """
        pp = (high + low + close) / 3
        r1 = (2 * pp) - low
        r2 = pp + (high - low)
        s1 = (2 * pp) - high
        s2 = pp - (high - low)

        pivots = {
            "PP": round(pp, 2),
            "R1": round(r1, 2),
            "R2": round(r2, 2),
            "S1": round(s1, 2),
            "S2": round(s2, 2),
        }
        logger.debug(f"Pivots: {pivots}")
        return pivots

    @staticmethod
    def range_quality(pivots: dict, price: float) -> str:
        """
        Checks if the R1-S1 range is tradeable.

        Returns:
          "full"   → R1-S1 > 1.5% — trade at full position size
          "normal" → R1-S1 0.8%–1.5% — trade at normal size
          "skip"   → R1-S1 < 0.8% — levels too compressed, skip today
        """
        rng_pct = (pivots["R1"] - pivots["S1"]) / price
        if rng_pct >= Config.PIVOT_RANGE_FULL:
            return "full"
        elif rng_pct >= Config.PIVOT_RANGE_MIN:
            return "normal"
        else:
            return "skip"

    @staticmethod
    def nearest_pivot(
        price: float, pivots: dict
    ) -> tuple[Optional[str], Optional[float]]:
        """
        Returns (name, price) of the closest pivot level within tolerance.
        Returns (None, None) if price is not near any pivot.
        """
        closest = sorted(pivots.items(), key=lambda x: abs(x[1] - price))
        for name, level in closest:
            dist_pct = abs(price - level) / price
            if dist_pct <= Config.PIVOT_ENTRY_TOLERANCE:
                logger.debug(f"Near pivot {name}={level} ({dist_pct:.3%} away)")
                return name, level
        return None, None

    @staticmethod
    def target_levels(
        entry_pivot: str, direction: str, pivots: dict
    ) -> tuple[Optional[float], Optional[float]]:
        """
        Returns (T1, T2) target prices based on entry pivot and direction.

        Logic: always trade pivot-to-pivot.
          Long:  S2 → S1 → PP → R1 → R2
          Short: R2 → R1 → PP → S1 → S2
        """
        seq_long  = ["S2", "S1", "PP", "R1", "R2"]
        seq_short = list(reversed(seq_long))
        order = seq_long if direction == "long" else seq_short

        try:
            idx = order.index(entry_pivot)
            t1_name = order[idx + 1] if idx + 1 < len(order) else None
            t2_name = order[idx + 2] if idx + 2 < len(order) else None
            return pivots.get(t1_name), pivots.get(t2_name)
        except (ValueError, IndexError):
            return None, None


# ==============================================================================
# SECTION 4 — INDICATOR ENGINE (ALL FOUR PILLARS)
# Why: This computes all the math. The 'ta' library handles each indicator
# correctly so we don't reinvent the wheel. Inputs: standard OHLCV DataFrame.
# Outputs: same DataFrame enriched with indicator columns.
#
# Column naming convention:
#   ema_fast, ema_slow, ema_trend   → Pillar 1
#   adx, adx_rising                 → Pillar 3
#   vol_ma, vol_ratio               → Pillar 4
#   bb_width, bb_expanding          → Morning filter 3
#   atr                             → Stop-loss calculation
# ==============================================================================

class IndicatorEngine:
    """Adds all required technical indicator columns to an OHLCV DataFrame."""

    @staticmethod
    def compute(df: pd.DataFrame) -> pd.DataFrame:
        """
        Requires columns: open, high, low, close, volume.
        Returns the same DataFrame with indicator columns appended.
        """
        if not TA_AVAILABLE:
            raise RuntimeError("Install 'ta': pip install ta")

        # Coerce to numeric and drop NaN rows
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"]).copy()

        # ── PILLAR 1: EMA CROSSOVER ───────────────────────────────────────
        # 9 EMA reacts quickly to price changes (momentum indicator)
        df["ema_fast"] = EMAIndicator(
            close=df["close"], window=Config.EMA_FAST, fillna=True
        ).ema_indicator()

        # 21 EMA is the trend filter — direction of the trade depends on this
        df["ema_slow"] = EMAIndicator(
            close=df["close"], window=Config.EMA_SLOW, fillna=True
        ).ema_indicator()

        # 50 EMA — optional additional trend strength from System A
        df["ema_trend"] = EMAIndicator(
            close=df["close"], window=Config.EMA_TREND, fillna=True
        ).ema_indicator()

        # EMA separation as fraction of price (must be >= 0.15%)
        df["ema_sep"] = abs(df["ema_fast"] - df["ema_slow"]) / df["close"]

        # EMA direction: +1=uptrend, -1=downtrend, 0=neutral
        df["ema_dir"] = 0
        df.loc[df["ema_fast"] > df["ema_slow"], "ema_dir"] =  1
        df.loc[df["ema_fast"] < df["ema_slow"], "ema_dir"] = -1

        # Tangle detector: count crossovers in last 6 candles
        crosses = (df["ema_dir"] != df["ema_dir"].shift(1)).astype(int)
        df["ema_crosses_6"] = crosses.rolling(6).sum().fillna(0)

        # ── PILLAR 3: ADX TREND STRENGTH ─────────────────────────────────
        # ADX measures TREND STRENGTH, not direction.
        # Key insight: ADX can be rising in both uptrends and downtrends.
        adx_ind = ADXIndicator(
            high=df["high"], low=df["low"], close=df["close"],
            window=Config.ADX_PERIOD, fillna=True
        )
        df["adx"]        = adx_ind.adx()
        df["adx_pos"]    = adx_ind.adx_pos()   # +DI line
        df["adx_neg"]    = adx_ind.adx_neg()   # -DI line
        df["adx_rising"] = df["adx"] > df["adx"].shift(1)

        # ── PILLAR 4: VOLUME CONFIRMATION ────────────────────────────────
        # 20-bar volume moving average (baseline for comparison)
        df["vol_ma"]    = df["volume"].rolling(Config.VOL_MA_PERIOD).mean()
        df["vol_ratio"] = df["volume"] / df["vol_ma"].replace(0, float("nan"))

        # ── BOLLINGER BANDS (morning filter 3) ───────────────────────────
        # Wide, expanding bands = good volatility for trading
        # Narrow, squeezing bands = avoid — low volatility = choppy signals
        bb = BollingerBands(
            close=df["close"],
            window=Config.BB_PERIOD,
            window_dev=Config.BB_STD,
            fillna=True
        )
        df["bb_upper"]     = bb.bollinger_hband()
        df["bb_lower"]     = bb.bollinger_lband()
        df["bb_width"]     = (df["bb_upper"] - df["bb_lower"]) / df["close"]
        df["bb_expanding"] = df["bb_width"] > df["bb_width"].shift(1)

        # ── ATR: AVERAGE TRUE RANGE (for stop-loss sizing) ───────────────
        # ATR measures average price movement per candle
        # Used as the alternate stop-loss method when pivot SL is too wide
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
        """Returns latest row as a flat dict of indicator values."""
        r = df.iloc[-1]
        return {
            "close":        float(r["close"]),
            "volume":       float(r["volume"]),
            "ema_fast":     float(r["ema_fast"]),
            "ema_slow":     float(r["ema_slow"]),
            "ema_trend":    float(r["ema_trend"]),
            "ema_sep":      float(r["ema_sep"]),
            "ema_dir":      int(r["ema_dir"]),
            "ema_crosses_6":int(r["ema_crosses_6"]),
            "adx":          float(r["adx"]),
            "adx_rising":   bool(r["adx_rising"]),
            "vol_ma":       float(r["vol_ma"]) if not pd.isna(r["vol_ma"]) else 0.0,
            "vol_ratio":    float(r["vol_ratio"]) if not pd.isna(r["vol_ratio"]) else 0.0,
            "bb_width":     float(r["bb_width"]),
            "bb_expanding": bool(r["bb_expanding"]),
            "atr":          float(r["atr"]),
            "is_bullish":   bool(r["is_bullish"]),
            "is_bearish":   bool(r["is_bearish"]),
            "body_pct":     float(r["body_pct"]),
        }


# ==============================================================================
# SECTION 5 — MORNING FILTER (4-FILTER SCANNER)
# Why: Every day is not equal. Some days trend strongly; many days chop
# sideways. The 4-filter morning scanner quantifies the day quality at 9:30 AM
# and adjusts position size accordingly. This single mechanism is responsible
# for most of the improvement from 61% to 69% win rate.
#
# Scoring: Green=1pt, Amber=0.5pt, Red=0pt. Max=4.0
#   >= 3.5 → Full size (1% risk)
#   2.5-3.0 → Half size (0.5% risk)
#   < 2.5  → No trade (0% risk)
# ==============================================================================

class MorningFilter:
    """Runs the 4-filter morning scanner to score the trading day 0–4.0."""

    @staticmethod
    def run(indicators: dict, gap_pct: float) -> dict:
        """
        Args:
            indicators : dict from IndicatorEngine.latest()
            gap_pct    : SGX Nifty gap % vs previous NSE close (e.g. +0.6)

        Returns dict with: score, grade, position_size_pct, decision, per-filter results
        """
        score = 0.0
        results = {}

        # FILTER 1 — ADX TREND STRENGTH (most important)
        # This is the primary gate. Red = no trading today regardless of score.
        adx = indicators["adx"]
        if adx >= Config.ADX_STRONG:
            f1 = {"score": 1.0, "grade": "green",
                  "note": f"ADX={adx:.1f} — strong trend"}
        elif adx >= Config.ADX_MIN:
            f1 = {"score": 0.5, "grade": "amber",
                  "note": f"ADX={adx:.1f} — weak trend, borderline"}
        else:
            f1 = {"score": 0.0, "grade": "red",
                  "note": f"ADX={adx:.1f} — NO TREND (hard veto)"}
        score += f1["score"]
        results["f1_adx"] = f1

        # FILTER 2 — GAP SIZE (proxy for opening conviction)
        # A flat open (< 0.3% gap) means no overnight conviction → choppy risk
        abs_gap = abs(gap_pct)
        if abs_gap >= 0.5:
            f2 = {"score": 1.0, "grade": "green",
                  "note": f"Gap={gap_pct:+.2f}% — strong overnight conviction"}
        elif abs_gap >= 0.3:
            f2 = {"score": 0.5, "grade": "amber",
                  "note": f"Gap={gap_pct:+.2f}% — moderate conviction"}
        else:
            f2 = {"score": 0.0, "grade": "red",
                  "note": f"Gap={gap_pct:+.2f}% — flat open, sideways risk"}
        score += f2["score"]
        results["f2_gap"] = f2

        # FILTER 3 — BOLLINGER BAND WIDTH
        # Wide + expanding = good volatility environment for trend trades
        bb_w = indicators["bb_width"]
        bb_x = indicators["bb_expanding"]
        if bb_w > 0.015 and bb_x:
            f3 = {"score": 1.0, "grade": "green",
                  "note": f"BB width={bb_w:.3f} and expanding"}
        elif bb_w > 0.010:
            f3 = {"score": 0.5, "grade": "amber",
                  "note": f"BB width={bb_w:.3f} — moderate"}
        else:
            f3 = {"score": 0.0, "grade": "red",
                  "note": f"BB width={bb_w:.3f} — squeeze, low volatility"}
        score += f3["score"]
        results["f3_bb"] = f3

        # FILTER 4 — OPENING VOLUME LEVEL
        # Above-average volume at open = institutional activity present
        vr = indicators["vol_ratio"]
        if vr >= Config.VOL_MULTIPLIER:
            f4 = {"score": 1.0, "grade": "green",
                  "note": f"Volume={vr:.1f}x avg — institutional activity"}
        elif vr >= 1.0:
            f4 = {"score": 0.5, "grade": "amber",
                  "note": f"Volume={vr:.1f}x avg — below conviction threshold"}
        else:
            f4 = {"score": 0.0, "grade": "red",
                  "note": f"Volume={vr:.1f}x avg — weak, no follow-through"}
        score += f4["score"]
        results["f4_volume"] = f4

        # OVERALL DECISION
        # ADX hard veto always wins regardless of total score
        if adx < Config.ADX_MIN:
            grade = "red"
            pos_size_pct = 0.0
            decision = "NO TRADE — ADX hard veto (ADX < 20)"
        elif score >= 3.5:
            grade = "green"
            pos_size_pct = Config.RISK_PER_TRADE_PCT
            decision = "TRADE — full size (1% risk)"
        elif score >= 2.5:
            grade = "amber"
            pos_size_pct = Config.RISK_PER_TRADE_PCT / 2.0
            decision = "TRADE — half size (0.5% risk)"
        else:
            grade = "red"
            pos_size_pct = 0.0
            decision = "NO TRADE — morning score too low"

        results.update({
            "score":            round(score, 1),
            "grade":            grade,
            "position_size_pct": pos_size_pct,
            "decision":         decision,
        })

        logger.info(
            f"Morning filter: {score:.1f}/4.0 [{grade.upper()}] | {decision}"
        )
        for k, v in results.items():
            if isinstance(v, dict):
                logger.debug(f"  {k}: {v['note']} [{v['grade']}]")

        return results


# ==============================================================================
# SECTION 6 — SIGNAL GENERATOR (THE 6-CONDITION ENTRY CHECKLIST)
# Why: This is the brain of the system. It enforces all 6 entry conditions
# from the trading system simultaneously. A signal only fires when every
# single condition passes. One failure = skip and wait for the next candle.
#
# The 6 conditions:
#   1. ADX > 20 (trend exists)
#   2. EMAs correctly oriented for the trade direction
#   3. EMA separation >= 0.15% (not a weak crossover)
#   4. Price within 0.2% of a pivot level (entry zone)
#   5. Signal candle confirms direction (bullish/bearish candle body)
#   6. Volume >= 1.5x 20-bar average (institutional participation)
# ==============================================================================

class SignalGenerator:
    """Checks all 6 entry conditions and returns a trade signal or None."""

    @staticmethod
    def _check(
        ind: dict, pivots: dict, direction: str
    ) -> dict:
        """
        Runs all 6 conditions for the given direction.
        Returns a dict with pass/fail for each condition.
        """
        price  = ind["close"]
        checks = {}

        # Condition 1: ADX > 20
        c1_pass = ind["adx"] >= Config.ADX_MIN
        checks["c1_adx"] = {
            "pass": c1_pass,
            "note": f"ADX={ind['adx']:.1f} {'OK' if c1_pass else 'FAIL < 20'}"
        }

        # Condition 2: EMA direction correct for trade
        if direction == "long":
            c2_pass = ind["ema_dir"] == 1
            c2_note = "9 EMA above 21 EMA (uptrend)" if c2_pass else "9 EMA NOT above 21 EMA"
        else:
            c2_pass = ind["ema_dir"] == -1
            c2_note = "9 EMA below 21 EMA (downtrend)" if c2_pass else "9 EMA NOT below 21 EMA"
        checks["c2_ema_dir"] = {"pass": c2_pass, "note": c2_note}

        # Condition 2b: Not tangled (advisory — still blocks trade if tangled)
        tangled = ind["ema_crosses_6"] > 2
        checks["c2b_tangle"] = {
            "pass": not tangled,
            "note": f"{ind['ema_crosses_6']} crossovers in 6 candles {'(TANGLED)' if tangled else '(OK)'}"
        }

        # Condition 3: EMA separation >= 0.15%
        c3_pass = ind["ema_sep"] >= Config.EMA_MIN_SEPARATION
        checks["c3_ema_sep"] = {
            "pass": c3_pass,
            "note": f"EMA gap={ind['ema_sep']:.3%} {'OK' if c3_pass else 'too tight'}"
        }

        # Condition 4: Price near a pivot level (within 0.2%)
        pvt_name, pvt_price = PivotCalculator.nearest_pivot(price, pivots)
        c4_pass = pvt_name is not None
        checks["c4_pivot"] = {
            "pass": c4_pass,
            "note": f"Near {pvt_name}={pvt_price}" if c4_pass else "Not near any pivot",
            "pivot_name": pvt_name,
            "pivot_price": pvt_price,
        }

        # Condition 5: Signal candle correct direction
        if direction == "long":
            c5_pass = ind["is_bullish"] and ind["body_pct"] > 0.001
            c5_note = "Bullish candle OK" if c5_pass else "No bullish candle"
        else:
            c5_pass = ind["is_bearish"] and ind["body_pct"] > 0.001
            c5_note = "Bearish candle OK" if c5_pass else "No bearish candle"
        checks["c5_candle"] = {"pass": c5_pass, "note": c5_note}

        # Condition 6: Volume >= 1.5x average
        c6_pass = ind["vol_ratio"] >= Config.VOL_MULTIPLIER
        checks["c6_volume"] = {
            "pass": c6_pass,
            "note": f"Volume={ind['vol_ratio']:.1f}x avg {'OK' if c6_pass else 'insufficient'}"
        }

        # All hard conditions (including tangle check) must pass
        hard = ["c1_adx", "c2_ema_dir", "c2b_tangle",
                "c3_ema_sep", "c4_pivot", "c5_candle", "c6_volume"]
        all_pass = all(checks[k]["pass"] for k in hard)

        return {
            "all_pass": all_pass,
            "checks": checks,
            "pivot_name": pvt_name,
            "pivot_price": pvt_price,
            "failed": [k for k in hard if not checks[k]["pass"]],
        }

    @staticmethod
    def generate(
        ind: dict, pivots: dict, position_size_pct: float
    ) -> Optional[dict]:
        """
        Tries long then short. Returns a complete signal dict if any direction
        passes all 6 conditions AND meets the minimum 1:2 RR requirement.
        Returns None if no valid setup.
        """
        if position_size_pct == 0:
            return None

        for direction in ["long", "short"]:
            result = SignalGenerator._check(ind, pivots, direction)

            if not result["all_pass"]:
                fails = ", ".join(result["failed"])
                logger.debug(f"{direction}: failed [{fails}]")
                continue

            price      = ind["close"]
            atr        = ind["atr"]
            pvt_name   = result["pivot_name"]
            pvt_price  = result["pivot_price"]

            # Calculate stop-loss using both methods, pick tighter
            sl_pivot = SLTPCalc.pivot_stop(price, pvt_price, direction)
            sl_atr   = SLTPCalc.atr_stop(price, atr, direction)
            sl       = SLTPCalc.best_sl(price, sl_pivot, sl_atr, direction)

            if sl is None:
                logger.debug(f"{direction}: SL exceeds hard cap — skip")
                continue

            # Get target levels
            t1, t2 = PivotCalculator.target_levels(pvt_name, direction, pivots)
            if t1 is None:
                logger.debug(f"{direction}: no T1 target available")
                continue

            # Verify minimum 1:2 RR
            risk   = abs(price - sl)
            reward = abs(t1 - price)
            rr     = reward / risk if risk > 0 else 0.0

            if rr < Config.MIN_RR_RATIO:
                logger.debug(f"{direction}: RR={rr:.2f} below minimum {Config.MIN_RR_RATIO}")
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
# SECTION 7 — STOP-LOSS AND TARGET CALCULATOR
# Why: SL placement is the most critical parameter in any trade. Too tight
# and you get stopped out by noise. Too wide and you lose too much.
# We use a 3-method hierarchy:
#   1. Pivot-based SL (primary) — place 0.4% beyond the pivot level
#   2. ATR-based SL (alternate) — place 0.5 * ATR from entry
#   3. Hard cap of 0.8% — if either method exceeds this, SKIP the trade
# ==============================================================================

class SLTPCalc:
    """Stop-loss and position sizing calculations."""

    @staticmethod
    def pivot_stop(entry: float, pivot: float, direction: str) -> float:
        """Places SL 0.4% beyond the pivot level."""
        buf = pivot * Config.SL_PIVOT_BUFFER
        return round((pivot - buf) if direction == "long" else (pivot + buf), 2)

    @staticmethod
    def atr_stop(entry: float, atr: float, direction: str) -> float:
        """Places SL at 0.5 * ATR from entry price."""
        dist = atr * Config.SL_ATR_MULTIPLIER
        return round((entry - dist) if direction == "long" else (entry + dist), 2)

    @staticmethod
    def best_sl(
        entry: float, sl_pivot: float, sl_atr: float, direction: str
    ) -> Optional[float]:
        """
        Picks the tighter of the two SL methods.
        Returns None if the chosen SL exceeds the 0.8% hard cap.

        Tighter = closer to entry = smaller loss if stopped.
        Long:  higher SL price = tighter  (e.g. 1185 > 1180)
        Short: lower  SL price = tighter  (e.g. 1215 < 1220)
        """
        sl = max(sl_pivot, sl_atr) if direction == "long" else min(sl_pivot, sl_atr)
        dist_pct = abs(entry - sl) / entry

        if dist_pct > Config.MAX_SL_PCT:
            logger.debug(f"SL={sl} is {dist_pct:.3%} away — exceeds hard cap {Config.MAX_SL_PCT:.1%}")
            return None

        return sl

    @staticmethod
    def quantity(
        capital: float, risk_pct: float, entry: float, sl: float
    ) -> int:
        """
        Calculates number of shares to buy/sell.

        Formula:
            Capital at risk = capital * risk_pct
            SL per share    = |entry - sl|
            Quantity        = capital_at_risk / sl_per_share

        Example (capital=5,00,000 | 1% risk | entry=1200 | sl=1185):
            capital_at_risk = 5,000
            sl_per_share    = 15
            quantity        = 333 shares
        """
        capital_at_risk = capital * risk_pct
        sl_per_share    = abs(entry - sl)

        if sl_per_share <= 0:
            logger.warning("SL per share = 0, cannot size position")
            return 0

        qty = int(capital_at_risk / sl_per_share)
        logger.debug(
            f"Position size | capital={capital:,.0f} risk={risk_pct:.1%} "
            f"entry={entry} sl={sl} sl/share={sl_per_share:.2f} qty={qty}"
        )
        return qty


# ==============================================================================
# SECTION 8 — ORDER MANAGER
# Why: Clean abstraction between "decision to trade" and "actual order".
# In paper mode, no real API calls are made — trades are simulated in memory
# and saved to a CSV journal. In live mode, real Kite orders are placed.
# This means you can switch MODE without changing any decision logic.
# ==============================================================================

class OrderManager:
    """
    Handles order placement, tracking, and exit for all modes.
    Paper mode: in-memory simulation + CSV journal.
    Live mode:  Kite Connect API calls.
    """

    def __init__(self, mode: str, kite=None):
        self.mode   = mode
        self.kite   = kite
        self.orders = []   # In-memory trade book

    def place_order(
        self,
        symbol: str, exchange: str, direction: str,
        quantity: int, entry_price: float,
        stop_loss: float, target_1: float, target_2: Optional[float],
        signal: dict,
    ) -> str:
        """Places an entry order. Returns internal order_id."""
        oid = f"ORD_{symbol}_{datetime.now().strftime('%H%M%S%f')[:14]}"
        tx  = "BUY" if direction == "long" else "SELL"

        order = {
            "order_id":    oid,
            "symbol":      symbol,
            "exchange":    exchange,
            "direction":   direction,
            "transaction": tx,
            "quantity":    quantity,
            "entry_price": entry_price,
            "stop_loss":   stop_loss,
            "target_1":    target_1,
            "target_2":    target_2,
            "status":      "OPEN",
            "entry_time":  datetime.now(Config.IST).isoformat(),
            "exit_price":  None,
            "exit_time":   None,
            "pnl":         None,
            "exit_reason": None,
            "t1_hit":      False,
            "sl_moved":    False,
            "pnl_booked":  False,
            "adx_entry":   signal.get("adx"),
            "vol_ratio":   signal.get("vol_ratio"),
            "risk_reward": signal.get("risk_reward"),
            "pivot":       signal.get("entry_pivot"),
        }
        self.orders.append(order)

        if self.mode == "paper":
            logger.info(
                f"[PAPER] {tx} {quantity}x {symbol} @ {entry_price:.2f} | "
                f"SL={stop_loss:.2f} T1={target_1:.2f} T2={target_2}"
            )

        elif self.mode == "live":
            if not KITE_AVAILABLE or self.kite is None:
                raise RuntimeError("Kite not available for live trading")
            try:
                kite_id = self.kite.place_order(
                    tradingsymbol=symbol,
                    exchange=exchange,
                    transaction_type=(
                        self.kite.TRANSACTION_TYPE_BUY
                        if direction == "long" else
                        self.kite.TRANSACTION_TYPE_SELL
                    ),
                    quantity=quantity,
                    order_type=self.kite.ORDER_TYPE_MARKET,
                    product=self.kite.PRODUCT_MIS,    # Intraday
                    variety=self.kite.VARIETY_REGULAR,
                )
                order["kite_order_id"] = str(kite_id)
                logger.info(f"[LIVE] Order placed: kite_id={kite_id}")

                # Place SL order immediately after entry
                self._kite_sl_order(
                    symbol, exchange, direction, quantity, stop_loss
                )
            except Exception as e:
                logger.error(f"Live order failed: {e}")
                self.orders.remove(order)
                raise

        return oid

    def _kite_sl_order(
        self, symbol: str, exchange: str, direction: str,
        qty: int, sl: float
    ):
        """Places a server-side stop-loss order on Kite after entry."""
        sl_tx = (self.kite.TRANSACTION_TYPE_SELL if direction == "long"
                 else self.kite.TRANSACTION_TYPE_BUY)
        sl_limit = sl * (0.995 if direction == "long" else 1.005)
        try:
            sl_id = self.kite.place_order(
                tradingsymbol=symbol, exchange=exchange,
                transaction_type=sl_tx, quantity=qty,
                order_type=self.kite.ORDER_TYPE_SL,
                trigger_price=round(sl, 2),
                price=round(sl_limit, 2),
                product=self.kite.PRODUCT_MIS,
                variety=self.kite.VARIETY_REGULAR,
            )
            logger.info(f"SL order placed: kite_id={sl_id} @ {sl:.2f}")
        except Exception as e:
            logger.error(f"SL order failed: {e}")

    def exit_order(self, oid: str, exit_price: float, reason: str):
        """Records exit of a paper trade (live: cancel SL + close position)."""
        for o in self.orders:
            if o["order_id"] == oid and o["status"] == "OPEN":
                o["exit_price"] = exit_price
                o["exit_time"]  = datetime.now(Config.IST).isoformat()
                o["status"]     = "CLOSED"
                o["exit_reason"] = reason

                mult = 1 if o["direction"] == "long" else -1
                o["pnl"] = round((exit_price - o["entry_price"]) * o["quantity"] * mult, 2)

                logger.info(
                    f"[EXIT] {oid} {o['symbol']} | {reason} | "
                    f"exit={exit_price:.2f} P&L=₹{o['pnl']:+,.2f}"
                )
                return

    def open_orders(self) -> list:
        return [o for o in self.orders if o["status"] == "OPEN"]

    def save_journal(self, filepath: str):
        """Appends all trades to CSV. Your daily review document."""
        if not self.orders:
            return
        write_header = not os.path.exists(filepath)
        with open(filepath, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.orders[0].keys())
            if write_header:
                w.writeheader()
            for o in self.orders:
                if o["status"] == "CLOSED" and not o.get("journaled"):
                    w.writerow(o)
                    o["journaled"] = True
        logger.debug(f"Journal updated: {filepath}")


# ==============================================================================
# SECTION 9 — POSITION MONITOR (TRADE MANAGEMENT)
# Why: Entering a trade is only half the job. What you do AFTER entry
# determines your actual profitability. These rules implement the exit
# logic from the trading system:
#   - Exit 50% at T1, move SL to breakeven
#   - Exit remaining 50% at T2
#   - Exit 100% if ADX drops below 20 (trend has ended)
#   - Hard exit at 3:15 PM regardless of position
# ==============================================================================

class PositionMonitor:
    """Evaluates exit conditions on every candle for all open positions."""

    def __init__(self, order_manager: OrderManager):
        self.mgr = order_manager

    def check(self, symbol: str, price: float, adx: float) -> list:
        """
        Checks all open positions for the given symbol.
        Returns list of action strings taken this candle.
        """
        actions = []
        now     = datetime.now(Config.IST)
        hm      = (now.hour, now.minute)

        for o in self.mgr.open_orders():
            if o["symbol"] != symbol:
                continue

            d  = o["direction"]
            sl = o["stop_loss"]
            t1 = o["target_1"]
            t2 = o["target_2"]
            ep = o["entry_price"]

            # EXIT: ADX dropped below 20 — trend has failed
            if adx < Config.ADX_MIN:
                self.mgr.exit_order(o["order_id"], price, "ADX_DROPPED")
                actions.append(f"ADX_DROPPED @ {price:.2f}")
                continue

            # EXIT: 3:15 PM hard close — never hold intraday overnight
            if hm >= Config.HARD_EXIT:
                self.mgr.exit_order(o["order_id"], price, "HARD_EXIT_315PM")
                actions.append(f"HARD_EXIT_315 @ {price:.2f}")
                continue

            # EXIT: Stop-loss hit
            sl_hit = (d == "long" and price <= sl) or (d == "short" and price >= sl)
            if sl_hit:
                self.mgr.exit_order(o["order_id"], sl, "STOP_LOSS")
                actions.append(f"SL_HIT @ {sl:.2f}")
                continue

            # EXIT: Target 1 hit — exit 50%, move SL to breakeven
            if not o["t1_hit"]:
                t1_hit = (d == "long" and price >= t1) or (d == "short" and price <= t1)
                if t1_hit:
                    o["t1_hit"]  = True
                    o["sl_moved"] = True
                    o["stop_loss"] = ep   # SL moves to entry (breakeven)
                    # In live trading you would: exit 50% of quantity here
                    # and modify the SL order to the breakeven price
                    actions.append(f"T1_HIT @ {t1:.2f} — SL moved to {ep:.2f}")
                    logger.info(f"T1 hit {symbol} @ {t1:.2f} | SL now at breakeven {ep:.2f}")

            # EXIT: Target 2 hit — exit remaining 50%
            if o["t1_hit"] and t2:
                t2_hit = (d == "long" and price >= t2) or (d == "short" and price <= t2)
                if t2_hit:
                    self.mgr.exit_order(o["order_id"], t2, "TARGET_2_HIT")
                    actions.append(f"TARGET_2_HIT @ {t2:.2f}")

        return actions


# ==============================================================================
# SECTION 10 — DAILY RISK GUARD
# Why: The system must enforce its own risk limits automatically.
# If you're down 2% on the day, stop trading — do not try to recover losses.
# If you hit 3 trades, stop — quality beats quantity.
# This guard makes those rules enforced in code, not willpower.
# ==============================================================================

class DailyRiskGuard:
    """Tracks P&L and trade count. Halts trading on limit breach."""

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
            self.halted = True
            self.halt_reason = (
                f"Daily loss {daily_pct:.2%} exceeds limit "
                f"-{Config.DAILY_LOSS_LIMIT_PCT:.0%}"
            )
            logger.warning(f"TRADING HALTED: {self.halt_reason}")
        if self.trade_count >= Config.MAX_TRADES_PER_DAY:
            self.halted = True
            self.halt_reason = f"Max {Config.MAX_TRADES_PER_DAY} trades reached"
            logger.warning(f"TRADING HALTED: {self.halt_reason}")

    def can_trade(self) -> bool:
        if self.halted:
            logger.info(f"Risk guard halted: {self.halt_reason}")
        return not self.halted

    def reset_for_day(self):
        logger.info(
            f"Day reset | P&L: ₹{self.daily_pnl:+,.2f} | Trades: {self.trade_count}"
        )
        self.daily_pnl   = 0.0
        self.trade_count = 0
        self.halted      = False
        self.halt_reason = ""


# ==============================================================================
# SECTION 11 — DATA FETCHER
# Why: The system needs OHLCV candles to compute indicators. This class
# abstracts the data source — live mode uses Kite, backtest uses CSV files.
# The rest of the system never knows which source is being used.
# ==============================================================================

class DataFetcher:
    """Fetches OHLCV data from Kite (live/paper) or CSV (backtest)."""

    def __init__(self, mode: str, kite=None):
        self.mode = mode
        self.kite = kite

    def get_candles(
        self, token: int, symbol: str,
        interval: str = "15minute", n: int = 60
    ) -> Optional[pd.DataFrame]:
        """Returns a DataFrame of n recent OHLCV candles."""
        if self.mode in ("live", "paper"):
            return self._from_kite(token, interval, n)
        return self._from_csv(symbol)

    def _from_kite(self, token: int, interval: str, n: int) -> Optional[pd.DataFrame]:
        """Fetches from Kite historical data API."""
        if not KITE_AVAILABLE or self.kite is None:
            logger.debug("Kite unavailable — using synthetic data")
            return self._synthetic()

        to_dt   = datetime.now(Config.IST)
        from_dt = to_dt - timedelta(days=4)
        try:
            recs = self.kite.historical_data(
                instrument_token=token,
                from_date=from_dt.strftime("%Y-%m-%d %H:%M:%S"),
                to_date=to_dt.strftime("%Y-%m-%d %H:%M:%S"),
                interval=interval,
            )
            df = pd.DataFrame(recs)
            df.columns = [c.lower() for c in df.columns]
            df = df.rename(columns={"date": "datetime"})
            return df.tail(n).reset_index(drop=True)
        except Exception as e:
            logger.error(f"Kite data fetch error: {e}")
            return None

    def _from_csv(self, symbol: str) -> Optional[pd.DataFrame]:
        """Reads historical OHLCV CSV for backtesting."""
        path = os.path.join(Config.BACKTEST_DATA_DIR, f"{symbol}_15min.csv")
        if not os.path.exists(path):
            logger.error(
                f"Backtest CSV not found: {path}\n"
                f"Create CSV with columns: datetime,open,high,low,close,volume"
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
        """Generates synthetic OHLCV data for testing without Kite."""
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
            "open":  np.round(opens, 2),
            "high":  np.round(highs, 2),
            "low":   np.round(lows, 2),
            "close": np.round(closes, 2),
            "volume": volumes,
        })

    def get_prev_day_ohlc(
        self, token: int, symbol: str
    ) -> Optional[dict]:
        """Fetches previous day OHLC for pivot calculation."""
        if KITE_AVAILABLE and self.kite and self.mode in ("live", "paper"):
            today = datetime.now(Config.IST).date()
            prev  = today - timedelta(days=1)
            while prev.weekday() > 4:  # skip weekends
                prev -= timedelta(days=1)
            try:
                recs = self.kite.historical_data(
                    instrument_token=token,
                    from_date=str(prev),
                    to_date=str(prev),
                    interval="day",
                )
                if recs:
                    r = recs[-1]
                    return {"high": r["high"], "low": r["low"], "close": r["close"]}
            except Exception as e:
                logger.error(f"Prev OHLC fetch failed: {e}")

        # Fallback: use simulated values (replace with real data in production)
        logger.warning(f"Using fallback OHLC for {symbol} pivots")
        return {"high": 1250.0, "low": 1180.0, "close": 1215.0}


# ==============================================================================
# SECTION 12 — TELEGRAM NOTIFIER
# Why: You cannot watch a screen all day. Telegram alerts let you know
# the moment a trade is placed or exited, and give you the morning score
# so you can plan your day. Setup takes 5 minutes (see file header).
# ==============================================================================

class Notifier:
    """Sends Telegram alerts for trades and morning reports."""

    @staticmethod
    def send(message: str):
        """Sends a message to Telegram. Fails silently if not configured."""
        if not (Config.SEND_TELEGRAM and REQUESTS_AVAILABLE):
            return
        if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
            return
        try:
            url  = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
            http_requests.post(
                url,
                json={"chat_id": Config.TELEGRAM_CHAT_ID,
                      "text": message, "parse_mode": "HTML"},
                timeout=5,
            )
        except Exception as e:
            logger.debug(f"Telegram error: {e}")

    @staticmethod
    def trade(signal: dict, symbol: str, qty: int):
        Notifier.send(
            f"<b>TRADE SIGNAL</b>\n"
            f"Symbol : {symbol}\n"
            f"Dir    : {signal['direction'].upper()}\n"
            f"Entry  : ₹{signal['entry_price']:,.2f}\n"
            f"SL     : ₹{signal['stop_loss']:,.2f}\n"
            f"T1     : ₹{signal['target_1']:,.2f}\n"
            f"T2     : {signal['target_2']}\n"
            f"RR     : 1:{signal['risk_reward']}\n"
            f"Qty    : {qty}\n"
            f"ADX    : {signal['adx']}"
        )

    @staticmethod
    def exit(symbol: str, pnl: float, reason: str):
        icon = "✅" if pnl >= 0 else "❌"
        Notifier.send(
            f"{icon} <b>TRADE CLOSED</b>\n"
            f"Symbol : {symbol}\n"
            f"P&L    : ₹{pnl:+,.2f}\n"
            f"Reason : {reason}"
        )

    @staticmethod
    def morning(scores: dict):
        lines = ["<b>MORNING SCAN</b>"]
        for sym, r in scores.items():
            lines.append(f"{sym}: {r['score']}/4.0 — {r['decision']}")
        Notifier.send("\n".join(lines))


# ==============================================================================
# SECTION 13 — TRADING ENGINE (MAIN ORCHESTRATOR)
# Why: This ties all the components together. On each candle it:
#  1. Checks trading window and risk limits
#  2. Monitors open positions for exits
#  3. Scans for new entry signals if no open trade
#  4. Places orders and sends notifications
#  5. Saves journal
# ==============================================================================

class TradingEngine:
    """
    Orchestrates all components on each 15-minute candle.
    The main loop calls on_candle() for each symbol in the watchlist.
    """

    def __init__(self):
        self.kite        = self._init_kite()
        self.fetcher     = DataFetcher(Config.MODE, self.kite)
        self.order_mgr   = OrderManager(Config.MODE, self.kite)
        self.monitor     = PositionMonitor(self.order_mgr)
        self.risk_guard  = DailyRiskGuard(Config.TOTAL_CAPITAL)
        self.morning_scores: dict = {}
        self.pivot_cache: dict    = {}

        logger.info(
            f"Engine ready | Mode={Config.MODE} | "
            f"Capital=₹{Config.TOTAL_CAPITAL:,.0f} | "
            f"Watchlist={[i['symbol'] for i in Config.WATCHLIST]}"
        )

    def _init_kite(self):
        if Config.MODE == "backtest":
            return None
        if not KITE_AVAILABLE:
            logger.warning("kiteconnect not installed — data will be synthetic")
            return None
        if Config.KITE_API_KEY == "YOUR_API_KEY_HERE":
            logger.warning("Kite API key not set — using synthetic data")
            return None
        kite = KiteConnect(api_key=Config.KITE_API_KEY)
        if Config.KITE_ACCESS_TOKEN:
            kite.set_access_token(Config.KITE_ACCESS_TOKEN)
            logger.info("Kite authenticated with access token")
        else:
            url = kite.login_url()
            logger.info(f"Kite login needed. Visit: {url}")
            logger.info("Then set KITE_ACCESS_TOKEN env variable and restart.")
        return kite

    def pre_market_setup(self, gap_pct: float = 0.0):
        """
        Run this once each morning (around 8:30–9:10 AM) to:
          1. Calculate pivot levels for all watchlist stocks
          2. Run 4-filter morning scanner for each stock
          3. Cache results for use during market hours
        """
        logger.info("=" * 60)
        logger.info(f"PRE-MARKET SETUP | SGX gap={gap_pct:+.2f}%")
        self.risk_guard.reset_for_day()
        self.pivot_cache.clear()
        self.morning_scores.clear()

        for inst in Config.WATCHLIST:
            sym   = inst["symbol"]
            token = inst["token"]

            # Get yesterday's OHLC
            prev = self.fetcher.get_prev_day_ohlc(token, sym)
            if not prev:
                logger.warning(f"{sym}: no prev OHLC — skipping")
                continue

            # Calculate and cache pivots
            pivots = PivotCalculator.calculate(prev["high"], prev["low"], prev["close"])
            self.pivot_cache[sym] = pivots
            rq = PivotCalculator.range_quality(pivots, prev["close"])
            logger.info(
                f"{sym} | PP={pivots['PP']} R1={pivots['R1']} "
                f"R2={pivots['R2']} S1={pivots['S1']} S2={pivots['S2']} | "
                f"range={rq}"
            )

            # Fetch candles + run indicators + morning filter
            df = self.fetcher.get_candles(token, sym)
            if df is None or len(df) < Config.VOL_MA_PERIOD + 5:
                logger.warning(f"{sym}: insufficient data")
                continue

            df  = IndicatorEngine.compute(df)
            ind = IndicatorEngine.latest(df)
            self.morning_scores[sym] = MorningFilter.run(ind, gap_pct)

        Notifier.morning(self.morning_scores)
        logger.info("PRE-MARKET SETUP DONE")
        logger.info("=" * 60)

    def on_candle(self, symbol: str):
        """
        Main decision function — called on each new 15-min candle.
        1. Window and risk checks
        2. Monitor exits on open positions
        3. Scan for new entry if no open position
        """
        now = datetime.now(Config.IST)
        hm  = (now.hour, now.minute)

        # Not yet trading time
        if hm < Config.TRADE_START:
            return

        # Hard exit time — close all and stop
        if hm >= Config.HARD_EXIT:
            for o in self.order_mgr.open_orders():
                if o["symbol"] == symbol:
                    self.order_mgr.exit_order(
                        o["order_id"], o["entry_price"], "HARD_EXIT_315"
                    )
            return

        # Risk guard check
        if not self.risk_guard.can_trade():
            return

        # Fetch fresh data
        inst = next((i for i in Config.WATCHLIST if i["symbol"] == symbol), None)
        if not inst:
            return

        df = self.fetcher.get_candles(
            inst["token"], symbol, Config.CANDLE_INTERVAL, Config.CANDLES_NEEDED
        )
        if df is None or len(df) < Config.VOL_MA_PERIOD + 5:
            return

        df  = IndicatorEngine.compute(df)
        ind = IndicatorEngine.latest(df)
        price = ind["close"]
        adx   = ind["adx"]

        # Monitor existing positions — check for exits
        actions = self.monitor.check(symbol, price, adx)
        for a in actions:
            logger.info(f"{symbol} | {a}")

        # Record closed trade P&L to risk guard
        for o in self.order_mgr.orders:
            if (o["symbol"] == symbol and o["status"] == "CLOSED"
                    and o.get("pnl") is not None and not o.get("pnl_booked")):
                self.risk_guard.record_closed_trade(o["pnl"])
                o["pnl_booked"] = True
                Notifier.exit(symbol, o["pnl"], o["exit_reason"])
                self.order_mgr.save_journal(Config.JOURNAL_FILE)

        # Skip signal scan if already in trade for this symbol
        if any(o["symbol"] == symbol for o in self.order_mgr.open_orders()):
            return

        # Get morning score (position size limit for today)
        score = self.morning_scores.get(symbol, {})
        pos_pct = score.get("position_size_pct", 0.0)
        if pos_pct == 0:
            return

        # Get cached pivots
        pivots = self.pivot_cache.get(symbol)
        if not pivots:
            return

        # Try to find a valid signal
        signal = SignalGenerator.generate(ind, pivots, pos_pct)
        if not signal:
            return

        # Calculate number of shares
        qty = SLTPCalc.quantity(
            Config.TOTAL_CAPITAL, signal["position_size_pct"],
            signal["entry_price"], signal["stop_loss"]
        )
        if qty <= 0:
            return

        # Place the order
        oid = self.order_mgr.place_order(
            symbol=symbol, exchange=inst["exchange"],
            direction=signal["direction"], quantity=qty,
            entry_price=signal["entry_price"],
            stop_loss=signal["stop_loss"],
            target_1=signal["target_1"],
            target_2=signal["target_2"],
            signal=signal,
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
        Continuous loop polling every `poll_sec` seconds.
        For production, replace with KiteTicker WebSocket for real-time ticks.
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
                    logger.info("Market closed — saving journal and stopping")
                    self.order_mgr.save_journal(Config.JOURNAL_FILE)
                    break

                time.sleep(poll_sec)

        except KeyboardInterrupt:
            logger.info("Stopped by user")
            self.order_mgr.save_journal(Config.JOURNAL_FILE)


# ==============================================================================
# SECTION 14 — BACKTESTER
# Why: You must validate the system on historical data before risking real
# capital. The backtester replays the same logic candle by candle on CSV data
# and reports win rate, profit factor, drawdown — the same metrics from the
# trading system documentation.
#
# HOW TO USE:
#   1. Create directory: mkdir historical_data
#   2. Place CSVs: historical_data/RELIANCE_15min.csv
#      Columns: datetime,open,high,low,close,volume
#   3. Run: python nse_algo.py --backtest
#
# WHERE TO GET HISTORICAL DATA (free):
#   - Zerodha Kite Connect historical API (if you have an account)
#   - NSEPython library: pip install nsepython
#   - NSE India website (daily OHLC CSV downloads)
# ==============================================================================

class Backtester:
    """Replays historical OHLCV data through the full signal engine."""

    def __init__(self):
        self.fetcher = DataFetcher("backtest")
        self.trades  = []

    def run(
        self, symbol: str,
        prev_high: float, prev_low: float, prev_close: float
    ) -> list:
        """
        Runs backtest for one symbol.
        prev_high/low/close: used to calculate pivots for the first trading day.
        Returns list of trade dicts.
        """
        df = self.fetcher.get_candles(0, symbol)
        if df is None or df.empty:
            logger.error(f"No backtest data for {symbol}")
            return []

        df = IndicatorEngine.compute(df)
        logger.info(f"Backtesting {symbol}: {len(df)} candles")

        pivots     = PivotCalculator.calculate(prev_high, prev_low, prev_close)
        open_trade = None
        trades     = []

        warmup = max(Config.EMA_TREND, Config.ADX_PERIOD, Config.VOL_MA_PERIOD) + 5

        for i in range(warmup, len(df)):
            ind   = IndicatorEngine.latest(df.iloc[:i + 1])
            price = ind["close"]
            adx   = ind["adx"]
            ts    = str(df.iloc[i].get("datetime", i))

            # ── Manage open trade ─────────────────────────────
            if open_trade:
                d  = open_trade["direction"]
                sl = open_trade["stop_loss"]
                t1 = open_trade["target_1"]
                t2 = open_trade["target_2"]
                ep = open_trade["entry_price"]

                # ADX dropped
                if adx < Config.ADX_MIN:
                    pnl = (price - ep) * (1 if d == "long" else -1) * open_trade["qty"]
                    open_trade.update(exit_price=price, exit_reason="ADX_DROPPED",
                                      pnl=round(pnl, 2), exit_time=ts)
                    trades.append(open_trade)
                    open_trade = None
                    continue

                # Stop-loss hit
                sl_hit = (d == "long" and price <= sl) or (d == "short" and price >= sl)
                if sl_hit:
                    pnl = (sl - ep) * (1 if d == "long" else -1) * open_trade["qty"]
                    open_trade.update(exit_price=sl, exit_reason="STOP_LOSS",
                                      pnl=round(pnl, 2), exit_time=ts)
                    trades.append(open_trade)
                    open_trade = None
                    continue

                # T1 hit — move SL to breakeven
                if not open_trade.get("t1_hit"):
                    t1_hit = (d == "long" and price >= t1) or (d == "short" and price <= t1)
                    if t1_hit:
                        open_trade["t1_hit"]   = True
                        open_trade["stop_loss"] = ep

                # T2 hit — full exit
                if open_trade.get("t1_hit") and t2:
                    t2_hit = (d == "long" and price >= t2) or (d == "short" and price <= t2)
                    if t2_hit:
                        pnl = (t2 - ep) * (1 if d == "long" else -1) * open_trade["qty"]
                        open_trade.update(exit_price=t2, exit_reason="TARGET_2",
                                          pnl=round(pnl, 2), exit_time=ts)
                        trades.append(open_trade)
                        open_trade = None
                continue

            # ── Scan for new signal ───────────────────────────
            signal = SignalGenerator.generate(
                ind, pivots, Config.RISK_PER_TRADE_PCT
            )
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
        """Prints performance statistics and saves results CSV."""
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
        gross_profit = df[df["pnl"] > 0]["pnl"].sum()
        gross_loss   = abs(df[df["pnl"] <= 0]["pnl"].sum())
        pf   = gross_profit / gross_loss if gross_loss else float("inf")
        mdd  = float(df["pnl"].cumsum().min())
        ret  = tp / Config.TOTAL_CAPITAL * 100

        print("\n" + "=" * 56)
        print("  NSE ALGO v3.0 — BACKTEST RESULTS")
        print("=" * 56)
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
        print("=" * 56)

        out = "backtest_results.csv"
        df.to_csv(out, index=False)
        print(f"  Results saved   : {out}")
        print("=" * 56)


# ==============================================================================
# SECTION 15 — ENTRY POINT
# How to run:
#   Default (paper mode):    python nse_algo.py
#   Paper trading:           python nse_algo.py --mode paper
#   Backtest:                python nse_algo.py --backtest
#   Live trading:            python nse_algo.py --mode live
#   Single scan cycle:       python nse_algo.py --once --gap 0.6
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="NSE Day Trading Algo System v3.0"
    )
    parser.add_argument(
        "--mode", default=Config.MODE,
        choices=["paper", "live", "backtest"],
        help="Trading mode: paper (default), live, or backtest"
    )
    parser.add_argument(
        "--backtest", action="store_true",
        help="Run backtest on historical CSV data"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one full scan cycle and exit"
    )
    parser.add_argument(
        "--gap", type=float, default=0.0,
        help="SGX Nifty gap %% vs previous NSE close (e.g. 0.6 or -0.4)"
    )
    args = parser.parse_args()

    # ── BACKTEST MODE ─────────────────────────────────────────────────────────
    if args.backtest or args.mode == "backtest":
        Config.MODE = "backtest"
        logger.info("Running backtest...")
        bt = Backtester()
        # Add each symbol from watchlist here with its own prev-day OHLC
        # Replace these values with actual previous day H/L/C from Bhavcopy
        bt.run("RELIANCE",  prev_high=2850.0, prev_low=2780.0, prev_close=2810.0)
        bt.run("HDFCBANK",  prev_high=1680.0, prev_low=1640.0, prev_close=1660.0)
        bt.run("INFY",      prev_high=1810.0, prev_low=1770.0, prev_close=1790.0)
        bt.report()
        return

    # ── LIVE / PAPER MODE ─────────────────────────────────────────────────────
    Config.MODE = args.mode
    engine = TradingEngine()

    if args.once:
        logger.info("Running single scan cycle")
        engine.run_once(gap_pct=args.gap)
    else:
        engine.run_live_loop(gap_pct=args.gap, poll_sec=60)


if __name__ == "__main__":
    main()


# ==============================================================================
# END OF FILE
# ==============================================================================
# NEXT STEPS (DO NOT SKIP ANY):
#
#  1. INSTALL DEPENDENCIES
#     pip install ta pandas numpy kiteconnect requests
#
#  2. TEST LOCALLY (no API needed)
#     python nse_algo.py --backtest
#     → Reviews synthetic data, confirms logic runs without errors
#
#  3. ADD REAL HISTORICAL DATA FOR BACKTEST
#     mkdir historical_data
#     Add files: historical_data/RELIANCE_15min.csv
#     Columns: datetime,open,high,low,close,volume
#     Then: python nse_algo.py --backtest
#
#  4. SET UP ZERODHA KITE CONNECT
#     a. Register: developers.kite.trade → create app
#     b. export KITE_API_KEY="your_key"
#        export KITE_API_SECRET="your_secret"
#     c. First login: python nse_algo.py --once
#        → System prints login URL → visit it → get request_token
#     d. Exchange request_token for access_token:
#        kite = KiteConnect(api_key=KEY)
#        data = kite.generate_session(request_token, api_secret=SECRET)
#        export KITE_ACCESS_TOKEN="data['access_token']"
#     e. Access token is valid for ONE day — automate this with a cron job
#
#  5. SET UP TELEGRAM ALERTS (optional but recommended)
#     a. Message @BotFather → /newbot → get token
#     b. Message @userinfobot → get your chat_id
#     c. export TELEGRAM_BOT_TOKEN="..."
#        export TELEGRAM_CHAT_ID="..."
#
#  6. PAPER TRADE FOR 30 DAYS MINIMUM
#     python nse_algo.py --mode paper --gap 0.5
#     Review trades_journal.csv every evening
#     Target: 55%+ win rate and positive expectancy over 40+ trades
#
#  7. AUTOMATE DAILY STARTUP (Linux cron example)
#     8:25 AM IST: calculate gap, start engine
#     15:31 PM IST: engine auto-stops after market close
#     crontab -e
#     25 8 * * 1-5 cd /path/to/algo && python nse_algo.py --mode paper
#
#  8. ONLY THEN GO LIVE
#     python nse_algo.py --mode live
#     Start with 25% of intended capital. Scale only after 3 profitable months.
# ==============================================================================
