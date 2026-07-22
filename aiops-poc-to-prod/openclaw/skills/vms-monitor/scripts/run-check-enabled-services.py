#!/usr/bin/env python3
"""
run-check-enabled-services.py

Launches the "check-enabled-services" Job Template in AAP, polls until
complete, then prints the compliance report from the job output.

Usage:
    # Simplest — values loaded from .env file:
    python3 run-check-enabled-services.py

    # Override host and/or token via CLI flags:
    python3 run-check-enabled-services.py --host https://aap.example.com --token <token>

    # Or via environment variables (lowest priority):
    export AAP_HOST="https://aap.example.com"
    export AAP_TOKEN="<your-aap-oauth-token>"
    python3 run-check-enabled-services.py

Resolution order (highest → lowest): CLI flag → .env file → environment variable

Exit codes:
    0 — Job completed successfully
    1 — Configuration or API error
    2 — Job failed or errored inside AAP
    3 — Timed out waiting for job to finish
"""

import argparse
import ast
import json
import os
import re
import sys
from datetime import datetime

from aap_client import AAPClient, load_dotenv, has_task_failures

JOB_TEMPLATE_NAME = "check-enabled-services"
POLL_INTERVAL = 10
POLL_TIMEOUT = 300


def print_compliance_report(client: AAPClient, job_id: int, stdout: str | None = None):
    full_output = stdout if stdout is not None else client.job_stdout(job_id)
    lines = full_output.splitlines()

    # ── Locate the "Display compliance JSON" task block ──────────────
    task_start = None
    task_end = len(lines)
    for i, line in enumerate(lines):
        if re.match(r"TASK \[Display compliance JSON\]", line, re.IGNORECASE):
            task_start = i
        elif task_start is not None and re.match(r"^TASK \[", line):
            task_end = i
            break

    # ── Extract and save the compliance JSON ─────────────────────────
    report_data = None
    output_path = None

    if task_start is not None:
        # Collect the JSON object that Ansible prints after "ok: [localhost] =>"
        in_json = False
        json_lines: list[str] = []
        for line in lines[task_start:task_end]:
            if re.match(r"ok: \[localhost\] =>", line):
                json_lines = [line.split("=>", 1)[1].strip()]
                in_json = True
            elif in_json:
                json_lines.append(line)
                # if line.strip() == "}":
                #     break

        if json_lines:
            try:
                result = json.loads("\n".join(json_lines))
                report_data = result["msg"] # result.get("msg", "")
                # Ansible serialises the list as a Python repr — use ast to parse it
                
            except (json.JSONDecodeError, KeyError) as exc:
                print(f"  WARNING: Could not parse compliance task output: {exc}")

    if report_data is not None:
        os.makedirs("/tmp/aap", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_path = f"/tmp/aap/compliance-report-{timestamp}.json"
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(report_data, fh, indent=2)

    # ── Console output ────────────────────────────────────────────────
    print()
    print("═" * 64)
    print(" Compliance Report")
    print("═" * 64)

    in_block = False
    compliance_pattern = re.compile(
        r"TASK \[.*(display compliance|[Ss]ummary|set workflow artifact)",
        re.IGNORECASE,
    )
    task_pattern = re.compile(r"^TASK \[")

    for line in lines:
        if compliance_pattern.search(line):
            in_block = True
        elif task_pattern.match(line):
            in_block = False
        if in_block:
            print(line)


    print("═" * 64)
    print(f" Full job log: {client.host}/#/jobs/playbook/{job_id}/output")
    print("═" * 64)
    print()
    if output_path:
        print(f"  Compliance report saved → {output_path}")
    else:
        print("  WARNING: Compliance report could not be extracted from job output.")


def main():
    dotenv = load_dotenv()

    parser = argparse.ArgumentParser(
        description="Launch the check-enabled-services Job Template in AAP."
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
        print("  Use --host, set AAP_HOST in .env, or set AAP_HOST env var.", file=sys.stderr)
        sys.exit(1)

    if not token:
        print("ERROR: AAP OAuth token is required.", file=sys.stderr)
        print("  Use --token, set AAP_TOKEN in .env, or set AAP_TOKEN env var.", file=sys.stderr)
        sys.exit(1)

    client = AAPClient(host, token)

    print(f"→ Looking up Job Template: '{JOB_TEMPLATE_NAME}'...")
    template_id = client.find_job_template(JOB_TEMPLATE_NAME)
    print(f"  Found template ID: {template_id}")

    print("→ Launching job...")
    job_id = client.launch_job(template_id)
    print(f"  Job ID  : {job_id}")
    print(f"  Job URL : {client.host}/#/jobs/playbook/{job_id}/output")

    print(f"→ Waiting for job to complete (timeout: {POLL_TIMEOUT}s)...")
    status = client.poll_job(job_id, POLL_INTERVAL, POLL_TIMEOUT)

    if status == "successful":
        print("  Job completed successfully.")
        print_compliance_report(client, job_id)
    elif status == "failed":
        stdout = client.job_stdout(job_id)
        if not has_task_failures(stdout):
            print("  WARNING: Job status 'failed' — unreachable host(s) only. Partial results follow.")
            print_compliance_report(client, job_id, stdout=stdout)
        else:
            print("\nERROR: Job finished with status 'failed' (task failure(s) detected).", file=sys.stderr)
            print("─" * 64, file=sys.stderr)
            print(stdout, file=sys.stderr)
            sys.exit(2)
    elif status in ("error", "canceled"):
        print(f"\nERROR: Job finished with status '{status}'.", file=sys.stderr)
        print("─" * 64, file=sys.stderr)
        print(client.job_stdout(job_id), file=sys.stderr)
        sys.exit(2)
    else:
        print(f"ERROR: Timed out after {POLL_TIMEOUT}s. Last status: {status}.", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
