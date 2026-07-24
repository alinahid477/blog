#!/usr/bin/env bash
# update-cluster-url.sh — Replace the cluster domain across all kagenti
# deployment files (.yaml, .yml, .sh, .md, .txt).
#
# Skips:  archived/ directories, kagenti-install/keep/ (historical snapshots)
#
# Usage:
#   ./update-cluster-url.sh --old <old-domain> --new <new-domain> [--dry-run]
#
# Example:
#   ./update-cluster-url.sh \
#     --old <your-cluster-domain> \
#     --new cluster-tjcll.dyn.redhatworkshops.io
#
#   # Preview without making changes:
#   ./update-cluster-url.sh \
#     --old <your-cluster-domain> \
#     --new cluster-tjcll.dyn.redhatworkshops.io \
#     --dry-run
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<EOF
Usage: $(basename "$0") --old <old-domain> --new <new-domain> [--dry-run]

Replaces all occurrences of <old-domain> with <new-domain> in .yaml, .yml,
and .sh files under the kagenti/ directory tree.

Skips: archived/ directories, kagenti-install/keep/, docs/text (.md, .txt).

Options:
  --old <domain>   The current cluster domain to replace.
  --new <domain>   The new cluster domain.
  --dry-run        Show what would change without modifying files.
  --help           Show this help and exit.
EOF
}

OLD=""
NEW=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --old)     OLD="$2";   shift 2 ;;
    --new)     NEW="$2";   shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --help)    usage; exit 0 ;;
    *)         echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "${OLD}" || -z "${NEW}" ]]; then
  echo "Error: both --old and --new are required." >&2
  usage
  exit 1
fi

if [[ "${OLD}" == "${NEW}" ]]; then
  echo "Old and new domains are identical. Nothing to do."
  exit 0
fi

echo "=== Cluster URL Migration ==="
echo "  Old domain : ${OLD}"
echo "  New domain : ${NEW}"
echo "  Scope      : ${SCRIPT_DIR}"
echo "  Dry run    : ${DRY_RUN}"
echo ""

# Build the list of candidate files
# Include: .yaml, .yml, .sh
# Exclude: archived/, kagenti-install/keep/, docs/text (.md, .txt)
mapfile -t FILES < <(
  find "${SCRIPT_DIR}" \
    -type f \( -name '*.yaml' -o -name '*.yml' -o -name '*.sh' \) \
    ! -path '*/archived/*' \
    ! -path '*/kagenti-install/keep/*' \
  | sort
)

CHANGED=0
TOTAL_REPLACEMENTS=0

for file in "${FILES[@]}"; do
  count=$(grep -c "${OLD}" "${file}" 2>/dev/null || true)
  if [[ "${count}" -gt 0 ]]; then
    rel="${file#"${SCRIPT_DIR}/"}"
    echo "  ${rel}  (${count} occurrence(s))"

    if [[ "${DRY_RUN}" == false ]]; then
      if [[ "$(uname)" == "Darwin" ]]; then
        sed -i '' "s|${OLD}|${NEW}|g" "${file}"
      else
        sed -i "s|${OLD}|${NEW}|g" "${file}"
      fi
    fi

    CHANGED=$((CHANGED + 1))
    TOTAL_REPLACEMENTS=$((TOTAL_REPLACEMENTS + count))
  fi
done

echo ""
if [[ "${DRY_RUN}" == true ]]; then
  echo "=== Dry run complete: ${TOTAL_REPLACEMENTS} replacement(s) in ${CHANGED} file(s) would be made. ==="
  echo "    Re-run without --dry-run to apply."
else
  echo "=== Done: ${TOTAL_REPLACEMENTS} replacement(s) in ${CHANGED} file(s). ==="
fi
