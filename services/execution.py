# services/execution.py
from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
import requests

# ---------- Order result ----------
@dataclass
class OrderResult:
    ok: bool
    id: Optional[str] = None
    status: Optional[str] = None
    filled_qty: Optional[float] = None
    avg_price: Optional[float] = None
    error: Optional[str] = None

# ---------- helpers ----------
_DECIMALS = int(os.getenv("DECIMAL_PLACES", "2"))
_PRICE_TICK = float(os.getenv("PRICE_TICK", "0.01"))

def _round_price(x: float) -> float:
    if _PRICE_TICK > 0:
        x = round(x / _PRICE_TICK) * _PRICE_TICK
    return round(x, _DECIMALS)

# ======================================================================
#                           SIMULATED BROKER
# ======================================================================
class PaperBroker:
    """ברוקר סימולטיבי לשימוש כשאין מפתחות Alpaca."""
    def __init__(self, paper: bool = True, **_: Any) -> None:
        self.paper = True
        self._pos: Dict[str, float] = {}
        self.log = logging.getLogger("PaperBroker")

    def position_qty(self, symbol: str) -> float:
        return float(self._pos.get(symbol.upper(), 0.0))

    def _sim_apply(self, symbol: str, side: str, qty: int) -> None:
        s = symbol.upper()
        pos = self._pos.get(s, 0.0)
        pos = pos + qty if side == "buy" else pos - qty
        self._pos[s] = pos

    def place_market(self, symbol: str, side: str, qty: int, tif: str = "day") -> OrderResult:
        self._sim_apply(symbol, side, qty)
        oid = f"sim-mkt-{int(time.time()*1000)}"
        self.log.info("[SIM] MARKET %s %s x%s tif=%s -> id=%s", side, symbol, qty, tif, oid)
        return OrderResult(ok=True, id=oid, status="accepted", filled_qty=float(qty), avg_price=None)

    def place_bracket(
        self, symbol: str, side: str, qty: int, entry_price: float, tp_pct: float, sl_pct: float, tif: str = "day"
    ) -> OrderResult:
        tp = _round_price(entry_price * (1 + tp_pct if side == "buy" else 1 - tp_pct))
        sl = _round_price(entry_price * (1 - sl_pct if side == "buy" else 1 + sl_pct))
        self._sim_apply(symbol, side, qty)
        oid = f"sim-bracket-{int(time.time()*1000)}"
        self.log.info("[SIM] BRACKET %s %s x%s entry=%.4f tp=%.4f sl=%.4f tif=%s -> id=%s",
                      side, symbol, qty, entry_price, tp, sl, tif, oid)
        return OrderResult(ok=True, id=oid, status="accepted", filled_qty=float(qty), avg_price=entry_price)

# ======================================================================
#                              ALPACA BROKER
# ======================================================================
class AlpacaBroker:
    """עטיפה פשוטה ל-Alpaca REST."""
    def __init__(self, paper: bool = True, key_id: Optional[str] = None,
                 secret_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
        self.paper = paper
        self.base_url = (base_url or "").rstrip("/") or (
            "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        )
        if not key_id or not secret_key:
            raise ValueError("AlpacaBroker: key_id/secret_key are required")

        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.log = logging.getLogger("AlpacaBroker")

    def _url(self, path: str) -> str:
        return f"{self.base_url}/v2/{path.lstrip('/')}"

    def _handle(self, resp: requests.Response) -> Dict[str, Any]:
        try:
            data = resp.json()
        except Exception:
            resp.raise_for_status()
            return {"ok": True, "raw": resp.text}
        if 200 <= resp.status_code < 300:
            return data
        msg = data.get("message") if isinstance(data, dict) else str(data)
        raise requests.HTTPError(f"HTTP {resp.status_code}: {msg}", response=resp)

    def position_qty(self, symbol: str) -> float:
        try:
            data = self._handle(self.session.get(self._url(f"positions/{symbol}"), timeout=30))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return 0.0
            raise
        try:
            return float(data.get("qty", 0))
        except Exception:
            return 0.0

    def _post_order(self, payload: Dict[str, Any]) -> OrderResult:
        try:
            j = self._handle(self.session.post(self._url("orders"), json=payload, timeout=30))
            return OrderResult(
                ok=True,
                id=j.get("id"),
                status=j.get("status"),
                filled_qty=float(j.get("filled_qty") or 0),
                avg_price=float(j.get("filled_avg_price") or 0) if j.get("filled_avg_price") else None,
            )
        except requests.HTTPError as e:
            try:
                err = e.response.json().get("message")  # type: ignore
            except Exception:
                err = str(e)
            return OrderResult(ok=False, error=err)

    def place_market(self, symbol: str, side: str, qty: int, tif: str = "day") -> OrderResult:
        payload = {
            "symbol": symbol.upper(), "qty": qty, "side": side,
            "type": "market", "time_in_force": tif,
        }
        self.log.info("Alpaca MARKET submit: %s", payload)
        return self._post_order(payload)

    def place_bracket(
        self, symbol: str, side: str, qty: int, entry_price: float, tp_pct: float, sl_pct: float, tif: str = "day"
    ) -> OrderResult:
        take_profit_price = _round_price(entry_price * (1 + tp_pct if side == "buy" else 1 - tp_pct))
        stop_loss_price   = _round_price(entry_price * (1 - sl_pct if side == "buy" else 1 + sl_pct))
        payload = {
            "symbol": symbol.upper(),
            "qty": qty,
            "side": side,
            "type": "market",
            "time_in_force": tif,
            "order_class": "bracket",
            "take_profit": {"limit_price": take_profit_price},
            "stop_loss":   {"stop_price":  stop_loss_price},
        }
        self.log.info("Alpaca BRACKET submit: %s", payload)
        return self._post_order(payload)