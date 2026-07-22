#!/usr/bin/env bash
# fix_apps.sh
# Self-contained remediation for backendapp, configreader, frontendapp,
# and mybusybox. All if/else logic is inside this script.
# The agent simply calls: bash fix_apps.sh <app-name>
#
# Supported app names: backendapp | configreader | frontendapp | mybusybox
#
# Logic per app (dbapps):
#   - Check if pods are running
#     → If NO pods  : restart the deployment
#     → If pods OK  : check if the app's Service exists
#         → If Service MISSING : recreate it via kubectl apply
#         → If Service OK      : restart the deployment (pods up but unhealthy)
#
# Logic for mybusybox (test):
#   - Restart the deployment (only available remediation)

set -uo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: fix_apps.sh <app-name>" >&2
  echo "Supported: backendapp | configreader | frontendapp | mybusybox" >&2
  exit 1
fi

APP="${1}"

# ── App configuration table ───────────────────────────────────────────────────
case "${APP}" in
  backendapp)
    NAMESPACE="dbapps"
    DEPLOYMENT="backendapp"
    SERVICE_NAME="backendapp-svc"
    APP_PORT=5001
    ;;
  configreader)
    NAMESPACE="dbapps"
    DEPLOYMENT="configreader"
    SERVICE_NAME="configreader-svc"
    APP_PORT=5000
    ;;
  frontendapp)
    NAMESPACE="dbapps"
    DEPLOYMENT="frontendapp"
    SERVICE_NAME="frontendapp-svc"
    APP_PORT=8080
    ;;
  mybusybox)
    NAMESPACE="test"
    DEPLOYMENT="mybusybox"
    SERVICE_NAME=""   # mybusybox: no service check, just restart
    APP_PORT=0
    ;;
  *)
    echo "[fix_apps] ERROR: Unknown app '${APP}'. Supported: backendapp, configreader, frontendapp, mybusybox." >&2
    exit 1
    ;;
esac

echo "[fix_apps] Remediating app='${APP}' namespace='${NAMESPACE}'..."

# ── mybusybox: restart only ───────────────────────────────────────────────────
if [ "${APP}" = "mybusybox" ]; then
  echo "[fix_apps] mybusybox: issuing rollout restart..."
  kubectl rollout restart "deployment/${DEPLOYMENT}" -n "${NAMESPACE}"
  EXIT_CODE=$?
  if [ "${EXIT_CODE}" -eq 0 ]; then
    echo "[fix_apps] Rollout restart issued for '${DEPLOYMENT}' in namespace '${NAMESPACE}'."
    exit 0
  else
    echo "[fix_apps] ERROR: rollout restart failed (exit ${EXIT_CODE})." >&2
    exit 1
  fi
fi

# ── dbapps apps: pod check → service check ────────────────────────────────────

# Step A: Check if pods are running
POD_COUNT=$(kubectl get pods -n "${NAMESPACE}" \
  --selector="app=${DEPLOYMENT}" \
  --field-selector="status.phase=Running" \
  --no-headers 2>/dev/null | grep -c . || true)

echo "[fix_apps] Running pods for '${DEPLOYMENT}': ${POD_COUNT}"

if [ "${POD_COUNT}" -eq 0 ]; then
  # No running pods — restart deployment
  echo "[fix_apps] No running pods found. Issuing rollout restart for '${DEPLOYMENT}'..."
  kubectl rollout restart "deployment/${DEPLOYMENT}" -n "${NAMESPACE}"
  EXIT_CODE=$?
  if [ "${EXIT_CODE}" -eq 0 ]; then
    echo "[fix_apps] Rollout restart issued successfully."
    exit 0
  else
    echo "[fix_apps] ERROR: rollout restart failed (exit ${EXIT_CODE})." >&2
    exit 1
  fi
fi

# Step B: Pods are running — check the Service
echo "[fix_apps] Pods are running. Checking Service '${SERVICE_NAME}'..."

if kubectl get svc "${SERVICE_NAME}" -n "${NAMESPACE}" &>/dev/null; then
  # Service exists but pods are up and still erroring — restart
  echo "[fix_apps] Service '${SERVICE_NAME}' exists. Pods running but errors detected."
  echo "[fix_apps] Issuing rollout restart for '${DEPLOYMENT}'..."
  kubectl rollout restart "deployment/${DEPLOYMENT}" -n "${NAMESPACE}"
  EXIT_CODE=$?
  if [ "${EXIT_CODE}" -eq 0 ]; then
    echo "[fix_apps] Rollout restart issued successfully."
    exit 0
  else
    echo "[fix_apps] ERROR: rollout restart failed (exit ${EXIT_CODE})." >&2
    exit 1
  fi

else
  # Service is missing — recreate it
  echo "[fix_apps] Service '${SERVICE_NAME}' NOT found. Recreating..."

  kubectl apply -f - -n "${NAMESPACE}" <<YAML
apiVersion: v1
kind: Service
metadata:
  name: ${SERVICE_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: ${DEPLOYMENT}
    managed-by: k8s-namespace-monitor
  annotations:
    k8s-namespace-monitor/created-by: fix_apps.sh
    k8s-namespace-monitor/created-at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
spec:
  selector:
    app: ${DEPLOYMENT}
  ports:
    - name: http
      protocol: TCP
      port: ${APP_PORT}
      targetPort: ${APP_PORT}
  type: ClusterIP
YAML

  if kubectl get svc "${SERVICE_NAME}" -n "${NAMESPACE}" &>/dev/null; then
    echo "[fix_apps] Service '${SERVICE_NAME}' recreated successfully."
    exit 0
  else
    echo "[fix_apps] ERROR: Service not found after apply." >&2
    exit 1
  fi
fi