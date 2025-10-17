# services/risk.py
import math
import os
from typing import Tuple

def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except Exception:
        return default

# כל הפרמטרים נשלטים מה-ENV:
#  - RISK_PCT או MAX_RISK_PCT (אותו דבר – נקרא לפי זמינות)
#  - ATR_STOP_MULT (לצורכי חישוב stop/take ללוג בלבד)
#  - ATR_TAKE_MULT
RISK_PCT      = _env_float("RISK_PCT", _env_float("MAX_RISK_PCT", 0.002))  # 0.2% כברירת מחדל
ATR_STOP_MULT = _env_float("ATR_STOP_MULT", 1.0)
ATR_TAKE_MULT = _env_float("ATR_TAKE_MULT", 2.0)

def position_size(equity: float, atr: float, price: float,
                  risk_pct: float = RISK_PCT) -> Tuple[int, float, float]:
    """
    חישוב גודל פוזיציה לפי ATR: מסכנים אחוז קבוע מההון (מה-ENV).
    stop/take מחושבים ע"י מכפילי ATR (גם מה-ENV) – לצורך לוג/תצוגה בלבד.
    ה-Broker מגביל גודל פקודה בפועל לפי buying power.
    """
    if atr is None or price is None:
        return 0, 0.0, 0.0
    if atr <= 0 or price <= 0 or equity <= 0 or risk_pct <= 0:
        return 0, 0.0, 0.0

    risk_amount = equity * risk_pct
    per_share_risk = atr if atr > 0 else price * 0.01  # fallback סביר
    qty = int(max(0, math.floor(risk_amount / per_share_risk)))

    stop = max(price - (ATR_STOP_MULT * atr), 0.0)
    take = price + (ATR_TAKE_MULT * atr)

    return qty, float(stop), float(take)