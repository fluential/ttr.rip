#!/bin/bash

# This script populates the ttl.rip application with sample health checks
# to test pagination and statistics display.

# --- Configuration ---
BASE_URL="http://localhost:8000"
# Replace with a valid 16-digit key from your application's UI
AUTH_KEY="1234567890123456" 
NUM_CHECKS=30

# --- Script ---
echo "Creating ${NUM_CHECKS} sample checks..."
echo "Using Auth Key: ${AUTH_KEY}"
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
