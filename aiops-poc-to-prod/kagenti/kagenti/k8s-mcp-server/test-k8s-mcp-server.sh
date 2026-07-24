#!/usr/bin/env bash
# Test the kubernetes-mcp-server directly (bypassing the gateway).
#
# Usage:
#   ./test-k8s-mcp-server.sh --option <secured|insecured>
#
#   secured   — fetches a Keycloak token and hits port 9090 with Authorization header.
#   insecured — hits the internal port 9091 without any token.
set -euo pipefail

NAMESPACE="mcp-servers"
DEPLOYMENT="kubernetes-mcp-server"
KEYCLOAK_URL="https://keycloak-keycloak.apps.<your-cluster-domain>"
REALM="kagenti"

URL_SECURED="http://kubernetes-mcp-server.mcp-servers.svc.cluster.local:9090/mcp"
URL_INSECURED="http://kubernetes-mcp-server-internal.mcp-servers.svc.cluster.local:9091/mcp"

# ─── Argument parsing ─────────────────────────────────────────────────────────
usage() {
  cat <<EOF
Usage: $(basename "$0") --option <secured|insecured>

Options:
  --option secured    Fetch a Keycloak token and send requests to port 9090
                      with Authorization: Bearer <token> header.
  --option insecured  Send requests to port 9091 without any token.
  --help              Show this help and exit.
EOF
}

OPTION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --option)
      OPTION="$2"
      shift 2
      ;;
    --help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown flag: $1" >&2
      usage
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

if [[ -z "${OPTION}" ]]; then
  echo "Error: --option is required." >&2
  usage
  exit 1
fi

if [[ "${OPTION}" != "secured" && "${OPTION}" != "insecured" ]]; then
  echo "Error: --option must be 'secured' or 'insecured' (got '${OPTION}')." >&2
  usage
  exit 1
fi

# ─── Mode-dependent setup ────────────────────────────────────────────────────
TOKEN=""
AUTH_HEADER=()

if [[ "${OPTION}" == "secured" ]]; then
  URL="${URL_SECURED}"

  echo "=== Mode: secured ===" >&2
  echo "=== Target: ${URL} ===" >&2
  echo "" >&2
  echo "=== Fetching Keycloak token ===" >&2

  SECRET=$(kubectl get secrets -n "${NAMESPACE}" \
    -o json \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
for item in data['items']:
  for ref in item.get('metadata', {}).get('ownerReferences', []):
    if ref.get('kind') == 'Deployment' and ref.get('name') == '${DEPLOYMENT}':
      print(item['metadata']['name'])
      sys.exit(0)
print('ERROR: no keycloak credentials secret found for deployment ${DEPLOYMENT}', file=sys.stderr)
sys.exit(1)
")
  echo "    Using secret: ${SECRET}" >&2

  CLIENT_ID=$(kubectl get secret "${SECRET}" -n "${NAMESPACE}" \
    -o jsonpath='{.data.client-id\.txt}' | base64 -d)

  CLIENT_SECRET=$(kubectl get secret "${SECRET}" -n "${NAMESPACE}" \
    -o jsonpath='{.data.client-secret\.txt}' | base64 -d)

  echo "    Client ID: ${CLIENT_ID}" >&2

  RESPONSE=$(curl -s -X POST \
    "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=client_credentials" \
    -d "client_id=${CLIENT_ID}" \
    -d "client_secret=${CLIENT_SECRET}")

  TOKEN=$(echo "${RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

  if [[ -z "${TOKEN}" ]]; then
    echo "ERROR: Failed to fetch token. Check client_id/secret." >&2
    exit 1
  fi
  AUTH_HEADER=(-H "Authorization: Bearer ${TOKEN}")
  echo "    Token acquired." >&2
else
  URL="${URL_INSECURED}"

  echo "=== Mode: insecured ===" >&2
  echo "=== Target: ${URL} ===" >&2
  echo "" >&2
  echo "=== Skipping token fetch (insecured mode) ===" >&2
fi

# ─── Send request ────────────────────────────────────────────────────────────
printf "\n=== Sending tools/call to %s ===\n\n" "${URL}" >&2

kubectl run test-mcp --rm -it --restart=Never --image=curlimages/curl -- \
  curl -s -X POST \
  "${URL}" \
  "${AUTH_HEADER[@]}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"namespaces_list","arguments":{}}}'
