#!/usr/bin/env bash
# Restarts the full local dev setup:
#   1/3  Restart agent deployment in the agents namespace
#   2/3  Restart kubernetes-mcp-server deployment in the mcp-servers namespace
#   3/3  Re-apply AuthBridge ConfigMap patches (07a and 07b)
#
# Use --phase mydeployments to run 1/3 + 2/3 only.
# Use --phase authbridge   to run 3/3 only.
# Omit --phase             to run all three steps.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_PATCH_DIR="$SCRIPT_DIR/k8s-namespace-monitor-agent"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--phase <mydeployments|authbridge>]

Options:
  --phase mydeployments   Run steps 1/3 and 2/3 only:
                            Restart agent and kubernetes-mcp-server deployments.
  --phase authbridge      Run step 3/3 only:
                            Re-apply AuthBridge ConfigMap patches (07a + 07b).
  (no --phase)            Run all three steps in order.
  --help                  Show this help and exit.
EOF
}

PHASE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase)
      PHASE="$2"
      shift 2
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -n "${PHASE}" && "${PHASE}" != "mydeployments" && "${PHASE}" != "authbridge" ]]; then
  echo "Error: --phase must be 'mydeployments' or 'authbridge' (got '${PHASE}')." >&2
  usage
  exit 1
fi

run_mydeployments() {
  echo "=== [1/3] Restarting agent deployment in 'agents' namespace ==="
  kubectl rollout restart deployment/k8s-namespace-monitor-agent -n agents
  kubectl rollout status deployment/k8s-namespace-monitor-agent -n agents --timeout=120s

  echo ""
  echo "=== [2/3] Restarting kubernetes-mcp-server deployment in 'mcp-servers' namespace ==="
  kubectl rollout restart deployment/kubernetes-mcp-server -n mcp-servers
  kubectl rollout status deployment/kubernetes-mcp-server -n mcp-servers --timeout=120s
}

run_authbridge() {
  echo "=== [3/3] Applying AuthBridge ConfigMap patches ==="
  echo "--- 07a: authbridge-runtime-config ---"
  kubectl apply -f "$AGENT_PATCH_DIR/07a-patch-authbridge-runtime-config.yaml"

  echo "--- 07b: authbridge-config-k8s-namespace-monitor-agent ---"
  kubectl apply -f "$AGENT_PATCH_DIR/07b-patch-authbridge-config-k8s-namespace-monitor-agent.yaml"
}

case "${PHASE}" in
  mydeployments)
    run_mydeployments
    ;;
  authbridge)
    echo ""
    run_authbridge
    ;;
  "")
    run_mydeployments
    echo ""
    run_authbridge
    ;;
esac

echo ""
echo "=== Done. ==="
