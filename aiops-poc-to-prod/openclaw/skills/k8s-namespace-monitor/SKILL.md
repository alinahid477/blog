---
name: k8s-namespace-monitor
description: Monitor Kubernetes namespaces (dbapps, test), analyse logs from the last hour, rank errors, remediate using known fixes, verify results, and notify via Telegram channel to the configured user.
metadata: {"openclaw":{"emoji":"🔭","requires":{"bins":["oc","kubectl","bash","python3"]},"os":["linux","darwin"]}}
---

## ⚠️ Resolve Base Directory First
Before running any command in this skill, you MUST determine the value of `{baseDir}`.

Run this command first:
```bash
dirname $(find /home /root /app /tmp -name "SKILL.md" -path "*/k8s-namespace-monitor/*" 2>/dev/null | head -1)
```

Store the printed path as the base directory. Substitute it for every occurrence of `{baseDir}` in all commands below.

If the above returns empty, your base directory was injected into your system prompt as `<location>` — use that value directly.

---

# k8s-namespace-monitor

## What it does
Monitors logs from all applications in the `dbapps` and `test` Kubernetes namespaces over the past 1 hour. It offloads all heavy lifting — login verification, log collection, filtering, ranking, knowledge-base lookup, remediation, and verification — to dedicated scripts. The agent reads structured JSON output files at each step, keeps context lean, and sends notifications via the existing Telegram channel to the configured user.

---

## Monitored Applications

### Namespace: dbapps
| App | Deployment | Crash? | Notes |
|---|---|---|---|
| backendapp | `backendapp` | YES | Crashes on DB conn failure or missing configreader values |
| frontendapp | `frontendapp` | NO | Throws WARN, does not crash |
| configreader | `configreader` | NO | Throws 404, does not crash |

- Database: **PostgreSQL** (pgsql). The DB instance itself never goes down but its Kubernetes Service may be deleted.
- If the `postgresql` Service is missing in `dbapps`, `backendapp` cannot connect and will crash.

### Namespace: test
| App | Deployment | Notes |
|---|---|---|
| mybusybox | `mybusybox` | Nginx-based, scale=1 (user may scale up). Typical nginx errors. Restart is the only remediation. |

---

## Directory layout

```
{baseDir}/
├── SKILL.md
├── log-meaning-and-remediation.json
└── scripts/
    ├── do_login.sh              # Step 0 — verify/refresh OpenShift login
    ├── get_namespacelogs.sh     # Step 1 — collect + filter logs
    ├── log_ranker.py            # Step 2 — rank errors, write ranked-errors.json
    ├── get_remediations.py      # Step 3 — match errors to knowledge base
    ├── fix_pgsqldb.sh           # Step 5a — DB connection remediation
    ├── fix_apps.sh              # Step 5b/5c — Service/pod remediation for dbapps
    └── verify_remediation.py    # Step 6 — verify and update ranked-errors.json
```

Working files (all written under `/tmp/k8s-monitoring-agent/logs/`):
```
aggregated_log_{timestamp}.log   # raw filtered logs from all apps
ranked-errors.json               # top-5 ranked errors + remediation tracking
error-meaning.json               # matched meanings + remediation instructions
```

---

## Inputs needed
- `oc` (OpenShift CLI) on PATH
- `kubectl` on PATH
- `python3` on PATH
- `{baseDir}/log-meaning-and-remediation.json` — the error knowledge base
- OpenClaw Telegram channel (pre-connected — use the existing Telegram channel, do NOT create a new webhook)

---

## Workflow

### Step 0 — Verify OpenShift login

This is always the **first step**. Do not proceed to any other step if this step fails.

```bash
bash {baseDir}/scripts/do_login.sh
```

**If the script exits 0:** proceed to Step 1.

**If the script exits non-zero:** send the following message via Telegram to the configured user and **stop immediately**:

```
😢 *k8s-namespace-monitor — Login Failed*

I was unable to authenticate to the OpenShift cluster and cannot proceed with log monitoring or remediation. I'm aborting the rest of the workflow, sorry.
```

---

### Step 1 — Collect and filter logs

Run the log collection script. It fetches logs from all monitored deployments for the last 1 hour, strips DEBUG lines at source, and writes a single aggregated log file.

```bash
bash {baseDir}/scripts/get_namespacelogs.sh
```

The script prints the path to the written log file, e.g.:
```
LOG_FILE=/tmp/k8s-monitoring-agent/logs/aggregated_log_20260504T091500.log
```

Capture this path — pass it to Step 2. If the script exits non-zero, send an error message via Telegram to the configured user and stop.

