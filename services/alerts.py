# services/alerts.py
from __future__ import annotations

import os
import time
import json
import logging
from typing import Optional, Any, Dict

import requests


log = logging.getLogger("alerts")


class TelegramAlerter:
    """
    עוטף את ממשק ה-HTTP של טלגרם ושולח הודעות מובנות לבוט/צ׳אט שלך.
    קורא משתני סביבה:
      TELEGRAM_BOT_TOKEN   - טוקן של הבוט (חובה)
      TELEGRAM_CHAT_ID     - מזהה צ׳אט (חובה)
      HEARTBEAT_EVERY      - מרווח פעימה בשניות (אופציונלי, ברירת מחדל 0=כבוי)

    שימוש מהקוד:
        from services.alerts import get_alerter
        alerter = get_alerter()
        alerter.notify_start(service_name="trading-bot", paper=True, ticker="AAPL", interval="30m")
        alerter.notify_trade(side="buy", ticker="AAPL", price=123.45, qty=10, score=0.61, reason="RSI<30")
        alerter.notify_error("Download failed: status 429")
        alerter.maybe_heartbeat("trading-bot alive")
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        heartbeat_every: Optional[int] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.heartbeat_every = int(os.getenv("HEARTBEAT_EVERY", "0") if heartbeat_every is None else heartbeat_every)
        self._last_heartbeat_at = 0.0
        self._session = session or requests.Session()

        if not self.bot_token or not self.chat_id:
            log.warning("TelegramAlerter misconfigured: missing TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID")

    # ---------- low level ----------
    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    def _send(self, text: str, markdown: bool = False) -> None:
        if not self.enabled:
            return
        try:
            payload = {"chat_id": self.chat_id, "text": text}
            if markdown:
                payload["parse_mode"] = "MarkdownV2"
            resp = self._session.post(self._api("sendMessage"), data=payload, timeout=15)
            if resp.status_code != 200:
                log.warning("Telegram send failed: %s %s", resp.status_code, resp.text)
        except Exception as e:
            log.exception("Telegram send exception: %s", e)

    # ---------- high level helpers ----------
    def notify_start(
        self,
        service_name: str,
        paper: bool,
        ticker: str,
        interval: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        התראה בתחילת ריצה.
        """
        tag = "PAPER" if paper else "LIVE"
        parts = [
            f"🚀 *{service_name}* started",
            f"• mode: *{tag}*",
            f"• ticker: *{ticker}*",
            f"• interval: *{interval}*",
        ]
        if self.heartbeat_every > 0:
            parts.append(f"• heartbeat: every *{self.heartbeat_every}s*")
        if extra:
            parts.append(f"• extra: `{json.dumps(extra, ensure_ascii=False)}`")

        self._send("\n".join(parts), markdown=True)

    def notify_trade(
        self,
        side: str,
        ticker: str,
        price: float,
        qty: float,
        score: Optional[float] = None,
        reason: Optional[str] = None,
        stop: Optional[float] = None,
        take: Optional[float] = None,
    ) -> None:
        """
        התראה על עסקה שבוצעה/סופקה.
        """
        emoji = "🟢" if side.lower() == "buy" else "🔴" if side.lower() == "sell" else "⚪️"
        lines = [
            f"{emoji} *{side.upper()}* `{ticker}` @ *{price:.4f}*",
            f"• qty: *{qty}*",
        ]
        if score is not None:
            lines.append(f"• score: *{score:.3f}*")
        if reason:
            # בטלגרם MarkdownV2 צריך לברוח תווים בעייתיים, כאן נשמור פשוט כרגיל (לרוב עובד)
            lines.append(f"• reason: {reason}")
        if stop is not None or take is not None:
            lines.append(f"• SL/TP: {stop if stop is not None else '-'} / {take if take is not None else '-'}")

        self._send("\n".join(lines), markdown=True)

    def notify_error(self, message: str) -> None:
        """
        התראה על שגיאה מהותית.
        """
        self._send(f"⚠️ *Error*: {message}", markdown=True)

    # ---------- heartbeat ----------
    def maybe_heartbeat(self, text: str = "alive") -> None:
        """
        שולח פעימת חיים לפי המרווח המוגדר (HEARTBEAT_EVERY).
        אם HEARTBEAT_EVERY=0 — לא שולח.
        """
        if self.heartbeat_every <= 0:
            return
        now = time.time()
        if now - self._last_heartbeat_at >= self.heartbeat_every:
            self._last_heartbeat_at = now
            self._send(f"💜 {text}")


# ---------- singleton convenience ----------
_singleton: Optional[TelegramAlerter] = None


def get_alerter() -> TelegramAlerter:
    """
    החזרה של יחידת alerter יחידה (Lazy singleton).
    """
    global _singleton
    if _singleton is None:
        _singleton = TelegramAlerter()
    return _singleton