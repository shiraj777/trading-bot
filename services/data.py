# services/data.py
from __future__ import annotations
import os
import logging
from typing import Optional
import pandas as pd
import yfinance as yf

log = logging.getLogger("data")

# כללי Yahoo לקיצור אוטומטי
_LIMITS = {
    "1m": 30, "2m": 30,
    "5m": 60, "15m": 60,
    # ל-30m ומעלה בד"כ אין בעיה עד הרבה חודשים/שנים, נשאיר None
}

def _normalize_period_for_interval(period: str, interval: str) -> str:
    """אם period חורג מהמגבלה המוכרת ל-interval, נכווץ ל-60d/30d בהתאם."""
    try:
        max_days = _LIMITS.get(interval)
        if not max_days:
            return period
        # period בפורמט 60d / 3mo / 1y...
        if period.endswith("d"):
            days = int(period[:-1])
        elif period.endswith("mo"):
            days = int(period[:-2]) * 30
        elif period.endswith("y"):
            days = int(period[:-1]) * 365
        else:
            return period

        if days > max_days:
            new_p = f"{max_days}d"
            log.warning("yf: period %s too long for interval %s -> using %s",
                        period, interval, new_p)
            return new_p
        return period
    except Exception:
        return period

def fetch_bars(ticker: str, period: Optional[str] = None, interval: Optional[str] = None) -> pd.DataFrame:
    """מוריד היסטוריית מחירים עם yfinance, עם 'קיצור' אוטומטי במקרה של חריגה."""
    tkr = ticker or os.getenv("TICKER", "AAPL")
    per = (period or os.getenv("PERIOD", "1mo")).strip()
    itv = (interval or os.getenv("INTERVAL", "30m")).strip()

    per_adj = _normalize_period_for_interval(per, itv)

    def _once(p: str) -> pd.DataFrame:
        df = yf.Ticker(tkr).history(period=p, interval=itv, auto_adjust=False)
        if df is None or df.empty:
            raise RuntimeError(f"empty history for {tkr} ({p}, {itv})")
        df = df.rename(columns=str.lower)
        return df

    # נסה עם period מקוצר (אם צריך)
    try:
        return _once(per_adj)
    except Exception as e:
        log.error("yfinance failed (%s, %s): %s", per_adj, itv, e)

    # נסה שוב עם רזולוציה גסה יותר (למשל 30m→1h) אם נכשל
    fallback_map = {"15m": "30m", "30m": "1h"}
    itv_fb = fallback_map.get(itv)
    if itv_fb:
        try:
            log.warning("retrying with coarser interval %s (was %s)", itv_fb, itv)
            df = yf.Ticker(tkr).history(period=per, interval=itv_fb, auto_adjust=False)
            if df is None or df.empty:
                raise RuntimeError("empty fallback")
            return df.rename(columns=str.lower)
        except Exception as e2:
            log.error("fallback failed (%s, %s): %s", per, itv_fb, e2)

    # אם הכל נכשל – זרקי שגיאה למעלה
    raise