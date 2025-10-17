# services/execution.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

import requests
from services.alpaca_client import AlpacaClient


@dataclass
class ExecResult:
    ok: bool
    id: Optional[str] = None
    status: Optional[str] = None
    filled_qty: Optional[str] = None
    avg_price: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------
# Paper (simulated) broker
# ---------------------------------------------------------------------
class PaperBroker:
    """
    סימולטור פשוט לתהליכי שליחה/פוזיציה.
    """
    def __init__(self, paper: bool = True) -> None:
        self.paper = paper
        self.log = logging.getLogger(self.__class__.__name__)
        self._pos: Dict[str, float] = {}  # symbol -> qty float

    # --- ממשקים משותפים ---
    def position_qty(self, symbol: str) -> float:
        return float(self._pos.get(symbol.upper(), 0.0))

    def _apply_pos(self, symbol: str, side: str, qty: int):
        s = symbol.upper()
        cur = self._pos.get(s, 0.0)
        if side == "buy":
            cur += qty
        else:
            cur -= qty
        self._pos[s] = cur

    def place_order(
        self, symbol: str, side: str, qty: int, price: float,
        stop: float, take: float
    ) -> ExecResult:
        self._apply_pos(symbol, side, qty)
        self.log.info(
            "[SIM] MARKET %s %s x%s @%.4f | stop=%.4f take=%.4f",
            side, symbol, qty, price, stop, take
        )
        return ExecResult(ok=True, id="sim-order-1", status="accepted",
                          filled_qty=str(qty), avg_price=f"{price:.4f}")

    def place_bracket(
        self, symbol: str, side: str, qty: int,
        entry_price: float, tp_pct: float, sl_pct: float
    ) -> ExecResult:
        # חישוב מחירי יעד לצורך לוגים בלבד
        if side == "buy":
            tp = entry_price * (1 + tp_pct)
            sl = entry_price * (1 - sl_pct)
        else:
            tp = entry_price * (1 - tp_pct)  # רווח בסל=ירידה
            sl = entry_price * (1 + sl_pct)  # SL בסל=עליה
        self._apply_pos(symbol, side, qty)
        self.log.info(
            "[SIM] BRACKET %s %s x%s @%.4f | tp=%.2f sl=%.2f (tp%%=%.3f sl%%=%.3f)",
            side, symbol, qty, entry_price, tp, sl, tp_pct*100, sl_pct*100
        )
        return ExecResult(ok=True, id="sim-bracket-1", status="accepted",
                          filled_qty=str(qty), avg_price=f"{entry_price:.4f}")


# ---------------------------------------------------------------------
# Alpaca broker (real REST)
# ---------------------------------------------------------------------
class AlpacaBroker:
    """
    עטיפה ל-Alpaca REST (Paper/Live) עם תמיכה ב-MARKET וב-BRACKET.
    """
    def __init__(
        self,
        paper: bool = True,
        key_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.paper = paper
        self.client = AlpacaClient(
            api_key=key_id,
            api_secret=secret_key,
            base_url=base_url,
            session=session,
        )
        self.log = logging.getLogger(self.__class__.__name__)

    # --------- עזר/ממשקים ---------
    def position_qty(self, symbol: str) -> float:
        """
        מנסה להביא פוזיציה ל-symbol. אם אין פוזיציה תחזור 0.
        """
        try:
            data = self.client._get(f"positions/{symbol.upper()}")
            qty = float(data.get("qty", 0.0))
            return qty
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return 0.0
            raise

    # --------- MARKET ---------
    def place_order(
        self, symbol: str, side: str, qty: int,
        price: float, stop: float, take: float
    ) -> ExecResult:
        payload = {
            "symbol": symbol.upper(),
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        try:
            self.log.info("Alpaca MARKET submit: %s", payload)
            data = self.client._post("orders", json=payload)
            return ExecResult(
                ok=True,
                id=data.get("id"),
                status=data.get("status"),
                filled_qty=data.get("filled_qty"),
                avg_price=data.get("filled_avg_price"),
            )
        except requests.HTTPError as e:
            err = self._extract_err(e)
            self.log.error("MARKET error: %s", err)
            return ExecResult(ok=False, error=err)

    # --------- BRACKET ---------
    def place_bracket(
        self,
        symbol: str,
        side: str,
        qty: int,
        entry_price: float,
        tp_pct: float,
        sl_pct: float,
    ) -> ExecResult:
        """
        שולח הזמנת BRACKET ל-Alpaca עם עיגול מחירים לפי 0.01 כדי למנוע sub-penny error.
        """
        symbol = symbol.upper()

        # חישוב מחירי TP/SL לפי צד
        if side == "buy":
            tp_price = entry_price * (1 + tp_pct)
            sl_price = entry_price * (1 - sl_pct)
        else:
            tp_price = entry_price * (1 - tp_pct)
            sl_price = entry_price * (1 + sl_pct)

        # ✅ עיגול לשתי ספרות אחרי הנקודה (0.01)
        tp_price = round(tp_price, 2)
        sl_price = round(sl_price, 2)

        if side == "buy":
            stop_loss = {"stop_price": sl_price}
            take_profit = {"limit_price": tp_price}
        else:
            stop_loss = {"stop_price": sl_price}
            take_profit = {"limit_price": tp_price}

        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": take_profit,
            "stop_loss": stop_loss,
        }

        try:
            self.log.info("Alpaca BRACKET submit: %s", payload)
            data = self.client._post("orders", json=payload)
            return ExecResult(
                ok=True,
                id=data.get("id"),
                status=data.get("status"),
                filled_qty=data.get("filled_qty"),
                avg_price=data.get("filled_avg_price"),
            )
        except requests.HTTPError as e:
            err = self._extract_err(e)
            self.log.error("BRACKET error: %s", err)
            return ExecResult(ok=False, error=err)

    # --------- helpers ---------
    @staticmethod
    def _extract_err(e: requests.HTTPError) -> str:
        try:
            j = e.response.json()
            msg = j.get("message", j)
        except Exception:
            msg = e.response.text if e.response is not None else str(e)
        return f"HTTP {getattr(e.response,'status_code', '???')}: {msg}"