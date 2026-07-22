#!/usr/bin/env bash
# get_namespacelogs.sh
# Collects logs from all monitored deployments for the last 1 hour.
# Filters out DEBUG lines at source to keep output clean.
# Writes a single aggregated log file and prints its path as:
#   LOG_FILE=/tmp/k8s-monitoring-agent/logs/aggregated_log_{timestamp}.log
#
# Usage: bash get_namespacelogs.sh
# No arguments required.
#
# Token-efficiency note: by stripping DEBUG lines here, the agent never
# sees them, reducing context consumption in all downstream steps.

set -uo pipefail

OUTDIR="/tmp/k8s-monitoring-agent/logs"
TIMESTAMP=$(date -u +%Y%m%dT%H%M%S)
OUTFILE="${OUTDIR}/aggregated_log_${TIMESTAMP}.log"
ERRORS=0

mkdir -p "${OUTDIR}"

# ── Deployments to monitor ──────────────────────────────────────────────────
# Format: "namespace/deployment"
DEPLOYMENTS=(
  "dbapps/backendapp"
  "dbapps/frontendapp"
  "dbapps/configreader"
  "test/mybusybox"
)

# ── Log level filter ─────────────────────────────────────────────────────────
# Lines are KEPT only if they contain one of these patterns.
# HTTP access log lines (200/301/302/404 etc.) are explicitly excluded.
# DEBUG lines are excluded.
KEEP_PATTERN='\[warn\]|\[error\]|\[crit\]|\[alert\]|\[emerg\]|ERROR|WARN|CRIT|CRITICAL'

# Lines matching this pattern are DROPPED even if they matched KEEP_PATTERN above.
# Covers: HTTP access logs, var: dump lines, load_all_configs banners, ISS data rows.
DROP_PATTERN='HTTP/1\.[01]" [0-9]{3}|^\s*var: |load_all_configs|RealDictRow'

echo "# k8s-namespace-monitor log collection" > "${OUTFILE}"
echo "# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${OUTFILE}"
echo "# Namespaces: dbapps, test | Window: last 1 hour" >> "${OUTFILE}"
echo "#" >> "${OUTFILE}"

for entry in "${DEPLOYMENTS[@]}"; do
  NAMESPACE="${entry%%/*}"
  DEPLOYMENT="${entry##*/}"

  echo "## [${NAMESPACE}/${DEPLOYMENT}]" >> "${OUTFILE}"

  # Attempt to collect logs; capture exit code separately
  RAW_OUTPUT=$(kubectl logs \
    -n "${NAMESPACE}" \
    "deployment/${DEPLOYMENT}" \
    --since=1h \
    --all-containers \
    2>&1) || true

  # Check if kubectl returned an error (no pods, deployment not found, etc.)
  if echo "${RAW_OUTPUT}" | grep -qiE '^error:|not found|no pods'; then
    echo "# WARNING: Could not retrieve logs for ${NAMESPACE}/${DEPLOYMENT}: ${RAW_OUTPUT}" >> "${OUTFILE}"
    echo "WARNING: Could not retrieve logs for ${NAMESPACE}/${DEPLOYMENT}" >&2
    ERRORS=$((ERRORS + 1))
    echo "" >> "${OUTFILE}"
    continue
  fi

  # Filter: keep only relevant log levels, discard DEBUG
  # Step 1: keep only lines with known error-level keywords
  # Step 2: drop access logs, var dumps, and other known noise
  # Step 3: drop DEBUG lines
  FILTERED=$(echo "${RAW_OUTPUT}" \
    | grep -E "${KEEP_PATTERN}" \
    | grep -vE "${DROP_PATTERN}" \
    | grep -viE '\bDEBUG\b')
    
  # FILTERED=$(echo "${RAW_OUTPUT}")
  LINE_COUNT=$(echo "${FILTERED}" | grep -c . || true)

  if [ "${LINE_COUNT}" -eq 0 ]; then
    echo "# INFO: No relevant log lines found for ${NAMESPACE}/${DEPLOYMENT} in the last 1 hour." >> "${OUTFILE}"
  else
    echo "${FILTERED}" >> "${OUTFILE}"
    echo "# INFO: ${LINE_COUNT} relevant lines collected from ${NAMESPACE}/${DEPLOYMENT}" >&2
  fi

  echo "" >> "${OUTFILE}"
done

echo "# END OF AGGREGATED LOG" >> "${OUTFILE}"

# Print the output path in a machine-readable format for the agent to capture
echo "LOG_FILE=${OUTFILE}"

# Exit non-zero if ALL deployments failed — at least one success is acceptable
TOTAL=${#DEPLOYMENTS[@]}
if [ "${ERRORS}" -eq "${TOTAL}" ]; then
  echo "ERROR: Failed to collect logs from all deployments. Aborting." >&2
  exit 1
fi

exit 0