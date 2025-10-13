# services/data.py
import pandas as pd
import yfinance as yf

def fetch_bars(ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    df = yf.download(
        tickers=ticker,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        group_by="ticker",  # מוודא קבלת רמות תקינות
        threads=True,
    )

    if df is None or df.empty:
        raise ValueError(f"No data for ticker: {ticker}")

    # אם העמודות הן MultiIndex עם רמה עליונה בשם הטיקר
    if isinstance(df.columns, pd.MultiIndex):
        if ticker in df.columns.get_level_values(0):
            df = df[ticker]  # נוריד את רמת הטיקר ונשאיר רק את ['Open', 'High', ...]
        df.columns = [c.lower() for c in df.columns]

    else:
        # flatten and normalize
        df.columns = [str(c).strip().lower() for c in df.columns]

    # במידה ועדיין אין close, ננסה מקורות חלופיים
    if "close" not in df.columns:
        if "adj close" in df.columns:
            df["close"] = df["adj close"]
        elif "last" in df.columns:
            df["close"] = df["last"]

    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    if "close" not in keep:
        raise ValueError(f"No 'close' column found. Columns={list(df.columns)}")

    df = df[keep].copy()
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df.index = pd.to_datetime(df.index)
    df = df.dropna()
    return df