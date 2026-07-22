#!/usr/bin/env python3
"""
launch-remediate-services.py

Launches the "remediate-missing-services" Job Template in AAP with the given
list of services and hosts, then exits immediately — does NOT poll or wait.

Designed to be called once per vms-monitor remediation run.  Polling is
handled by poll-remediate-services.py.

Usage:
    python3 launch-remediate-services.py \
        --services auditd,firewalld,chronyd \
        --hosts host1.example.com,host2.example.com

    # Override AAP connection via CLI flags:
    python3 launch-remediate-services.py \
        --aap-host https://aap.example.com --aap-token <token> \
        --services auditd,firewalld --hosts host1,host2

    # Or via environment variables / .env file:
    export AAP_HOST="https://aap.example.com"
    export AAP_TOKEN="<your-aap-oauth-token>"

Resolution order (highest → lowest): CLI flag → .env file → environment variable

Output (stdout, machine-parseable):
    JOB_ID=<id>
    JOB_URL=<url>

Exit codes:
    0 — Job launched successfully
    1 — Configuration or API error
"""

import argparse
import os
import sys

from aap_client import AAPClient, load_dotenv

JOB_TEMPLATE_NAME = "remediate-missing-services"


def main():
    dotenv = load_dotenv()

    parser = argparse.ArgumentParser(
        description="Launch the remediate-missing-services Job Template in AAP (no polling)."
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
        help="Comma-separated service names to remediate (e.g. auditd,firewalld,chronyd).",
    )
    parser.add_argument(
        "--hosts",
        required=True,
        help="Comma-separated host names that require remediation (e.g. host1.example.com,host2.example.com).",
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
        print("Nothing to do — services list is empty.")
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
    print(f"  Job queued successfully.")
    print(f"JOB_ID={job_id}")
    print(f"JOB_URL={client.host}/#/jobs/playbook/{job_id}/output")


if __name__ == "__main__":
    main()
