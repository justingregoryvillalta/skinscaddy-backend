# SkinsCaddy API

Python + FastAPI backend for user accounts, JWT authentication, friends, token wallets, challenges, live feed, photos, and scramble sync.
Local development uses a SQLite file (`backend/skinscaddy.db`). PostgreSQL is still the production database on Render.

The existing Flask/SQLite app in `server/` is unchanged. This is the new backend.

## Project layout

```
backend/
  app/
    main.py              # FastAPI app, CORS, /health
    config.py            # env settings
    database.py          # SQLAlchemy engine + sessions
    core/security.py     # bcrypt + JWT
    models/              # User, friends, wallet, rounds, challenges, live status
    schemas/             # request/response shapes
    services/            # business logic
    api/v1/              # HTTP routes
  docker-compose.yml     # optional local Postgres
  requirements.txt
  start.sh               # production start (init tables + $PORT)
  Procfile               # Render web process
```

## Run locally

Local development does **not** need Postgres. The default `DATABASE_URL` is a SQLite file at `backend/skinscaddy.db`.

To use Postgres later (Docker or Render), set `DATABASE_URL` in `.env`:

```bash
# docker compose up -d   # optional local Postgres
DATABASE_URL=postgresql+psycopg://skinscaddy:skinscaddy@localhost:5432/skinscaddy
```

### 1. Create a virtualenv and install deps

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

### 2. Start the API

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Or: `./run.sh`

- API: http://127.0.0.1:8000
- Swagger docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health

Tables are created automatically on first startup (`create_all`). On Render this also runs when the web process starts.

## Try the auth flow

Register (then activate via the emailed link before login):

```bash
curl -s http://127.0.0.1:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"justin","password":"password123","first_name":"Justin","last_name":"Villalta","email":"justin@example.com","postal_code":"M5V 1A1"}'
```

In development the response includes `verification_url`. Open it, then log in:

```bash
curl -s http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"justin","password":"password123"}'
```

Both return:

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 604800,
  "user": { "id": 1, "username": "justin", "created_at": "..." }
}
```

Protected routes (send the token):

```bash
TOKEN='<paste access_token>'

curl -s http://127.0.0.1:8000/api/v1/me \
  -H "Authorization: Bearer $TOKEN"

curl -s http://127.0.0.1:8000/api/v1/protected \
  -H "Authorization: Bearer $TOKEN"
```

`/api/v1/protected` is a test-only example: if the token is valid you get `{"ok": true, "message": "Token is valid.", ...}`.

## Friends

All friend routes require `Authorization: Bearer <token>`.

```bash
# Send a request by username
curl -s http://127.0.0.1:8000/api/v1/friends/requests \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"bob"}'

# Incoming / outgoing pending
curl -s http://127.0.0.1:8000/api/v1/friends/requests/incoming \
  -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:8000/api/v1/friends/requests/outgoing \
  -H "Authorization: Bearer $TOKEN"

# Accept or decline (addressee only)
curl -s -X POST http://127.0.0.1:8000/api/v1/friends/requests/1/accept \
  -H "Authorization: Bearer $TOKEN"
curl -s -X POST http://127.0.0.1:8000/api/v1/friends/requests/1/decline \
  -H "Authorization: Bearer $TOKEN"

# Current friends
curl -s http://127.0.0.1:8000/api/v1/friends \
  -H "Authorization: Bearer $TOKEN"
```

Self-requests, unknown usernames, duplicate pending requests, and requests between people who are already friends are rejected.

## Token wallet

All wallet routes require `Authorization: Bearer <token>`. Balance is stored on the user and every change is written to `token_ledger`. Debits that would go below zero are rejected and leave no ledger row.

```bash
# Current balance
curl -s http://127.0.0.1:8000/api/v1/wallet \
  -H "Authorization: Bearer $TOKEN"

# Credit (rewards / completed holes)
curl -s http://127.0.0.1:8000/api/v1/wallet/credit \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"amount":15,"source":"birdie","reason":"Birdie on 7"}'

# Debit (wagers / forfeits)
curl -s http://127.0.0.1:8000/api/v1/wallet/debit \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"amount":10,"source":"wager","reason":"Hole 3 skin"}'

# Full history (newest first)
curl -s http://127.0.0.1:8000/api/v1/wallet/history \
  -H "Authorization: Bearer $TOKEN"
