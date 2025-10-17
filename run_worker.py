# run_worker.py
from __future__ import annotations

import os
import time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.data import fetch_bars
from services.indicators import add_indicators
from services.policy import decide
from services.risk import position_size
from services import alerts
from services.execution import PaperBroker, AlpacaBroker


# --------------------- logging & version ---------------------
def _env(name: str, default: Optional[str] = None) -> str:
    v = os.getenv(name)
    if v is None or v == "":
        return default if default is not None else ""
    return v


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except Exception:
        return default


APP_VERSION = _env("APP_VERSION", "dev")
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)
log = logging.getLogger("worker")
log.info("🚀 Starting worker version=%s", APP_VERSION)


# --------------------- env ---------------------
PAPER: bool         = _env_bool("PAPER", True)
TICKER: str         = _env("TICKER", "AAPL")
PERIOD: str         = _env("PERIOD", "1mo")
INTERVAL: str       = _env("INTERVAL", "30m")
POLL_INTERVAL: float = _env_float("POLL_INTERVAL", 30.0)

EQUITY: float       = _env_float("EQUITY", 10_000.0)
RISK_PCT: float     = _env_float("RISK_PCT", 0.01)

ALLOW_SHORT: bool   = _env_bool("ALLOW_SHORT", False)

# BRACKET controls
BRACKET_MODE: bool  = _env_bool("BRACKET_MODE", True)
TAKE_PROFIT_PCT: float = _env_float("TAKE_PROFIT_PCT", 0.01)   # 1.0% by default
STOP_LOSS_PCT: float   = _env_float("STOP_LOSS_PCT", 0.005)    # 0.5% by default

# Min seconds to allow the same action or a flip (debounce)
MIN_FLIP_SECS: float  = _env_float("MIN_FLIP_SECS", 60.0)

# Heartbeat cadence (seconds); alerts.maybe_heartbeat משתמש בזה
_env("HEARTBEAT_EVERY", "60")  # הערך נצרך בתוך מודול alerts עצמו


