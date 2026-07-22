#!/usr/bin/env bash
# do_login.sh
# Ensures a valid OpenShift session exists before the monitoring workflow runs.
# Reads OC_SERVER, OC_USER, OC_PASS from the environment.
# Exits 0 on success. Exits 1 on any failure.

set -uo pipefail
OC_SERVER="${OC_SERVER:-}"
OC_USER="${OC_USER:-}"
OC_PASS="${OC_PASS:-}"
# Step 1 — Validate required environment variables
MISSING=()
[ -z "${OC_SERVER:-}"   ] && MISSING+=("OC_SERVER")
[ -z "${OC_USER:-}"     ] && MISSING+=("OC_USER")
[ -z "${OC_PASS:-}" ] && MISSING+=("OC_PASS")

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "do_login: ERROR Required environment variables not set: ${MISSING[*]}" >&2
  exit 1
fi

# Step 2 — Verify oc is available
if ! command -v oc &>/dev/null; then
  echo "do_login: ERROR 'oc' binary not found on PATH." >&2
  exit 1
fi

# Step 3 — Check if already logged in with a valid, non-expired session
echo "do_login: Checking current login status..."

if oc whoami --request-timeout=10s &>/dev/null; then
  CURRENT_USER="$(oc whoami 2>/dev/null)"
  echo "do_login: Already logged in as '${CURRENT_USER}'. Session is valid. No action needed."
  exit 0
fi

# Step 4 — Session expired or kubeconfig missing — clean up and re-login
echo "do_login: Session is expired or kubeconfig is missing. Performing fresh login..."

KUBECONFIG_FILE="${KUBECONFIG:-$HOME/.kube/config}"

if [ -f "${KUBECONFIG_FILE}" ]; then
  echo "do_login: Removing stale kubeconfig at ${KUBECONFIG_FILE}..."
  rm -f "${KUBECONFIG_FILE}"
  if [ -f "${KUBECONFIG_FILE}" ]; then
    echo "do_login: ERROR Failed to remove stale kubeconfig at ${KUBECONFIG_FILE}." >&2
    exit 1
  fi
  echo "do_login: Stale kubeconfig removed."
fi

mkdir -p "$(dirname "${KUBECONFIG_FILE}")"

# Step 5 — Perform oc login
echo "do_login: Logging in to ${OC_SERVER} as ${OC_USER}..."

LOGIN_OUTPUT="$(oc login "${OC_SERVER}" \
  -u "${OC_USER}" \
  -p "${OC_PASS}" \
  --insecure-skip-tls-verify \
  2>&1)"

LOGIN_EXIT=$?

if [ ${LOGIN_EXIT} -ne 0 ]; then
  echo "do_login: ERROR oc login failed (exit ${LOGIN_EXIT})." >&2
  echo "do_login: oc output: ${LOGIN_OUTPUT}" >&2
  exit 1
fi

echo "do_login: oc login completed. Verifying session..."

# Step 6 — Verify the new session is actually usable
if ! oc whoami --request-timeout=10s &>/dev/null; then
  echo "do_login: ERROR Login appeared to succeed but 'oc whoami' still fails. Session is not usable." >&2
  exit 1
fi

VERIFIED_USER="$(oc whoami 2>/dev/null)"
echo "do_login: Login verified. Authenticated as '${VERIFIED_USER}' on ${OC_SERVER}."
exit 0