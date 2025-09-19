# BreakTheICX Bot

BreakTheICX is a Telegram moderation bot that marries
[python-telegram-bot](https://docs.python-telegram-bot.org/en/v20.7/) with
Firebase Realtime Database storage. The project is tuned for headless Docker
deployments so you can keep group management tooling online without babysitting
it.

## Features

- Persisted per-group configuration and blacklists in Firebase
- Customisable welcome/goodbye messages with templated variables (`{first}`,
  `{last}`, `{username}`)
- Flood protection, keyword filters, and basic moderation commands (ban, mute,
  promote, demote)
- Optional logging to a separate chat/channel
- Sangmata-style username history tracking
- Ready-to-run Dockerfile and docker-compose definition for headless hosting

## Prerequisites

- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A Firebase project with a Realtime Database enabled
- A Firebase service account JSON key with read/write access to the database

### Environment variables

| Variable | Description |
| --- | --- |
| `BOT_TOKEN` | Telegram bot token issued by BotFather. |
| `FIREBASE_DB_URL` | Realtime Database URL (ends with `firebaseio.com`). |
| `FIREBASE_CRED` | Path to the Firebase service account JSON file. |
| `LOG_LEVEL` *(optional)* | Python logging level (`INFO`, `DEBUG`, etc.). |

`FIREBASE_CRED` should point to the location *inside the container* when running
under Docker (the default compose file mounts it to
`/app/firebase-service-account.json`).

## Deploying with Docker Compose

1. **Copy the sample environment file**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and provide the values described above.

2. **Place the Firebase credentials**

   Download your Firebase service account JSON and save it alongside the
   repository root as `firebase-service-account.json`. The filename is already
   listed in `.gitignore` and will be mounted read-only into the container.

3. **Allow yourself to administer the bot**

   In the Firebase Realtime Database set `admins/<telegram_user_id>` to `true` so
   your account can configure the bot. You can add additional admins the same
   way later.

4. **Build and launch the container**

   ```bash
   docker compose up -d --build
   ```

5. **Tail the logs** *(optional)*

   ```bash
   docker compose logs -f telegram-bot
   ```

   The bot automatically connects to Telegram and Firebase once the container is
   running. Any missing environment variables or credential issues are reported
   through the logs.

## Running locally (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN="123456789:example-token"
export FIREBASE_DB_URL="https://your-project-default-rtdb.firebaseio.com/"
export FIREBASE_CRED="/absolute/path/to/firebase-service-account.json"
python bot.py
```

## Firebase structure

The bot expects the following top-level keys in the Realtime Database:

- `admins/<user_id>` – `true` for users allowed to configure the bot
- `groups/<chat_id>` – per-group configuration (welcome text, filters, flood
  limits, log channel, blacklist)
- `users/<user_id>/history` – Sangmata-style name history entries

Ensure your service account has permission to read and write these paths.
