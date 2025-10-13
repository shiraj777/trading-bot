FROM python:3.11-slim

# שכבת מערכת בסיסית כדי לא להיתקע בגלל build wheels (נדרש ל-ta/yfinance, אבל בטוח)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# התקנת תלויות
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir yfinance ta \
    && python - << 'PY'
import sys
for p in ["yfinance","ta","fastapi","uvicorn","pandas","numpy"]:
    __import__(p)
print("Deps OK")
PY

# קוד האפליקציה
COPY . .

EXPOSE 8000
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000"]