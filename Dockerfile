# Stage 1: Build a custom Caddy with required plugins
FROM caddy:2-builder AS caddy-builder
RUN xcaddy build \
    --with github.com/mholt/caddy-ratelimit \
    --with github.com/ueffel/caddy-brotli \
    --with github.com/caddyserver/caddy-geoip

# Stage 2: Runtime image
FROM python:3.11-slim

WORKDIR /app

# Install minimal runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    bash \
    && rm -rf /var/lib/apt/lists/*

# Copy custom Caddy binary from builder
COPY --from=caddy-builder /usr/bin/caddy /usr/bin/caddy

# Download GeoIP database
RUN mkdir -p /usr/share/GeoIP/
RUN curl -L -o /usr/share/GeoIP/GeoLite2-Country.mmdb.gz "https://git.io/GeoLite2-Country.mmdb.gz" && \
    gunzip /usr/share/GeoIP/GeoLite2-Country.mmdb.gz

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Copy Caddy configuration
COPY Caddyfile /etc/caddy/Caddyfile

# Create cache directory for Caddy
RUN mkdir -p /var/cache/caddy && chown -R nobody:nogroup /var/cache/caddy

# Create a startup script
RUN echo '#!/bin/bash\n\
# Start FastAPI in the background\n\
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 &\n\
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
