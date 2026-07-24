#!/usr/bin/env bash
# Test the MCP Gateway full flow (initialize → tools/list → tools/call).
# Based on: https://github.com/Kuadrant/mcp-gateway/blob/main/docs/guides/register-mcp-servers.md#step-5-test-tool-discovery
#
# Runs directly on the host (no kubectl pod) because the URL is the external
# OpenShift route, reachable from outside the cluster.
#
# Usage:
#   ./test-mcp-gateway.sh --option <secured|insecured> [<tool_name> [<field_selector>]]
#
#   secured   — all requests carry an Authorization: Bearer <keycloak-token> header.
#   insecured — all requests are sent without any Authorization header.
set -euo pipefail

# ─── Defaults ────────────────────────────────────────────────────────────────
KEYCLOAK_URL="https://keycloak-keycloak.apps.<your-cluster-domain>"
REALM="kagenti"
MCP_CLIENT_ID="mcp-gateway-custom"
MCP_CLIENT_SECRET="your-secret-here"
URL="https://mcp-gateway-gateway-system.apps.<your-cluster-domain>/mcp"
HEADERS_FILE="/tmp/mcp_headers_$$"   # $$ = PID avoids collisions with parallel runs

# ─── Argument parsing ─────────────────────────────────────────────────────────
usage() {
  cat <<EOF
Usage: $(basename "$0") --option <secured|insecured> [<tool_name> [<field_selector>]]

Options:
  --option secured    Send Authorization: Bearer <token> header on all requests.
                      A Keycloak client-credentials token is fetched first.
  --option insecured  Send all requests without any Authorization header.
  --help              Show this help and exit.

Positional (optional):
  tool_name       MCP tool to call in Steps 3 & 4  (default: kubernetes_namespaces_list)
  field_selector  fieldSelector argument for Step 4 (default: metadata.name=kube-system)

Examples:
  $(basename "$0") --option secured
  $(basename "$0") --option insecured kubernetes_namespaces_list metadata.name=default
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
      break   # remaining positional args
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

TOOL="${1:-kubernetes_namespaces_list}"
FIELD_SELECTOR="${2:-metadata.name=kube-system}"

trap "rm -f ${HEADERS_FILE}" EXIT

# ─── Auth header array ────────────────────────────────────────────────────────
# Populated after token fetch.  Use as: "${AUTH_HEADER[@]}" in curl calls.
# Bash arrays preserve argument boundaries, avoiding word-splitting issues.
TOKEN=""
AUTH_HEADER=()

# ─────────────────────────────────────────────────────────────────────────────
echo "=== Mode: ${OPTION} ===" >&2
echo "=== Tool: ${TOOL} | FieldSelector: ${FIELD_SELECTOR} ===" >&2
echo "=== Target: ${URL} ===" >&2

# ---------------------------------------------------------------------------
# Step 0: Fetch Keycloak token — only needed for secured mode
# ---------------------------------------------------------------------------
if [[ "${OPTION}" == "secured" ]]; then
  echo "" >&2
  echo "=== Step 0: Fetching Keycloak token ===" >&2
  echo "    Keycloak URL : ${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" >&2
  echo "    Client ID    : ${MCP_CLIENT_ID}" >&2

  TOKEN=$(curl -sk -X POST \
    "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=client_credentials" \
    -d "client_id=${MCP_CLIENT_ID}" \
    -d "client_secret=${MCP_CLIENT_SECRET}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token') or d)")

  if [[ -z "${TOKEN}" || "${TOKEN}" == "{"* ]]; then
    echo "ERROR: Failed to fetch token. Check client_id/secret." >&2
    exit 1
  fi
  AUTH_HEADER=(-H "Authorization: Bearer ${TOKEN}")
  echo "    Token acquired." >&2
else
  echo "" >&2
  echo "=== Step 0: Skipping token fetch (insecured mode) ===" >&2
fi

# ---------------------------------------------------------------------------
# Helper: run_step <step_label> <curl args...>
# Runs curl, prints HTTP code + response body, and exits with a clear error
# message if the HTTP code is not in the 2xx range or curl itself fails.
# ---------------------------------------------------------------------------
run_step() {
  local label="$1"
  shift

  echo "" >&2
  echo "=== ${label} ===" >&2

  local body http_code curl_exit=0
  # -s  silent  -w  write HTTP code as last line  -o  body to variable via process sub
  body=$(curl -sk -w "\nHTTP_CODE:%{http_code}" "$@" 2>&1) || curl_exit=$?

  http_code=$(printf '%s' "${body}" | grep '^HTTP_CODE:' | cut -d: -f2 | tr -d '[:space:]')
  body=$(printf '%s' "${body}" | grep -v '^HTTP_CODE:')

  echo "--- HTTP code ---" >&2
  if [[ -z "${http_code}" ]]; then
    echo "  (no response — curl exit code: ${curl_exit})" >&2
  else
    echo "  ${http_code}" >&2
  fi

  echo "--- response body ---" >&2
  if [[ -n "${body}" ]]; then
    printf '%s\n' "${body}" | python3 -m json.tool 2>/dev/null \
      || printf '%s\n' "${body}"
  else
    echo "  (empty)" >&2
  fi

  if [[ -z "${http_code}" ]]; then
    echo "" >&2
    echo "ERROR: ${label} — curl failed with exit code ${curl_exit}." >&2
    echo "  Check: network connectivity, TLS/certificate issues, or URL '${URL}'." >&2
    exit 1
  fi

  if [[ "${http_code}" != 2* ]]; then
    echo "" >&2
    echo "ERROR: ${label} returned HTTP ${http_code} (expected 2xx)." >&2
    echo "  Possible causes:" >&2
    case "${http_code}" in
      401) echo "  401 Unauthorized — token missing, expired, or wrong client_id/secret." >&2 ;;
      403) echo "  403 Forbidden    — token valid but not authorized for this resource." >&2 ;;
      404) echo "  404 Not Found    — wrong URL or MCP endpoint not registered." >&2 ;;
      503) echo "  503 Unavailable  — MCP gateway pod down or backend not reachable." >&2 ;;
      000) echo "  000              — curl could not connect (DNS failure, refused, timeout)." >&2 ;;
      *)   echo "  Check response body above for details." >&2 ;;
    esac
    exit 1
  fi

  # Return the body so callers can capture it
  printf '%s' "${body}"
}

