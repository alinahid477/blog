---
name: vms-monitor
description: Monitor VMs connected to Red Hat Insights for service compliance using AAP Job Templates, notify issues via Telegram to the configured user, wait for user approval, then remediate and report results via Telegram.
metadata: {"openclaw":{"emoji":"🖳","requires":{"bins":["python3"]},"os":["linux","darwin"]}}
---

## ⚠️ Resolve Base Directory First

Before running any command in this skill, you MUST determine the value of `{baseDir}`.

Run this command first:
```bash
dirname $(find /home /root /app /tmp -name "SKILL.md" -path "*/vms-monitor/*" 2>/dev/null | head -1)
```

Store the printed path as the base directory. Substitute it for every occurrence of `{baseDir}` in all commands below.

If the above returns empty, your base directory was injected into your system prompt as `<location>` — use that value directly.

---

# vms-monitor

## What it does

Runs a compliance check against all VMs registered with Red Hat Insights via an AAP Job Template, reads the structured JSON report, notifies the user via Telegram, waits for approval, then runs a remediation AAP Job Template on affected hosts and reports the results back via Telegram.

All heavy lifting (AAP API calls, polling, JSON extraction) is handled by the Python scripts. The agent's job is to: run the scripts, read their output files, compose human-friendly messages, and send them via Telegram.

---

## Directory layout

```
{baseDir}/
├── SKILL.md
└── scripts/
    ├── launch-check-services.py     # Step 1 — launch compliance check job, print JOB_ID
    ├── poll-check-loop.sh           # Step 2 — poll up to 5×, break on terminal status
    ├── poll-check-services.py       #         (called internally by poll-check-loop.sh)
    ├── launch-remediate-services.py # Step 6 — launch remediation job, print JOB_ID
    ├── poll-remediate-loop.sh       # Step 7 — poll up to 5×, break on terminal status
    └── poll-remediate-services.py   #         (called internally by poll-remediate-loop.sh)
```

Output files (written by the scripts under `/tmp/aap/`):
```
/tmp/aap/compliance-report-<timestamp>.json    # written by Step 2 (poll-check-loop.sh)
/tmp/aap/remediation-report-<timestamp>.json   # written by Step 7 (poll-remediate-loop.sh)
```

---

## Inputs needed

- `python3` on PATH
- A `.env` file in `{baseDir}/scripts/` containing `AAP_HOST` and `AAP_TOKEN` (or equivalent env vars)
- OpenClaw Telegram channel (pre-connected — use the existing Telegram channel, do NOT create a new webhook)

---

## Workflow

### Step 1 — Launch compliance check job

```bash
cd {baseDir}/scripts && python3 launch-check-services.py
```

Parse `JOB_ID=<number>` and `JOB_URL=<url>` from stdout. Store them as `{jobId}` and `{jobUrl}`. Print both to the agent output so the user can see them. If the script exits non-zero, send this message via Telegram to the configured user and **stop**:

```
😢 *vms-monitor — Compliance Check Failed*

I was unable to launch the compliance check job in AAP. I'm aborting, sorry.
```

If the script exits 0, **immediately proceed to Step 2 without waiting for user input**.

---

### Step 2 — Poll until compliance job completes

**Run this command now. Do not wait for user input:**

```bash
cd {baseDir}/scripts && bash poll-check-loop.sh {jobId}
```

The script polls up to 5 times (15 seconds apart) and exits. Read `LOOP_RESULT` from its stdout:

- `LOOP_RESULT=still_running` (exit 0) — job is still in progress. **Run the command again immediately.**
- `LOOP_RESULT=successful` (exit 10) — job done. Parse `REPORT_PATH=<path>` from the output, store as `{compliancePath}`, proceed to Step 3.
- `LOOP_RESULT=failed` / `error` / `canceled` (exit 1) — send the failure message below and **stop**.
- `LOOP_RESULT=network_error` (exit 2) — send the failure message below and **stop**.

```
😢 *vms-monitor — Compliance Check Failed*

I was unable to run the compliance check against Red Hat Insights. The AAP job did not complete successfully. I'm aborting, sorry.
```

---

### Step 3 — Read compliance report and notify user via Telegram

