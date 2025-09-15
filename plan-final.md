# Unified Implementation Plan: ttl.rip

This document outlines a comprehensive plan to build `ttl.rip`, a self-hosted health check monitoring service. It merges the best aspects of two initial plans to create a robust and scalable roadmap.

## 1. Project Overview

`ttl.rip` will be a self-hosted service for monitoring cron jobs, background tasks, and other services. It will provide a simple UI to create, manage, and monitor "checks." Each check will have a unique URL. When the monitored service completes its job, it will send an HTTP request to this URL. If the URL is not requested within a specified time period, the check is marked as "down," and a notification can be sent.

## 2. Core Technologies

- **Backend Framework**: FastAPI
- **Programming Language**: Python 3.11+
- **Database**: SQLAlchemy 2.0 (async) with `aiosqlite` for development and `asyncpg` for production (PostgreSQL).
- **Server**: Uvicorn
- **API Validation**: Pydantic
- **Authentication**: JWT (JSON Web Tokens) with `passlib[bcrypt]` for hashing.
- **Frontend**: Jinja2 for templating, Tailwind CSS for styling.
- **Dependency Management**: `pip` with `requirements.txt`.

## 3. Project Structure

This structure is modular and scalable, separating concerns clearly.

```
ttl.rip/
├── app/
│   ├── __init__.py
│   ├── main.py             # FastAPI app instance, lifespan events, routers
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py       # Pydantic settings management (from .env)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py         # SQLAlchemy engine, session management, Base
│   │   └── models.py       # SQLAlchemy ORM models (User, Check)
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── routes.py       # Combined API router
│   │       └── endpoints/
│   │           ├── __init__.py
│   │           ├── checks.py   # /api/v1/checks CRUD endpoints
│   │           └── login.py    # /api/v1/token endpoint
│   ├── services/
│   │   ├── __init__.py
│   │   └── scheduler.py    # Background task for monitoring checks
│   ├── web/
│   │   ├── __init__.py
│   │   ├── routes.py       # Web UI routes (dashboard, login)
│   │   └── templates/      # Jinja2 templates
│   │       ├── base.html
│   │       ├── login.html
│   │       └── dashboard.html
│   ├── static/
│   │   └── css/
│   │       └── style.css   # Compiled Tailwind CSS
│   ├── schemas.py          # Pydantic schemas for API validation
│   ├── crud.py             # Database CRUD functions
│   └── security.py         # Auth helpers (hashing, JWT creation/validation)
│
├── requirements.txt
└── scripts/
    └── create_admin.py     # Script to create the initial admin user
```

## 4. Database Schema (`app/db/models.py`)

- **User Model**:
  - `id` (Integer, Primary Key)
  - `username` (String, Unique, Indexed)
  - `hashed_password` (String)

- **Check Model**:
  - `id` (Integer, Primary Key)
  - `uuid` (String, Unique, Indexed) - For the ping URL.
  - `name` (String) - A friendly name (e.g., "Daily Backup").
  - `status` (String) - "new", "up", "down".
  - `interval_seconds` (Integer) - The expected time between pings.
  - `grace_seconds` (Integer) - Additional grace period before marking as down.
  - `last_ping` (DateTime, Nullable) - Timestamp of the last successful ping.
  - `owner_id` (Integer, Foreign Key to `users.id`)

## 5. Endpoints

### Public Ping Endpoint
- **`GET /ping/{uuid}`**: The public URL that monitored services will hit. This should be fast, unauthenticated, and update the check's `last_ping` and `status`.

### JSON API (`/api/v1`)
- **`POST /api/v1/token`**: Login endpoint. Takes username/password, returns a JWT token.
- **`GET /api/v1/checks`**: List all checks for the authenticated user.
- **`POST /api/v1/checks`**: Create a new check.
- **`PUT /api/v1/checks/{check_id}`**: Update an existing check.
- **`DELETE /api/v1/checks/{check_id}`**: Delete a check.

### Web UI (Server-Rendered HTML)
- **`GET /`**: Renders the main dashboard (`dashboard.html`). Requires authentication.
- **`GET /login`**: Renders the login page (`login.html`).

## 6. Background Worker (`app/services/scheduler.py`)

The monitoring logic will be an async function integrated into the FastAPI application's lifespan.
- It will run in an infinite loop (`asyncio.sleep` for delay).
- On each iteration, it will query the database for all active checks.
- For each check, it calculates if it's overdue: `current_time > check.last_ping + interval_seconds + grace_seconds`.
- If a check is overdue and its status is "up", it updates the status to "down".
- This will be started via `asyncio.create_task` in the `lifespan` startup event in `app/main.py`.

## 7. Detailed Development Roadmap

### Phase 1: Project Foundation
1.  **Setup**: Create the project directory structure and files.
2.  **Dependencies**: Create `requirements.txt` and install packages.
3.  **Configuration**: Set up `app/core/config.py` to load settings from environment variables.
4.  **Database**: Implement `app/db/base.py` for the async engine and session. Define models in `app/db/models.py`.

### Phase 2: Core Backend Logic
1.  **Schemas**: Define Pydantic models in `app/schemas.py` for API request/response validation.
2.  **CRUD**: Write the database interaction logic in `app/crud.py`.
3.  **Ping Endpoint**: Create the public `GET /ping/{uuid}` endpoint in `app/main.py` (or its own router). It should use the CRUD functions to update the check.

### Phase 3: Authentication
1.  **Security Helpers**: Implement password hashing and JWT creation/verification functions in `app/security.py`.
2.  **Login Endpoint**: Create the `/api/v1/token` endpoint in `app/api/v1/endpoints/login.py`.
3.  **Dependencies**: Create a FastAPI dependency in `app/security.py` to protect routes by verifying the JWT token.

### Phase 4: API and UI Implementation
1.  **API Endpoints**: Build the CRUD endpoints for checks in `app/api/v1/endpoints/checks.py`, protecting them with the authentication dependency.
2.  **Templating**: Configure Jinja2 and mount the `/static` directory in `app/main.py`.
3.  **Web Routes**: Create the routes in `app/web/routes.py` to serve `login.html` and the main `dashboard.html`. The dashboard route must be protected.
4.  **Frontend**: Write basic JavaScript in the dashboard to interact with the JSON API for creating, listing, and deleting checks dynamically.

### Phase 5: Monitoring & Finalization
1.  **Scheduler**: Implement the monitoring logic in `app/services/scheduler.py`.
2.  **Integration**: Integrate the scheduler into the FastAPI app's lifespan in `app/main.py`.
3.  **Admin User**: Create the `scripts/create_admin.py` script to initialize the first user.
4.  **Testing**: Perform end-to-end testing of the entire flow.

### Phase 6: Deployment
1.  **Containerization**: Create a `Dockerfile` and `docker-compose.yml` for easy local development and deployment.
2.  **Migrations**: (Optional but recommended) Add Alembic for database schema migrations.

## 8. Future Enhancements
- **Notifications**: Add support for sending notifications (email, webhooks) when a check goes down.
- **Multi-User Support**: The schema already supports it, but the UI and API would need to be expanded.
- **API Keys**: Allow users to manage checks programmatically via API keys instead of JWTs.
- **Detailed History**: Log every ping and status change for audit and debugging.
