FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY frontend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY frontend/app.py .

EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--worker-class", "gthread", "--threads", "4", "--access-logfile", "-", "--error-logfile", "-", "--capture-output", "app:app"]
