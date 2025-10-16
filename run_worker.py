# run_worker.py
from __future__ import annotations

import os
import time
import logging
from datetime import datetime
from typing import Tuple

from services.data import fetch_bars
from services.indicators import add_indicators
from services.policy import decide
from services.risk import position_size

# חדש: התראות טלגרם
from services.alerts import get_alerter

# ---------- logging ----------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(levelname)s:%(name)s:%(message)s"
)
log = logging.getLogger("worker")

# ---------- config from env with defaults ----------
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
        return default

PAPER         = _env("PAPER", "true").lower() == "true"
TICKER        = _env("TICKER", "AAPL")
PERIOD        = _env("PERIOD", "1mo")          # דוגמה: 6mo / 3mo / 1mo / ytd
INTERVAL      = _env("INTERVAL", "30m")        # דוגמה: 1d / 1h / 30m / 15m
POLL_INTERVAL = _env_float("POLL_INTERVAL", 30.0)
EQUITY        = _env_float("EQUITY", 10_000.0)
RISK_PCT      = _env_float("RISK_PCT", 0.01)   # 1% ברירת מחדל

# אובייקט ההתראות (סינגלטון)
alerter = get_alerter()


def describe_row(row) -> str:
    """Build a short string with last indicators (if exist)."""
    parts = []
    def add(name, fmt="{:.4f}"):
        if name in row:
            try:
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


def _size_from_row(last) -> Tuple[float, float, float]:
    """מחשב גודל פוזיציה, סטופ וטייק מהשורה האחרונה של הדאטה"""
    price = float(last.get("close"))
    atr   = float(last.get("atr", 0.0))
    qty, stop, take = position_size(
        equity=EQUITY, atr=atr, price=price, risk_pct=RISK_PCT
    )
    return qty, stop, take


def _maybe_notify_trade(dec, last, qty, stop, take) -> None:
    """
    שולח התראת עסקה במקרה של BUY/SELL.
    (כרגע ההרצה היא PAPER בלבד, לכן זה רק התראה + לוג)
    """
    if dec.side not in ("buy", "sell") or qty <= 0:
        return

    price = float(last.get("close"))
    alerter.notify_trade(
        side=dec.side,
        ticker=TICKER,
        price=price,
        qty=qty,
        score=float(dec.score),
        reason=dec.reason,
        stop=stop,
        take=take,
    )

    if PAPER:
        log.info(
            "PAPER TRADE: %s %s @ ~%.4f (qty=%s, stop=%.4f, take=%.4f)",
            dec.side.upper(), TICKER, price, qty, stop, take
        )
    else:
        # כאן בהמשך ייכנס הקוד לחיבור לברוקר אמיתי
        log.warning("LIVE TRADE (not implemented): %s %s qty=%s", dec.side, TICKER, qty)


def once():
    """
    Single polling iteration:
    1) fetch -> add indicators
    2) decision
    3) size calc (qty/stop/take)
    4) paper 'execution' (log) + הודעת טלגרם
    """
    raw = fetch_bars(TICKER, period=PERIOD, interval=INTERVAL)
    df  = add_indicators(raw)

    if df is None or df.empty or len(df) < 20:
        raise ValueError("Not enough data for decision after indicators.")

    last = df.iloc[-1]
    dec  = decide(df)
    qty, stop, take = _size_from_row(last)

    # DEBUG summary
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log.info(
        "[%s] %s | %s | decision=%s score=%.3f reason=\"%s\" | size=%s stop=%.4f take=%.4f",
        stamp, TICKER, describe_row(last), dec.side, float(dec.score), dec.reason,
        qty, stop, take
    )

    # שליחת התראת עסקה אם צריך
    _maybe_notify_trade(dec, last, qty, stop, take)


def main():
    # הודעת סטארט לטלגרם
    alerter.notify_start(
        service_name="trading-bot",
        paper=PAPER,
        ticker=TICKER,
        interval=INTERVAL,
        extra={"period": PERIOD}
    )

    log.info(
        "Starting worker polling %s every %s s (PAPER=%s, PERIOD=%s, INTERVAL=%s)",
        TICKER, POLL_INTERVAL, PAPER, PERIOD, INTERVAL
    )

    while True:
        try:
            once()
            # פעימת חיים (רק אם HEARTBEAT_EVERY>0 הוגדר בסביבה)
            alerter.maybe_heartbeat("worker alive")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            # לוג + התראת שגיאה לטלגרם
            log.error("iteration failed: %s", e)
            alerter.notify_error(str(e))
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user.")