#!/usr/bin/env python3
"""
poll-remediate-services.py

Makes a SINGLE status check for a running "remediate-missing-services" AAP
job and exits immediately.  Designed to be called repeatedly by an external
loop (SKILL.md or run-remediate-services-v2.py) — each invocation is a
short-lived process with a fresh network connection, which avoids sandbox
network-cutoff issues.

When the job reaches a terminal status this script also fetches the Ansible
job stdout, extracts the per-host remediation JSON, and saves it to /tmp/aap/.

Special handling for status "failed":
  - If only remote hosts were unreachable (no task failures), the job is
    treated as a partial success: the report is extracted and saved, and
    STATUS=partial is printed instead of STATUS=failed.
  - If there were actual task failures, STATUS=failed is printed and the
    script exits with code 3.

Usage:
    python3 poll-remediate-services.py --job-id 146 [--aap-host URL] [--aap-token TOKEN]

Output (stdout, machine-parseable):
    STATUS=<status>                                  — always printed
    REPORT_PATH=/tmp/aap/remediation-report-*.json   — when STATUS=successful or STATUS=partial

Exit codes:
    0 — API call succeeded (read STATUS to decide whether to keep polling)
    1 — Configuration error (missing host/token)
    2 — Network or HTTP error reaching AAP (transient — caller may retry)
    3 — Job failed with actual task failures (not just unreachable hosts)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

from aap_client import AAPClient, load_dotenv, has_task_failures

# Must match _API in aap_client.py
_AAP_API = "/api/controller/v2"

TERMINAL_STATUSES = {"successful", "failed", "error", "canceled"}


def extract_and_save_report(client: AAPClient, job_id: int, stdout: str | None = None) -> str | None:
    """
    Fetch (or reuse) the job stdout, extract per-host remediation JSON blocks,
    and save the result to /tmp/aap/. Returns the saved file path, or None if
    extraction failed.
    """
    try:
        full_output = stdout if stdout is not None else client.job_stdout(job_id)
    except SystemExit:
        print("  WARNING: Could not fetch job stdout to extract remediation report.", file=sys.stderr)
        return None

    lines = full_output.splitlines()

    task_pattern = re.compile(r"^TASK \[")
    host_task_pattern = re.compile(r"^TASK \[Remediation report JSON for ", re.IGNORECASE)
    ok_pattern = re.compile(r"^ok: \[([^\]]+)\] =>")
    ok_start_pattern = re.compile(r"^ok: \[")

    report_records: list[dict] = []
    i = 0
    while i < len(lines):
        if host_task_pattern.match(lines[i]):
            block_end = len(lines)
            for j in range(i + 1, len(lines)):
                if task_pattern.match(lines[j]):
                    block_end = j
                    break
            block = lines[i + 1:block_end]

            for k, line in enumerate(block):
                m = ok_pattern.match(line)
                if m:
                    json_text = line.split("=>", 1)[1].strip()
                    for rest in block[k + 1:]:
                        if ok_start_pattern.match(rest):
                            break
                        json_text += "\n" + rest
                    try:
                        result = json.loads(json_text)
                        record = result.get("msg", {})
                        if isinstance(record, dict):
                            report_records.append(record)
                    except (json.JSONDecodeError, KeyError) as exc:
                        print(f"  WARNING: Could not parse remediation JSON for a host: {exc}", file=sys.stderr)
            i = block_end
        else:
            i += 1

    if not report_records:
        print("  WARNING: No remediation output could be parsed from job stdout.", file=sys.stderr)
        return None

    os.makedirs("/tmp/aap", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    output_path = f"/tmp/aap/remediation-report-{timestamp}.json"
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(report_records, fh, indent=2)

    return output_path


def main():
    dotenv = load_dotenv()

    parser = argparse.ArgumentParser(
        description="Poll a single AAP remediation job status — one network call per invocation."
    )
    parser.add_argument(
        "--job-id",
        required=True,
        type=int,
        help="The AAP job ID returned by launch-remediate-services.py.",
    )
    parser.add_argument(
        "--aap-host",
        default=None,
        help="AAP host URL (1st: this flag, 2nd: AAP_HOST in .env, 3rd: AAP_HOST env var).",
    )
    parser.add_argument(
        "--aap-token",
        default=None,
        help="AAP OAuth token (1st: this flag, 2nd: AAP_TOKEN in .env, 3rd: AAP_TOKEN env var).",
    )
    args = parser.parse_args()

    aap_host = args.aap_host or dotenv.get("AAP_HOST") or os.environ.get("AAP_HOST")
    aap_token = args.aap_token or dotenv.get("AAP_TOKEN") or os.environ.get("AAP_TOKEN")

    if not aap_host:
        print("ERROR: AAP host is required.", file=sys.stderr)
        sys.exit(1)
    if not aap_token:
        print("ERROR: AAP OAuth token is required.", file=sys.stderr)
        sys.exit(1)

    client = AAPClient(aap_host, aap_token)

    # Single status poll — catch network/HTTP errors and exit with code 2
    # so the caller can distinguish transient failures from config errors.
    try:
        data = client.get(f"{_AAP_API}/jobs/{args.job_id}/")
    except SystemExit:
        sys.exit(2)

    status = data.get("status", "unknown")

    if status == "successful":
        print(f"STATUS={status}")
        report_path = extract_and_save_report(client, args.job_id)
        if report_path:
            print(f"REPORT_PATH={report_path}")
        else:
            print("WARNING: Job succeeded but remediation report could not be extracted.", file=sys.stderr)

    elif status == "failed":
        # Fetch stdout once to reuse in both has_task_failures check and report extraction
        try:
            stdout = client.job_stdout(args.job_id)
        except SystemExit:
            # Can't fetch stdout — treat as hard failure
            print("STATUS=failed")
            print("  ERROR: Job failed and stdout could not be fetched.", file=sys.stderr)
            sys.exit(3)

        if not has_task_failures(stdout):
            # Only unreachable hosts — treat as partial success, extract what we have
            print("STATUS=partial")
            print("  WARNING: Job status 'failed' — unreachable host(s) only. Extracting partial results.", file=sys.stderr)
            report_path = extract_and_save_report(client, args.job_id, stdout=stdout)
            if report_path:
                print(f"REPORT_PATH={report_path}")
        else:
            print("STATUS=failed")
            print("  ERROR: Job failed with task failures. Check the AAP job log for details.", file=sys.stderr)
            sys.exit(3)

    elif status in ("error", "canceled"):
        print(f"STATUS={status}")
        print(f"  Job ended with terminal status '{status}'.", file=sys.stderr)

    else:
        # Still running (pending / waiting / running) or unknown
        print(f"STATUS={status}")


if __name__ == "__main__":
    main()
