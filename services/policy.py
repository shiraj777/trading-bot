# services/policy.py
import pandas as pd
from dataclasses import dataclass

@dataclass
class Decision:
    side: str   # "buy" | "sell" | "hold"
    score: float
    reason: str

def decide(df: pd.DataFrame) -> Decision:
    """
    לוגיקה פשוטה לאות מסחר:
    - קנייה: RSI נמוך ו-MACD משתפר.
    - מכירה: RSI גבוה ו-MACD מתדרדר.
    - אחרת: החזק.
    """
    last = df.iloc[-1]
    prev = df.iloc[-2]

    rsi = last["rsi"]
    dh, dh_prev = last["macd_hist"], prev["macd_hist"]

    # BUY: RSI נמוך + MACD עולה
    if rsi < 35 and dh > dh_prev and dh > -0.2:
        score = max(0.2, min(1.0, (35 - rsi) / 35 + (dh - dh_prev)))
        return Decision("buy", round(float(score), 3),
                        f"RSI={rsi:.1f} נמוך ו-MACD משתפר")

    # SELL: RSI גבוה + MACD יורד
    if rsi > 65 and dh < dh_prev and dh < 0.2:
        score = -max(0.2, min(1.0, (rsi - 65) / 35 + (dh_prev - dh)))
        return Decision("sell", round(float(score), 3),
                        f"RSI={rsi:.1f} גבוה ו-MACD יורד")

    # אחרת – אין איתות חזק
    return Decision("hold", 0.0, "אין איתות חזק")