from fastapi import FastAPI, HTTPException, Query
from services.data import fetch_bars
from services.indicators import add_indicators
from services.policy import decide
from services.risk import position_size
from services.utils import SignalResponse

app = FastAPI(title="Trading Bot API", version="0.1.0")

@app.get("/")
def root():
    return {"status": "ok", "service": "Trading Bot API"}

@app.get("/signals/{ticker}", response_model=SignalResponse)
def get_signal(
    ticker: str,
    period: str = Query("6mo"),
    interval: str = Query("1d"),
    equity: float = Query(10000.0),
    risk_pct: float = Query(0.01),
):
    try:
        raw = fetch_bars(ticker, period=period, interval=interval)
        df = add_indicators(raw)
        if df.empty or len(df) < 20:
            raise HTTPException(status_code=400, detail="Not enough data after indicators.")
        decision = decide(df)

        price = float(df["close"].iloc[-1])
        atr = float(df["atr"].iloc[-1])
        qty, stop, take = position_size(equity=equity, atr=atr, price=price, risk_pct=risk_pct)

        return SignalResponse(
            ticker=ticker.upper(),
            side=decision.side,
            score=decision.score,
            reason=decision.reason,
            price=round(price, 4),
            qty=qty,
            stop=round(stop, 4),
            take=round(take, 4),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

    # DEBUG ONLY: החזר עמודות גולמיות
@app.get("/debug/cols/{ticker}")
def debug_columns(ticker: str, period: str = "6mo", interval: str = "1d"):
    import yfinance as yf
    import pandas as pd
    raw = yf.download(ticker, period=period, interval=interval, auto_adjust=False, progress=False, group_by="column")
    if raw is None or raw.empty:
        return {"cols": [], "note": "empty"}
    cols = list(raw.columns)
    # נשטח עמודות אם זה MultiIndex
    if isinstance(raw.columns, pd.MultiIndex):
        cols = list(raw.columns.get_level_values(-1))
    return {"cols": [str(c) for c in cols]}