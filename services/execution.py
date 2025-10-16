# services/execution.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

import urllib.request
import urllib.error


@dataclass
class OrderResult:
    ok: bool
    id: Optional[str] = None
    status: Optional[str] = None
    filled_qty: Optional[str] = None
    avg_price: Optional[str] = None
    error: Optional[str] = None


# -------------------- Paper (סימולציה) --------------------
class PaperBroker:
    def __init__(self, paper: bool = True):
        self.paper = paper

    def place_order(
        self, symbol: str, side: str, qty: int, price: float, stop: float, take: float
    ) -> OrderResult:
        # סימולציה בלבד – כאילו בוצע
        return OrderResult(ok=True, id="paper-sim", status="accepted",
                           filled_qty="0", avg_price=str(price))


# -------------------- Alpaca --------------------
class AlpacaBroker:
    """
    Broker ל-Alpaca. קורא ישירות ל-REST API עם urllib (בלי תלות חיצונית).
    תומך ב-base_url, key_id, secret_key (עם ברירת־מחדל מ-env).
    """

    def __init__(
        self,
        paper: bool = True,
        key_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.paper = paper
        self.key_id = key_id or os.getenv("ALPACA_KEY_ID") or ""
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY") or ""
        # שימי לב: אנחנו שומרים את הבסיס בלי '/' בסוף
        default_base = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        self.base = (base_url or default_base).rstrip("/")

        if not self.key_id or not self.secret_key:
            raise ValueError("ALPACA_KEY_ID / ALPACA_SECRET_KEY not provided")

    # url /v2/orders – גם אם כתבו base עם/בלי /v2
    def _orders_url(self) -> str:
        if self.base.endswith("/v2"):
            return f"{self.base}/orders"
        return f"{self.base}/v2/orders"

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json",
        }

    def place_order(
        self, symbol: str, side: str, qty: int, price: float, stop: float, take: float
    ) -> OrderResult:
        url = self._orders_url()

        # הזמנה בסיסית (Market, DAY). אם יש גם stop וגם take – נשתמש ב-bracket.
        payload: dict = {
            "symbol": symbol,
            "qty": qty,
            "side": side,           # "buy" / "sell"
            "type": "market",
            "time_in_force": "day",
        }

        if stop and take and stop > 0 and take > 0:
            payload["order_class"] = "bracket"
            payload["take_profit"] = {"limit_price": round(float(take), 4)}
            payload["stop_loss"] = {"stop_price": round(float(stop), 4)}

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                obj = json.loads(body)
                return OrderResult(
                    ok=True,
                    id=obj.get("id"),
                    status=obj.get("status"),
                    filled_qty=obj.get("filled_qty"),
                    avg_price=obj.get("filled_avg_price"),
                )
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                err_body = str(e)
            return OrderResult(ok=False, error=f"HTTP {e.code}: {err_body}")
        except Exception as e:
            return OrderResult(ok=False, error=str(e))