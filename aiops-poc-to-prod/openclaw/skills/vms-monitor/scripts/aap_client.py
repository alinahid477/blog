"""
aap_client.py — shared AAP REST API helpers used by all run-*.py scripts.
"""

import json
import pathlib
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def load_dotenv() -> dict:
    """
    Parse KEY=VALUE pairs from a .env file located in the same directory as
    this module.  Lines that are blank or start with '#' are ignored.
    Returns a plain dict; never raises — missing file is treated as empty.
    """
    env_file = pathlib.Path(__file__).parent / ".env"
    result: dict = {}
    if not env_file.exists():
        return result
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


TERMINAL_STATUSES = {"successful", "failed", "error", "canceled"}


def has_task_failures(stdout: str) -> bool:
    """
    Parse the PLAY RECAP and return True only when EVERY non-localhost host
    failed (unreachable or task failure).  Returns False as soon as at least
    one non-localhost host completed with ok > 0.

    Examples:
      localhost(ignored) + host-A(ok)  + host-B(unreachable) → False
      localhost(ignored) + host-A(unreachable) + host-B(unreachable) → True
      localhost(ignored) + host-A(ok)  + host-B(ok)           → False
    """
    recap_start = re.search(r"^PLAY RECAP ", stdout, re.MULTILINE)
    if not recap_start:
        return False
    recap_section = stdout[recap_start.start():]

    # Each host line looks like:  hostname : ok=N changed=N unreachable=N failed=N ...
    total = 0
    failed_count = 0
    for m in re.finditer(
        r"^(\S+).*:\s+ok=(\d+).*\bunreachable=(\d+).*\bfailed=(\d+)",
        recap_section,
        re.MULTILINE,
    ):
        hostname = m.group(1)
        if hostname == "localhost":
            continue
        total += 1
        ok, unreachable, failed = int(m.group(2)), int(m.group(3)), int(m.group(4))
        if ok == 0 and (unreachable > 0 or failed > 0):
            failed_count += 1

    return total > 0 and failed_count == total

# AAP 2.5+ unified gateway moves the Controller API under this prefix.
# Change to "/api/v2" if targeting an older AAP 2.4 / AWX instance.
_API = "/api/controller/v2"


class AAPClient:
    def __init__(self, host: str, token: str):
        self.host = host.rstrip("/")
        self.token = token
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | str:
        url = f"{self.host}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=30) as resp:
                raw = resp.read().decode()
                if "json" in resp.headers.get("Content-Type", ""):
                    return json.loads(raw)
                return raw
        except urllib.error.HTTPError as e:
            body_text = e.read().decode()
            print(f"ERROR: HTTP {e.code} from {url}", file=sys.stderr)
            print(f"  Response: {body_text}", file=sys.stderr)
            sys.exit(1)
        except urllib.error.URLError as e:
            print(f"ERROR: Could not reach AAP at {url}: {e.reason}", file=sys.stderr)
            sys.exit(1)

    def get(self, path: str) -> dict | str:
        return self._request("GET", path)

    def post(self, path: str, body: dict | None = None) -> dict:
        return self._request("POST", path, body or {})

    # ── Lookup helpers ─────────────────────────────────────────────────────────

    def find_job_template(self, name: str) -> int:
        """Return the ID of a job template by name, exit(1) if not found."""
        encoded = urllib.parse.quote(name)
        data = self.get(f"{_API}/job_templates/?name={encoded}")
        if data["count"] == 0:
            print(f"ERROR: Job Template '{name}' not found.", file=sys.stderr)
            sys.exit(1)
        return data["results"][0]["id"]

    def find_workflow_template(self, name: str) -> int:
        """Return the ID of a workflow job template by name, exit(1) if not found."""
        encoded = urllib.parse.quote(name)
        data = self.get(f"{_API}/workflow_job_templates/?name={encoded}")
        if data["count"] == 0:
            print(f"ERROR: Workflow Template '{name}' not found.", file=sys.stderr)
            sys.exit(1)
        return data["results"][0]["id"]

    # ── Launch helpers ─────────────────────────────────────────────────────────

    def launch_job(self, template_id: int, extra_vars: dict | None = None) -> int:
        """Launch a job template and return the job ID."""
        body = {}
        if extra_vars:
            body["extra_vars"] = extra_vars
        response = self.post(f"{_API}/job_templates/{template_id}/launch/", body)
        job_id = response.get("id")
        if not job_id:
            print(f"ERROR: Launch did not return a job ID. Response: {response}", file=sys.stderr)
            sys.exit(1)
        return job_id

    def launch_workflow(self, template_id: int, extra_vars: dict | None = None) -> int:
        """Launch a workflow job template and return the workflow job ID."""
        body = {}
        if extra_vars:
            body["extra_vars"] = extra_vars
        response = self.post(f"{_API}/workflow_job_templates/{template_id}/launch/", body)
        job_id = response.get("id")
        if not job_id:
            print(f"ERROR: Workflow launch did not return a job ID. Response: {response}", file=sys.stderr)
            sys.exit(1)
        return job_id

    # ── Poll helpers ───────────────────────────────────────────────────────────

    def poll_job(self, job_id: int, poll_interval: int = 10, timeout: int = 300) -> str:
        """Poll a job until it reaches a terminal status. Returns the final status."""
        elapsed = 0
        status = "pending"
        while elapsed < timeout:
            data = self.get(f"{_API}/jobs/{job_id}/")
            status = data.get("status", "unknown")
            print(f"  [{elapsed:3d}s] Status: {status}")
            if status in TERMINAL_STATUSES:
                return status
            time.sleep(poll_interval)
            elapsed += poll_interval
        return status  # timed-out caller handles this

    def poll_workflow(self, wf_job_id: int, poll_interval: int = 10, timeout: int = 600) -> str:
        """Poll a workflow job until it reaches a terminal status. Returns the final status."""
        elapsed = 0
        status = "pending"
        while elapsed < timeout:
            data = self.get(f"{_API}/workflow_jobs/{wf_job_id}/")
            status = data.get("status", "unknown")
            print(f"  [{elapsed:3d}s] Status: {status}")
            if status in TERMINAL_STATUSES:
                return status
            time.sleep(poll_interval)
            elapsed += poll_interval
        return status

    # ── Output helpers ─────────────────────────────────────────────────────────

    def job_stdout(self, job_id: int) -> str:
        return self.get(f"{_API}/jobs/{job_id}/stdout/?format=txt")

    def workflow_nodes(self, wf_job_id: int) -> list[dict]:
        """Return all workflow node results for a completed workflow job."""
        data = self.get(f"{_API}/workflow_jobs/{wf_job_id}/workflow_nodes/")
        return data.get("results", [])
