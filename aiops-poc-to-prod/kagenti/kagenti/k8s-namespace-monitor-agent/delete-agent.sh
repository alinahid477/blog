#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<EOF
Usage: $(basename "$0")

Delete the k8s-namespace-monitor-agent in reverse deployment order:
  05-authproxy-routes.yaml
  04-agentruntime.yaml
  03-deployment.yaml               ← waits 30 seconds before deleting
  02b-system-openshift-scc-kagenti-authbridge.yaml
  02a-agent-authbridge-scc.yaml
  02-rbac.yaml
  01-namespace.yaml                ← prompts for confirmation

Options:
  --help   Show this help and exit.
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

remove() {
  local file="$1"
  echo "[*] Deleting ${file}..."
  kubectl delete -f "${SCRIPT_DIR}/${file}" --ignore-not-found
  echo "    Sleeping 2 seconds..."
  sleep 2
  echo "[+] Done: ${file}"
  echo
}

echo "========================================="
echo " Deleting k8s-namespace-monitor-agent"
echo "========================================="
echo

remove 05-authproxy-routes.yaml
remove 04-agentruntime.yaml

echo "[*] Waiting 30 seconds before deleting the Deployment (allows AgentRuntime"
echo "    cleanup and sidecar de-registration to complete)..."
for i in $(seq 30 -1 1); do
  printf "\r    %2ds remaining..." "$i"
  sleep 1
done
echo
echo "[+] Wait complete."
echo

remove 03-deployment.yaml
remove 02b-system-openshift-scc-kagenti-authbridge.yaml
remove 02a-agent-authbridge-scc.yaml
remove 02-rbac.yaml

echo "[?] Do you also want to delete 01-namespace.yaml (agents namespace)? [y/N]"
read -r CONFIRM
if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
  remove 01-namespace.yaml
  echo "[+] Namespace deleted."
else
  echo "[-] Skipping namespace deletion."
fi

echo "========================================="
echo " Deletion complete."
echo "========================================="
