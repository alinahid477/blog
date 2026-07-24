#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<EOF
Usage: $(basename "$0") --phase <toolonly|gatewayintegration>

Delete kubernetes-mcp-server resources in reverse deployment order.

Phases:
  gatewayintegration  Delete gateway integration resources in reverse order:
                        08c, 08b, 07d, 06b, 05
  toolonly            Delete core resources in reverse order:
                        04, 03b, 03a, 02b, 02a, 02
                      Prompts before deleting 01 (namespace).

Options:
  --phase <phase>   Phase to delete (toolonly or gatewayintegration).
  --help            Show this help message and exit.

Examples:
  $(basename "$0") --phase gatewayintegration
  $(basename "$0") --phase toolonly
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

remove() {
  local file="$1"
  echo "[*] Deleting ${file}..."
  kubectl delete -f "${SCRIPT_DIR}/${file}" --ignore-not-found
  echo "sleeping for 2 seconds..."
  sleep 2
  echo "[+] Done: ${file}"
  echo
}

case "$PHASE" in
  gatewayintegration)
    echo "========================================="
    echo " Phase: gatewayintegration (teardown)"
    echo "========================================="
    echo

    remove 08c-mcp-gateway-for-agents.yaml
    remove 08b-gateway-auth-policy.yaml
    remove 07d-mcpserverregistration-internal.yaml
    remove 06b-httproute-internal.yaml
    remove 05-mcp-servers-reference-grant.yaml

    echo "========================================="
    echo " Phase gatewayintegration teardown complete."
    echo "========================================="
    ;;

  toolonly)
    echo "========================================="
    echo " Phase: toolonly (teardown)"
    echo "========================================="
    echo

    remove 04-agent-runtime.yaml
    remove 03a-deployment.yaml
    remove 03b-k8s-mcp-server-service-internal.yaml
    remove 02b-system-openshift-scc-kagenti-authbridge.yaml
    remove 02a-mcp-servers-authbridge-scc.yaml
    remove 02-openshift-rbac.yaml

    echo "[?] Do you also want to delete 01-namespace.yaml (mcp-servers namespace)? [y/N]"
    read -r CONFIRM
    if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
      remove 01-namespace.yaml
      echo "[+] Namespace deleted."
    else
      echo "[-] Skipping namespace deletion."
    fi

    echo "========================================="
    echo " Phase toolonly teardown complete."
    echo "========================================="
    ;;

  *)
    echo "Error: unknown phase '$PHASE'. Must be 'toolonly' or 'gatewayintegration'." >&2
    usage
    exit 1
    ;;
esac
