# syntax=docker/dockerfile:1

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# כלי build בסיסיים (לבניית wheels) + ניקוי שכבת apt
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# התקנת תלויות פייתון
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt \
 # חיזוק מפני מחיקה בטעות מה-reqs:
 && pip install yfinance ta

# אימות שהתלויות נטענות באמת בתוך התמונה
RUN python - <<'PY'
import sys
pkgs = ["yfinance", "ta", "fastapi", "uvicorn", "pandas", "numpy", "scikit-learn", "python-dotenv"]
ok = True
for p in pkgs:
    mod = "sklearn" if p == "scikit-learn" else p
    try:
        __import__(mod)
    except Exception as e:
        print(f"FAILED import: {p}: {e}", file=sys.stderr)
        ok = False
if not ok:
    sys.exit(1)
print("Deps OK")
PY

# קוד האפליקציה
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]