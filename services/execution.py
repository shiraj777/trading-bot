# services/execution.py
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any

from .alpaca_client import AlpacaClient


# ---------- כלי עזר לסביבה ----------
def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None or v == "" else v


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


# ---------- תוצאות ברוקר ----------
@dataclass
class BrokerResult:
    ok: bool
    id: Optional[str] = None
    status: Optional[str] = None
    filled_qty: Optional[str] = None
    avg_price: Optional[str] = None
    error: Optional[str] = None


# ---------- ברוקר דמה (לסימולציה) ----------
class PaperBroker:
    """
    ברוקר דמה. לא שולח לשום מקום, מיועד לסימולציה בלבד.
    """
    paper: bool = True

    def __init__(self, paper: bool = True) -> None:
        self.paper = paper
        self.log = logging.getLogger("PaperBroker")
        if not self.log.handlers:
            logging.basicConfig(level=logging.INFO,
                                format="%(asctime)s %(levelname)s:%(name)s:%(message)s")

    def place_order(
        self, symbol: str, side: str, qty: int, price: float,
        stop: float, take: float
    ) -> BrokerResult:
        self.log.info("[PAPER] %s %s x%s @%.2f (stop=%.2f, take=%.2f)",
                      side, symbol, qty, price, stop, take)
        # מחזירים כאילו הכול הצליח
        return BrokerResult(ok=True, id="paper-sim", status="accepted",
                            filled_qty="0", avg_price=str(price))


# ---------- ברוקר Alpaca ----------
class AlpacaBroker:
    """
    שליחת הוראות אמיתיות ל-Alpaca.
    מיישם ברירת־מחדל רכה לביצוע ב-BRACKET (TP/SL) כדי למנוע שגיאות 422.
    ניתן לכבות ברקט דרך BRACKET_MODE=false.
    """

    paper: bool = True

    def __init__(self, paper: bool = True) -> None:
        self.paper = paper
        self.log = logging.getLogger("AlpacaBroker")
        if not self.log.handlers:
            logging.basicConfig(level=logging.INFO,
                                format="%(asctime)s %(levelname)s:%(name)s:%(message)s")

        # קריאה ל־env – Render / .env
        # בסיס ה־URL אמור להיות "https://paper-api.alpaca.markets" או "https://api.alpaca.markets"
        base = _env("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

        # הגדרות אסטרטגיה
        self.bracket_mode = _env_bool("BRACKET_MODE", True)  # שליחת ברקט כברירת מחדל
        self.tp_pct       = _env_float("TAKE_PROFIT_PCT", 0.010)  # 1% רווח
        self.sl_pct       = _env_float("STOP_LOSS_PCT",   0.005)  # 0.5% הפסד
        self.allow_short  = _env_bool("ALLOW_SHORT", True)
        # מניעת "פליפים" מהירים מדי
        self.min_flip_secs = _env_float("MIN_FLIP_SECS", 2.0)
        self._last_side_ts: Dict[str, float] = {}

        # יצירת לקוח ה-REST
        self.client = AlpacaClient(base_url=base)

        # בדיקת חיבור (לא חובה אך נחמד)
        try:
            acct = self.client.check_connection()
            self.log.info("Alpaca ready. equity=%s buying_power=%s shorting_enabled=%s",
                          acct.get("equity"), acct.get("buying_power"), acct.get("shorting_enabled"))
        except Exception as e:
            self.log.warning("could not pre-check account: %s", e)

    # ---------- מניעת פליפים חדים ----------
    def _throttle_side(self, symbol: str, side: str) -> None:
        # side יכול להיות 'buy' או 'sell'
        now = time.time()
        key = f"{symbol}:{side}"
        last = self._last_side_ts.get(key, 0.0)
        if now - last < self.min_flip_secs:
            time.sleep(max(0.0, self.min_flip_secs - (now - last)))
        self._last_side_ts[key] = time.time()

    # ---------- חישובי TP/SL ----------
    def _derive_pcts_from_prices(
        self, side: str, entry_price: float, stop: float, take: float
    ) -> tuple[float, float]:
        """
        אם עברו לנו גם stop/take (מהאלגוריתם), נתרגם אותם לאחוזים.
        אחרת, נשאר עם tp/sl ברירת המחדל מה-env.
        """
        tp_pct = self.tp_pct
        sl_pct = self.sl_pct
        if entry_price and stop > 0 and take > 0:
            if side == "buy":
                tp_pct = max(0.0001, (take - entry_price) / entry_price)
                sl_pct = max(0.0001, (entry_price - stop) / entry_price)
            else:  # sell/short
                tp_pct = max(0.0001, (entry_price - take) / entry_price)
                sl_pct = max(0.0001, (stop - entry_price) / entry_price)
        return tp_pct, sl_pct

    # ---------- שליחת הוראה ----------
    def place_order(
        self, symbol: str, side: str, qty: int, price: float,
        stop: float, take: float
    ) -> BrokerResult:
        """
        side: 'buy' או 'sell'
        אם BRACKET_MODE=true -> שולח ברקט עם TP/SL המחושבים סביב 'price'.
        אחרת -> הוראת Market רגילה.
        """
        try:
            if side not in ("buy", "sell"):
                return BrokerResult(ok=False, error=f"invalid side: {side}")

            if side == "sell" and not self.allow_short:
                return BrokerResult(ok=False, error="shorts disabled (ALLOW_SHORT=false)")

            self._throttle_side(symbol, side)

            # אם ברקט:
            if self.bracket_mode:
                tp_pct, sl_pct = self._derive_pcts_from_prices(side, price, stop, take)

                # הגנה: אם חישוב ה־pct יוצא אפסי/שווה – תרחיקי אותם קצת
                if abs(tp_pct - sl_pct) < 1e-6:
                    tp_pct += 0.001

                self.log.info(
                    "Submitting BRACKET | %s %s x%s @%.4f | tp=%.3f%% sl=%.3f%%",
                    side, symbol, qty, price, tp_pct * 100.0, sl_pct * 100.0
                )

                res = self.client.submit_bracket(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    entry_price=float(price),
                    tp_pct=float(tp_pct),
                    sl_pct=float(sl_pct),
                    time_in_force="day",
                )
            else:
                # הוראת Market בסיסית
                self.log.info("Submitting MARKET | %s %s x%s", side, symbol, qty)
                res = self.client.submit_order(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    order_type="market",
                    time_in_force="day",
                )

            # החזרה מפורקת לתוצאת ברוקר
            return BrokerResult(
                ok=True,
                id=res.get("id"),
                status=res.get("status"),
                filled_qty=res.get("filled_qty"),
                avg_price=res.get("filled_avg_price"),
            )

        except Exception as e:
            # שגיאה ברורה ללוג / טלגרם
            self.log.exception("alpaca order failed")
            return BrokerResult(ok=False, error=str(e))