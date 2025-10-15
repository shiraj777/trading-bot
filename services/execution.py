# services/execution.py

from dataclasses import dataclass
from typing import Optional

@dataclass
class OrderResult:
    ok: bool
    id: Optional[str] = None
    message: str = ""

def place_order(
    side: str,
    qty: int,
    ticker: str,
    *,
    paper: bool = True,
    price: Optional[float] = None,
    stop: Optional[float] = None,
    take: Optional[float] = None,
    **kwargs,  # כדי להיות future-proof אם יישלחו פרמטרים נוספים
) -> OrderResult:
    """
    Paper/live execution shim.
    כרגע: מצב נייר בלבד – מדפיס ומחזיר תוצאה מוצלחת.
    side: 'buy' | 'sell'
    qty: כמות
    ticker: הסימבול (למשל 'TSLA')
    price: מחיר כניסה (אם None – שוק)
    stop: סטופ-לוס
    take: טייק-פרופיט
    """

    if paper:
        # הדפסה נחמדה שתראי את כל הפרטים
        print(
            f"[PAPER] {side.upper()} {qty}x {ticker} "
            f"@ {price if price is not None else 'MKT'} | "
            f"SL={stop if stop is not None else '-'} | "
            f"TP={take if take is not None else '-'}"
        )
        return OrderResult(ok=True, id="paper-ord-1", message="simulated")

    # פה מחברים לברוקר אמיתי (IBKR/Alpaca וכו') אם תרצי בהמשך
    # לדוגמה: לקרוא ל־REST API של הברוקר, ולבנות OrderResult לפי התגובה.
    raise NotImplementedError("Live trading is not implemented yet")