# ---------------------------------------------------------------------------
# Step 1: initialize — dump headers to file, extract Mcp-Session-Id.
# We need -D for the headers file so we handle this step manually.
# ---------------------------------------------------------------------------
echo "" >&2
echo "=== Step 1: initialize ===" >&2

HTTP_CODE_FILE="/tmp/mcp_http_code_$$"
trap "rm -f ${HEADERS_FILE} ${HTTP_CODE_FILE}" EXIT

echo "--- AUTH_HEADER: ${AUTH_HEADER[*]:-"(none)"} ---" >&2
echo "" >&2

INIT_BODY=$(curl -sk \
  -D "${HEADERS_FILE}" \
  -w "\nHTTP_CODE:%{http_code}" \
  -X POST "${URL}" \
  "${AUTH_HEADER[@]}" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test-mcp-gateway","version":"1.0.0"}}}' \
  2>&1) || true

INIT_HTTP_CODE=$(printf '%s' "${INIT_BODY}" | grep '^HTTP_CODE:' | cut -d: -f2 | tr -d '[:space:]')
INIT_BODY=$(printf '%s' "${INIT_BODY}" | grep -v '^HTTP_CODE:')

echo "--- HTTP code ---" >&2
echo "  ${INIT_HTTP_CODE:-"(no response — curl failed)"}" >&2
echo "--- response headers ---" >&2
cat "${HEADERS_FILE}" >&2
echo "--- response body ---" >&2
printf '%s\n' "${INIT_BODY}" | python3 -m json.tool 2>/dev/null || printf '%s\n' "${INIT_BODY}"

if [[ -z "${INIT_HTTP_CODE}" ]]; then
  echo "" >&2
  echo "ERROR: Step 1 (initialize) — curl could not connect." >&2
  echo "  Check network connectivity and URL: ${URL}" >&2
  exit 1
fi

if [[ "${INIT_HTTP_CODE}" != 2* ]]; then
  echo "" >&2
  echo "ERROR: Step 1 (initialize) returned HTTP ${INIT_HTTP_CODE} (expected 2xx)." >&2
  case "${INIT_HTTP_CODE}" in
    401) echo "  401 Unauthorized — token missing, expired, or wrong client credentials." >&2 ;;
    403) echo "  403 Forbidden    — token valid but not authorized." >&2 ;;
    404) echo "  404 Not Found    — wrong URL or gateway not deployed." >&2 ;;
    503) echo "  503 Unavailable  — MCP gateway pod is down or not ready." >&2 ;;
    000) echo "  000              — DNS failure, connection refused, or timeout." >&2 ;;
  esac
  exit 1
fi

SESSION_ID=$(grep -i "mcp-session-id:" "${HEADERS_FILE}" | cut -d' ' -f2 | tr -d '\r\n' || true)

if [[ -z "${SESSION_ID}" ]]; then
  echo "" >&2
  echo "ERROR: Step 1 succeeded (HTTP ${INIT_HTTP_CODE}) but no Mcp-Session-Id header in response." >&2
  echo "  This usually means the gateway is running but the MCP server is not registered." >&2
  exit 1
fi
echo "" >&2
echo "Session ID: ${SESSION_ID}" >&2

# ---------------------------------------------------------------------------
# Step 2: tools/list
# ---------------------------------------------------------------------------
run_step "Step 2: tools/list" \
  -X POST "${URL}" \
  "${AUTH_HEADER[@]}" \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: ${SESSION_ID}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# ---------------------------------------------------------------------------
# Step 3: tools/call
# ---------------------------------------------------------------------------
echo "" >&2
run_step "Step 3: tools/call (${TOOL})" \
  -X POST "${URL}" \
  "${AUTH_HEADER[@]}" \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: ${SESSION_ID}" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"${TOOL}\",\"arguments\":{}}}"

# ---------------------------------------------------------------------------
# Step 4: tools/call with fieldSelector
# ---------------------------------------------------------------------------
echo "" >&2
run_step "Step 4: tools/call (${TOOL} — fieldSelector: ${FIELD_SELECTOR})" \
  -X POST "${URL}" \
  "${AUTH_HEADER[@]}" \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: ${SESSION_ID}" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"${TOOL}\",\"arguments\":{\"fieldSelector\":\"${FIELD_SELECTOR}\"}}}"

echo "" >&2
