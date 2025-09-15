# Implementation Plan: ttl.rip

This document outlines the plan to build `ttl.rip`, a self-hosted health check monitoring service inspired by deadmanssnitch.com and healthchecks.io.

## 1. Core Technologies

- **Backend Framework**: FastAPI
- **Programming Language**: Python 3.11+
- **Database**: SQLAlchemy 2.0 (async) with `aiosqlite` for development and `asyncpg` for production (PostgreSQL).
- **Frontend Templating**: Jinja2
- **Server**: Uvicorn
- **Dependency Management**: `pip` with `requirements.txt`

## 2. Project Structure

A clear and scalable project structure.

```
ttl.rip/
├── app/
│   ├── __init__.py
│   ├── main.py             # FastAPI app instance, startup/shutdown events
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py       # Configuration management (e.g., from .env)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py         # SQLAlchemy base and session management
│   │   └── models.py       # SQLAlchemy ORM models (Check, User)
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       └── endpoints/
│   │           ├── __init__.py
│   │           └── ping.py # The /ping/{uuid} endpoint
│   ├── services/
│   │   ├── __init__.py
│   │   └── scheduler.py    # Background task for monitoring checks
│   ├── web/
│   │   ├── __init__.py
│   │   ├── routes.py       # Web UI routes (dashboard, login, CRUD)
│   │   └── templates/      # Jinja2 templates
│   │       ├── base.html
│   │       ├── login.html
│   │       └── dashboard.html
│   └── static/
│       └── style.css       # Basic CSS
├── requirements.txt
└── scripts/
    └── create_admin.py     # Script to create the initial admin user
```

## 3. Phase 1: Backend Foundation

### Step 3.1: Initial Setup
- Create the project directory structure as outlined above.
- Set up a Python virtual environment.
- Create `requirements.txt` with initial dependencies:
  ```
  fastapi
  uvicorn[standard]
  sqlalchemy[asyncio]
  aiosqlite
  jinja2
  pydantic
  passlib[bcrypt]
  python-multipart
  ```

### Step 3.2: Database Models (`app/db/models.py`)
- **User Model**: `id`, `username` (unique), `hashed_password`.
- **Check Model**: `id`, `uuid` (unique, for ping URL), `name`, `status` (e.g., "new", "up", "down"), `interval_seconds`, `grace_seconds`, `last_ping` (datetime, nullable).

### Step 3.3: Database Session (`app/db/base.py`)
- Configure an async SQLAlchemy engine and session maker.
- Create a FastAPI dependency to provide a database session to path operations.

### Step 3.4: Ping Endpoint (`app/api/v1/endpoints/ping.py`)
- Create a GET endpoint at `/ping/{check_uuid}`.
- It should find the `Check` by its UUID.
- If found, update its `last_ping` to the current time and set its `status` to "up".
- Return a simple `200 OK` response.

### Step 3.5: Initial Admin User
- Create a script `scripts/create_admin.py` that connects to the database.
- It should create a user with username `admin` and a hashed password for "password".

## 4. Phase 2: Background Monitoring

### Step 4.1: Scheduler Logic (`app/services/scheduler.py`)
- Create an async function `check_jobs()`.
- This function will run in an infinite loop with a sleep interval (e.g., 60 seconds).
- Inside the loop:
    1. Query the database for all checks that are not in "new" status.
    2. For each check, calculate if it's overdue: `current_time > check.last_ping + interval_seconds + grace_seconds`.
    3. If a check is overdue and its status is "up", update its status to "down".

### Step 4.2: Integrate Scheduler (`app/main.py`)
- Use FastAPI's `lifespan` context manager (or `@app.on_event("startup")`).
- In the startup event handler, create a background task that runs the `check_jobs()` function using `asyncio.create_task`.

## 5. Phase 3: Web UI & Authentication

### Step 5.1: Templating Setup (`app/main.py`)
- Configure Jinja2 templates and mount the `/static` directory.

### Step 5.2: Authentication (`app/web/routes.py`)
- Create a login page at `/login` (GET and POST).
    - GET: Renders `login.html`.
    - POST: Validates username/password from a form. On success, sets a session cookie and redirects to the dashboard.
- Create a `/logout` route to clear the session cookie.
- Implement a dependency that checks for the session cookie to protect authenticated routes.

### Step 5.3: Dashboard (`app/web/routes.py`)
- Create the main dashboard route at `/`.
- This route must be protected by authentication.
- It will fetch all checks from the database and pass them to the `dashboard.html` template.
- The template will render a table showing each check's name, status, full ping URL, and last ping time.

### Step 5.4: Check Management (CRUD)
- **Create**:
    - A route `/checks/new` (GET, POST).
    - GET displays a form to create a new check (name, interval, grace period).
    - POST validates the form data, creates a new `Check` record (with a new UUID), and redirects to the dashboard.
- **Update**:
    - A route `/checks/edit/{check_id}` (GET, POST).
    - Allows editing the name, interval, and grace period.
- **Delete**:
    - A route `/checks/delete/{check_id}` (POST).
    - Deletes the specified check and redirects to the dashboard.

## 6. Phase 4: Deployment & Refinements

### Step 6.1: Configuration (`app/core/config.py`)
- Move hardcoded values (like database URL, secret key for cookies) to environment variables using Pydantic's `BaseSettings`.

### Step 6.2: Containerization
- Create a `Dockerfile` to build a container image for the application.
- Create a `docker-compose.yml` for local development, defining the app service and a PostgreSQL database service.

### Step 6.3: Database Migrations
- (Optional but recommended) Introduce Alembic for managing database schema changes.

## 7. Future Enhancements

- **Notifications**: Add support for sending notifications (email, webhooks) when a check goes down.
- **Multi-User Support**: Expand the user model and associate checks with specific users.
- **API Keys**: Allow users to manage checks programmatically via a REST API with token authentication.
- **Detailed History**: Log every ping and status change for audit and debugging.
- **UI Improvements**: Use a modern CSS framework and add client-side interactivity.
