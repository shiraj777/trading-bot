# services/policy.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict


@dataclass
class Decision:
    """ייצוג החלטה של הפוליסי."""
    side: str   # "buy" | "sell" | "hold"
    score: float
    reason: str


# -------- Helpers -------- #

def _env_float(name: str, default: float) -> float:
    """
    קריאת float מ-ENV עם ברירת מחדל בטוחה.
    * תומך במחרוזות ריקות/לא קיימות/לא מספריות.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _clip01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


# -------- Defaults (can be overridden via ENV) -------- #
# אלו ערכי ברירת מחדל יציבים; ניתן לדרוס אותם ב-ENV
#   RSI_BUY, RSI_SELL, MACD_HIST_BUY, MACD_HIST_SELL, MIN_SCORE, MACD_SCALE

RSI_BUY_DEFAULT: float        = 55.0
RSI_SELL_DEFAULT: float       = 46.0
MACD_HIST_BUY_DEFAULT: float  = 0.00
MACD_HIST_SELL_DEFAULT: float = 0.00
MIN_SCORE_DEFAULT: float      = 0.10
MACD_SCALE_DEFAULT: float     = 0.08  # קובע רגישות לתרומת MACD_hist לציון


def _active_thresholds() -> Dict[str, float]:
    """
    החזרת הספים הפעילים בפועל (אחרי override מ-ENV אם יש).
    """
    return {
        "rsi_buy":        _env_float("RSI_BUY", RSI_BUY_DEFAULT),
        "rsi_sell":       _env_float("RSI_SELL", RSI_SELL_DEFAULT),
        "macd_hist_buy":  _env_float("MACD_HIST_BUY", MACD_HIST_BUY_DEFAULT),
        "macd_hist_sell": _env_float("MACD_HIST_SELL", MACD_HIST_SELL_DEFAULT),
        "min_score":      _env_float("MIN_SCORE", MIN_SCORE_DEFAULT),
        "macd_scale":     _env_float("MACD_SCALE", MACD_SCALE_DEFAULT),
    }


def describe_thresholds() -> Dict[str, float]:
    """להצגה בלוגים / דיאגנוסטיקה."""
    return _active_thresholds()


# -------- Policy logic -------- #

def decide(df) -> Decision:
    """
    df: DataFrame עם העמודות:
        close, rsi, macd, macd_signal, macd_hist, atr (אחרי add_indicators)
    לוגיקה 'רכה': שילוב RSI + MACD_hist מנורמל → ציון → החלטה.
    """
    if df is None or len(df.index) == 0:
        return Decision("hold", 0.0, "no data")

    last = df.iloc[-1]
    try:
        rsi: float = float(last.get("rsi", 0.0))
    except Exception:
        rsi = 0.0

    try:
        macd_hist: float = float(last.get("macd_hist", 0.0))
    except Exception:
        macd_hist = 0.0

    th = _active_thresholds()
    rsi_buy        = th["rsi_buy"]
    rsi_sell       = th["rsi_sell"]
    macd_hist_buy  = th["macd_hist_buy"]
    macd_hist_sell = th["macd_hist_sell"]
    macd_scale     = max(1e-9, th["macd_scale"])  # למנוע חלוקה באפס
    min_score      = th["min_score"]

    # ---- BUY components ----
    # התאמה ל-RSI מעל סף הקנייה, מנורמל לטווח עד ~70
    buy_rsi_part = (rsi - rsi_buy) / max(1.0, (70.0 - rsi_buy))
    buy_rsi_part = _clip01(buy_rsi_part)

    # תרומת MACD_hist מעל סף הקנייה, מנורמל ע"י MACD_SCALE
    if macd_hist > macd_hist_buy:
        buy_macd_part = (macd_hist - macd_hist_buy) / macd_scale
    else:
        buy_macd_part = 0.0
    buy_macd_part = _clip01(buy_macd_part)

    buy_score = _clip01(0.7 * buy_rsi_part + 0.3 * buy_macd_part)

    # ---- SELL components ----
    # התאמה ל-RSI מתחת לסף המכירה, מנורמל לטווח עד ~30
    sell_rsi_part = (rsi_sell - rsi) / max(1.0, (rsi_sell - 30.0))
    sell_rsi_part = _clip01(sell_rsi_part)

    # תרומת MACD_hist מתחת לסף המכירה (שלילי יותר), מנורמל
    if macd_hist < macd_hist_sell:
        sell_macd_part = (macd_hist_sell - macd_hist) / macd_scale
    else:
        sell_macd_part = 0.0
    sell_macd_part = _clip01(sell_macd_part)

    sell_score = _clip01(0.7 * sell_rsi_part + 0.3 * sell_macd_part)

    # ---- Final decision ----
    if buy_score >= sell_score and buy_score >= min_score:
        return Decision(
            "buy",
            float(round(buy_score, 3)),
            f"RSI={rsi:.1f} >= {rsi_buy}, MACD_hist={macd_hist:.3f} >= {macd_hist_buy} (macd_scale={macd_scale})"
        )

    if sell_score > buy_score and sell_score >= min_score:
        return Decision(
            "sell",
            float(round(sell_score, 3)),
            f"RSI={rsi:.1f} <= {rsi_sell}, MACD_hist={macd_hist:.3f} <= {macd_hist_sell} (macd_scale={macd_scale})"
        )

    return Decision("hold", 0.0, "neutral/low confidence")