FROM python:3.11-slim

WORKDIR /app

# Install build tools for custom Caddy
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    golang \
    && rm -rf /var/lib/apt/lists/*

# Build Caddy with the rate-limit module
RUN go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
RUN /root/go/bin/xcaddy build --with github.com/mholt/caddy-ratelimit
RUN mv ./caddy /usr/bin/caddy

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
