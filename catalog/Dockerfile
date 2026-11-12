FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY catalog/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY catalog/app.py .

EXPOSE 5001
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "1", "--worker-class", "gthread", "--threads", "4", "--access-logfile", "-", "--error-logfile", "-", "--capture-output", "app:app"]
