#!/usr/bin/env python3
"""
poll-check-services.py

Makes a SINGLE status check for a running "check-enabled-services" AAP job
and exits immediately.  Designed to be called repeatedly by an external loop
(SKILL.md or run-check-services-v2.py) rather than blocking internally —
each invocation is a short-lived process with a fresh network connection,
which avoids sandbox network-cutoff issues.

When the job status is "successful" this script also fetches the Ansible
job stdout, extracts the compliance JSON, and saves it to /tmp/aap/.

Usage:
    python3 poll-check-services.py --job-id 146 [--host URL] [--token TOKEN]

Output (stdout, machine-parseable):
    STATUS=<status>                              — always printed
    REPORT_PATH=/tmp/aap/compliance-*.json       — only when STATUS=successful

Exit codes:
    0 — API call completed (read STATUS line to decide whether to keep polling)
    1 — Configuration error (missing host/token)
    2 — Network or HTTP error reaching AAP (transient — caller may retry)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

from aap_client import AAPClient, load_dotenv

# Must match _API in aap_client.py
_AAP_API = "/api/controller/v2"

TERMINAL_STATUSES = {"successful", "failed", "error", "canceled"}


def extract_and_save_report(client: AAPClient, job_id: int) -> str | None:
    """
    Fetch job stdout, extract the compliance JSON block, save to /tmp/aap/.
    Returns the saved file path, or None if extraction failed.
    """
    try:
        full_output = client.job_stdout(job_id)
    except SystemExit:
        print("  WARNING: Could not fetch job stdout to extract compliance report.", file=sys.stderr)
        return None

    lines = full_output.splitlines()

    # Locate the "Display compliance JSON" task block
    task_start = None
    task_end = len(lines)
    for i, line in enumerate(lines):
        if re.match(r"TASK \[Display compliance JSON\]", line, re.IGNORECASE):
            task_start = i
        elif task_start is not None and re.match(r"^TASK \[", line):
            task_end = i
            break

    if task_start is None:
        print("  WARNING: 'Display compliance JSON' task block not found in job output.", file=sys.stderr)
        return None

    # Extract the JSON object printed after "ok: [localhost] =>"
    json_lines: list[str] = []
    in_json = False
    for line in lines[task_start:task_end]:
        if re.match(r"ok: \[localhost\] =>", line):
            json_lines = [line.split("=>", 1)[1].strip()]
            in_json = True
        elif in_json:
            json_lines.append(line)

    if not json_lines:
        print("  WARNING: No JSON content found in compliance task block.", file=sys.stderr)
        return None

    try:
        result = json.loads("\n".join(json_lines))
        report_data = result["msg"]
    except (json.JSONDecodeError, KeyError) as exc:
        print(f"  WARNING: Could not parse compliance JSON: {exc}", file=sys.stderr)
        return None

    os.makedirs("/tmp/aap", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    output_path = f"/tmp/aap/compliance-report-{timestamp}.json"
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(report_data, fh, indent=2)

    return output_path


def main():
    dotenv = load_dotenv()

    parser = argparse.ArgumentParser(
        description="Poll a single AAP job status — one network call per invocation."
    )
    parser.add_argument(
        "--job-id",
        required=True,
        type=int,
        help="The AAP job ID returned by launch-check-services.py.",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="AAP host URL (1st: this flag, 2nd: AAP_HOST in .env, 3rd: AAP_HOST env var).",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="AAP OAuth token (1st: this flag, 2nd: AAP_TOKEN in .env, 3rd: AAP_TOKEN env var).",
    )
    args = parser.parse_args()

    host = args.host or dotenv.get("AAP_HOST") or os.environ.get("AAP_HOST")
    token = args.token or dotenv.get("AAP_TOKEN") or os.environ.get("AAP_TOKEN")

    if not host:
        print("ERROR: AAP host is required.", file=sys.stderr)
        sys.exit(1)
    if not token:
        print("ERROR: AAP OAuth token is required.", file=sys.stderr)
        sys.exit(1)

    client = AAPClient(host, token)

    # Single status poll — catch network/HTTP errors and exit with code 2
    # so the caller can distinguish transient failures from config errors.
    try:
        data = client.get(f"{_AAP_API}/jobs/{args.job_id}/")
    except SystemExit:
        # aap_client calls sys.exit(1) on URLError/HTTPError — surface as exit 2
        sys.exit(2)

    status = data.get("status", "unknown")
    print(f"STATUS={status}")

    if status == "successful":
        report_path = extract_and_save_report(client, args.job_id)
        if report_path:
            print(f"REPORT_PATH={report_path}")
        else:
            print("WARNING: Job succeeded but compliance report could not be extracted.", file=sys.stderr)
    elif status in ("failed", "error", "canceled"):
        print(f"  Job ended with terminal status '{status}'.", file=sys.stderr)


if __name__ == "__main__":
    main()
