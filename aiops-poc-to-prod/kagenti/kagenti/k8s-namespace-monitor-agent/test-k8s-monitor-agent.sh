#!/usr/bin/env bash
# test-k8s-monitor-agent.sh
#
# Triggers the k8s-namespace-monitor agent A2A endpoint from a local machine
# that has a valid kubeconfig.
#
# The script:
#   1. Obtains a bearer token (Keycloak client-credentials or short-lived SA token)
#   2. Launches a temporary curl pod inside the cluster (same network as the agent)
#   3. POSTs the A2A sendMessage JSON-RPC request to the agent Service
#   4. Prints the response (pretty-printed JSON)
#   5. Deletes the temporary pod on exit (even on Ctrl-C or error)
#
# Token strategies
# ─────────────────────────────────────────────────────────────────────────────
#   keycloak  (default)
#     Reads the Keycloak client-id / client-secret from the Secret that the
#     Kagenti operator creates and owns on behalf of the agent Deployment, then
#     does a client_credentials grant against Keycloak.
#     Requires: --keycloak-url and --realm (or edit the defaults below).
#
#   sa
#     Calls `kubectl create token <serviceaccount>` to obtain a short-lived
#     Kubernetes/OpenShift ServiceAccount token (no Keycloak needed).
#     Useful when Kagenti AuthBridge is not enforcing Keycloak tokens, or when
#     testing on a plain Kubernetes cluster.
#
# Usage
# ─────────────────────────────────────────────────────────────────────────────
#   ./test-k8s-monitor-agent.sh                              # Keycloak, defaults
#   ./test-k8s-monitor-agent.sh --token-strategy sa          # SA token
#   ./test-k8s-monitor-agent.sh \
#     --keycloak-url https://keycloak.example.com \
#     --realm kagenti
#
# The A2A call is long-running (the agent executes its full 7-step pipeline).
# curl timeout is set to 10 minutes; adjust CURL_TIMEOUT_SECS if needed.
# ─────────────────────────────────────────────────────────────────────────────

# set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
NAMESPACE="agents"
DEPLOYMENT="k8s-namespace-monitor-agent"
SERVICE_HOST="k8s-namespace-monitor-agent.agents.svc.cluster.local"
SERVICE_PORT="8000"
TOKEN_STRATEGY="keycloak"
KEYCLOAK_URL="https://keycloak-keycloak.apps.<your-cluster-domain>"
REALM="kagenti"
POD_NAME="test-agent-trigger"
CURL_TIMEOUT_SECS="600"   # 10 min — agent runs the full remediation pipeline

# ── Parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --token-strategy)  TOKEN_STRATEGY="$2";  shift 2 ;;
    --keycloak-url)    KEYCLOAK_URL="$2";    shift 2 ;;
    --realm)           REALM="$2";           shift 2 ;;
    --namespace)       NAMESPACE="$2";       shift 2 ;;
    --timeout)         CURL_TIMEOUT_SECS="$2"; shift 2 ;;
    --help|-h)
      sed -n '/^# Usage/,/^# ──/p' "$0"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Run with --help for usage." >&2
      exit 1
      ;;
  esac
done

AGENT_URL="http://${SERVICE_HOST}:${SERVICE_PORT}/jsonrpc/"

# ── Cleanup on exit / Ctrl-C ─────────────────────────────────────────────────
cleanup() {
  local code=$?
  echo "" >&2
  if kubectl get pod "${POD_NAME}" -n "${NAMESPACE}" &>/dev/null 2>&1; then
    echo "Deleting temporary pod ${POD_NAME} in namespace ${NAMESPACE} ..." >&2
    kubectl delete pod "${POD_NAME}" -n "${NAMESPACE}" --ignore-not-found --grace-period=0 >&2
    echo "Pod deleted." >&2
  fi
  exit "${code}"
}
trap cleanup EXIT INT TERM

# ── Verify kubeconfig is working ─────────────────────────────────────────────
echo "Verifying cluster connectivity ..." >&2
kubectl cluster-info --request-timeout=10s >/dev/null

# ── Obtain bearer token ───────────────────────────────────────────────────────
TOKEN=""

