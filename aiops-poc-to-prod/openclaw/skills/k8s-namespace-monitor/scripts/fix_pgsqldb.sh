#!/usr/bin/env bash
# fix_pgsqldb.sh
# Remediates DB connection failures for backendapp in the dbapps namespace.
#
# Logic (no agent branching needed):
#   1. Check if the postgresql Service exists.
#   2. If MISSING  → recreate the Service.
#   3. If PRESENT  → restart deployment/backendapp (Service is fine, app needs bounce).
#
# Usage: bash fix_pgsqldb.sh
# Exit 0 = action taken successfully. Exit 1 = failure.

set -uo pipefail

NAMESPACE="dbapps"
SERVICE_NAME="postgresql"
DEPLOYMENT="backendapp"
DB_PORT=5432

echo "[fix_pgsqldb] Checking postgresql Service in namespace '${NAMESPACE}'..."

if kubectl get svc "${SERVICE_NAME}" -n "${NAMESPACE}" &>/dev/null; then
  # ── Service is present ───────────────────────────────────────────────────────
  echo "[fix_pgsqldb] Service '${SERVICE_NAME}' exists. Restarting deployment '${DEPLOYMENT}'..."
  kubectl rollout restart "deployment/${DEPLOYMENT}" -n "${NAMESPACE}"
  EXIT_CODE=$?
  if [ "${EXIT_CODE}" -eq 0 ]; then
    echo "[fix_pgsqldb] Rollout restart issued successfully for '${DEPLOYMENT}'."
    exit 0
  else
    echo "[fix_pgsqldb] ERROR: kubectl rollout restart failed (exit ${EXIT_CODE})." >&2
    exit 1
  fi

else
  # ── Service is missing ───────────────────────────────────────────────────────
  echo "[fix_pgsqldb] Service '${SERVICE_NAME}' NOT found. Recreating..."

  # Auto-detect pod label
  PGSQL_LABEL=""
  for label in "app=postgresql" "app=postgres" "app.kubernetes.io/name=postgresql"; do
    if kubectl get pods -n "${NAMESPACE}" -l "${label}" --no-headers 2>/dev/null | grep -q .; then
      PGSQL_LABEL="${label}"
      break
    fi
  done

  if [ -z "${PGSQL_LABEL}" ]; then
    echo "[fix_pgsqldb] WARNING: Could not detect pod label. Defaulting to app=postgresql."
    PGSQL_LABEL="app=postgresql"
  fi

  LABEL_KEY="${PGSQL_LABEL%%=*}"
  LABEL_VAL="${PGSQL_LABEL##*=}"

  kubectl apply -f - -n "${NAMESPACE}" <<YAML
apiVersion: v1
kind: Service
metadata:
  name: ${SERVICE_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: postgresql
    managed-by: k8s-namespace-monitor
  annotations:
    k8s-namespace-monitor/created-by: fix_pgsqldb.sh
    k8s-namespace-monitor/created-at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
spec:
  selector:
    ${LABEL_KEY}: ${LABEL_VAL}
  ports:
    - name: pgsql
      protocol: TCP
      port: ${DB_PORT}
      targetPort: ${DB_PORT}
  type: ClusterIP
YAML

  if kubectl get svc "${SERVICE_NAME}" -n "${NAMESPACE}" &>/dev/null; then
    echo "[fix_pgsqldb] Service '${SERVICE_NAME}' recreated successfully."
    exit 0
  else
    echo "[fix_pgsqldb] ERROR: Service creation failed — Service not found after apply." >&2
    exit 1
  fi
fi