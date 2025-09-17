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
1. Place Firebase service account JSON in project root.

   ```firebase-service-account.json```

2. Fill out `.env` file.
3. Build and run using Docker:

   ```
   docker-compose build
   docker-compose up -d
   ```

   The bot container will use the values from your `.env` file. Check the logs with `docker-compose logs -f bot` to confirm it connected successfully.
