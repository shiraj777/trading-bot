# run_worker.py
from __future__ import annotations

import os
import time
import logging
import inspect
from datetime import datetime
from typing import Dict, Any

from services.data import fetch_bars
from services.indicators import add_indicators
from services.policy import decide
from services.risk import position_size

# ---------- logging ----------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(levelname)s:%(name)s:%(message)s"
)
log = logging.getLogger("worker")

# ---------- small helpers ----------
def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None or v == "" else v

def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        log.warning("Invalid float for %s=%r -> using default=%s", name, v, default)
        return default

def _env_optional_float(name: str) -> float | None:
    """Return float if set & valid, otherwise None (used for policy overrides)."""
    v = os.getenv(name)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        log.warning("Ignoring invalid float for %s=%r", name, v)
        return None

# ---------- config from env with sane defaults ----------
PAPER         = _env("PAPER", "true").lower() == "true"
TICKER        = _env("TICKER", "AAPL")
PERIOD        = _env("PERIOD", "1mo")          # 6mo / 3mo / 1mo / ytd ...
INTERVAL      = _env("INTERVAL", "30m")        # 1d / 1h / 30m / 15m ...
POLL_INTERVAL = _env_float("POLL_INTERVAL", 30.0)
EQUITY        = _env_float("EQUITY", 10_000.0)
RISK_PCT      = _env_float("RISK_PCT", 0.01)   # 1% ברירת מחדל

# Optional policy overrides via env (all optional)
POLICY_OVERRIDES: Dict[str, Any] = {}
_rsi_buy  = _env_optional_float("RSI_BUY")
_rsi_sell = _env_optional_float("RSI_SELL")
_macd_hb  = _env_optional_float("MACD_HIST_BUY")
_macd_hs  = _env_optional_float("MACD_HIST_SELL")
_min_sc   = _env_optional_float("MIN_SCORE")

if _rsi_buy  is not None: POLICY_OVERRIDES["rsi_buy"] = _rsi_buy
if _rsi_sell is not None: POLICY_OVERRIDES["rsi_sell"] = _rsi_sell
if _macd_hb  is not None: POLICY_OVERRIDES["macd_hist_buy"]  = _macd_hb
if _macd_hs  is not None: POLICY_OVERRIDES["macd_hist_sell"] = _macd_hs
if _min_sc   is not None: POLICY_OVERRIDES["min_score"] = _min_sc

def describe_row(row) -> str:
    """Build a short string with last indicators (if exist)."""
    parts = []
    def add(name, fmt="{:.4f}"):
        try:
            if name in row:
                parts.append(f"{name}=" + (fmt.format(float(row[name]))))
        except Exception:
            parts.append(f"{name}=?")
    add("close")
    add("rsi", "{:.1f}")
    add("macd", "{:.3f}")
    add("macd_signal", "{:.3f}")
    add("macd_hist", "{:.3f}")
    add("atr", "{:.3f}")
    return ", ".join(parts)

def _decide_with_overrides(df):
    """
    Call services.policy.decide(df, **overrides) if it supports kwargs with these names.
    Otherwise, call decide(df) normally.
    """
    if not POLICY_OVERRIDES:
        return decide(df)

    # Check the callable signature and pass only supported params.
    sig = inspect.signature(decide)
    supported = {k: v for k, v in POLICY_OVERRIDES.items() if k in sig.parameters}
    if supported:
        return decide(df, **supported)  # type: ignore[arg-type]
    return decide(df)

def once():
    """
    Single polling iteration:
    1) fetch -> add indicators
    2) decision
    3) size calc (qty/stop/take)
    4) paper 'execution' (log only)
    """
    # --- Fetch bars + indicators
    raw = fetch_bars(TICKER, period=PERIOD, interval=INTERVAL)
    df  = add_indicators(raw)

    if df is None or df.empty or len(df) < 20:
        raise ValueError("Not enough data for decision after indicators.")

    last = df.iloc[-1]

    # --- Decision (with optional overrides)
    dec = _decide_with_overrides(df)

    # --- Sizing
    price = float(last.get("close"))
    atr   = float(last.get("atr", 0.0))
    qty, stop, take = position_size(equity=EQUITY, atr=atr, price=price, risk_pct=RISK_PCT)

    # --- Log summary
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    policy_str = ""
    if POLICY_OVERRIDES:
        policy_str = " overrides=" + ",".join(f"{k}={v}" for k, v in POLICY_OVERRIDES.items())

    log.info(
        "[%s] %s | %s | decision=%s score=%.3f reason=\"%s\" | qty=%s stop=%.4f take=%.4f%s",
        stamp, TICKER, describe_row(last), dec.side, float(dec.score), dec.reason,
        qty, stop, take, policy_str
    )

    # --- Paper trade only (log)
    if dec.side in ("buy", "sell") and qty > 0:
        if PAPER:
            log.info("PAPER TRADE: %s %s @ ~%.4f (qty=%s, stop=%.4f, take=%.4f)",
                     dec.side.upper(), TICKER, price, qty, stop, take)
        else:
            # כאן בעתיד תתחבר/י לברוקר אמיתי
            log.warning("LIVE TRADE (not implemented): %s %s qty=%s", dec.side, TICKER, qty)

def main():
    log.info(
        "Starting worker polling %s every %s s (PAPER=%s, PERIOD=%s, INTERVAL=%s, EQUITY=%s, RISK_PCT=%s)",
        TICKER, POLL_INTERVAL, PAPER, PERIOD, INTERVAL, EQUITY, RISK_PCT
    )
    if POLICY_OVERRIDES:
        log.info("Active policy overrides: %s", POLICY_OVERRIDES)

    while True:
        try:
            once()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            # נשמור את ה־traceback בלוגים אם תרצי: log.exception(...)
            log.error("worker iteration failed: %s", e)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user.")