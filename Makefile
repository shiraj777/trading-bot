# ==========================
# Trading Bot - Makefile
# ==========================

# הפעלת השרת לוקאלית עם FastAPI + Uvicorn
run:
	source .venv/bin/activate && uvicorn main:app --reload

# התקנת כל התלויות (כולל FastAPI ו-Uvicorn)
install:
	python3 -m venv .venv && source .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt fastapi uvicorn

# בדיקה שהכול תקין
check:
	@echo "Python version:" && python3 --version
	@echo "FastAPI version:" && python3 -m pip show fastapi | grep Version || echo "FastAPI not installed"
	@echo "Uvicorn version:" && python3 -m pip show uvicorn | grep Version || echo "Uvicorn not installed"

# ניקוי קבצי cache
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# העלאת קוד ל־GitHub + דיפלוי אוטומטי ל־Render
deploy:
	git add .
	git commit -m "Auto-deploy update"
	git push origin main
	@echo "🚀 Deployment triggered on Render!"