```

Credit sources: `reward`, `welcome`, `round_complete_9`, `round_complete_18`, `par`, `birdie`, `eagle`, `skins_win`, `challenge_win`, `adjustment`.

Debit sources: `wager`, `forfeit`, `purchase`, `adjustment`.

Later award code should call `award_tokens()` in `app/services/wallet.py` (default amounts already exist for 9/18, par/birdie/eagle, skins, and challenges).

## Challenges & wagers

All challenge and round routes require a JWT. Only friends can be challenged (1–3 usernames). The host picks one of their completed solo cards, a wager, and a 1–4 week limit.

On accept, each side’s wager is escrowed into the pot (`token_ledger` source `wager`). If the deadline hits and an accepted player has not finished, they forfeit and finishers with the best (lowest) total receive the pot (`challenge_win`). If everyone who joined finishes, the lowest total wins the pot. Ties split evenly.

```bash
# Save a completed solo card
curl -s http://127.0.0.1:8000/api/v1/rounds \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"course_name":"Pebble Beach","num_holes":9,"scores":[4,4,5,3,4,4,5,4,4]}'

# Challenge up to 3 friends
curl -s http://127.0.0.1:8000/api/v1/challenges \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"usernames":["bob"],"round_id":1,"wager_amount":10,"weeks":2}'

curl -s http://127.0.0.1:8000/api/v1/challenges/incoming \
  -H "Authorization: Bearer $BOB"
curl -s -X POST http://127.0.0.1:8000/api/v1/challenges/1/accept \
  -H "Authorization: Bearer $BOB"
curl -s -X POST http://127.0.0.1:8000/api/v1/challenges/1/scores \
  -H "Authorization: Bearer $BOB" \
  -H "Content-Type: application/json" \
  -d '{"scores":[4,4,4,4,4,4,4,4,4]}'

# History
curl -s http://127.0.0.1:8000/api/v1/challenges \
  -H "Authorization: Bearer $TOKEN"
```

Statuses: `pending`, `active`, `completed`, `expired`, `forfeited`. `POST /challenges/{id}/settle` applies a missed deadline (also runs automatically on read/accept/score).

## Live status / friends feed

JWT required. The Kivy home ticker and Friends Live section should poll `GET /api/v1/feed`.

Statuses: `idle`, `playing`, `finished`. Playing requires a course and a mode (`solo` = Solo 2.0, `skins`, `scramble`).

Privacy matches Solo 2.0:

- `full` — friends see hole, scores, and total
- `limited` — friends still see course + hole number, but not scores or total

Only **accepted friends** appear. Your own card is `GET /api/v1/status`, not the friends feed. Playing sessions with no update for 6 hours drop off the live list.

```bash
# Start / update a live round (send this on each hole)
curl -s -X PUT http://127.0.0.1:8000/api/v1/status \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"state":"playing","mode":"solo","course_name":"Pebble Beach","hole":4,"privacy":"limited"}'

# Poll for ticker + Friends Live
curl -s http://127.0.0.1:8000/api/v1/feed \
  -H "Authorization: Bearer $TOKEN"

# Optional incremental activity: ?since=2026-08-18T12:00:00Z&activity_limit=30

# Finished
curl -s -X PUT http://127.0.0.1:8000/api/v1/status \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"state":"finished","mode":"solo","course_name":"Pebble Beach","total":38}'

# Extra ticker events (won skins, etc.)
curl -s http://127.0.0.1:8000/api/v1/feed/events \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"kind":"won_skins","course_name":"Pebble Beach","mode":"skins"}'
```

`GET /api/v1/feed` returns `{ "generated_at", "live": [...], "activity": [...] }`. `live` is friends currently playing; `activity` is started/finished rounds, skins wins, and challenge wins.

## Photos (view-once)

JWT required. Use this for **challenge photos** and **hole prop** photos. Files live on the server until a recipient downloads them, or until 7 / 14 days pass.

- Sender can re-open the file without deleting it
- The first challenged user / recipient who `GET`s the file consumes it (then `410 Gone`)
- Only the sender and named recipients (challenge players, or friends listed on a prop) can access it
- JPEG, PNG, or WebP; max 5 MB (compress on the phone first)

```bash
# Challenge photo
curl -s http://127.0.0.1:8000/api/v1/photos \
  -H "Authorization: Bearer $TOKEN" \
  -F kind=challenge \
  -F challenge_id=1 \
  -F expires_in_days=7 \
  -F caption="Beat this lie" \
  -F file=@shot.jpg

# Hole prop photo
curl -s http://127.0.0.1:8000/api/v1/photos \
  -H "Authorization: Bearer $TOKEN" \
  -F kind=prop \
  -F recipients=bob \
  -F hole=7 \
  -F expires_in_days=14 \
  -F caption="Closest to pin" \
  -F file=@prop.jpg

