FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
ENV PYTHONPATH=/app/src

# PORT dari env (Cloud Run/Render). HF Spaces tak set PORT -> default 7860.
CMD exec gunicorn --bind :${PORT:-7860} --workers 1 --threads 4 --timeout 120 bot:app
