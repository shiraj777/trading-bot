# services/execution.py
from __future__ import annotations
import os, json, logging
from dataclasses import dataclass
import requests

log = logging.getLogger("execution")

@dataclass
class OrderResult:
    ok: bool
    id: str | None = None
    status: str | None = None
    filled_qty: float | None = None
    avg_price: float | None = None
    error: str | None = None

class PaperBroker:
    """ברוקר סימולטיבי (לוג בלבד)."""
    def __init__(self, paper: bool = True):
        self.paper = paper

    def place_order(self, symbol: str, side: str, qty: float, price: float, stop: float, take: float) -> OrderResult:
        log.info("[PAPER %s] %s %s @%.2f (stop=%.2f take=%.2f)",
                 "BUY" if side=="buy" else "SELL", qty, symbol, price, stop, take)
        return OrderResult(ok=True, id="paper-sim", status="accepted", filled_qty=0.0, avg_price=None)

class AlpacaBroker:
    """
    ברוקר Alpaca.
    אפשר להעביר key_id/secret_key/ base בבנאי, ואם לא – נלקח מה-ENV:
      ALPACA_KEY_ID / APCA_API_KEY_ID
      ALPACA_SECRET_KEY / APCA_API_SECRET_KEY
      ALPACA_BASE_URL (או ברירת מחדל לפי paper)
    """
    def __init__(
        self,
        paper: bool = True,
        key_id: str | None = None,
        secret_key: str | None = None,
        base: str | None = None,
        allow_short: bool = False,
    ):
        self.paper = paper
        self.allow_short = allow_short

        # בסיס URL
        self.base = (
            base
            or os.getenv("ALPACA_BASE_URL")
            or ("https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets")
        )

        # מפתחות
        self.key_id = key_id or os.getenv("ALPACA_KEY_ID") or os.getenv("APCA_API_KEY_ID")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
        if not self.key_id or not self.secret_key:
            raise RuntimeError("Missing Alpaca API keys (ALPACA_KEY_ID / ALPACA_SECRET_KEY)")

        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        log.info("AlpacaBroker initialized (paper=%s, base=%s)", self.paper, self.base)

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def place_order(self, symbol: str, side: str, qty: float, price: float, stop: float, take: float) -> OrderResult:
        """
        שולח הזמנה Market/Day פשוטה. (את ה-stop/take אנחנו לא מצרפים כאן כדי להימנע מ-403 ‘wash trade’/ניגוד)
        """
        # הגנה בסיסית: לא לאפשר short אם כבוי
        if side == "sell" and not self.allow_short:
            # אם אין פוזיציה – Reject “שורט”
            try:
                pos = self.session.get(self._url(f"/v2/positions/{symbol}")).json()
                if "symbol" not in pos:  # אין פוזיציה פתוחה
                    return OrderResult(ok=False, error="short not allowed and no long position to close")
            except Exception:
                pass  # אם נכשל – ניתן לאלפאקה להחליט

        url = self._url("/v2/orders")
        data = {
            "symbol": symbol,
            "qty": max(1, int(qty)),
            "side": "buy" if side == "buy" else "sell",
            "type": "market",
            "time_in_force": "day",
        }

        try:
            r = self.session.post(url, data=json.dumps(data), timeout=10)
            if r.status_code >= 400:
                # דוגמאות נפוצות: 401/403, wash-trade וכו׳
                try:
                    body = r.json()
                except Exception:
                    body = {"message": r.text}
                msg = body.get("message") or body
                log.warning("execution:alpaca order failed: HTTP %s: %s", r.status_code, msg)
                return OrderResult(ok=False, error=f"HTTP {r.status_code}: {msg}")

            body = r.json()
            return OrderResult(
                ok=True,
                id=body.get("id"),
                status=body.get("status"),
                filled_qty=float(body.get("filled_qty") or 0.0),
                avg_price=(float(body.get("filled_avg_price")) if body.get("filled_avg_price") else None),
            )
        except Exception as e:
            log.exception("alpaca request failed (POST /v2/orders)")
            return OrderResult(ok=False, error=str(e))