Read the JSON file at `{compliancePath}`. It is a JSON array. Each element represents one host and has these fields:

- `host` — the hostname (string)
- `stopped_services` — list of service names that are **not running** (may be empty `[]`)
- `missing_services` — list of service names that are **not installed** (may be empty `[]`)

Ignore the `enabled_services` field entirely — do not read, display, or act on it.

**Compose the Telegram notification message** using the rules below.

#### Message header (always include):

```
🔭 *VMs-monitor — Results*

Hi! I've scanned your VMs for the past hour and found the following:

```

#### Per-host block:

For **each host** in the JSON array:

- If both `stopped_services` and `missing_services` are empty lists → write:
  ```
  🖳 In host called `<host>` no issues found. ✅
  ```

- Otherwise → write the host header and then only the non-empty issue lines:
  ```
  🖳 In host called `<host>` below issues found

  ```
  Then for each non-empty field, add a numbered line (skip the line entirely if the list is empty):
  - If `stopped_services` is non-empty: `1. 🟡 *Services not running* — <comma-separated service names>`
  - If `missing_services` is non-empty: `2. 🔴 *Services not installed* — <comma-separated service names>`
  
  Re-number the items sequentially (1, 2, 3 …) — only count lines that are actually included.

Separate each host block with a blank line.

#### Message footer (always append):

```

Would you like me to remediate the issues found? Reply *yes* to proceed or *no* to skip.
```

**Send this composed message via the existing Telegram channel to the configured user.**

---

### Step 4 — Wait for user response

After sending the Telegram notification, **wait for the user to reply** before doing anything else.

- If the user replies **yes** (or any affirmative — "yes", "y", "go", "proceed", "do it", "fix it", "sure", "ok"): proceed to Step 5.
- If the user replies **no** (or any negative — "no", "n", "skip", "cancel", "stop"): send the following message via Telegram to the configured user and **stop**:

```
👍 Got it — skipping remediation. Let me know if you change your mind!
```

- If the response is unclear: ask the user to clarify with a simple yes/no.

---

### Step 5 — Prepare remediation parameters

Before running the remediation script you must compute two parameters from the compliance report you already read in Step 3.

#### Compute `{services_param}`:

Collect all unique service names from:
- `stopped_services` across **all hosts** in the report
- `missing_services` across **all hosts** in the report

Deduplicate them (keep each name only once). Join them into a single comma-separated string.

Example: if host A has `stopped_services: ["nginx","firewalld"]` and host B has `missing_services: ["nginx","httpd"]`, then `{services_param}` = `nginx,firewalld,httpd`

If the resulting string is empty (no host has any stopped or missing services), send this message via Telegram to the configured user and **stop**:

```
✅ *vms-monitor — Nothing to Remediate*

All checked services are already installed and running on all hosts. No remediation needed!
```

#### Compute `{hosts_param}`:

Collect the `host` field for every entry in the compliance report where **at least one** of the following is true:
- `stopped_services` is non-empty
- `missing_services` is non-empty

Exclude hosts where both `stopped_services` and `missing_services` are empty.

Join the collected hostnames into a single comma-separated string.

---

### Step 6 — Launch remediation job

```bash
cd {baseDir}/scripts && python3 launch-remediate-services.py \
  --services "{services_param}" \
  --hosts "{hosts_param}"
```

Parse `JOB_ID=<number>` and `JOB_URL=<url>` from stdout. Store them as `{remJobId}` and `{remJobUrl}`. Print both to the agent output so the user can see them. If the script exits non-zero, send this message via Telegram to the configured user and **stop**:

```
😢 *vms-monitor — Remediation Failed*

I was unable to launch the remediation job in AAP. I'm aborting, sorry.
```

If the script exits 0, **immediately proceed to Step 7 without waiting for user input**.

---

### Step 7 — Poll until remediation job completes

**Run this command now. Do not wait for user input:**

```bash
cd {baseDir}/scripts && bash poll-remediate-loop.sh {remJobId}
```

The script polls up to 5 times (15 seconds apart) and exits. Read `LOOP_RESULT` from its stdout:

