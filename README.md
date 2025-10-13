# Trading Bot API (FastAPI)

API פשוט שמחשב אינדיקטורים (RSI / MACD / ATR) ומספק איתותי Buy / Sell / Hold  
כולל חישוב גודל פוזיציה וסטופ לפי רמת סיכון.

---

## 📦 התקנה והרצה מקומית

```bash
python -m venv .venv
source .venv/bin/activate     # הפעלת סביבה וירטואלית
pip install -r requirements.txt
python -m uvicorn main:app --reload