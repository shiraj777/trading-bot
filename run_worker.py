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
    return str(v).lower() in {"1", "true", "yes", "y", "on"}

# ---------- env ----------
PAPER         = _env_bool("PAPER", True)
TICKER        = _env("TICKER", "AAPL")
PERIOD        = _env("PERIOD", "1mo")
INTERVAL      = _env("INTERVAL", "30m")
POLL_INTERVAL = _env_float("POLL_INTERVAL", 30.0)
EQUITY        = _env_float("EQUITY", 10_000.0)
RISK_PCT      = _env_float("RISK_PCT", 0.01)

# חדש: לאפשר/למנוע פתיחת שורט אם אין לונג
ALLOW_SHORT   = _env_bool("ALLOW_SHORT", False)

# חדש: מצב BRACKET + אחוזי TP/SL
BRACKET_MODE   = _env_bool("BRACKET_MODE", False)
TAKE_PROFIT_PCT = _env_float("TAKE_PROFIT_PCT", 0.010)   # 1.0%
STOP_LOSS_PCT   = _env_float("STOP_LOSS_PCT",   0.005)   # 0.5%

SERVICE_NAME  = "trading-bot-worker"

# ---------- broker factory ----------
def _make_broker():
    # שימי לב: שמות משתני הסביבה הם ALPACA_KEY_ID/ALPACA_SECRET_KEY/ALPACA_BASE_URL
    if os.getenv("ALPACA_KEY_ID") and os.getenv("ALPACA_SECRET_KEY"):
        paper = _env_bool("ALPACA_PAPER", True)
        base  = _env("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        log.info("Using Alpaca broker (paper=%s, base=%s)", paper, base)
        return AlpacaBroker(
            paper=paper,
            key_id=os.getenv("ALPACA_KEY_ID"),
            secret_key=os.getenv("ALPACA_SECRET_KEY"),
            base_url=base,
        )
    log.info("Using Paper broker (simulated)")
    return PaperBroker(paper=True)

BROKER = _make_broker()

# ---------- utils ----------
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

# ---------- main iteration ----------
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

    # מצב פוזיציה נוכחי (לניהול לוגיקה של שורט/לונג)
    try:
        pos_qty = float(BROKER.position_qty(TICKER))
    except Exception:
        pos_qty = 0.0

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

    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log.info("[%s] %s | %s | decision=%s score=%.3f reason=\"%s\" pos=%s | size=%s stop=%.4f take=%.4f",
             stamp, TICKER, describe_row(last), dec.side, float(dec.score), dec.reason,
             pos_qty, qty, stop, take)

    # Heartbeat
    alerts.maybe_heartbeat(SERVICE_NAME)

    if not (can_send and order_side and qty > 0):
        log.debug("skip: decision=%s pos=%s allow_short=%s", dec.side, pos_qty, ALLOW_SHORT)
        return

    # ---------- send order ----------
    try:
        if BRACKET_MODE and hasattr(BROKER, "place_bracket"):
            # נשלחת הזמנת BRACKET לפי אחוזים סביב מחיר הכניסה
            log.info(
                "Sending BRACKET | %s %s x%s @%.4f | tp=%s sl=%s",
                order_side, TICKER, qty, price,
                f"{TAKE_PROFIT_PCT*100:.3f}%", f"{STOP_LOSS_PCT*100:.3f}%"
            )
            res = BROKER.place_bracket(
                symbol=TICKER,
                side=order_side,
                qty=qty,
                entry_price=price,
                tp_pct=TAKE_PROFIT_PCT,
                sl_pct=STOP_LOSS_PCT,
            )
            sent_kind = "BRACKET"
        else:
            # שליחת MARKET/מנגנון הישן (עם מחירי stop/take מוחלטים)
            log.info(
                "Sending MARKET | %s %s x%s @%.4f | stop=%.4f take=%.4f (BRACKET_MODE=%s not used)",
                order_side, TICKER, qty, price, stop, take, BRACKET_MODE
            )
            res = BROKER.place_order(TICKER, order_side, qty, price, stop, take)
            sent_kind = "MARKET"

        if res.ok:
            alerts.notify_trade(
                order_side, TICKER, qty, price, stop, take,
                f"{dec.reason}{reason_ex} [{sent_kind} tp={TAKE_PROFIT_PCT*100:.2f}% sl={STOP_LOSS_PCT*100:.2f}%]",
                paper=BROKER.paper
            )
            log.info(
                "%s order ok id=%s status=%s filled=%s avg=%s",
                sent_kind, res.id, res.status, res.filled_qty, res.avg_price
            )
        else:
            alerts.notify_error(
                f"{sent_kind} order failed for {order_side} {TICKER} x{qty}",
                Exception(res.error or "order error")
            )
            log.error("%s order failed: %s", sent_kind, res.error)

    except Exception as e:
        alerts.notify_error("order exception", e)
        log.exception("order exception")

def main():
    alerts.notify_start(SERVICE_NAME, paper=PAPER, ticker=TICKER, interval=INTERVAL)
    log.info(
        "Starting worker polling %s every %.0f s (PAPER=%s, PERIOD=%s, INTERVAL=%s | BRACKET_MODE=%s tp=%s sl=%s)",
        TICKER, POLL_INTERVAL, PAPER, PERIOD, INTERVAL,
        BRACKET_MODE, f"{TAKE_PROFIT_PCT*100:.3f}%", f"{STOP_LOSS_PCT*100:.3f}%"
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