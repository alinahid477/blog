#!/usr/bin/env python3
"""
run-remediate-missing-services.py

Launches the "remediate-missing-services" Job Template in AAP with a given
list of services, polls until complete, then prints the remediation report.

Usage:
    # Simplest — aap_host/aap_token loaded from .env file:
    python3 run-remediate-missing-services.py --services auditd,firewalld,chronyd --hosts host1,host2

    # Override aap_host and/or aap_token via CLI flags:
    python3 run-remediate-missing-services.py --aap-host https://aap.example.com --aap-token <token> \
        --services auditd,firewalld --hosts host1,host2

    # Or via environment variables (lowest priority):
    export AAP_HOST="https://aap.example.com"
    export AAP_TOKEN="<your-aap-oauth-token>"
    python3 run-remediate-missing-services.py --services auditd,firewalld,chronyd --hosts host1,host2

Resolution order for aap_host/aap_token (highest → lowest): CLI flag → .env file → environment variable

Exit codes:
    0 — Job completed successfully
    1 — Configuration or API error
    2 — Job failed or errored inside AAP
    3 — Timed out waiting for job to finish
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

from aap_client import AAPClient, load_dotenv, has_task_failures

JOB_TEMPLATE_NAME = "remediate-missing-services"
POLL_INTERVAL = 10
POLL_TIMEOUT = 600   # remediation can take longer than a read-only check


def print_remediation_report(client: AAPClient, job_id: int, stdout: str | None = None):
    full_output = stdout if stdout is not None else client.job_stdout(job_id)
    lines = full_output.splitlines()

    task_pattern = re.compile(r"^TASK \[")
    host_task_pattern = re.compile(r"^TASK \[Remediation report JSON for ", re.IGNORECASE)
    ok_pattern = re.compile(r"^ok: \[([^\]]+)\] =>")

    # ── Parse one JSON block per host ─────────────────────────────────
    # A single TASK [Remediation report JSON for ...] block contains one
    # "ok: [hostname] => {...}" entry per host. We iterate over ALL of them
    # and stop collecting json_text lines as soon as the next "ok: [" starts.
    report_records: list[dict] = []
    ok_start_pattern = re.compile(r"^ok: \[")
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
                            break   # next host's output starts — stop here
                        json_text += "\n" + rest
                    try:
                        result = json.loads(json_text)
                        record = result.get("msg", {})
                        if isinstance(record, dict):
                            report_records.append(record)
                    except (json.JSONDecodeError, KeyError) as exc:
                        print(f"  WARNING: Could not parse remediation JSON for a host: {exc}")
            i = block_end
        else:
            i += 1

    # ── Save JSON report ──────────────────────────────────────────────
    output_path = None
    if report_records:
        os.makedirs("/tmp/aap", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_path = f"/tmp/aap/remediation-report-{timestamp}.json"
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(report_records, fh, indent=2)

    # ── Console output ────────────────────────────────────────────────
    print()
    print("═" * 64)
    print(" Remediation Report")
    print("═" * 64)

    for record in report_records:
        print(f"  Host                 : {record.get('host', 'unknown')}")
        print(f"  Enabled              : {', '.join(record.get('enabled', [])) or 'none'}")
        print(f"  Installed + enabled  : {', '.join(record.get('installed_and_enabled', [])) or 'none'}")
        print(f"  Failed to enable     : {', '.join(record.get('failed_to_enable', [])) or 'none'}")
        print(f"  Failed to install    : {', '.join(record.get('failed_to_install', [])) or 'none'}")
        print()

    if not report_records:
        print("  WARNING: No remediation output could be parsed from job output.")


    print("═" * 64)
    print(f" Full job log: {client.host}/#/jobs/playbook/{job_id}/output")
    print("═" * 64)
    print()
    if output_path:
        print(f"  Remediation report saved → {output_path}")


def main():
    dotenv = load_dotenv()

    parser = argparse.ArgumentParser(
        description="Launch the remediate-missing-services Job Template in AAP."
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
    parser.add_argument(
        "--services",
        required=True,
        help="Comma-separated list of service names to remediate (e.g. auditd,firewalld,chronyd).",
    )
    parser.add_argument(
        "--hosts",
        required=True,
        help="Comma-separated list of host names that require remediation (e.g. host1.example.com,host2.example.com).",
    )
    args = parser.parse_args()

    aap_host = args.aap_host or dotenv.get("AAP_HOST") or os.environ.get("AAP_HOST")
    aap_token = args.aap_token or dotenv.get("AAP_TOKEN") or os.environ.get("AAP_TOKEN")

    if not aap_host:
        print("ERROR: AAP host is required.", file=sys.stderr)
        print("  Use --aap-host, set AAP_HOST in .env, or set AAP_HOST env var.", file=sys.stderr)
        sys.exit(1)

    if not aap_token:
        print("ERROR: AAP OAuth token is required.", file=sys.stderr)
        print("  Use --aap-token, set AAP_TOKEN in .env, or set AAP_TOKEN env var.", file=sys.stderr)
        sys.exit(1)

    services_csv = ",".join(s.strip() for s in args.services.split(",") if s.strip())
    if not services_csv:
        print("All good. Nothing to do.")
        sys.exit(0)

    hosts_csv = ",".join(h.strip() for h in args.hosts.split(",") if h.strip())

    client = AAPClient(aap_host, aap_token)

    print(f"→ Looking up Job Template: '{JOB_TEMPLATE_NAME}'...")
    template_id = client.find_job_template(JOB_TEMPLATE_NAME)
    print(f"  Found template ID: {template_id}")

    print(f"→ Launching job with services: {services_csv}")
    print(f"  Hosts to remediate: {hosts_csv}")
    job_id = client.launch_job(
        template_id,
        extra_vars={
            "missing_services_csv": services_csv,
            "remediate_hosts": hosts_csv,
        },
    )
    print(f"  Job ID  : {job_id}")
    print(f"  Job URL : {client.host}/#/jobs/playbook/{job_id}/output")

    print(f"→ Waiting for job to complete (timeout: {POLL_TIMEOUT}s)...")
    status = client.poll_job(job_id, POLL_INTERVAL, POLL_TIMEOUT)

    if status == "successful":
        print("  Job completed successfully.")
        print_remediation_report(client, job_id)
    elif status == "failed":
        stdout = client.job_stdout(job_id)
        if not has_task_failures(stdout):
            print("  WARNING: Job status 'failed' — unreachable host(s) only. Partial results follow.")
            print_remediation_report(client, job_id, stdout=stdout)
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
