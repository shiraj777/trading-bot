# ---- Base image ----
FROM python:3.11-slim

# ---- Set working directory ----
WORKDIR /app

# ---- Copy dependency files ----
COPY requirements.txt .

# ---- Install dependencies ----
RUN pip install --no-cache-dir -r requirements.txt

# ---- Copy the rest of the code ----
COPY . .

# ---- Environment configuration ----
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

# ---- Expose the port (Render expects $PORT) ----
EXPOSE 10000

# ---- Run the FastAPI app ----
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]