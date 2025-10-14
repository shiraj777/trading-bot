from fastapi import FastAPI, HTTPException, Query
from services.data import fetch_bars
from services.indicators import add_indicators
from services.policy import decide
from services.risk import position_size
from services.utils import SignalResponse

# יצירת האפליקציה הראשית
app = FastAPI(title="Trading Bot API", version="0.1.0")

# ✅ נתיב בריאות עבור Render
@app.get("/healthz")
def health_check():
    """
    Endpoint for Render health checks.
    Returns 200 OK if the service is alive.
    """
    return {"status": "ok"}

# נתיב בדיקה ראשי
@app.get("/")
def root():
    return {"status": "ok", "service": "Trading Bot API"}

# יצירת אות מסחר
@app.get("/signals/{ticker}", response_model=SignalResponse)
def get_signal(
    ticker: str,
    period: str = Query("6mo", description="ytd, 1mo, 3mo, 6mo, 1y, etc."),
    interval: str = Query("1d", description="1d, 1h, 30m, 15m (yfinance-supported)"),
    equity: float = Query(10000.0, description="Capital in USD"),
    risk_pct: float = Query(0.01, description="Risk per trade (0.01 = 1%)"),
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

# DEBUG ONLY (optional): inspect raw columns from yfinance
@app.get("/debug/cols/{ticker}")
def debug_columns(ticker: str, period: str = "6mo", interval: str = "1d"):
    import yfinance as yf
    t = yf.Ticker(ticker)
    raw = t.history(period=period, interval=interval, auto_adjust=True)
    return {"cols": [str(c) for c in raw.columns]} if raw is not None else {"cols": []}