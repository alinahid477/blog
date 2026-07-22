#!/usr/bin/env python3
"""
verify_remediation.py
Reads ranked-errors.json, verifies each remediation action via kubectl,
and writes results back to the same file in-place.

Each record's remediation_result field is updated to one of:
  "success"     — kubectl confirmed the fix is in place
  "failed"      — kubectl check failed or returned unexpected output
  "unverified"  — action type not recognised; could not verify
  "n/a"         — entry was skipped/not actioned; nothing to verify

Usage:
    python3 verify_remediation.py <path_to_ranked-errors.json>

Exit codes:
    0 — verification pass completed (individual failures recorded in JSON)
    1 — script-level failure (file not found, JSON parse error, etc.)
"""

import json
import os
import re
import subprocess
import sys

ROLLOUT_TIMEOUT = "60s"
KUBECTL_TIMEOUT = 70   # seconds for subprocess.run timeout (slightly over rollout timeout)
SVC_TIMEOUT     = 15   # seconds for simple kubectl get svc calls

# Maps deployment name → its expected Service name for Service-creation verifications
DEPLOYMENT_TO_SVC = {
    "backendapp":  "backendapp-svc",
    "configreader": "configreader-svc",
    "frontendapp": "frontendapp-svc",
    "postgresql":  "postgresql",
    "mybusybox":   "",   # mybusybox has no service to verify — rollout only
}

# Statuses that mean no remediation was attempted — nothing to verify
SKIP_STATUSES = {
    "pending",
    "skipped_duplicate",
    "skipped_no_auto_fix",
    "skipped_script_missing",
}


def kubectl_rollout_status(deployment: str, namespace: str) -> tuple[bool, str]:
    """
    Runs: kubectl rollout status deployment/<name> -n <ns> --timeout=60s
    Returns (success: bool, message: str).
    """
    cmd = [
        "kubectl", "rollout", "status",
        f"deployment/{deployment}",
        "-n", namespace,
        f"--timeout={ROLLOUT_TIMEOUT}",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=KUBECTL_TIMEOUT,
        )
        combined = (result.stdout + result.stderr).strip()
        if result.returncode == 0 and "successfully rolled out" in result.stdout:
            return True, combined
        return False, combined
    except subprocess.TimeoutExpired:
        return False, f"kubectl rollout status timed out after {KUBECTL_TIMEOUT}s"
    except FileNotFoundError:
        return False, "kubectl not found on PATH"


def kubectl_get_svc(svc_name: str, namespace: str) -> tuple[bool, str]:
    """
    Runs: kubectl get svc <name> -n <ns>
    Returns (exists: bool, message: str).
    """
    cmd = ["kubectl", "get", "svc", svc_name, "-n", namespace]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SVC_TIMEOUT,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, f"kubectl get svc timed out after {SVC_TIMEOUT}s"
    except FileNotFoundError:
        return False, "kubectl not found on PATH"


def determine_verification_type(action: str, deployment: str) -> str:
    """
    Inspect the remediation_action string to decide what kind of
    kubectl check is needed.
    Returns: "rollout" | "service" | "unknown"
    """
    if not action:
        return "unknown"
    action_lower = action.lower()
    if "restart" in action_lower or "rollout" in action_lower:
        return "rollout"
    if "svc" in action_lower or "service" in action_lower or "fix_pgsqldb" in action_lower:
        return "service"
    # fix_apps.sh may have created a service OR done a rollout restart.
    # If the deployment is mybusybox or we see "fix_apps" without other hints,
    # default to rollout since restart is the most common fix_apps outcome.
    if "fix_apps" in action_lower or "fix_pgsqldb" in action_lower:
        return "rollout"
    return "unknown"


def extract_svc_name_from_action(action: str, deployment: str, namespace: str) -> str:
    """
    Try to extract a Service name from the remediation_action string.
    Falls back to the DEPLOYMENT_TO_SVC map, then to deployment + '-svc'.
    """
    # e.g. "ran fix_pgsqldb.sh (svc postgresql)" or "ran fix_apps.sh backendapp (svc backendapp-svc)"
    match = re.search(r"svc\s+([\w-]+)", action, re.IGNORECASE)
    if match:
        return match.group(1)

    # Special case: postgresql Service is in dbapps even though the "deployment"
    # triggering it was backendapp
    if "fix_pgsqldb" in action.lower():
        return "postgresql"

    return DEPLOYMENT_TO_SVC.get(deployment, deployment + "-svc")


def verify_record(rec: dict) -> str:
    """
    Verify a single ranked-errors record.
    Returns the remediation_result string.
    """
    status     = rec.get("remediation_status", "pending")
    action     = rec.get("remediation_action") or ""
    namespace  = rec.get("namespace", "")
    deployment = rec.get("deployment", "")
    rank       = rec.get("rank", "?")

    # Nothing to verify for skipped/pending entries
    if status in SKIP_STATUSES or not action:
        print(f"  [verify] rank={rank} status='{status}' — skipping (no action taken).")
        return "n/a"

    verification_type = determine_verification_type(action, deployment)
    print(f"  [verify] rank={rank} deployment='{deployment}' "
          f"ns='{namespace}' action='{action}' → type='{verification_type}'")

    if verification_type == "rollout":
        success, msg = kubectl_rollout_status(deployment, namespace)
        print(f"    → rollout {'✓ success' if success else '✗ failed'}: {msg[:120]}")
        return "success" if success else "failed"

    elif verification_type == "service":
        svc_name = extract_svc_name_from_action(action, deployment, namespace)
        success, msg = kubectl_get_svc(svc_name, namespace)
        print(f"    → svc '{svc_name}' {'✓ found' if success else '✗ not found'}: {msg[:120]}")
        return "success" if success else "failed"

    else:
        print(f"    → unrecognised action type. Marking as 'unverified'.")
        return "unverified"


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: verify_remediation.py <ranked-errors.json>", file=sys.stderr)
        return 1

    ranked_file = sys.argv[1]

    if not os.path.isfile(ranked_file):
        print(f"ERROR: File not found: {ranked_file}", file=sys.stderr)
        return 1

    try:
        with open(ranked_file, "r", encoding="utf-8") as fh:
            records = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: Could not read ranked-errors.json: {exc}", file=sys.stderr)
        return 1

    if not isinstance(records, list):
        print("ERROR: ranked-errors.json must contain a JSON array.", file=sys.stderr)
        return 1

    print(f"[verify] Starting verification for {len(records)} record(s) in: {ranked_file}")

    for rec in records:
        rec["remediation_result"] = verify_record(rec)

    try:
        with open(ranked_file, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2)
    except OSError as exc:
        print(f"ERROR: Could not write updated ranked-errors.json: {exc}", file=sys.stderr)
        return 1

    print(f"[verify] Done. Results written back to: {ranked_file}")

    # Print a quick summary to stderr for the agent log
    for rec in records:
        result = rec.get("remediation_result", "n/a")
        symbol = {"success": "✓", "failed": "✗", "n/a": "–", "unverified": "?"}.get(result, "?")
        print(f"  {symbol} rank={rec.get('rank')} [{rec.get('namespace')}/{rec.get('deployment')}] "
              f"{rec.get('error', '')[:60]} → {result}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())