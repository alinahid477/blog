#!/usr/bin/env python3
"""
launch-check-services.py

Launches the "check-enabled-services" Job Template in AAP and exits
immediately — does NOT poll or wait for completion.

Designed to be called once per vms-monitor run.  The caller is responsible
for polling via poll-check-services.py.

Usage:
    # Values loaded from .env file in the same directory:
    python3 launch-check-services.py

    # Override via CLI flags:
    python3 launch-check-services.py --host https://aap.example.com --token <token>

    # Or via environment variables:
    export AAP_HOST="https://aap.example.com"
    export AAP_TOKEN="<your-aap-oauth-token>"
    python3 launch-check-services.py

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

JOB_TEMPLATE_NAME = "check-enabled-services"


def main():
    dotenv = load_dotenv()

    parser = argparse.ArgumentParser(
        description="Launch the check-enabled-services Job Template in AAP (no polling)."
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
    print(f"  Job queued successfully.")
    print(f"JOB_ID={job_id}")
    print(f"JOB_URL={client.host}/#/jobs/playbook/{job_id}/output")


if __name__ == "__main__":
    main()
