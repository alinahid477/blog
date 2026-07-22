#!/usr/bin/env bash
set -euo pipefail

export OPENCLAW_GATEWAY_TOKEN=<your-openclaw-gateway-token>

export OPENCLAW_SANDBOX=1

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="$ROOT_DIR/scripts/docker/setup.sh"

if [[ ! -f "$SCRIPT_PATH" ]]; then
  echo "Docker setup script not found at $SCRIPT_PATH" >&2
  exit 1
fi

exec "$SCRIPT_PATH" "$@"
