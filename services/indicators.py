# services/indicators.py

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import AverageTrueRange

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    # שמות עמודות: open, high, low, close, volume
    c = df["close"]
    h = df["high"]
    l = df["low"]

    rsi = RSIIndicator(close=c, window=14).rsi()
    macd_i = MACD(close=c, window_fast=12, window_slow=26, window_sign=9)
    macd = macd_i.macd()
    macd_sig = macd_i.macd_signal()
    macd_hist = macd_i.macd_diff()
    atr = AverageTrueRange(high=h, low=l, close=c, window=14).average_true_range()

    out = df.copy()
    out["rsi"] = rsi
    out["macd"] = macd
    out["macd_sig"] = macd_sig
    out["macd_hist"] = macd_hist
    out["atr"] = atr
    out.dropna(inplace=True)
    return out