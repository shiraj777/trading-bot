# run_worker.py
from __future__ import annotations
import os, time, logging
from datetime import datetime

from dotenv import load_dotenv  # לוקאלי: טוען .env (ברנדר לא חובה)
from services.data import fetch_bars
from services.indicators import add_indicators
from services.policy import decide
from services.risk import position_size
from services import alerts
from services.execution import PaperBroker, AlpacaBroker

# ---------- load .env locally (safe no-op in Render) ----------
load_dotenv()

# ---------- logging ----------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)
log = logging.getLogger("worker")


# ---------- helpers ----------
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


# ---------- config from env ----------
PAPER         = _env("PAPER", "true").lower() == "true"
TICKER        = _env("TICKER", "AAPL")
PERIOD        = _env("PERIOD", "1mo")
INTERVAL      = _env("INTERVAL", "30m")
POLL_INTERVAL = _env_float("POLL_INTERVAL", 30.0)
EQUITY        = _env_float("EQUITY", 10_000.0)
RISK_PCT      = _env_float("RISK_PCT", 0.01)

SERVICE_NAME  = "trading-bot-worker"

# ---------- Alpaca creds pulled explicitly and passed to broker ----------
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID") or ""
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET") or os.getenv("ALPACA_SECRET_KEY") or ""
ALPACA_BASE_URL   = (os.getenv("ALPACA_BASE_URL") or "https://paper-api.alpaca.markets").strip()
# normalize base url (no trailing slash, no /v2)
ALPACA_BASE_URL   = ALPACA_BASE_URL.rstrip("/")
if ALPACA_BASE_URL.endswith("/v2"):
    ALPACA_BASE_URL = ALPACA_BASE_URL[:-3]
ALPACA_PAPER      = (os.getenv("ALPACA_PAPER", "true").lower() == "true")


def _make_broker():
    """
    אם יש מפתחות Alpaca — נשתמש ב-AlpacaBroker עם הזרמת הפרמטרים ישירות.
    אחרת — PaperBroker בלבד (סימולציה).
    """
    if ALPACA_API_KEY and ALPACA_API_SECRET:
        log.info("Using Alpaca broker (paper=%s, base=%s)", ALPACA_PAPER, ALPACA_BASE_URL)
        return AlpacaBroker(
            paper=ALPACA_PAPER,
            key_id=ALPACA_API_KEY,
            secret_key=ALPACA_API_SECRET,
            base_url=ALPACA_BASE_URL,
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

    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log.info("[%s] %s | %s | decision=%s score=%.3f reason=\"%s\" | size=%s stop=%.4f take=%.4f",
             stamp, TICKER, describe_row(last), dec.side, float(dec.score), dec.reason,
             qty, stop, take)

    # Heartbeat (אחת לכמה דקות לפי HEARTBEAT_EVERY)
    alerts.maybe_heartbeat(SERVICE_NAME)

    if dec.side in ("buy", "sell") and qty > 0:
        # שליחת פקודה לברוקר
        res = BROKER.place_order(TICKER, dec.side, qty, price, stop, take)
        if res.ok:
            alerts.notify_trade(dec.side, TICKER, qty, price, stop, take, dec.reason, paper=BROKER.paper)
            log.info("order ok id=%s status=%s filled=%s avg=%s", res.id, res.status, res.filled_qty, res.avg_price)
        else:
            alerts.notify_error(f"order failed for {dec.side} {TICKER} x{qty}", Exception(res.error or "order error"))
    else:
        log.debug("nothing to do")


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