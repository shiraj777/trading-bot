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
    return _env(name, "true" if default else "false").lower() == "true"

# ---------- logging ----------
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)
log = logging.getLogger("worker")

# ---------- env (רק מה־ENV, בלי קבועים בקוד) ----------
PAPER            = _env_bool("PAPER", True)
TICKER           = _env("TICKER", "TSLA")
PERIOD           = _env("PERIOD", "60d")
INTERVAL         = _env("INTERVAL", "15m")
POLL_INTERVAL    = _env_float("POLL_INTERVAL", 60.0)          # שניות בין הרצות once()
EQUITY           = _env_float("EQUITY", 10000.0)
RISK_PCT         = _env_float("RISK_PCT", _env_float("MAX_RISK_PCT", 0.001))
ALLOW_SHORT      = _env_bool("ALLOW_SHORT", True)
BRACKET_MODE     = _env_bool("BRACKET_MODE", True)            # אם הברוקר תומך, ישתמש ב־bracket מה־ENV
TAKE_PROFIT_PCT  = _env_float("TAKE_PROFIT_PCT", 0.01)        # לשימוש לוגי/לוגים בלבד; בפועל הברוקר יכול להשתמש במה שב־ENV
STOP_LOSS_PCT    = _env_float("STOP_LOSS_PCT", 0.005)
MIN_FLIP_SECS    = _env_float("MIN_FLIP_SECS", _env_float("MIN_FLIP_SECS", 60))  # דיבאונס לאותים

SERVICE_NAME     = "trading-bot-worker"

# זיכרון קל של זמנים אחרונים לכל כיוון (דיבאונס)
_last_signal_ts = {"buy": 0.0, "sell": 0.0}

def _make_broker():
    """
    בוחר ברוקר לפי ENV:
    - אם קיימים ALPACA_KEY_ID + ALPACA_SECRET_KEY => AlpacaBroker
    - אחרת PaperBroker (סימולטור)
    """
    key_id     = os.getenv("ALPACA_KEY_ID") or os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_API_SECRET")
    base       = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    if key_id and secret_key:
        log.info("Using Alpaca broker (paper=%s, base=%s)", PAPER, base)
        return AlpacaBroker(
            paper=PAPER,
            key_id=key_id,
            secret_key=secret_key,
            base_url=base,
        )
    else:
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

def _has_open_order_for_symbol(symbol: str) -> bool:
    """
    מזעור כפילויות: אם לברוקר יש API להזמנות פתוחות – נשתמש בו.
    אחרת נחזיר False ונמשיך כרגיל.
    """
    try:
        if hasattr(BROKER, "has_open_orders"):
            return bool(BROKER.has_open_orders(symbol))
        if hasattr(BROKER, "open_orders"):
            orders = BROKER.open_orders(symbol)  # צפוי להחזיר list
            return bool(orders)
    except Exception as e:
        log.debug("open-orders check failed: %s", e)
    return False

def _debounce(side: str) -> bool:
    """
    לא לאפשר שני אותות ברצף לאותו כיוון תוך פחות מ-MIN_FLIP_SECS.
    """
    now = time.time()
    last = _last_signal_ts.get(side, 0.0)
    if now - last < MIN_FLIP_SECS:
        log.info("debounce: skipping %s (only %.0fs since last %s)", side, now - last, side)
        return False
    _last_signal_ts[side] = now
    return True

def once():
    raw = fetch_bars(TICKER, period=PERIOD, interval=INTERVAL)
    df  = add_indicators(raw)
    if df is None or df.empty or len(df) < 20:
        raise ValueError("Not enough data after indicators.")
    last = df.iloc[-1]

    dec  = decide(df)  # dec.side in {"buy","sell","hold"}, dec.score, dec.reason
    price = float(last.get("close"))
    atr   = float(last.get("atr", 0.0))

    # חישוב גודל פוזיציה לפי ה־ENV (בלי קבועים בקוד)
    qty, stop, take = position_size(
        equity=EQUITY,
        atr=atr,
        price=price,
        risk_pct=RISK_PCT
    )

    # מצב פוזיציה קיים
    try:
        pos_qty = float(BROKER.position_qty(TICKER))
    except Exception:
        pos_qty = 0.0

    # האם ברקט או מרקט, לצורך לוג בלבד (הברוקר עצמו כבר יודע מה לעשות לפי ENV)
    order_class = "bracket" if BRACKET_MODE else "market"
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log.info("[%s] %s | %s | decision=%s score=%.3f reason=\"%s\" pos=%s | size=%s stop=%.4f take=%.4f | mode=%s tp=%.3f%% sl=%.3f%%",
             stamp, TICKER, describe_row(last), dec.side, float(dec.score), dec.reason,
             pos_qty, qty, stop, take, order_class, TAKE_PROFIT_PCT*100, STOP_LOSS_PCT*100)

    # heartbeat
    alerts.maybe_heartbeat(SERVICE_NAME)

    # תנאים לוגיים לשליחת הוראה
    if dec.side not in ("buy", "sell") or qty <= 0:
        log.debug("skip: side=%s qty=%s", dec.side, qty)
        return

    # דיבאונס
    if not _debounce(dec.side):
        return

    # לא לשלוח שוב אם יש כבר הזמנה פתוחה ל-symbol (אם נתמך)
    if _has_open_order_for_symbol(TICKER):
        log.info("skip: open order already exists for %s", TICKER)
        return

    # לא לשלוח שוב אם כבר יש פוזיציה באותו כיוון
    # pos_qty > 0 => לונג. pos_qty < 0 => שורט.
    can_send   = False
    order_side = None
    reason_ex  = ""

    if dec.side == "buy":
        if pos_qty > 0:
            log.info("skip: already long (pos=%.0f)", pos_qty)
            return
        # buy מכסה שורט (אם יש) או פותח לונג
        can_send   = True
        order_side = "buy"
        if pos_qty < 0:
            reason_ex = " (cover short)"

    elif dec.side == "sell":
        if pos_qty < 0:
            log.info("skip: already short (pos=%.0f)", pos_qty)
            return
        if pos_qty > 0:
            # sell יסגור/יקטין לונג
            can_send   = True
            order_side = "sell"
        else:
            # אין לונג – זה פתיחת שורט
            if ALLOW_SHORT:
                can_send   = True
                order_side = "sell"
                reason_ex  = " (open short)"
            else:
                log.info("skip: short not allowed and no long to close")
                return

    if not can_send or not order_side:
        log.debug("skip: decision=%s pos=%s allow_short=%s", dec.side, pos_qty, ALLOW_SHORT)
        return

    # שליחת הוראה
    try:
        res = BROKER.place_order(TICKER, order_side, qty, price, stop, take)
        if res.ok:
            alerts.notify_trade(order_side, TICKER, qty, price, stop, take,
                                dec.reason + reason_ex, paper=BROKER.paper)
            log.info("order ok id=%s status=%s filled=%s avg=%s", res.id, res.status, res.filled_qty, res.avg_price)
        else:
            alerts.notify_error(f"order failed for {order_side} {TICKER} x{qty}", Exception(res.error or "order error"))
            log.error("order failed: %s", res.error)
    except Exception as e:
        alerts.notify_error("order exception", e)
        log.exception("order exception")

def main():
    alerts.notify_start(SERVICE_NAME, paper=PAPER, ticker=TICKER, interval=INTERVAL)
    log.info("Starting worker polling %s every %.0f s (PAPER=%s, PERIOD=%s, INTERVAL=%s, BRACKET_MODE=%s)",
             TICKER, POLL_INTERVAL, PAPER, PERIOD, INTERVAL, BRACKET_MODE)
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