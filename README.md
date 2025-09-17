# BreakTheICX Bot

BreakTheICX is a Telegram moderation bot inspired by GroupHelpBot/Rose. It
combines Firebase persistence with python-telegram-bot v20's async engine so
you can run a full-featured group management bot in a headless Docker
environment.

## Features

- Local per-group bans stored in Firebase
- Customisable welcome/goodbye messages with templated variables (`{first}`,
  `{last}`, `{username}`)
- Flood protection and keyword filters
- Logging to a dedicated channel
- Sangmata-style name history tracking
- Admin-only moderation commands (ban, mute, promote, etc.)
- Docker & docker-compose ready deployment

## Available Commands

| Category | Commands |
| --- | --- |
| General | `/start`, `/help`, `/about` |
| Group Management | `/welcome on|off`, `/goodbye on|off`, `/setwelcome <text>`, `/setgoodbye <text>` |
| Moderation | `/ban`, `/unban`, `/kick`, `/mute`, `/unmute`, `/promote`, `/demote` *(all via reply)* |
| Filters & Anti-Spam | `/setflood <number>`, `/addfilter <word> <reply>`, `/delfilter <word>`, `/filters` |
| Logging | `/setlog <chat_id>`, `/unsetlog`, `/logstatus` |
| Sangmata | `/history`, `/history @username` |

## Running with Docker

1. **Create the environment file**

   ```bash
   cp .env.example .env
   ```

   Update `.env` with your bot token and Firebase Realtime Database URL. The
   default `FIREBASE_CRED` points to `/app/firebase-service-account.json`, which
   is where the credentials file will be mounted inside the container.

2. **Provide Firebase credentials**

   Download the Firebase service account JSON from the Google Cloud console and
   save it next to `docker-compose.yml` as `firebase-service-account.json` (the
   file is ignored by git). Docker Compose mounts it read-only into the
   container.

3. **Build and start the container**

   ```bash
   docker-compose build
   docker-compose up -d
   ```

   The bot will connect to Telegram and Firebase automatically once the
   container is running.

## Running Locally (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN="123456789:example-token"
export FIREBASE_DB_URL="https://your-project-default-rtdb.firebaseio.com/"
export FIREBASE_CRED="/path/to/firebase-service-account.json"
python bot.py
```

## Firebase Structure

The bot stores configuration under the following top-level keys:

- `admins/<user_id>` – boolean flag for global admins allowed to configure the bot
- `groups/<chat_id>` – per-group configuration (welcome text, filters, flood limits, log channel)
- `users/<user_id>/history` – Sangmata-style name history

Make sure the Firebase Realtime Database rules allow the bot's service account
to read/write these paths.
