# services/risk.py
import math

def position_size(equity: float, atr: float, price: float, risk_pct: float = 0.01):
    """
    חישוב גודל פוזיציה לפי ATR: מסכנים אחוז קבוע מההון לכל עסקה.
    stop ≈ 1*ATR מתחת למחיר כניסה, take ≈ 2*ATR מעל.
    """
    if atr is None or price is None:
        return 0, 0.0, 0.0
    if atr <= 0 or price <= 0 or equity <= 0 or risk_pct <= 0:
        return 0, 0.0, 0.0

    risk_amount = equity * risk_pct              # כמה דולרים מסכנים בעסקה
    per_share_risk = atr                         # סיכון למניה ≈ ATR אחד
    if per_share_risk <= 0:
        return 0, 0.0, 0.0

    qty = math.floor(risk_amount / per_share_risk)
    stop = max(price - atr, 0.0)
    take = price + 2.0 * atr
    return max(qty, 0), float(stop), float(take)