#!/usr/bin/env python3
"""
run-check-services-v2.py

End-to-end test of the split check workflow.  Orchestrates
launch-check-services.py and poll-check-services.py as short-lived
subprocesses so that each burst of network activity runs in its own fresh
process context — working around sandbox environments that revoke network
access after a short period within a single process.

Workflow:
    1. Subprocess: launch-check-services.py  → captures JOB_ID
    2. Loop:       poll-check-services.py --job-id <id>
                   each iteration is a new subprocess (fresh network context)
                   → loops until STATUS is terminal
    3. On success: prints REPORT_PATH to stdout

Usage:
    python3 run-check-services-v2.py [--poll-interval N] [--poll-timeout N]

Exit codes:
    0  — Compliance report produced successfully
    1  — Launch failed
    2  — Job failed/error/canceled in AAP
    3  — Timed out waiting for job
    4  — Unexpected poll script error
"""

import argparse
import os
import subprocess
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

RUNNING_STATUSES = {"pending", "waiting", "running"}
TERMINAL_SUCCESS = "successful"
TERMINAL_FAIL = {"failed", "error", "canceled"}

DEFAULT_POLL_INTERVAL = 15   # seconds between polls
DEFAULT_POLL_TIMEOUT = 300   # seconds before giving up
MAX_CONSECUTIVE_NETWORK_ERRORS = 5


def script_path(name: str) -> str:
    return os.path.join(SCRIPTS_DIR, name)


def run_subprocess(*args) -> subprocess.CompletedProcess:
    """Run a Python script from SCRIPTS_DIR, capturing stdout and stderr."""
    cmd = ["python3"] + list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=SCRIPTS_DIR,
    )


def parse_kv(output: str, key: str) -> str | None:
    """Parse the first KEY=value line from script stdout."""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            return stripped.split("=", 1)[1].strip()
    return None


def banner(msg: str) -> None:
    print()
    print("═" * 64)
    print(f"  {msg}")
    print("═" * 64)


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end test: launch + poll check-enabled-services via subprocesses."
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        metavar="N",
        help=f"Seconds between status polls (default: {DEFAULT_POLL_INTERVAL}).",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=DEFAULT_POLL_TIMEOUT,
        metavar="N",
        help=f"Max seconds to wait for job completion (default: {DEFAULT_POLL_TIMEOUT}).",
    )
    args = parser.parse_args()

    # ── Step 1: Launch ────────────────────────────────────────────────────────
    banner("Step 1 — Launching check-enabled-services job")
    result = run_subprocess(script_path("launch-check-services.py"))

    # Always relay subprocess output to the terminal
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)

    if result.returncode != 0:
        print(f"\nERROR: Launch script exited with code {result.returncode}.", file=sys.stderr)
        sys.exit(1)

    job_id = parse_kv(result.stdout, "JOB_ID")
    job_url = parse_kv(result.stdout, "JOB_URL")

    if not job_id:
        print("ERROR: Could not parse JOB_ID from launch script output.", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Job ID  : {job_id}")
    print(f"  Job URL : {job_url}")

    # ── Step 2: Poll loop ─────────────────────────────────────────────────────
    banner(f"Step 2 — Polling job {job_id} (interval={args.poll_interval}s, timeout={args.poll_timeout}s)")

    elapsed = 0
    consecutive_network_errors = 0
    report_path = None

    while elapsed <= args.poll_timeout:
        print(f"\n  [{elapsed:4d}s] Polling job {job_id}...")

        # Each call is a fresh subprocess — fresh process, fresh network context
        poll_result = run_subprocess(
            script_path("poll-check-services.py"),
            "--job-id", str(job_id),
        )

        if poll_result.stdout:
            print(poll_result.stdout.rstrip())
        if poll_result.stderr:
            print(poll_result.stderr.rstrip(), file=sys.stderr)

        if poll_result.returncode == 2:
            # Transient network error — retry up to MAX_CONSECUTIVE_NETWORK_ERRORS times
            consecutive_network_errors += 1
            print(
                f"  WARNING: Network error during poll "
                f"({consecutive_network_errors}/{MAX_CONSECUTIVE_NETWORK_ERRORS}). "
                f"Retrying in {args.poll_interval}s...",
                file=sys.stderr,
            )
            if consecutive_network_errors >= MAX_CONSECUTIVE_NETWORK_ERRORS:
                print(
                    f"ERROR: Exceeded {MAX_CONSECUTIVE_NETWORK_ERRORS} consecutive network errors. Aborting.",
                    file=sys.stderr,
                )
                sys.exit(4)
            time.sleep(args.poll_interval)
            elapsed += args.poll_interval
            continue

        if poll_result.returncode not in (0, 1):
            print(f"ERROR: Poll script exited with unexpected code {poll_result.returncode}.", file=sys.stderr)
            sys.exit(4)

        # Reset error counter on any successful API call
        consecutive_network_errors = 0

        status = parse_kv(poll_result.stdout, "STATUS")
        if not status:
            print("WARNING: Could not parse STATUS from poll output. Retrying...", file=sys.stderr)
            time.sleep(args.poll_interval)
            elapsed += args.poll_interval
            continue

        print(f"  Status: {status}")

        if status == TERMINAL_SUCCESS:
            report_path = parse_kv(poll_result.stdout, "REPORT_PATH")
            break

        if status in TERMINAL_FAIL:
            print(f"\nERROR: Job ended with status '{status}'.", file=sys.stderr)
            sys.exit(2)

        # Still running — wait and poll again
        time.sleep(args.poll_interval)
        elapsed += args.poll_interval

    else:
        print(f"\nERROR: Timed out after {args.poll_timeout}s waiting for job {job_id}.", file=sys.stderr)
        print(f"  Check the job at: {job_url}", file=sys.stderr)
        sys.exit(3)

    # ── Done ──────────────────────────────────────────────────────────────────
    banner("Done")
    print(f"  Job {job_id} completed successfully.")
    if report_path:
        print(f"  Compliance report saved → {report_path}")
    else:
        print("  WARNING: Job succeeded but the compliance report could not be extracted.", file=sys.stderr)
    print()


if __name__ == "__main__":
    main()
