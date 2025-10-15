# services/policy.py
from __future__ import annotations

import os
from dataclasses import dataclass
import pandas as pd


@dataclass
class Decision:
    side: str   # "buy" | "sell" | "hold"
    score: float
    reason: str


def _env_float(name: str, default: float) -> float:
    """Read float from env if exists, else default."""
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


# ------------------------------
#  ספים רגישים כברירת מחדל
#  ניתן לשנות דרך משתני סביבה:
#  RSI_BUY, RSI_SELL, MACD_HIST_BUY, MACD_HIST_SELL, MIN_SCORE
# ------------------------------
RSI_BUY = _env_float("RSI_BUY", 45.0)           # היה 35
RSI_SELL = _env_float("RSI_SELL", 55.0)         # היה 65
MACD_HIST_BUY = _env_float("MACD_HIST_BUY", 0.00)   # היה 0.20
MACD_HIST_SELL = _env_float("MACD_HIST_SELL", 0.00) # היה -0.20
MIN_SCORE = _env_float("MIN_SCORE", 0.05)       # מינימום כדי לא להחזיר hold


def decide(df: pd.DataFrame) -> Decision:
    """
    קבלת החלטה על בסיס RSI + MACD (גרסה רגישת־יתר לצורכי בדיקות).

    df חייב להכיל עמודות:
    - 'close', 'rsi', 'macd', 'macd_signal', 'macd_hist'
    """
    if df is None or df.empty:
        return Decision("hold", 0.0, "no data")

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last

    rsi = float(last.get("rsi", 50.0))
    macd_hist = float(last.get("macd_hist", 0.0))
    macd_hist_prev = float(prev.get("macd_hist", macd_hist))

    # --- BUY conditions (רגישים יותר) ---
    buy_rsi = rsi <= RSI_BUY
    buy_macd = macd_hist >= MACD_HIST_BUY and macd_hist_prev <= macd_hist  # היסט עולה/לא יורד
    if buy_rsi and buy_macd:
        # דירוג רגיש: ככל שה-RSI נמוך יותר וה-MACD היסט גבוה/עולה – ציון גבוה יותר
        rsi_term = max(0.0, (RSI_BUY - rsi) / 20.0)        # 0..~1
        macd_term = max(0.0, macd_hist / 0.3)             # נרמול קל
        momentum = max(0.0, (macd_hist - macd_hist_prev) / 0.3)
        score = min(1.0, 0.4 * rsi_term + 0.4 * macd_term + 0.2 * momentum)
        if score >= MIN_SCORE:
            return Decision("buy", round(score, 3),
                            f"RSI={rsi:.1f}<=RSI_BUY({RSI_BUY}), MACD_hist={macd_hist:.3f}↑")

    # --- SELL conditions (רגישים יותר) ---
    sell_rsi = rsi >= RSI_SELL
    sell_macd = macd_hist <= MACD_HIST_SELL and macd_hist_prev >= macd_hist  # היסט יורד/לא עולה
    if sell_rsi and sell_macd:
        # דירוג רגיש: ככל שה-RSI גבוה יותר וה-MACD היסט שלילי/יורד – ציון גבוה יותר
        rsi_term = max(0.0, (rsi - RSI_SELL) / 20.0)
        macd_term = max(0.0, (-macd_hist) / 0.3)
        momentum = max(0.0, (macd_hist_prev - macd_hist) / 0.3)
        score = min(1.0, 0.4 * rsi_term + 0.4 * macd_term + 0.2 * momentum)
        if score >= MIN_SCORE:
            return Decision("sell", round(score, 3),
                            f"RSI={rsi:.1f}>=RSI_SELL({RSI_SELL}), MACD_hist={macd_hist:.3f}↓")

    # אם לא עבר את הסף – נשארים hold
    return Decision("hold", 0.0, f"neutral: RSI={rsi:.1f}, MACD_hist={macd_hist:.3f}")
