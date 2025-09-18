# Stage 1: Build a custom Caddy with required plugins
FROM caddy:2-builder AS caddy-builder
RUN xcaddy build \
    --with github.com/mholt/caddy-ratelimit \
    --with github.com/caddyserver/cache-handler

# Stage 2: Runtime image
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install minimal runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    bash \
    && rm -rf /var/lib/apt/lists/*

# Copy custom Caddy binary from builder
COPY --from=caddy-builder /usr/bin/caddy /usr/bin/caddy


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Copy Caddy configuration
COPY Caddyfile /etc/caddy/Caddyfile

# Create cache directory for Caddy
RUN mkdir -p /var/cache/caddy && chown -R nobody:nogroup /var/cache/caddy

# Create a startup script
RUN echo '#!/bin/bash\n\
set -euo pipefail\n\
\n\
ALEMBIC_MAX_TRIES=${ALEMBIC_MAX_TRIES:-30}\n\
ALEMBIC_SLEEP_SECONDS=${ALEMBIC_SLEEP_SECONDS:-2}\n\
\n\
echo "Running database migrations..."\n\
attempt=1\n\
until alembic upgrade head; do\n\
  if [ $attempt -ge $ALEMBIC_MAX_TRIES ]; then\n\
    echo "Migrations failed after $attempt attempts"\n\
    exit 1\n\
  fi\n\
  echo "Alembic not ready (attempt $attempt/$ALEMBIC_MAX_TRIES). Retrying in ${ALEMBIC_SLEEP_SECONDS}s..."\n\
  attempt=$((attempt+1))\n\
  sleep "$ALEMBIC_SLEEP_SECONDS"\n\
done\n\
echo "Migrations complete."\n\
\n\
# Start FastAPI in the background\n\
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --log-level "${UVICORN_LOG_LEVEL:-info}" &\n\
FASTAPI_PID=$!\n\
\n\
# Start Caddy in the foreground\n\
caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &\n\
CADDY_PID=$!\n\
\n\
# Function to forward signals\n\
forward_signal() {\n\
  kill -$1 $FASTAPI_PID\n\
  kill -$1 $CADDY_PID\n\
}\n\
\n\
# Handle signals\n\
trap "forward_signal TERM" TERM\n\
trap "forward_signal INT" INT\n\
\n\
# Wait for any process to exit\n\
wait -n\n\
\n\
# Exit with status of process that exited first\n\
exit $?\n' > /app/start-with-caddy.sh && chmod +x /app/start-with-caddy.sh

EXPOSE 8080

CMD ["./start-with-caddy.sh"]
