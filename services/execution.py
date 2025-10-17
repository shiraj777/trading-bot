# services/execution.py
import requests, logging, time

class AlpacaBroker:
    def __init__(self, paper=True, key_id=None, secret_key=None, base_url=None):
        self.paper = paper
        self.key_id = key_id
        self.secret_key = secret_key
        self.base_url = base_url or (
            "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        )
        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "Content-Type": "application/json"
        })
        self.log = logging.getLogger("AlpacaBroker")

    # ------------------------------------------------------------------
    def place_bracket(self, symbol, side, qty, entry_price, tp_pct, sl_pct):
        """
        שולח פקודת BRACKET מסודרת ל-Alpaca.
        entry_price חובה.
        """
        take_profit_price = round(entry_price * (1 + tp_pct if side == "buy" else 1 - tp_pct), 2)
        stop_loss_price   = round(entry_price * (1 - sl_pct if side == "buy" else 1 + sl_pct), 2)

        data = {
            "symbol": symbol,
            "qty": int(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",  # Bracket תמיד DAY ב-Alpaca
            "order_class": "bracket",
            "take_profit": {"limit_price": take_profit_price},
            "stop_loss": {"stop_price": stop_loss_price},
        }

        url = f"{self.base_url}/v2/orders"
        self.log.info("Alpaca BRACKET submit: %s", data)

        for attempt in range(3):
            resp = self.session.post(url, json=data)
            if resp.status_code == 200 or resp.status_code == 201:
                return SimpleResponse(True, data.get("symbol"), "", resp.json().get("id"))
            elif resp.status_code == 403:
                self.log.error("Alpaca 403: insufficient buying power or qty — attempt %s", attempt+1)
                time.sleep(1)
                continue
            else:
                self.log.error("Alpaca error %s: %s", resp.status_code, resp.text)
                return SimpleResponse(False, data.get("symbol"), resp.text)
        return SimpleResponse(False, data.get("symbol"), "HTTP 403 repeated")

    # ------------------------------------------------------------------
    def place_market(self, symbol, side, qty, tif="day"):
        """
        שליחת פקודת MARKET רגילה
        """
        data = {
            "symbol": symbol,
            "qty": int(qty),
            "side": side,
            "type": "market",
            "time_in_force": tif,
        }
        url = f"{self.base_url}/v2/orders"
        self.log.info("Alpaca MARKET submit: %s", data)
        resp = self.session.post(url, json=data)
        ok = resp.status_code in (200, 201)
        return SimpleResponse(ok, symbol, resp.text if not ok else "", None)