if [[ "${TOKEN_STRATEGY}" == "keycloak" ]]; then
  echo "" >&2
  echo "Strategy: Keycloak client_credentials" >&2
  echo "Looking for Kagenti-owned Keycloak secret for Deployment '${DEPLOYMENT}' in namespace '${NAMESPACE}' ..." >&2

  SECRET=$(kubectl get secrets -n "${NAMESPACE}" -o json \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
for item in data['items']:
    for ref in item.get('metadata', {}).get('ownerReferences', []):
        if ref.get('kind') == 'Deployment' and ref.get('name') == '${DEPLOYMENT}':
            print(item['metadata']['name'])
            sys.exit(0)
print('ERROR: no Keycloak credentials secret found owned by Deployment ${DEPLOYMENT}.', file=sys.stderr)
print('Tip: try --token-strategy sa if AuthBridge / Keycloak is not configured.', file=sys.stderr)
sys.exit(1)
")

  echo "Found secret: ${SECRET}" >&2

  CLIENT_ID=$(kubectl get secret "${SECRET}" -n "${NAMESPACE}" \
    -o jsonpath='{.data.client-id\.txt}' | base64 -d)
  CLIENT_SECRET=$(kubectl get secret "${SECRET}" -n "${NAMESPACE}" \
    -o jsonpath='{.data.client-secret\.txt}' | base64 -d)

  echo "client_id: ${CLIENT_ID}" >&2
  echo "Fetching token from ${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token ..." >&2

  TOKEN_RESPONSE=$(curl -sf -X POST \
    "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=client_credentials" \
    -d "client_id=${CLIENT_ID}" \
    -d "client_secret=${CLIENT_SECRET}")

  TOKEN=$(echo "${TOKEN_RESPONSE}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

  echo "Keycloak token obtained." >&2

elif [[ "${TOKEN_STRATEGY}" == "sa" ]]; then
  echo "" >&2
  echo "Strategy: Kubernetes ServiceAccount token" >&2
  # kubectl create token asks the API server to sign a short-lived JWT for the
  # named ServiceAccount. It never touches the pod filesystem — it works from
  # any machine that has kubeconfig access to the cluster.
  echo "Requesting 10-minute token for SA '${DEPLOYMENT}' in '${NAMESPACE}' ..." >&2
  TOKEN=$(kubectl create token "${DEPLOYMENT}" -n "${NAMESPACE}" --duration=10m)
  echo "SA token created." >&2

else
  echo "ERROR: Unknown --token-strategy '${TOKEN_STRATEGY}'. Valid values: keycloak, sa" >&2
  exit 1
fi

# ── Delete any leftover pod from a previous failed run ───────────────────────
if kubectl get pod "${POD_NAME}" -n "${NAMESPACE}" &>/dev/null 2>&1; then
  echo "Removing leftover pod '${POD_NAME}' from a previous run ..." >&2
  kubectl delete pod "${POD_NAME}" -n "${NAMESPACE}" --ignore-not-found --grace-period=0 >&2
fi

# ── A2A payload ───────────────────────────────────────────────────────────────
# The agent ignores the text content — any message triggers the full pipeline.
A2A_PAYLOAD='{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":{"role":"ROLE_USER","parts":[{"text":"run k8s namespace monitoring"}]}}}'

# ── Launch temporary curl pod and send A2A request ────────────────────────────
echo "" >&2
echo "Launching temporary pod '${POD_NAME}' in namespace '${NAMESPACE}' ..." >&2
echo "Sending A2A message/send to ${AGENT_URL} (timeout: ${CURL_TIMEOUT_SECS}s) ..." >&2
echo "The agent runs a full 7-step pipeline — this may take a few minutes." >&2
echo "" >&2

# kubectl run --rm --restart=Never:
#   - Creates the pod, streams its stdout/stderr to your terminal
#   - --rm: deletes the pod automatically after the container exits (success or failure)
#   - --restart=Never: pod runs once and exits (no restart loop)
#
# The TOKEN is obtained here on the local machine via kubectl/Keycloak and
# passed as a plain argument to the curl process inside the pod. This is why
# `cat /var/run/secrets/.../token` would NOT work from localhost — that path
# only exists inside the running pod at container start time.

kubectl run "${POD_NAME}" \
  --rm \
  --restart=Never \
  --image=curlimages/curl:latest \
  -n "${NAMESPACE}" \
  -i \
  -- \
  curl -s \
    --max-time "${CURL_TIMEOUT_SECS}" \
    -X POST "${AGENT_URL}" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -d "${A2A_PAYLOAD}" \
#| python3 -m json.tool

echo "" >&2
echo "Done." >&2
