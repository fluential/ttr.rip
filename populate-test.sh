#!/bin/bash

# This script populates the ttl.rip application with sample health checks
# to test pagination and statistics display.

# --- Configuration ---
BASE_URL="http://localhost:8000"
NUM_CHECKS=30

# --- Script ---
if [ -z "${AUTH_KEY}" ]; then
  echo "Error: AUTH_KEY environment variable is not set."
  echo "Please set it to your 32-character access key before running this script."
  echo "Example: export AUTH_KEY=your-key-here"
  exit 1
fi

echo "Creating ${NUM_CHECKS} sample checks..."
echo "Using auth key from AUTH_KEY environment variable."
echo "Targeting API at: ${BASE_URL}"
echo "------------------------------------"

for i in $(seq 1 $NUM_CHECKS)
do
  # Randomize intervals for more varied data
  INTERVAL=$(( ( RANDOM % 200 ) + 60 )) # 1 to 4 minutes
  GRACE=$(( ( RANDOM % 50 ) + 10 ))     # 10 to 60 seconds

  # Construct JSON payload
  JSON_PAYLOAD=$(cat <<EOF
{
  "name": "Sample Check ${i}",
  "interval_seconds": ${INTERVAL},
  "grace_seconds": ${GRACE}
}
EOF
)

  # Send POST request using curl
  echo "Creating check ${i}..."
  curl -s -X POST "${BASE_URL}/api/v1/checks" \
    -H "Content-Type: application/json" \
    -H "X-Auth-Key: ${AUTH_KEY}" \
    -d "${JSON_PAYLOAD}" > /dev/null
  
  # Add a small delay to avoid overwhelming the server
  sleep 0.1
done

echo "------------------------------------"
echo "Population complete."
echo "Created ${NUM_CHECKS} checks for user with key ${AUTH_KEY}."
echo "You can now visit the dashboard to see the results."
