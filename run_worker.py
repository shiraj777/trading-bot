# run_worker.py
from __future__ import annotations
import os, time, logging
from datetime import datetime

from services.data import fetch_bars
from services.indicators import add_indicators
from services.policy import decide
from services.risk import position_size
from services import alerts
from services.execution import PaperBroker, AlpacaBroker

# ---------- env helpers ----------
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

def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return str(v).lower() in ("1", "true", "yes", "on")

# ---------- config from ENV ----------
LOG_LEVEL      = _env("LOG_LEVEL", "INFO").upper()
PAPER          = _env_bool("PAPER", True)
TICKER         = _env("TICKER", "AAPL")
PERIOD         = _env("PERIOD", "1mo")
INTERVAL       = _env("INTERVAL", "30m")
POLL_INTERVAL  = _env_float("POLL_INTERVAL", 30.0)

EQUITY         = _env_float("EQUITY", 10_000.0)
RISK_PCT       = _env_float("RISK_PCT", 0.01)

# שליטה במוד של שליחת פקודה
BRACKET_MODE   = _env_bool("BRACKET_MODE", True)              # true = bracket, false = market
ORDER_TIF      = _env("ORDER_TIF", "day")                      # 'day'/'gtc' וכו'
TAKE_PROFIT_PCT= _env_float("TAKE_PROFIT_PCT", 0.01)           # 1% כברירת מחדל
STOP_LOSS_PCT  = _env_float("STOP_LOSS_PCT", 0.005)            # 0.5% כברירת מחדל

# האם לאפשר פתיחת שורט כשאין לונג
ALLOW_SHORT    = _env_bool("ALLOW_SHORT", False)

SERVICE_NAME   = "trading-bot-worker"

# ---------- logging ----------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)
log = logging.getLogger("worker")

# ---------- broker factory ----------
def _make_broker():
    # שמות ה־ENV כפי שהראית שיש לך:
    api_key    = os.getenv("ALPACA_API_KEY")
    api_secret = os.getenv("ALPACA_API_SECRET")
    base_url   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    paper_flag = _env_bool("ALPACA_PAPER", True)   # אם אין, פשוט יישאר True (paper)

    if api_key and api_secret:
        log.info("Using Alpaca broker (paper=%s, base=%s)", paper_flag, base_url)
        return AlpacaBroker(
            paper=paper_flag,
            key_id=api_key,
            secret_key=api_secret,
            base_url=base_url,
        )
    log.info("Using Paper broker (simulated)")
    return PaperBroker(paper=True)

BROKER = _make_broker()

# ---------- utility ----------
def describe_row(row) -> str:
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

# ---------- main cycle ----------
def once():
    raw = fetch_bars(TICKER, period=PERIOD, interval=INTERVAL)
    df  = add_indicators(raw)
    if df is None or df.empty or len(df) < 20:
        raise ValueError("Not enough data after indicators.")

    last   = df.iloc[-1]
    price  = float(last.get("close"))
    atr    = float(last.get("atr", 0.0))

    dec    = decide(df)  # dec.side in {"buy","sell","hold"}, dec.score, dec.reason
    qty, stop_abs, take_abs = position_size(equity=EQUITY, atr=atr, price=price, risk_pct=RISK_PCT)

    # מצב פוזיציה נוכחי לשיקולי short/long
    try:
        pos_qty = float(BROKER.position_qty(TICKER))
    except Exception:
        pos_qty = 0.0

    # החלטה האם בכלל לשלוח פקודה
    can_send   = False
    order_side = None
    reason_ex  = ""

    if dec.side == "buy":
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
    else:
        can_send = False

    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log.info("[%s] %s | %s | decision=%s score=%.3f reason=\"%s\" pos=%s | size=%s stop=%.4f take=%.4f | mode=%s tp=%.3f%% sl=%.3f%%",
             stamp, TICKER, describe_row(last), dec.side, float(dec.score), dec.reason,
             pos_qty, qty, stop_abs, take_abs, "BRACKET" if BRACKET_MODE else "MARKET",
             TAKE_PROFIT_PCT*100.0, STOP_LOSS_PCT*100.0)

    # heartbeat
    alerts.maybe_heartbeat(SERVICE_NAME)

    if not (can_send and order_side and qty > 0):
        log.debug("skip: decision=%s pos=%s allow_short=%s qty=%s", dec.side, pos_qty, ALLOW_SHORT, qty)
        return

    # שליחת הוראה בפועל
    try:
        if BRACKET_MODE:
            # *** התיקון הקריטי: להעביר entry_price ***
            res = BROKER.place_bracket(
                symbol=TICKER,
                side=order_side,
                qty=qty,
                entry_price=price,               # <— זה היה חסר ויצר TypeError
                tp_pct=TAKE_PROFIT_PCT,
                sl_pct=STOP_LOSS_PCT,
                tif=ORDER_TIF,
            )
            log.info("BRACKET sent: side=%s qty=%s entry=%.4f tp%%=%.3f sl%%=%.3f tif=%s",
                     order_side, qty, price, TAKE_PROFIT_PCT, STOP_LOSS_PCT, ORDER_TIF)
        else:
            res = BROKER.place_market(
                symbol=TICKER,
                side=order_side,
                qty=qty,
                tif=ORDER_TIF,
            )
            log.info("MARKET sent: side=%s qty=%s tif=%s", order_side, qty, ORDER_TIF)

        if res.ok:
            alerts.notify_trade(order_side, TICKER, qty, price, stop_abs, take_abs,
                                dec.reason + reason_ex, paper=BROKER.paper)
            log.info("order ok id=%s status=%s filled=%s avg=%s", res.id, res.status, res.filled_qty, res.avg_price)
        else:
            alerts.notify_error(f"order failed for {order_side} {TICKER} x{qty}",
                                Exception(res.error or "order error"))
            log.error("order failed: %s", res.error)

    except Exception as e:
        alerts.notify_error("order exception", e)
        log.exception("order exception")

def main():
    alerts.notify_start(SERVICE_NAME, paper=PAPER, ticker=TICKER, interval=INTERVAL)
    log.info(
        "Starting worker polling %s every %.0f s (PAPER=%s, PERIOD=%s, INTERVAL=%s, BRACKET_MODE=%s)",
        TICKER, POLL_INTERVAL, PAPER, PERIOD, INTERVAL, BRACKET_MODE
    )
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