## Authentication
How Authentication Works (Overall Flow)

The application has two distinct authentication mechanisms:

 1 API Authentication (JWT-based): This is the primary, secure method used by the frontend JavaScript to communicate with the backend API (/api/v1/*).
 2 Web UI Authentication (Cookie-based): This is a simpler mechanism used only to control access to the HTML dashboard page itself.

The flow for the API is as follows:

 1 When the dashboard page (/) loads, the JavaScript in dashboard.html immediately calls the loginAndGetToken() function.
 2 This function sends a POST request to /api/v1/token with the hardcoded credentials username: 'admin' and password: 'password'.
 3 The /api/v1/token endpoint (in app/api/v1/endpoints/login.py) verifies these credentials against the user in the database.
 4 If the credentials are correct, it generates a JSON Web Token (JWT) and returns it to the browser.
 5 The JavaScript stores this JWT in a variable (apiToken).
 6 For all subsequent API requests (like fetching or creating checks), the JavaScript includes this token in the Authorization header, like so: Authorization: Bearer <the_jwt_token>.
 7 The API endpoints for checks (in app/api/v1/endpoints/checks.py) are protected and use a dependency to validate this token on every request.

How JWT is Used and Validated

JWT Creation:

 • The creation happens in app/security.py within the create_access_token function.
 • When a user logs in successfully via the /api/v1/token endpoint, this function is called.
 • It creates a Python dictionary (the "payload") containing the user's username (as sub, a standard JWT claim for "subject") and an expiration timestamp (exp).
 • It then uses the jose.jwt.encode() method to sign this payload. The signing process uses the SECRET_KEY and the ALGORITHM (HS256) defined in app/core/config.py.
 • The result is the compact, signed JWT string that is sent back to the client.

JWT Validation:

 • Validation happens in app/security.py inside the get_current_user function, which acts as a FastAPI dependency.
 • Protected API endpoints, like read_checks in app/api/v1/endpoints/checks.py, include this function in their signature: current_user: db_models.User = Depends(security.get_current_user).
 • FastAPI automatically extracts the token from the Authorization: Bearer ... header.
 • The get_current_user function then uses jose.jwt.decode() to verify and decode the token. This process uses the same SECRET_KEY and ALGORITHM to check the token's signature and ensure it hasn't been tampered
   with. It also automatically checks if the token has expired.
 • If the token is valid, the function extracts the username from the payload, fetches the corresponding user from the database, and returns the user object.
 • If the token is invalid, expired, or the signature doesn't match, a 401 Unauthorized HTTP exception is raised, and the request is denied.

Where the Validation Keys are Stored

The application uses a symmetric algorithm (HS256), which means it uses a single secret key for both signing and validating tokens, not a public/private key pair.

This secret key is managed in app/core/config.py:

```python
# app/core/config.py

class Settings(BaseSettings):
    # ...
    SECRET_KEY: str = "a_very_secret_key"
    ALGORITHM: str = "HS256"
    # ...

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()
```

The value is loaded from environment variables. It has a default value of "a_very_secret_key" for development but is intended to be overridden in production by setting a SECRET_KEY environment variable or placing
it in a .env file, as shown in .env.example.
# ttr.rip — Simple, resilient health‑check monitoring

ttr.rip is a FastAPI-based uptime and job monitoring service with anonymous key-based access, a clean, themeable UI, and robust Redis/Celery-powered background processing. It’s easy to run on a single node and durable enough for production, featuring adaptive rate control, flapping suppression, and Prometheus metrics out of the box.

- Elegant web UI with multiple themes (Cyberpunk, Retro, Blueprint, Terminal, Solarized, Arcade)
- Anonymous “access key” login, optional “Login with Telegram”
- Public status pages with shareable badges
- Telegram, Slack, Discord, and generic Webhook notifications
- Adaptive rate control (AIMD + backoff) and flapping detection
- Prometheus metrics and operational summary APIs
- Docker-first deployment (Postgres, Redis, web, worker, beat, Caddy)

---

## Table of contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Quickstart (Docker)](#quickstart-docker)
- [Configuration](#configuration)
- [Running locally (without Docker)](#running-locally-without-docker)
- [Concepts & Architecture](#concepts--architecture)
- [API overview](#api-overview)
- [Admin & Security model](#admin--security-model)
- [Observability & Metrics](#observability--metrics)
- [Background processing](#background-processing)
- [Cleanup & Lifecycle](#cleanup--lifecycle)
- [Import/Export](#importexport)
- [Development](#development)
- [Roadmap](#roadmap)
- [License](#license)

---

## Features

User & Auth
- Anonymous key-based access via cookie or X-Auth-Key header
- Optional “Login with Telegram”
- Per-user slug for clean ping URLs and public pages
- CSRF protection for forms and APIs (Double Submit Cookie pattern)

Checks
- Scheduling: interval, cron, or systemd OnCalendar
- Durable status in DB: up / down / new, with last_ping, last_start, last_duration_seconds
- Deadlines and grace windows computed/persisted in DB (reliable overdue detection)
- Optional content validation (present/absent or regex) on ping payloads
- Pause/resume with correct counters and metrics updates
- Cursor-based pagination, ETag’d list/aggregate responses

Integrations & Alerts
- Telegram, Slack, Discord, and generic Webhook
- Adaptive global rate control (AIMD + exponential backoff), cross-worker via Redis
- Flapping detection with suppression windows
- Test flows: immediate send or queue-based

Status Pages
- Public pages under /s/{user_slug}/{page_slug}
- Layouts: cards, grid, timeline
- Safe “recent activity” with country code/name and connection hints
- Badge endpoint: /p/{user_slug}/{check_id_or_slug}/badge.svg

UI & Theming
- Multiple themes, light/dark mode, persisted user preference
- Compact, accessible dashboard with inline actions and quick copy
- Real-time feel with periodic refresh, countdowns, and subtle glow indicators

Observability
- Prometheus metrics (/metrics, admin-only)
- Summary API (/api/v1/metrics/summary) with ETag caching
- Cross-worker latency aggregation in Redis
- Worker heartbeats and “workers online” gauge

Performance & Resilience
- Redis-backed runtime hints (e.g., last_content, recent pings)
- Buffered Redis HINCRBY with coalesced flushes
- Fail-open design on metrics and cache paths

Maintenance
- Periodic cleanup of long-inactive checks/users (configurable)
- Alembic migrations
- Import/export checks as JSON

---

## Screenshots

- Dashboard: user checks, status counters, pagination, quick actions
- Integrations: per-check settings and live rate snapshots
- Public status pages: cards/grid/timeline views

(See app/web/templates and app/static/css/themes for layouts and styles.)

---

## Quickstart (Docker)

Requirements:
- Docker and docker-compose
- A valid Fernet ENCRYPTION_KEY (32 url-safe base64-encoded bytes)

1) Prepare environment
- Copy the defaults and edit as needed:
  cp .env.example .env
- Generate a Fernet key and set ENCRYPTION_KEY in .env:
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

2) Start the stack
- Bring up Postgres, Redis, web, worker, beat, and Caddy:
  docker-compose up -d --build

3) Access
- Via Caddy (recommended): http://localhost:8080
- Direct FastAPI (dev): http://localhost:8000

4) Get a key
- Click “Get a New Key” to obtain an access key, then go to /dashboard.
- Your key is stored in an HttpOnly cookie; keep it safe.

Admin UI
- Create an admin user (see “Admin & Security model”).
- Visit http://localhost:8080/admin/login

Stop & logs
- Stop: docker-compose down
- Logs: docker-compose logs -f web (or worker/beat)

---

## Configuration

Settings live in app/core/config.py and are overridden by .env (see .env.example):

Core
- DATABASE_URL: DB DSN (Postgres recommended)
- REDIS_URL: Redis broker/result and cache
- SECRET_KEY: JWT signing secret
- ENCRYPTION_KEY: Fernet key (required) for encrypting user secrets
- DEBUG_MODE: If true, Celery tasks run eagerly (no broker required)

Scheduling & Worker
- SCHEDULER_INTERVAL_SECONDS
- AUTO_START_EMBEDDED_WORKER, WORKER_CONCURRENCY
- EMBED_BEAT (use worker -B or separate beat service)

Redis Counters Buffer
- INCR_BUFFER_ENABLED, INCR_BUFFER_FLUSH_INTERVAL_MS, INCR_BUFFER_MAX_OPS

Telegram
- TELEGRAM_AUTH_ENABLED, TELEGRAM_BOT_NAME, TELEGRAM_BOT_TOKEN

Cleanup
- CLEANUP_ENABLED, CLEANUP_INACTIVE_DAYS, CLEANUP_INTERVAL_HOURS

Security & CSP
- USER_SLUG_ENABLED
- XAUTH_ENFORCE_ORIGIN, XAUTH_ENFORCE_IP
- CSP_USER_DASHBOARD (Content-Security-Policy for user-facing pages)

GeoIP & Logging
- SAVE_CHECK_LAST_LOGS
- GEOIP_DATABASE_PATH (optional)
- LOG_LEVEL, UVICORN_LOG_LEVEL

Most features degrade gracefully if Redis is absent or DEBUG_MODE is enabled.

---

## Running locally (without Docker)

1) Install dependencies
- Python 3.11+
- Postgres and Redis running
- pip install -r requirements.txt

2) Configure environment
- cp .env.example .env
- Set ENCRYPTION_KEY (Fernet key)

3) Migrate DB
- alembic upgrade head

4) Run services
- API (dev): uvicorn app.main:app --reload
- Worker: celery -A app.worker.celery_app worker --loglevel=info -P solo
- Beat (if not embedded): celery -A app.worker.celery_app beat --loglevel=info

Open http://localhost:8000

---

## Concepts & Architecture

- FastAPI application (app/main.py) serving:
  - Public pages (/, /dashboard, /check/{id}/integrations)
  - Public status pages (/s/{user_slug}/{page_slug})
  - REST APIs under /api/v1
  - Admin SPA endpoints (/admin/*)
- Database (SQLAlchemy + Alembic): Users, Checks, Tags, StatusPages
- Redis:
  - Celery broker/result backend
  - Runtime cache (recent pings, last content)
  - Global counters and latency aggregation
  - Worker heartbeats
- Celery worker (app/worker.py):
  - Notification tasks (Telegram/Slack/Discord/Webhook)
  - Periodic overdue/long-running detection (Beat)
- Adaptive rate control (app/services/rate_control.py):
  - AIMD refill, min drip, exponential backoff
  - Per-identity state (e.g., sha256(token)[:10])
- Alerting (app/services/alerting.py):
  - Flapping detection with suppression TTL

Data flow examples
- Ping endpoint (/p/{user_slug}/{check_identifier}):
  - Optionally logs geo-hints and UA to Redis
  - Validates content, updates DB state and deadlines
  - Schedules notifications (with adaptive rate + retries)
- Metrics:
  - /metrics for Prometheus
  - /api/v1/metrics/summary for UI (ETag-cached JSON)

---

## API overview

Public (X-Auth-Key)
- GET /api/v1/checks
  - Query params: size, sort_by, sort_direction, cursor, tag
  - ETag’d responses, cursor pagination
- GET /api/v1/checks/aggregate
  - One-call dashboard aggregate (checks + stats + metrics + tags)
- GET /api/v1/checks/stats
- POST /api/v1/checks
- PUT /api/v1/checks/{check_id}
- DELETE /api/v1/checks/{check_id}
- PUT /api/v1/checks/{id}/{integration} (telegram|slack|discord|webhook)
- POST /api/v1/checks/{id}/{integration}/test (immediate)
- POST /api/v1/checks/{id}/{integration}/test-queue (enqueue)
- GET /api/v1/checks/{id}/{integration}/rate (live rate snapshot)
- Status pages: /api/v1/status-pages (CRUD)
- Import/Export:
  - GET /api/v1/checks/export
  - POST /api/v1/checks/import
- Pings:
  - /p/{user_slug}/{check_identifier} (GET/POST)
  - /p/{user_slug}/{check_identifier}/start
  - /p/{user_slug}/{check_identifier}/fail
  - /p/{user_slug}/{check_identifier}/badge.svg

Admin (JWT)
- GET /api/v1/admin/stats
- GET /metrics (Prometheus, admin-only)

Public Pages
- GET /s/{user_slug}/{page_slug}
- GET /s/{user_slug}/{page_slug}/data

---

## Admin & Security model

- Admin users:
  - Username/password -> short-lived access token (Bearer) + HttpOnly refresh cookie
  - SPA flow with token refresh (CSRF-protected)
- Public users:
  - X-Auth-Key via cookie/header
  - Optional Telegram binding
  - Blacklisting: keys can be temporarily blacklisted in Redis
- CSRF:
  - Double Submit Cookie pattern for forms/APIs
- CSP:
  - Strict CSP applied to user-facing pages (configurable)

---

## Observability & Metrics

- Prometheus endpoint: /metrics (admin-only)
- Metrics summary API: /api/v1/metrics/summary
  - Totals (checks, users, notifications)
  - Average latencies (API/DB/Redis/queue), queue depth
  - Health colors for quick at-a-glance
- Redis-based cross-worker aggregation for accurate averages
- Worker heartbeats in Redis to compute “workers online”

---

## Background processing

- Celery worker tasks:
  - Notifications with retries and RateLimitedError handling
  - Overdue checks and long-running detection (Beat every few seconds)
- Heartbeats:
  - metrics:workers_online:set + per-worker TTL keys
- Eager mode in DEBUG (no broker required)

---

## Cleanup & Lifecycle

- Periodic cleanup (app/tasks/cleanup.py) when enabled:
  - Deletes long-inactive checks
  - Deletes users without active checks and no Telegram linkage
  - Best-effort Redis cleanup of related keys
- Manual run:
  - python app/commands/cleanup_cmd.py

---

## Import/Export

- Export:
  - GET /api/v1/checks/export → JSON list (includes integration flags/urls)
- Import:
  - POST /api/v1/checks/import → accepts same format
  - Secrets are re-encrypted using the current user’s auth key

---

## Development

- Stack:
  - FastAPI, SQLAlchemy (async), Alembic
  - Redis asyncio client with pooled connections
  - Celery (Redis broker/result), Prometheus client
- Useful entry points:
  - app/main.py (FastAPI app, routes mounting)
  - app/api/v1/endpoints/* (REST endpoints)
  - app/web/* (templates and routes)
  - app/services/* (notifications, alerting, rate control, queue stats)
  - app/worker.py (Celery config, periodic tasks)
  - app/db/models.py (ORM models)
- Local dev:
  - uvicorn app.main:app --reload
  - celery -A app.worker.celery_app worker --loglevel=info -P solo
  - celery -A app.worker.celery_app beat --loglevel=info
- Code style:
  - Use your preferred formatters/linters (e.g., black/ruff/mypy)

---

## Roadmap

- More integrations (email/SMS gateways)
- Quotas/rate-plans and richer admin controls
- Secret backends (KMS/HSM adapters)
- Multi-region setups and sharding options
- Deeper analytics and dashboards

---

## License

Licensed under the terms of the LICENSE file in this repository.

---