---

### Step 2 — Rank errors

Run the ranker script, passing the log file path from Step 1:

```bash
python3 {baseDir}/scripts/log_ranker.py \
  <LOG_FILE> \
  {baseDir}/log-meaning-and-remediation.json
```

The script groups log lines by static error prefix (ignoring dynamic suffixes), counts occurrences, ranks them, keeps the top 5, and writes:

```
/tmp/k8s-monitoring-agent/logs/ranked-errors.json
```

Read `ranked-errors.json`. It contains at most 5 records in this format:

```json
[
  {
    "rank": 1,
    "namespace": "dbapps",
    "deployment": "backendapp",
    "error": "ERROR: Database connection failed",
    "sample": "ERROR: Database connection failed. FATAL: password authentication failed for user \"app\"",
    "count": 42,
    "remediation_status": "pending",
    "remediation_action": null,
    "remediation_result": null
  }
]
```

Use these 5 records for the pre-notification via the Telegram channel to the configured user (Step 4) and all further processing. Do not re-read the raw log file again.

---

### Step 3 — Load knowledge base and match remediations

Run the remediation matcher script, passing the ranked errors file and the knowledge base:

```bash
python3 {baseDir}/scripts/get_remediations.py \
  /tmp/k8s-monitoring-agent/logs/ranked-errors.json \
  {baseDir}/log-meaning-and-remediation.json
```

The script reads both files, matches each error to the knowledge base using prefix-first then substring fallback, and writes:

```
/tmp/k8s-monitoring-agent/logs/error-meaning.json
```

Read `error-meaning.json`. It is a list of at most 5 records:

```json
[
  {
    "rank": 1,
    "namespace": "dbapps",
    "deployment": "backendapp",
    "error": "ERROR: Database connection failed",
    "count": 42,
    "meaning": "The backendapp could not connect to the PostgreSQL database...",
    "remediation_instruction": "Run fix_pgsqldb.sh",
    "remediation_script": "fix_pgsqldb.sh",
    "remediation_args": [],
    "no_auto_fix": false
  }
]
```

Use `error-meaning.json` exclusively for Steps 4–6. Do not re-read `log-meaning-and-remediation.json` again.

---

### Step 4 — Send pre-remediation notification via Telegram

Use the **existing Telegram channel** (do not create a new webhook or new authentication).

Compose a friendly message from `error-meaning.json`. Include:
- Top 5 errors (rank, deployment, namespace, count, one-line meaning)
- What fixes are about to run (scripts to be called)
- A note that external API errors will not be auto-fixed

Example:
```
🔭 *k8s-namespace-monitor — Issues Found*

Hi! I've scanned your Kubernetes namespaces for the past hour and found the following:

1. 🔴 *Database connection failed* — `backendapp` (dbapps) — 42 occurrences
   ↳ backendapp cannot reach the PostgreSQL service. Will run fix_pgsqldb.sh.

2. 🟡 *Configreader down* — `backendapp` (dbapps) — 17 occurrences
   ↳ backendapp cannot load config. Will check configreader pods/service.

3. 🔵 *SSL handshake failure* — `mybusybox` (test) — 8 occurrences
   ↳ nginx SSL error. Will restart mybusybox deployment.

🔧 Running fixes now — stand by for results!
```

---

### Step 5 — Execute remediation (once per run)

**Critical constraint: execute each distinct remediation action ONLY ONCE per run.**

**Restart deduplication rule:** If multiple errors share the same restart target (e.g. `deployment/backendapp`), execute that restart once and mark all subsequent entries targeting the same deployment as `"remediation_status": "skipped_duplicate"` in `ranked-errors.json`.

Process `error-meaning.json` entries in rank order. For each entry, determine the action from `remediation_script` and `no_auto_fix`:

#### 5a — DB connection failure (`fix_pgsqldb.sh`)
When `remediation_script == "fix_pgsqldb.sh"`:

```bash
bash {baseDir}/scripts/fix_pgsqldb.sh
```

The script internally checks if the `postgresql` Service exists:
- If **missing** → recreates the Service
- If **present** → restarts `deployment/backendapp`

No branching needed from the agent. Update `ranked-errors.json` field `remediation_action` to `"ran fix_pgsqldb.sh"`.

#### 5b — Backend down (`fix_apps.sh backendapp`)
When `remediation_script == "fix_apps.sh"` and deployment is `backendapp`:

```bash
bash {baseDir}/scripts/fix_apps.sh backendapp
```

The script internally checks pods and Services for `backendapp`. No branching needed from the agent.

