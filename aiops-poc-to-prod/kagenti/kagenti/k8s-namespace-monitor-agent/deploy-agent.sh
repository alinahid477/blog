#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<EOF
Usage: $(basename "$0")

Deploy the k8s-namespace-monitor-agent in the following order:
  01-namespace.yaml
  02-rbac.yaml
  02a-agent-authbridge-scc.yaml
  02b-system-openshift-scc-kagenti-authbridge.yaml
  03-deployment.yaml               ← waits for pod to reach 3/3 ready
  04-agentruntime.yaml
  05-authproxy-routes.yaml

Options:
  --help   Show this help and exit.
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

apply() {
  local file="$1"
  echo "[*] Applying ${file}..."
  kubectl create -f "${SCRIPT_DIR}/${file}"
  echo "    Sleeping 2 seconds..."
  sleep 2
  echo "[+] Done: ${file}"
  echo
}

echo "========================================="
echo " Deploying k8s-namespace-monitor-agent"
echo "========================================="
echo

apply 01-namespace.yaml
apply 02-rbac.yaml
apply 02a-agent-authbridge-scc.yaml
apply 02b-system-openshift-scc-kagenti-authbridge.yaml
apply 03-deployment.yaml

echo "[*] Waiting for deployment 'k8s-namespace-monitor-agent' in namespace 'agents' to be fully ready (3/3 containers)..."
kubectl rollout status deployment/k8s-namespace-monitor-agent -n agents --timeout=5m
echo "[+] Deployment is ready."
echo

apply 04-agentruntime.yaml
apply 05-authproxy-routes.yaml

echo "========================================="
echo " Deployment complete."
echo "========================================="
