FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
ENV PYTHONPATH=/app/src

# Cloud Run mengirim PORT lewat env. gunicorn melayani Flask app `bot:app`.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 120 bot:app
