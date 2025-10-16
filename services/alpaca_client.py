# services/alpaca_client.py
from __future__ import annotations

import os
import logging
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv


class AlpacaClient:
    """
    לקוח פשוט לעבודה מול Alpaca (Paper/Live) באמצעות REST.
    - טוען משתני סביבה (.env)
    - בודק חיבור
    - מחזיר מידע על חשבון/שוק
    - שולח פקודות קנייה/מכירה
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        load_dotenv()  # טען .env אם קיים לוקאלית

        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.api_secret = api_secret or os.getenv("ALPACA_API_SECRET")
        # חשוב: בלי /v2 כאן. אנו נוסיף אותו בהמשך לנתיבים.
        self.base_url = (base_url or os.getenv("ALPACA_BASE_URL") or "").rstrip("/")

        if not self.api_key or not self.api_secret or not self.base_url:
            raise ValueError(
                "חסר אחד מהמשתנים: ALPACA_API_KEY / ALPACA_API_SECRET / ALPACA_BASE_URL"
            )

        self.session = session or requests.Session()
        self.headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        # לוג בסיסי
        self.log = logging.getLogger(self.__class__.__name__)
        if not self.log.handlers:
            logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # --------- כלי עזר --------- #
    def _url(self, path: str) -> str:
        """בנה URL מלא. תמיד ניגשים ל־/v2/..."""
        path = path.lstrip("/")
        return f"{self.base_url}/v2/{path}"

    def _get(self, path: str, **kwargs) -> Dict[str, Any]:
        resp = self.session.get(self._url(path), headers=self.headers, timeout=30, **kwargs)
        return self._handle(resp)

    def _post(self, path: str, json: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        resp = self.session.post(self._url(path), headers=self.headers, json=json, timeout=30, **kwargs)
        return self._handle(resp)

    def _delete(self, path: str, **kwargs) -> Dict[str, Any]:
        resp = self.session.delete(self._url(path), headers=self.headers, timeout=30, **kwargs)
        return self._handle(resp)

    @staticmethod
    def _handle(resp: requests.Response) -> Dict[str, Any]:
        try:
            data = resp.json()
        except Exception:
            resp.raise_for_status()
            # אם לא JSON אבל סטטוס OK:
            return {"ok": True, "raw": resp.text}

        if 200 <= resp.status_code < 300:
            return data

        # שגיאה קריאה וברורה
        msg = data.get("message") if isinstance(data, dict) else None
        raise requests.HTTPError(
            f"Alpaca API error [{resp.status_code}]: {msg or data}", response=resp
        )

    # --------- פעולות עיקריות --------- #
    def check_connection(self) -> Dict[str, Any]:
        """
        בדיקת חשבון בסיסית. ב-Paper אמור לחזור אובייקט חשבון.
        """
        data = self._get("account")
        self.log.info("Connected to Alpaca. Account ID: %s | Status: %s", data.get("id"), data.get("status"))
        return data

    def get_clock(self) -> Dict[str, Any]:
        """מצב השעון (האם השוק פתוח, זמן שרת וכו')."""
        return self._get("clock")

    def get_positions(self) -> Dict[str, Any]:
        """רשימת הפוזיציות הפתוחות."""
        return self._get("positions")

    def cancel_all_orders(self) -> Dict[str, Any]:
        """ביטול כל ההוראות הפתוחות."""
        return self._delete("orders")

    def submit_order(
        self,
        symbol: str,
        qty: int,
        side: str = "buy",           # 'buy' / 'sell'
        order_type: str = "market",  # 'market' / 'limit' / ...
        time_in_force: str = "day",  # 'day' / 'gtc' / ...
        limit_price: Optional[float] = None,
        **extra,
    ) -> Dict[str, Any]:
        """
        שליחת הוראת קנייה/מכירה פשוטה.
        """
        payload: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "qty": qty,
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if order_type == "limit":
            if limit_price is None:
                raise ValueError("limit_price נדרש בהוראת LIMIT")
            payload["limit_price"] = limit_price

        payload.update(extra or {})
        self.log.info("Submitting order: %s", payload)
        return self._post("orders", json=payload)


# הרצה לבדיקה מקומית:
if __name__ == "__main__":
    client = AlpacaClient()
    acct = client.check_connection()
    print("Equity:", acct.get("equity"), "| Buying Power:", acct.get("buying_power"))

    clock = client.get_clock()
    print("Market open:", clock.get("is_open"), "| Next open:", clock.get("next_open"))