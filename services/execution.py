# services/execution.py
from __future__ import annotations

import json
import time
import os
import logging
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict

import urllib.request
import urllib.error

log = logging.getLogger("execution")


# --------------------------------------------
# תוצאת הזמנה אחידה לכל הברוקרים
# --------------------------------------------
@dataclass
class OrderResult:
    ok: bool
    id: Optional[str] = None
    status: Optional[str] = None
    filled_qty: Optional[str] = None
    avg_price: Optional[str] = None
    error: Optional[str] = None


# --------------------------------------------
# PaperBroker – סימולציה פנימית (לוג בלבד)
# --------------------------------------------
class PaperBroker:
    def __init__(self, paper: bool = True):
        self.paper = True
        self._positions: Dict[str, float] = {}

    def place_order(self, symbol: str, side: str, qty: float,
                    price: float | None, stop: float | None, take: float | None) -> OrderResult:
        # סימולציה: נעדכן "פוזיציה" בזיכרון כדי לאפשר מכירה אחרי קניה
        q = float(qty)
        if side == "buy":
            self._positions[symbol] = self._positions.get(symbol, 0.0) + q
        elif side == "sell":
            pos = self._positions.get(symbol, 0.0)
            if pos <= 0.0:
                log.warning("PaperBroker: no position for %s; skipping SELL", symbol)
                return OrderResult(ok=False, error="no_position_to_sell")
            self._positions[symbol] = max(0.0, pos - q)

        log.info("PaperBroker: %s %s x%.2f @ market (stop=%s, take=%s)",
                 side, symbol, q, stop, take)
        return OrderResult(ok=True, id="paper", status="filled",
                           filled_qty=str(q), avg_price=str(price or 0.0))

    # לא חובה – עוזר ל־worker לבדוק קיימת פוזיציה
    def get_position_qty(self, symbol: str) -> float:
        return float(self._positions.get(symbol, 0.0))


# --------------------------------------------
# AlpacaBroker – עבודה מול Alpaca REST
#   כולל:
#   * ביטול הזמנות פתוחות בצד ההפוך למניעת 403 wash trade
#   * הימנעות מ-Sell כשאין פוזיציה
#   * הדפסת גוף השגיאה (JSON) כשיש HTTPError
#   * Content-Type: application/json
# --------------------------------------------
class AlpacaBroker:
    def __init__(self, paper: bool = True):
        self.paper = paper
        base = os.getenv("ALPACA_BASE_URL", "").strip()
        if not base:
            base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        # מקובל לעבוד מול /v2
        if not base.endswith("/v2"):
            base = base.rstrip("/") + "/v2"
        self.base = base

        self.key = os.getenv("ALPACA_KEY_ID") or os.getenv("APCA_API_KEY_ID")
        self.secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

        if not self.key or not self.secret:
            raise RuntimeError("Missing ALPACA_KEY_ID / ALPACA_SECRET_KEY")

        # כדי לא להפוך צד מהר מדי (חלק מה-rejectים)
        self._last_side_time: Dict[Tuple[str, str], float] = {}
        self._min_flip_secs = float(os.getenv("MIN_FLIP_SECS", "2"))

        log.info("AlpacaBroker initialized (paper=%s, base=%s)", paper, self.base)

    # ---------- כלי עזר HTTP ----------
    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key,
            "APCA-API-SECRET-KEY": self.secret,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body: dict | None = None
                 ) -> Tuple[int, str, Optional[dict]]:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    return resp.getcode(), raw, json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    return resp.getcode(), raw, None
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            log.error("Alpaca HTTPError %s %s | body=%s", e.code, path, err_body)
            raise
        except Exception:
            log.exception("Alpaca request failed %s %s", method, path)
            raise

    # ---------- מידע עזר ----------
    def get_position_qty(self, symbol: str) -> float:
        try:
            _, _, js = self._request("GET", f"/positions/{symbol.upper()}")
            if js and "qty" in js:
                return float(js["qty"])
        except urllib.error.HTTPError as e:
            # 404 כשאין פוזיציה – נחשב 0
            if e.code == 404:
                return 0.0
            raise
        return 0.0

    def _get_open_orders(self, symbol: Optional[str] = None) -> List[dict]:
        q = ""
        if symbol:
            q = f"?symbols={symbol.upper()}"
        _, _, js = self._request("GET", f"/orders{q}")
        return js or []

    def _cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"/orders/{order_id}")

    def _cancel_opposite_open_orders(self, symbol: str, side: str) -> None:
        opposite = "sell" if side == "buy" else "buy"
        orders = self._get_open_orders(symbol)
        for o in orders:
            if o.get("side") == opposite and o.get("status") in {"new", "accepted"}:
                log.info("Cancel opposite open order %s (%s %s)", o.get("id"), opposite, symbol)
                try:
                    self._cancel_order(o["id"])
                except Exception:
                    log.exception("Failed to cancel opposite order %s", o.get("id"))

    # ---------- שליחת הזמנה ----------
    def place_order(self, symbol: str, side: str, qty: float,
                    price: float | None, stop: float | None, take: float | None) -> OrderResult:

        symbol = symbol.upper()
        now = time.time()
        last_key = (symbol, side)
        if now - self._last_side_time.get(last_key, 0.0) < self._min_flip_secs:
            log.warning("Flip too fast: %s %s – throttled", side, symbol)
            return OrderResult(ok=False, error="flip_throttled")

        # אל תמכרי אם אין פוזיציה (אלא אם את *באמת* רוצה Short)
        if side == "sell":
            pos = self.get_position_qty(symbol)
            if pos <= 0.0:
                log.warning("No position in %s; skipping SELL to avoid unintended short.", symbol)
                return OrderResult(ok=False, error="no_position_to_sell")

        # לבטל הזמנות בצד ההפוך כדי למנוע wash trade
        self._cancel_opposite_open_orders(symbol, side)

        payload = {
            "symbol": symbol,
            "qty": str(max(1, int(round(qty)))) if qty >= 1 else "1",
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }

        try:
            code, raw, js = self._request("POST", "/orders", body=payload)
            # תגובות הצלחה של Alpaca הן 200/201 עם JSON הזמנה
            if js and "id" in js:
                self._last_side_time[last_key] = now
                return OrderResult(
                    ok=True,
                    id=str(js.get("id")),
                    status=str(js.get("status")),
                    filled_qty=str(js.get("filled_qty")),
                    avg_price=str(js.get("filled_avg_price")),
                )

            # אם לא קיבלנו JSON – נחזיר מה שיש
            return OrderResult(ok=200 <= code < 300, error=raw)

        except urllib.error.HTTPError as e:
            # ה-body הודפס כבר ב-log, אבל נחזיר גם ל-caller
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                body = str(e)
            return OrderResult(ok=False, error=f"HTTP {e.code}: {body}")
        except Exception as ex:
            return OrderResult(ok=False, error=str(ex))