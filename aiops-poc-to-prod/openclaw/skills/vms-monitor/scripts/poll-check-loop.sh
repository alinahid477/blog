#!/usr/bin/env bash
# poll-check-loop.sh
#
# Calls poll-check-services.py up to MAX_POLLS times (POLL_INTERVAL seconds
# apart) and breaks as soon as a terminal STATUS is seen.
#
# Each Python invocation is a fresh subprocess (fresh sockets / DNS context).
# The sleep between polls uses no network.
#
# Usage:
#   bash poll-check-loop.sh <job-id>
#
# Output (stdout, machine-parseable — always on their own lines):
#   STATUS=<status>               relayed from poll-check-services.py
#   REPORT_PATH=<path>            relayed when STATUS=successful
#   LOOP_RESULT=successful        terminal — job done, proceed
#   LOOP_RESULT=still_running     non-terminal after MAX_POLLS — call again
#   LOOP_RESULT=<failed|error|canceled>   terminal — job failed
#   LOOP_RESULT=network_error     too many consecutive network errors
#
# Exit codes:
#   0  — still running after MAX_POLLS polls; call this script again
#   10 — job successful; REPORT_PATH is in stdout
#   1  — job ended with a failure status
#   2  — exceeded MAX_NET_ERRORS consecutive network errors

JOB_ID="${1:?ERROR: job-id is required. Usage: $0 <job-id>}"
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"

MAX_POLLS=5
POLL_INTERVAL=15
MAX_NET_ERRORS=5

net_errors=0

for poll in $(seq 1 $MAX_POLLS); do
    echo "  [Poll ${poll}/${MAX_POLLS}] Checking job ${JOB_ID}..."

    # Capture stdout for parsing; stderr flows directly to the terminal
    OUTPUT=$(cd "$SCRIPTS_DIR" && python3 poll-check-services.py --job-id "$JOB_ID")
    EXIT_CODE=$?

    # Relay the Python script's stdout so the agent can see STATUS / REPORT_PATH
    echo "$OUTPUT"

    # ── Handle network / HTTP errors (exit 2 from poll script) ───────────────
    if [ "$EXIT_CODE" -eq 2 ]; then
        net_errors=$((net_errors + 1))
        echo "  Network error (${net_errors}/${MAX_NET_ERRORS} consecutive)."
        if [ "$net_errors" -ge "$MAX_NET_ERRORS" ]; then
            echo "LOOP_RESULT=network_error"
            exit 2
        fi
        # No sleep adjustment needed — fall through to the sleep below
    else
        net_errors=0

        STATUS=$(echo "$OUTPUT" | grep "^STATUS=" | cut -d= -f2 | tr -d '[:space:]')

        case "$STATUS" in
            successful)
                echo "LOOP_RESULT=successful"
                exit 10
                ;;
            failed|error|canceled)
                echo "LOOP_RESULT=${STATUS}"
                exit 1
                ;;
        esac
        # STATUS=running / pending / waiting — continue loop
    fi

    if [ "$poll" -lt "$MAX_POLLS" ]; then
        echo "  Job still in progress. Waiting ${POLL_INTERVAL}s before next poll..."
        sleep "$POLL_INTERVAL"
    fi
done

echo "LOOP_RESULT=still_running"
exit 0