#### 5c — Configreader down (`fix_apps.sh configreader`)
When `remediation_script == "fix_apps.sh"` and deployment is `configreader`:

```bash
bash {baseDir}/scripts/fix_apps.sh configreader
```

The script internally checks pods and Services for `configreader`. No branching needed from the agent.

#### 5d — backendapp could not load required values
When error matches `ERROR: app could not load required values`:

This depends on configreader being healthy first. If `configreader` remediation was already executed in 5c, proceed:

```bash
bash {baseDir}/scripts/fix_apps.sh backendapp
```

Skip if `backendapp` was already actioned in 5a or 5b (deduplication rule).

#### 5e — External API errors (no action)
When `no_auto_fix == true`:

Set `remediation_status: "skipped_no_auto_fix"` in `ranked-errors.json`. Do not run any script. Note it in the final Telegram message.

#### 5f — mybusybox / nginx errors (`fix_apps.sh mybusybox`)
When namespace is `test` and deployment is `mybusybox`:

```bash
bash {baseDir}/scripts/fix_apps.sh mybusybox
```

Run **once** regardless of how many nginx error entries appear for `mybusybox`.

---

### Step 6 — Verify remediation

Run the verification script:

```bash
python3 {baseDir}/scripts/verify_remediation.py \
  /tmp/k8s-monitoring-agent/logs/ranked-errors.json
```

The script updates each record's `remediation_result` field to `"success"`, `"failed"`, `"unverified"`, or `"n/a"`. Read the updated `ranked-errors.json` to compose the final Telegram message. Do not run any further kubectl commands manually.

---

### Step 7 — Send final Telegram notification

Use the existing Telegram channel.

Read `ranked-errors.json` and compose the result message:

**On full success:**
```
✅ *k8s-namespace-monitor — Remediation Complete*

All automated fixes completed successfully:
• `postgresql` Service recreated ✅
• `backendapp` restarted ✅
• `mybusybox` restarted ✅

Items flagged for manual review (no auto-fix available):
• ERROR: Error fetching ISS position feed — external API issue
```

**On partial/full failure:**
```
⚠️ *k8s-namespace-monitor — Remediation Partially Failed*

Results:
• `backendapp` restart: ✅ Success
• `postgresql` Service creation: ❌ Failed — manual intervention required

Please check the cluster and investigate failed items.
```

---

## Guardrails

- **Never loop.** Each script runs once. Verify once. Notify once. Done.
- **Never restart the same deployment twice** — deduplication is tracked in `ranked-errors.json`.
- **Do not run any kubectl or oc command directly** except where explicitly stated in this workflow. All kubectl or oc work is delegated to scripts.
- **Do not re-read raw log files** after Step 2 completes. Use `ranked-errors.json` and `error-meaning.json` only.
- **Do not re-read `log-meaning-and-remediation.json`** after Step 3 completes. Use `error-meaning.json` only.
- **External API errors are never actioned** — flag as `skipped_no_auto_fix`.
- **DEBUG logs are never processed** — filtered out by `get_namespacelogs.sh` before the agent sees any logs.
- **Do not modify scripts or JSON knowledge base** during a run.
- **If Step 0 (`do_login.sh`) fails**, send the login-failure message via Telegram to the configured user and stop immediately.
- **If `oc`, `kubectl`, or `python3` are unavailable**, send an error message via Telegram to the configured user and stop immediately.

---

## Failure handling

- **`do_login.sh` fails** — send the crying-emoji as error messages via Telegram (see Step 0), stop. Do not proceed.
- **`get_namespacelogs.sh` fails** — send error messages with the script's stderr output via Telegram to the configured user, stop.
- **`log_ranker.py` fails** — send error message via Telegram to the configured user, stop. Do not attempt to parse raw logs manually.
- **`get_remediations.py` fails** — send error message via Telegram to the configured user, stop. Do not attempt manual knowledge-base lookup.
- **A remediation script (`fix_pgsqldb.sh`, `fix_apps.sh`) is missing** — set `remediation_status: "skipped_script_missing"` in `ranked-errors.json`, continue with remaining entries, note in final message via Telegram to the configured user.
- **A remediation script exits non-zero** — set `remediation_result: "failed"`, continue with remaining entries. Do not retry.
- **`verify_remediation.py` fails** — send message via Telegram to the configured user that verification could not complete, report all remediation results as `"unverified"`.
- **Telegram channel unavailable** — print the final summary to agent stdout.
- **A deployment has no logs** (e.g. scaled to 0) — `get_namespacelogs.sh` logs a warning line and continues. The absence of logs is not an error.
