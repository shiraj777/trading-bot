# services/execution.py
from __future__ import annotations
import os, time, logging, json, urllib.request
from dataclasses import dataclass
from typing import Optional, Dict, Any

log = logging.getLogger("execution")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")


# -----------------------------
# תוצאת שליחת הוראה (אחיד לכל ברוקר)
# -----------------------------
@dataclass
class OrderResult:
    ok: bool
    id: Optional[str] = None
    status: str = "accepted"
    filled_qty: int = 0
    avg_price: Optional[float] = None
    raw: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# -----------------------------
# בסיס לכל ברוקר
# -----------------------------
class BaseBroker:
    def __init__(self, paper: bool = True):
        self.paper = paper

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        stop: Optional[float] = None,
        take: Optional[float] = None,
    ) -> OrderResult:
        raise NotImplementedError


# -----------------------------
# סימולציה (Paper פנימי)
# -----------------------------
class PaperBroker(BaseBroker):
    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        stop: Optional[float] = None,
        take: Optional[float] = None,
    ) -> OrderResult:
        log.info("[PAPER] %s %s x%d @ %.4f (SL=%s, TP=%s)",
                 side.upper(), symbol.upper(), qty, price,
                 f"{stop:.4f}" if stop is not None else "-",
                 f"{take:.4f}" if take is not None else "-")
        return OrderResult(
            ok=True,
            id=f"paper-{int(time.time())}",
            status="filled",
            filled_qty=qty,
            avg_price=price,
            raw={
                "symbol": symbol.upper(),
                "side": side,
                "qty": qty,
                "price": price,
                "stop": stop,
                "take": take,
                "paper": True,
            },
        )


# -----------------------------
# Alpaca (Paper/Live דרך API)
# -----------------------------
class AlpacaBroker(BaseBroker):
    """
    ברוקר Alpaca. ניתן להזין מפתחות ו-base_url ישירות לבנאי,
    או להשאיר ריק כדי לטעון מ-ENV:
      ALPACA_KEY_ID, ALPACA_SECRET_KEY, ALPACA_BASE_URL, ALPACA_PAPER
    הערה: ALPACA_BASE_URL צריך להיות בלי /v2 בסוף (הקלאס מוסיף לבד).
           לדוגמה: https://paper-api.alpaca.markets
    """

    def __init__(
        self,
        paper: Optional[bool] = None,
        key_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 15,
    ):
        # קרא ברירת־מחדל מה-ENV אם לא סופק בבנאי
        env_key_id = os.getenv("ALPACA_KEY_ID") or os.getenv("ALPACA_API_KEY")
        env_secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_API_SECRET")
        env_base   = os.getenv("ALPACA_BASE_URL", "").strip()
        env_paper  = (os.getenv("ALPACA_PAPER", "true").lower() == "true")

        self.key_id     = (key_id or env_key_id or "").strip()
        self.secret_key = (secret_key or env_secret or "").strip()
        self.base_url   = (base_url or env_base or "https://paper-api.alpaca.markets").rstrip("/")
        self.paper      = env_paper if paper is None else bool(paper)
        self.timeout    = timeout

        # נרמול base_url – בלי /v2 (נוסיף לבד)
        if self.base_url.endswith("/v2"):
            self.base_url = self.base_url[:-3]

        if not self.key_id or not self.secret_key:
            raise RuntimeError("Missing Alpaca credentials (ALPACA_KEY_ID/SECRET_KEY).")

        self.orders_url = f"{self.base_url}/v2/orders"
        self.account_url = f"{self.base_url}/v2/account"

        log.info("AlpacaBroker initialized (paper=%s, base=%s)", self.paper, self.base_url)

    # --------- helpers ---------
    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = None if json_body is None else json.dumps(json_body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    j = json.loads(raw)
                except Exception:
                    if 200 <= resp.status < 300:
                        return {"ok": True, "raw": raw}
                    raise
                if 200 <= resp.status < 300:
                    return j
                msg = j.get("message") if isinstance(j, dict) else raw
                raise RuntimeError(f"Alpaca API error [{resp.status}]: {msg}")
        except Exception as e:
            log.exception("Alpaca request failed (%s %s)", method, url)
            raise

    # חשבון (בדיקה/מידע)
    def get_account(self) -> Dict[str, Any]:
        return self._request("GET", self.account_url)

    # שליחת הוראת שוק פשוטה (market). אפשר להרחיב ל-limit/oco בהמשך.
    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        stop: Optional[float] = None,
        take: Optional[float] = None,
    ) -> OrderResult:
        payload: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "qty": qty,
            "side": side.lower(),           # 'buy' או 'sell'
            "type": "market",               # לשלב ראשון: Market
            "time_in_force": "day",
        }

        try:
            res = self._request("POST", self.orders_url, json_body=payload)
            oid    = res.get("id")
            status = (res.get("status") or "accepted").lower()
            filled = int((res.get("filled_qty") or "0").split(".")[0])
            avg    = res.get("filled_avg_price")
            avg_f  = float(avg) if avg not in (None, "", 0) else None

            # לוג ידידותי
            log.info("[ALPACA %s] %s x%d submitted (id=%s, status=%s, avg=%s)",
                     side.upper(), symbol.upper(), qty, oid, status, avg)

            return OrderResult(
                ok=True,
                id=oid,
                status=status,
                filled_qty=filled,
                avg_price=avg_f,
                raw=res,
            )
        except Exception as e:
            log.warning("alpaca order failed: %s", e)
            return OrderResult(ok=False, error=str(e))