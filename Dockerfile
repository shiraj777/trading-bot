FROM python:3.11-slim

# סביבת פייתון בריאה
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 1) חבילות מערכת דרושות: build + libgomp ל-scikit-learn
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libgomp1 \
 && rm -rf /var/lib/apt/lists/*

# 2) ספריות פייתון
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
 # אגרסיבי – גם אם בטעות ימחקו מה-requirements, נבטיח שהן בפנים
 && pip install --no-cache-dir yfinance ta

# 3) אימות ייבוא חבילות בזמן build (עוצר את ה-build אם משהו נשבר)
RUN python - <<'PY'
import sys
pkgs = ["yfinance","ta","fastapi","uvicorn","pandas","numpy","scikit-learn","python-dotenv"]
ok = True
for p in pkgs:
    mod = "sklearn" if p == "scikit-learn" else p
    try:
        __import__(mod)
    except Exception as e:
        ok = False
        print(f"FAILED import: {p}: {e}", file=sys.stderr)
if not ok:
    sys.exit(1)
print("Deps OK")
PY

# 4) קוד האפליקציה
COPY . .

EXPOSE 8000
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000"]