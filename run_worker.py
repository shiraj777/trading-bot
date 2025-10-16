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

# ---------- logging ----------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)
log = logging.getLogger("worker")

# ---------- env ----------
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
PERIOD        = _env("PERIOD", "1mo")
INTERVAL      = _env("INTERVAL", "30m")
POLL_INTERVAL = _env_float("POLL_INTERVAL", 30.0)
EQUITY        = _env_float("EQUITY", 10_000.0)
RISK_PCT      = _env_float("RISK_PCT", 0.01)

# חדש: לאפשר/למנוע פתיחת שורט אם אין לונג
ALLOW_SHORT   = _env("ALLOW_SHORT", "false").lower() == "true"

SERVICE_NAME  = "trading-bot-worker"

# בתוך run_worker.py

def _make_broker():
    if os.getenv("ALPACA_KEY_ID") and os.getenv("ALPACA_SECRET_KEY"):
        paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"
        base  = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        log.info("Using Alpaca broker (paper=%s, base=%s)", paper, base)
        return AlpacaBroker(
            paper=paper,
            key_id=os.getenv("ALPACA_KEY_ID"),
            secret_key=os.getenv("ALPACA_SECRET_KEY"),
            base_url=base,        # << זה השם הנכון שה-class מקבל
        )
    log.info("Using Paper broker (simulated)")
    return PaperBroker(paper=True)


BROKER = _make_broker()

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

def once():
    raw = fetch_bars(TICKER, period=PERIOD, interval=INTERVAL)
    df  = add_indicators(raw)
    if df is None or df.empty or len(df) < 20:
        raise ValueError("Not enough data after indicators.")
    last = df.iloc[-1]

    dec  = decide(df)  # dec.side in {"buy","sell","hold"}, dec.score, dec.reason
    price = float(last.get("close"))
    atr   = float(last.get("atr", 0.0))
    qty, stop, take = position_size(equity=EQUITY, atr=atr, price=price, risk_pct=RISK_PCT)

    # מה הפוזיציה הנוכחית?
    try:
        pos_qty = float(BROKER.position_qty(TICKER))
    except Exception:
        pos_qty = 0.0

    # לוגיקה כדי למנוע SELL אם אין לונג (אלא אם ALLOW_SHORT=true)
    can_send   = False
    order_side = None
    reason_ex  = ""

    if dec.side == "buy":
        # אם אין לונג (pos<=0) נקנה; אם יש שורט (pos<0) זה מכסה אותו.
        if pos_qty <= 0:
            can_send   = True
            order_side = "buy"
            if pos_qty < 0:
                reason_ex = " (cover short)"
    elif dec.side == "sell":
        if pos_qty > 0:
            # יש לונג – מוכרים (סגירה/הקטנה)
            can_send   = True
            order_side = "sell"
        elif ALLOW_SHORT:
            # אין לונג – מותר לפתוח שורט
            can_send   = True
            order_side = "sell"
            reason_ex  = " (open short)"
        else:
            can_send = False
    else:
        can_send = False

    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log.info("[%s] %s | %s | decision=%s score=%.3f reason=\"%s\" pos=%s | size=%s stop=%.4f take=%.4f",
             stamp, TICKER, describe_row(last), dec.side, float(dec.score), dec.reason,
             pos_qty, qty, stop, take)

    # Heartbeat
    alerts.maybe_heartbeat(SERVICE_NAME)

    if can_send and order_side and qty > 0:
        res = BROKER.place_order(TICKER, order_side, qty, price, stop, take)
        if res.ok:
            alerts.notify_trade(order_side, TICKER, qty, price, stop, take,
                                dec.reason + reason_ex, paper=BROKER.paper)
            log.info("order ok id=%s status=%s filled=%s avg=%s", res.id, res.status, res.filled_qty, res.avg_price)
        else:
            alerts.notify_error(f"order failed for {order_side} {TICKER} x{qty}", Exception(res.error or "order error"))
    else:
        log.debug("skip: decision=%s pos=%s allow_short=%s", dec.side, pos_qty, ALLOW_SHORT)

def main():
    alerts.notify_start(SERVICE_NAME, paper=PAPER, ticker=TICKER, interval=INTERVAL)
    log.info("Starting worker polling %s every %.0f s (PAPER=%s, PERIOD=%s, INTERVAL=%s)",
             TICKER, POLL_INTERVAL, PAPER, PERIOD, INTERVAL)
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