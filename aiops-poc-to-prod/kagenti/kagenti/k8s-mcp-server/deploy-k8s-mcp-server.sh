#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<EOF
Usage: $(basename "$0") --phase <toolonly|gatewayintegration> [OPTIONS]

Deploy the kubernetes-mcp-server in one or two phases.

Phases:
  toolonly            Deploy core namespaced resources:
                        01, 02, 02a, 02b, 03a, 03b
                      Waits for the pod to reach 3/3 ready, then deploys 04.
  gatewayintegration  Deploy gateway integration resources:
                        05, 06b, 07d (then waits 30s), 08b, 08c

Options:
  --phase <phase>   Phase to deploy (toolonly or gatewayintegration).
  --help            Show this help message and exit.

Examples:
  $(basename "$0") --phase toolonly
  $(basename "$0") --phase gatewayintegration
EOF
}

if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

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

if [[ -z "$PHASE" ]]; then
  echo "Error: --phase is required." >&2
  usage
  exit 1
fi

apply() {
  local file="$1"
  echo "[*] Applying ${file}..."
  kubectl create -f "${SCRIPT_DIR}/${file}"
  echo "sleeping for 2 seconds..."
  sleep 2
  echo "[+] Done: ${file}"
  echo
}

case "$PHASE" in
  toolonly)
    echo "========================================="
    echo " Phase: toolonly"
    echo "========================================="
    echo

    apply 01-namespace.yaml
    apply 02-openshift-rbac.yaml
    apply 02a-mcp-servers-authbridge-scc.yaml
    apply 02b-system-openshift-scc-kagenti-authbridge.yaml
    apply 03a-deployment.yaml
    apply 03b-k8s-mcp-server-service-internal.yaml

    echo "[*] Waiting for deployment 'kubernetes-mcp-server' in namespace 'mcp-servers' to be fully ready (3/3 containers)..."
    kubectl rollout status deployment/kubernetes-mcp-server -n mcp-servers --timeout=5m
    echo "[+] Deployment is ready."
    echo

    apply 04-agent-runtime.yaml

    echo "========================================="
    echo " Phase toolonly complete."
    echo "========================================="
    ;;

  gatewayintegration)
    echo "========================================="
    echo " Phase: gatewayintegration"
    echo "========================================="
    echo

    apply 05-mcp-servers-reference-grant.yaml
    apply 06b-httproute-internal.yaml
    apply 07d-mcpserverregistration-internal.yaml

    echo "[*] Waiting 30 seconds for MCPServerRegistration to reconcile..."
    for i in $(seq 30 -1 1); do
      printf "\r    %2ds remaining..." "$i"
      sleep 1
    done
    echo
    echo "[+] Wait complete."
    echo

    echo
    echo "--------------------------------"
    echo "kubectl get mcpserverregistration -n mcp-system"
    kubectl get mcpserverregistration -n mcp-system
    echo "--------------------------------"
    echo

    apply 08b-gateway-auth-policy.yaml
    apply 08c-mcp-gateway-for-agents.yaml

    echo "========================================="
    echo " Phase gatewayintegration complete."
    echo "========================================="
    echo
    echo "[*] Watching MCPServerRegistration status (Ctrl+C to exit)..."
    kubectl get mcpserverregistration -n mcp-system -w
    ;;

  *)
    echo "Error: unknown phase '$PHASE'. Must be 'toolonly' or 'gatewayintegration'." >&2
    usage
    exit 1
    ;;
esac
