# BreakTheICX Bot


Telegram group moderation bot inspired by GroupHelpBot/Rose.


## Features
- Local per-group bans stored in Firebase
- Welcome/Goodbye messages with variables
- Filters & flood protection
- Logging channel
- Name history tracking (Sangmata)
- Admin-only moderation commands
- Docker-ready deployment


## Commands
(Include all General, Group Management, Filters, Logging, Sangmata commands as per your list)


## Setup
1. Provide the Firebase service account credentials using one of the supported options:

   - Place the JSON file (for example `firebase-service-account.json`) in the project root and set `FIREBASE_CRED` to the file
     name or relative path.
   - Set `FIREBASE_CRED_JSON` to the raw JSON string.
   - Set `FIREBASE_CRED_BASE64` to the base64 encoded JSON content (useful when the JSON contains newlines that are hard to
     express in environment files).

2. Fill out `.env` file with your bot token, Firebase database URL, and one of the credential variables above.
3. Build and run using Docker:

   ```
   docker-compose build
   docker-compose up -d
   ```

   The bot container will use the values from your `.env` file. Check the logs with `docker-compose logs -f bot` to confirm it connected successfully.
