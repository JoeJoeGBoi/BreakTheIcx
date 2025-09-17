FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py ./

ENV BOT_TOKEN="" \
    FIREBASE_CRED="/app/firebase-service-account.json" \
    FIREBASE_DB_URL=""

CMD ["python", "bot.py"]
