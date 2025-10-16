# services/alerts.py
from __future__ import annotations
import os, time, json, logging, urllib.parse, urllib.request

log = logging.getLogger("alerts")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()
HEARTBEAT_EVERY    = int(os.getenv("HEARTBEAT_EVERY", "0") or 0)  # דקות

_last_heartbeat_ts = 0.0

def _enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

def _post(method: str, data: dict) -> None:
    if not _enabled(): 
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    body = urllib.parse.urlencode(data).encode("utf-8")
    req  = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                log.warning("telegram %s non-200: %s", method, resp.status)
    except Exception as e:
        log.warning("telegram %s failed: %s", method, e)

def send_text(text: str, parse_mode: str | None = None) -> None:
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode or "HTML"
    }
    _post("sendMessage", payload)

def notify_start(service_name: str, paper: bool, ticker: str, interval: str) -> None:
    mode = "PAPER" if paper else "LIVE"
    send_text(f"🚀 *worker started* — {service_name}\n"
              f"• mode: *{mode}*\n• ticker: *{ticker}*\n• interval: *{interval}*",
              parse_mode="Markdown")

def notify_trade(side: str, symbol: str, qty: int, price: float, stop: float, take: float, reason: str, paper: bool) -> None:
    badge = "🟢 BUY" if side == "buy" else "🔴 SELL"
    mode  = "PAPER" if paper else "LIVE"
    send_text(
        f"{badge} *{symbol}* x{qty} @ {price:.4f}\n"
        f"⛑ stop: {stop:.4f} | 🎯 take: {take:.4f}\n"
        f"📝 reason: {reason}\n"
        f"🔧 mode: *{mode}*",
        parse_mode="Markdown"
    )

def notify_error(msg: str, exc: Exception | None = None) -> None:
    tail = f"\n• err: `{type(exc).__name__}: {exc}`" if exc else ""
    send_text(f"⚠️ *worker error*\n{msg}{tail}", parse_mode="Markdown")

def maybe_heartbeat(service_name: str) -> None:
    global _last_heartbeat_ts
    if HEARTBEAT_EVERY <= 0: 
        return
    now = time.time()
    if now - _last_heartbeat_ts >= HEARTBEAT_EVERY * 60:
        _last_heartbeat_ts = now
        send_text(f"💜 {service_name} alive")