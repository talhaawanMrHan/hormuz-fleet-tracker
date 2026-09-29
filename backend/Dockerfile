FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --disable-pip-version-check --default-timeout=120 --retries=10 -r requirements.txt

COPY . .

RUN mkdir -p /data
ENV DB_PATH=/data/fleet.db

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