- `LOOP_RESULT=still_running` (exit 0) — job is still in progress. **Run the command again immediately.**
- `LOOP_RESULT=successful` (exit 10) — job done. Parse `REPORT_PATH=<path>` from the output, store as `{remediationPath}`, proceed to Step 8.
- `LOOP_RESULT=partial` (exit 10) — job done with partial results (some hosts unreachable). Parse `REPORT_PATH=<path>`, store as `{remediationPath}`, proceed to Step 8 and note in the Telegram message that results may be incomplete.
- `LOOP_RESULT=failed` / `error` / `canceled` / `task_failure` (exit 1) — send the failure message below and **stop**.
- `LOOP_RESULT=network_error` (exit 2) — send the failure message below and **stop**.

```
😢 *vms-monitor — Remediation Failed*

The remediation AAP job did not complete successfully. Please check the AAP instance for details.
```

---

### Step 8 — Read remediation report and notify user via Telegram

Read the JSON file at `{remediationPath}`. It is a JSON array. Each element represents one host and has these fields:

- `host` — the hostname (string)
- `enabled` — list of services that were already present but have now been enabled (may be empty `[]`)
- `installed_and_enabled` — list of services that were missing and have been installed and enabled (may be empty `[]`)
- `failed_to_enable` — list of services that were present but could not be enabled (may be empty `[]`)
- `failed_to_install` — list of services that could not be installed (may be empty `[]`)

**Compose the Telegram result message** using the rules below.

#### Message header (always include):

```
🛠 *VMs-monitor — Remediation Complete*

Here are the fixes that were applied:

```

#### Per-host block:

For **each host** in the JSON array:

Write the host header:
```
🛠 Below fixes were applied for host `<host>`:

```

Then for each non-empty field, add a numbered line (skip the line entirely if the list is empty):
- If `enabled` is non-empty: `1. ✅ *Enabled services* — <comma-separated names>`
- If `installed_and_enabled` is non-empty: `2. 📥 *Installed & Enabled* — <comma-separated names>`
- If `failed_to_enable` is non-empty: `3. ❌ *Failed to enable* — <comma-separated names>`
- If `failed_to_install` is non-empty: `4. ❌ *Failed to install* — <comma-separated names>`

Re-number the items sequentially (1, 2, 3 …) — only count lines that are actually included.

If all four fields are empty for a host, write:
```
🛠 For host `<host>`: nothing was changed — no issues were found or all items were already in the desired state. ✅
```

Separate each host block with a blank line.

**Send this composed message via the existing Telegram channel to the configured user.**

---

## Guardrails

- **Poll scripts run once per call.** Never block inside a single poll invocation — issue a new Exec call each time. Notify once when done.
- **Do not run any AAP API calls directly.** All AAP interaction is delegated to the Python scripts.
- **Do not modify the Python scripts** during a run or at anytime.
- **Do not guess service names or host names.** Only use values read directly from the JSON output files.
- **Do not skip the user-approval step (Step 3).** Always wait for explicit confirmation before running remediation.
- **If `python3` is unavailable**, send an error message via Telegram to the configured user and stop immediately.
- **If the compliance report JSON cannot be read or parsed**, send an error via Telegram and stop.
- **If the remediation report JSON cannot be read or parsed**, send an error via Telegram explaining results could not be parsed.
- **Telegram channel unavailable** — print the final summary to agent stdout as a fallback.

---

## Failure handling

| Failure | Action |
|---|---|
| `launch-check-services.py` exits non-zero | Send error message via Telegram (see Step 1), stop |
| `poll-check-loop.sh` LOOP_RESULT=failed/error/canceled/network_error | Send error message via Telegram (see Step 2), stop |
| Compliance REPORT_PATH not found in stdout | Send error message via Telegram, stop |
| Compliance report JSON unreadable / invalid | Send error message via Telegram, stop |
| `launch-remediate-services.py` exits non-zero | Send error message via Telegram (see Step 6), stop |
| `poll-remediate-loop.sh` LOOP_RESULT=failed/error/canceled/task_failure/network_error | Send error message via Telegram (see Step 7), stop |
| Remediation REPORT_PATH not found in stdout | Send warning via Telegram — job ran but report could not be parsed |
| Remediation report JSON unreadable / invalid | Send warning via Telegram that results could not be parsed |
| Telegram channel unavailable | Print the full message to stdout as a fallback |