# Metadata (does not consume)
curl -s http://127.0.0.1:8000/api/v1/photos/1 \
  -H "Authorization: Bearer $TOKEN"

# Display in the app — this GET is what deletes the file for a recipient
curl -s http://127.0.0.1:8000/api/v1/photos/1/file \
  -H "Authorization: Bearer $TOKEN" \
  -o /tmp/shot.jpg
```

The upload response includes `url` (`/api/v1/photos/{id}/file`). Point the image widget at `{API_BASE}{url}` and send the JWT.

## Groups / scramble sync

JWT required. Tee Off creates a scramble room and a 6-character join code (`skinscaddy://join?code=XXXXXX`). Joiners pick a team. Each player can only post for their own team. A hole’s scores stay hidden until every team has posted that hole; then skins settle in hole order (lowest wins, ties carry).

Poll `GET /api/v1/scrambles/{id}` (or `/by-code/{code}` after joining). Compare `revision` / `updated_at` to skip redraws.

```bash
# Host Tee Off
curl -s http://127.0.0.1:8000/api/v1/scrambles \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "course_name":"Pebble Beach",
    "num_holes":9,
    "wager_amount":5,
    "host_team_index":0,
    "teams":[{"name":"Birdies","start_hole":1},{"name":"Bogeys","start_hole":10}]
  }'

# Preview teams, then join
curl -s http://127.0.0.1:8000/api/v1/scrambles/by-code/ABC123 \
  -H "Authorization: Bearer $BOB"
curl -s http://127.0.0.1:8000/api/v1/scrambles/join \
  -H "Authorization: Bearer $BOB" \
  -H "Content-Type: application/json" \
  -d '{"code":"ABC123","team_index":1}'

# Post your team’s current hole, then refresh state
curl -s http://127.0.0.1:8000/api/v1/scrambles/1/scores \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"strokes":4}'
curl -s http://127.0.0.1:8000/api/v1/scrambles/1 \
  -H "Authorization: Bearer $TOKEN"
```

`holes[].posted` shows who is in without revealing strokes. `holes[].scores` and `teams[].scores` stay `null` for other teams until `revealed` is true.

## Tests

```bash
cd backend
source .venv/bin/activate
pytest -q
```

Tests use an in-memory SQLite database so they do not need Postgres.

## Environment

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Local default is SQLite (`sqlite+pysqlite:///skinscaddy.db`). On Render, set the Postgres URL — `postgres://...` is rewritten for SQLAlchemy and gets `sslmode=require` in production. |
| `SECRET_KEY` | JWT signing key. Required in production (deploy fails on the placeholder). |
| `ENV` | `development` locally. Must be `production` on Render (refuses SQLite and the default secret). |
| `PORT` | Listen port. Render injects this; the start command uses `$PORT`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Default `10080` (7 days). |
| `CORS_ORIGINS` | `*` locally, or a comma-separated list in production. |
| `PHOTO_DIR` | Local folder for temporary photos (default `backend/var/photos`). |
| `PHOTO_MAX_BYTES` | Upload cap (default 5 MB). |

## Deploy on Render

The API is a Python **Web Service** with Root Directory `backend/`. Tables are created on boot via `init_db()`.

### Option A — Blueprint

1. Push this repo to GitHub.
2. Render Dashboard → **New** → **Blueprint**.
3. Select the repo. `render.yaml` at the repo root creates:
   - a Postgres database (`skinscaddy-db`)
   - a web service (`skinscaddy-api`) with `ENV=production`, a generated `SECRET_KEY`, and `DATABASE_URL` linked to that database

### Option B — Manual Web Service

1. **New** → **PostgreSQL**. Copy the Internal (or External) Database URL.
2. **New** → **Web Service** → this repo.
3. Settings:

   | Field | Value |
   |---|---|
   | Root Directory | `backend` |
   | Runtime | Python 3 |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |

   Equivalent start command that also runs table creation first:

   `./start.sh`

4. Environment variables:

   | Key | Value |
   |---|---|
   | `ENV` | `production` |
   | `SECRET_KEY` | a long random string (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
   | `DATABASE_URL` | paste from the Postgres instance (Internal URL is fine on Render) |
   | `PYTHON_VERSION` | `3.12.8` (optional) |

5. Health check path: `/health`

Local development is unchanged: leave `.env` on SQLite (`sqlite+pysqlite:///skinscaddy.db`) and run `./run.sh` or uvicorn on port 8000.

