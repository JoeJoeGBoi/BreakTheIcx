FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY firebase-service-account.json .

ENV BOT_TOKEN=""
ENV FIREBASE_CRED="/app/firebase-service-account.json"
ENV FIREBASE_DB_URL=""

CMD ["python", "bot.py"]