# --------------------- broker selection ---------------------
def _make_broker():
    """
    לבחור ברוקר בהתאם ל־ENV.
    אם ALPACA_KEY_ID/ALPACA_SECRET_KEY/ALPACA_BASE_URL קיימים – נשתמש ב־Alpaca.
    אחרת – PaperBroker (סימולציה).
    """
    key_id = os.getenv("ALPACA_KEY_ID") or os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_API_SECRET")
    base   = _env("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    if key_id and secret:
        paper = _env_bool("ALPACA_PAPER", True)
        log.info("Using Alpaca broker (paper=%s, base=%s)", paper, base)
        return AlpacaBroker(
            paper=paper,
            key_id=key_id,
            secret_key=secret,
            base_url=base,
        )
    log.info("Using Paper broker (simulated)")
    return PaperBroker(paper=True)


BROKER = _make_broker()


# --------------------- helpers / state ---------------------
_last_action_side: Optional[str] = None   # 'buy' | 'sell'
_last_action_ts: float = 0.0              # unix time

def _throttle(side: str, min_secs: float) -> Optional[str]:
    """
    דיבאונס גם על “אותו צד” וגם על flip-flop.
    מחזיר סיבת דילוג (string) אם צריך לדלג, אחרת None.
    """
    global _last_action_side, _last_action_ts
    now = time.time()
    if _last_action_side is None:
        return None

    elapsed = now - _last_action_ts
    if elapsed < min_secs:
        if _last_action_side == side:
            return f"same-side throttle: {side} again after {elapsed:.1f}s (<{min_secs:.0f}s)"
        else:
            return f"flip debounce: {_last_action_side}→{side} after {elapsed:.1f}s (<{min_secs:.0f}s)"
    return None


def _update_action_clock(side: str) -> None:
    global _last_action_side, _last_action_ts
    _last_action_side = side
    _last_action_ts = time.time()


def _position_qty_safe(symbol: str) -> float:
    try:
        return float(BROKER.position_qty(symbol))
    except Exception:
        return 0.0


def _open_orders_safe(symbol: str) -> List[Dict[str, Any]]:
    try:
        if hasattr(BROKER, "open_orders"):
            return list(BROKER.open_orders(symbol))
    except Exception:
        pass
    return []


def _describe_row(row) -> str:
    parts = []
    def add(name, fmt="{:.4f}"):
        if name in row:
            try:
                parts.append(f"{name}=" + (fmt.format(float(row[name]))))
            except Exception:
                parts.append(f"{name}=?")
    add("close"); add("rsi", "{:.1f}"); add("macd", "{:.3f}")
    add("macd_signal", "{:.3f}"); add("macd_hist", "{:.3f}"); add("atr", "{:.3f}")
    return ", ".join(parts)


# --------------------- main iteration ---------------------
def once():
    raw = fetch_bars(TICKER, period=PERIOD, interval=INTERVAL)
    df  = add_indicators(raw)
    if df is None or df.empty or len(df) < 20:
        raise ValueError("Not enough data after indicators.")
    last = df.iloc[-1]

    dec  = decide(df)  # returns object: .side in {"buy","sell","hold"}, .score, .reason
    price = float(last.get("close"))
    atr   = float(last.get("atr", 0.0))
    qty, stop, take = position_size(equity=EQUITY, atr=atr, price=price, risk_pct=RISK_PCT)

    pos_qty = _position_qty_safe(TICKER)
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log.info("[%s] %s | %s | decision=%s score=%.3f reason=\"%s\" pos=%s | size=%s stop=%.4f take=%.4f | mode=%s tp=%.3f%% sl=%.3f%%",
             stamp, TICKER, _describe_row(last), dec.side, float(dec.score), dec.reason,
             pos_qty, qty, stop, take, "BRACKET" if BRACKET_MODE else "ATR",
             TAKE_PROFIT_PCT*100.0, STOP_LOSS_PCT*100.0)

    # heartbeat (אחת לכמה דקות לפי HEARTBEAT_EVERY בפנים)
    alerts.maybe_heartbeat("trading-bot-worker")

    # רק אם יש BUY/SELL — בודקים דיבאונס
    if dec.side in ("buy", "sell"):
        reason = _throttle(dec.side, MIN_FLIP_SECS)
        if reason:
            log.info("debounce: %s — skipping", reason)
            return

    # לוגיקת פתיחת/סגירת פוזיציה:
    can_send   = False
    order_side = None
    reason_ex  = ""

    if dec.side == "buy":
        # קונים רק אם אין לונג קיים (או יש שורט – אז זה מכסה אותו)
        if pos_qty <= 0:
            can_send   = True
            order_side = "buy"
            if pos_qty < 0:
                reason_ex = " (cover short)"
    elif dec.side == "sell":
        if pos_qty > 0:
            can_send   = True
            order_side = "sell"
        elif ALLOW_SHORT:
            can_send   = True
            order_side = "sell"
            reason_ex  = " (open short)"
        else:
            can_send = False

    # אם אין מה לשלוח – סיימנו
    if not (can_send and order_side and qty > 0):
        log.debug("skip: decision=%s pos=%s allow_short=%s qty=%s", dec.side, pos_qty, ALLOW_SHORT, qty)
        return

    # אם יש הזמנה פתוחה – לא שולחים כפול
    oo = _open_orders_safe(TICKER)
    if oo:
        log.info("skip: open order already exists for %s (n=%d)", TICKER, len(oo))
        return

    # שליחה: BRACKET או ATR
    res = None
    try:
        if BRACKET_MODE and hasattr(BROKER, "place_bracket"):
            # נשלח באחוזים (ה־PaperBroker/AlpacaBroker אצלך תומכים בקריאה הזו)
            log.info("Sending BRACKET | %s %s x%s | tp=%.3f%% sl=%.3f%%",
                     order_side.upper(), TICKER, qty, TAKE_PROFIT_PCT*100, STOP_LOSS_PCT*100)
            res = BROKER.place_bracket(
                symbol=TICKER,
                side=order_side,
                qty=int(qty),
                tp_pct=TAKE_PROFIT_PCT,
                sl_pct=STOP_LOSS_PCT,
            )
        else:
            # ATR-based (fallback)
            log.info("Sending MARKET | %s %s x%s | stop=%.4f take=%.4f (ATR mode)",
                     order_side.upper(), TICKER, qty, stop, take)
            res = BROKER.place_order(TICKER, order_side, int(qty), price, stop, take)

        if res and getattr(res, "ok", False):
            alerts.notify_trade(order_side, TICKER, qty, price, stop, take,
                                dec.reason + reason_ex, paper=BROKER.paper)
            log.info("order ok id=%s status=%s filled=%s avg=%s",
                     getattr(res, "id", "?"), getattr(res, "status", "?"),
                     getattr(res, "filled_qty", "?"), getattr(res, "avg_price", "?"))
            _update_action_clock(order_side)
        else:
            err_msg = getattr(res, "error", "order error")
            alerts.notify_error(f"order failed for {order_side} {TICKER} x{qty}", Exception(err_msg))
            log.error("order failed: %s", err_msg)

    except Exception as e:
        log.exception("order exception")
        alerts.notify_error("order exception", e)


# --------------------- main loop ---------------------
def main():
    alerts.notify_start("trading-bot-worker", paper=PAPER, ticker=TICKER, interval=INTERVAL)
    log.info("Starting worker polling %s every %.0f s (PAPER=%s, PERIOD=%s, INTERVAL=%s, BRACKET_MODE=%s tp=%.3f%% sl=%.3f%%)",
             TICKER, POLL_INTERVAL, PAPER, PERIOD, INTERVAL, BRACKET_MODE,
             TAKE_PROFIT_PCT*100.0, STOP_LOSS_PCT*100.0)
    while True:
        try:
            once()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log.exception("iteration failed")
            alerts.notify_error("iteration failed", e)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user.")