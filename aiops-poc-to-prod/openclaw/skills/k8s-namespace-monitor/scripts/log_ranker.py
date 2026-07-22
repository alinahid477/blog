#!/usr/bin/env python3
"""
log_ranker.py
Reads an aggregated log file, groups lines by static error prefix
(ignoring dynamic suffixes), counts occurrences per deployment,
ranks them, and writes the top 5 to ranked-errors.json.

Usage:
    python3 log_ranker.py <path_to_aggregated_log_file> <knowledge_base_json>

Output:
    /tmp/k8s-monitoring-agent/logs/ranked-errors.json

Token-efficiency note: all grouping, counting, and ranking happens here
in Python. The agent reads only the compact 5-record JSON — never the
raw log file.
"""

import json
import os
import re
import sys
from collections import defaultdict

OUTPUT_DIR = os.path.join("/tmp", "k8s-monitoring-agent", "logs")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "ranked-errors.json")

# ── Dynamic suffix patterns ───────────────────────────────────────────────────
# These regex patterns mark where a "dynamic" suffix begins on a log line.
# Everything from the match point onward is stripped before grouping,
# so lines with the same static prefix are counted as one error type.
# CHANGE FROM PREVIOUS VERSION:
#   No structural change here. These still handle dbapps error suffixes.
#   The nginx dynamic header is now handled upstream by normalize_nginx_line().
DYNAMIC_SUFFIX_PATTERNS = [
    r"\s*->\s+.*$",           # " -> <detail>"
    r"\s*:\s+[A-Z]{3,}.*$",   # ": FATAL: ..." style pgsql errors after prefix
    r"\s+\(.*\)\s*$",         # trailing parenthetical "(detail)"
    r"\s*\.\s+[A-Z].*$",      # ". Detail starts here"
    # r"\s+\*\d+\s+",           # nginx dynamic request id "  *8 "
]


# ── NEW: nginx datetime-prefixed log format normalizer ───────────────────────
# Matches lines from the new mybusybox log format:
#   2026/05/08 02:27:57 [error] 12#12: *3 <message>, client: ..., server: ...
#
# Capture group 1 = severity bracket e.g. [error]
# After the match, everything from m.end() onward is the raw message body.
NGINX_TS_PREFIX_RE = re.compile(
    r'^\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s+'   # timestamp
    r'(\[(?:error|warn|crit|alert|emerg)\])\s+'       # [severity]
    r'\d+#\d+:\s+'                                     # pid#tid:
    r'(?:\*\d+\s+)?'                                   # optional *requestid
)

# Trailing nginx context fields appended after the message body.
# These are dynamic (IP addresses, file paths, upstream URLs) and must be
# stripped so lines with the same error but different client IPs group together.
NGINX_CONTEXT_FIELDS_RE = re.compile(
    r',\s*client:.*$|'
    r',\s*server:.*$|'
    r',\s*request:.*$|'
    r',\s*upstream:.*$|'
    r',\s*host:.*$|'
    r',\s*referrer:.*$',
    re.IGNORECASE
)

def load_json(path: str) -> object:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ── NEW: build KNOWN_PREFIXES from the knowledge base ─────────────────────────
def build_known_prefixes(kb_entries: list) -> list:
    """
    Derive the KNOWN_PREFIXES list from the knowledge-base log entries.

    Normalization applied per entry:
      - If the KB log field is a datetime-prefixed nginx example line, it is
        normalized to its canonical "[severity] <message>" form — the same
        form that live log lines produce after normalize_nginx_line().
      - dbapps-style entries ("ERROR: ...", "WARN: ...") are kept as-is.

    Ordering:
      Entries are sorted longest-first so that startswith() checks in
      strip_dynamic_suffix() always match the most specific prefix first.
      e.g. "[error] connect() failed (111: Connection refused)" must be
      checked before the catch-all "[error]".

    Returns:
      A list of canonical prefix strings, longest first.
    """
    prefixes = set()

    for entry in kb_entries:
        raw_log = entry.get("log", "").strip()
        if not raw_log:
            continue

        normalized = normalize_nginx_line(raw_log)
        if normalized is not None:
            # nginx entry — use the canonical normalized form
            prefixes.add(normalized)
        else:
            # dbapps entry — use the raw log field as-is
            prefixes.add(raw_log)

    # Sort longest first so most-specific prefix wins in startswith() checks
    sorted_prefixes = sorted(prefixes, key=len, reverse=True)

    # DEBUG
    # print(f"  [known_prefixes] Built {len(sorted_prefixes)} prefixes from knowledge base.",
    #       file=sys.stderr)
    # for p in sorted_prefixes:
    #     print(f"    '{p}'", file=sys.stderr)

    return sorted_prefixes


# ── NEW: build DEPLOYMENT_MAP from the knowledge base ─────────────────────────
def build_deployment_map(kb_entries: list) -> dict:
    """
    Derive the DEPLOYMENT_MAP from the knowledge-base namespace and app fields.
    Returns:
      {
        "dbapps/backendapp":   {"namespace": "dbapps", "deployment": "backendapp"},
        "dbapps/frontendapp":  {"namespace": "dbapps", "deployment": "frontendapp"},
        "dbapps/configreader": {"namespace": "dbapps", "deployment": "configreader"},
        "test/mybusybox":      {"namespace": "test",   "deployment": "mybusybox"},
        ...
      }
    """
    deployment_map = {}

    for entry in kb_entries:
        namespace = entry.get("namespace", "").strip()
        app       = entry.get("app", "").strip()

        if not namespace or not app:
            continue

        key = f"{namespace}/{app}"
        if key not in deployment_map:
            deployment_map[key] = {"namespace": namespace, "deployment": app}

    # DEBUG
    # print(f"  [deployment_map] Built {len(deployment_map)} deployment(s) from knowledge base.",
    #       file=sys.stderr)
    # for k, v in deployment_map.items():
    #     print(f"    '{k}' → {v}", file=sys.stderr)

    return deployment_map

# ── Error line allowlist gate ─────────────────────────────────────────────────
# A line must match at least one of these to be processed at all.
# This is the belt-and-suspenders guard against noise that slips through bash.
#
# CHANGE FROM PREVIOUS VERSION:
#   Added \[error\], \[warn\], \[crit\], \[alert\], \[emerg\] bracket patterns
#   to recognise the new datetime-prefixed nginx log format, which no longer
#   starts with "nginx:".
ALLOWED_LINE_PATTERNS = re.compile(
    r'ERROR|WARN(?:ING)?|CRIT(?:ICAL)?|'
    r'\[error\]|\[warn\]|\[crit\]|\[alert\]|\[emerg\]',
    re.IGNORECASE
)

# ── Explicit noise rejection (belt-and-suspenders) ────────────────────────────
# Rejects lines that slipped through bash filtering.
NOISE_PATTERNS = re.compile(
    r'HTTP/1\.[01]" \d{3}|'   # HTTP access log lines
    r'var: \w+=|'              # var dump lines
    r'load_all_configs|'       # config banner
    r'RealDictRow',            # DB row dumps
    re.IGNORECASE
)


def normalize_nginx_line(line: str) -> str | None:
    """
    Normalize a new-format mybusybox/nginx log line to a stable canonical form.

    Input:
        2026/05/08 02:27:57 [error] 12#12: *3 upstream timed out (110: ...) while
        connecting to upstream, client: 172.19.0.2, server: , request: "GET ..."

    Output:
        [error] upstream timed out while connecting to upstream

    Returns None if the line does not match the nginx datetime format
    (caller should fall through to normal prefix handling).
    """
    m = NGINX_TS_PREFIX_RE.match(line)
    if not m:
        return None

    severity = m.group(1)           # e.g. [error]
    body = line[m.end():].strip()   # everything after pid#tid: *reqid

    # Strip dynamic trailing context (client IP, server, request, upstream, host)
    body = NGINX_CONTEXT_FIELDS_RE.sub('', body).strip()
    body = body.rstrip(',').strip()

    return f"{severity} {body}"

def strip_dynamic_suffix(line: str, known_prefixes: list) -> str:
    """
    Return the static prefix of a log line for grouping purposes.

    Processing order:
      1. Try normalize_nginx_line() for new datetime-prefixed nginx format.
         If it matches, use the normalized form and then check KNOWN_PREFIXES
         against that normalized form.
      2. Try KNOWN_PREFIXES against the raw line (dbapps apps).
      3. Fall back to regex suffix stripping (dbapps apps with variable tails).
    """
    
    # Step 1: attempt nginx datetime normalization
    normalized = normalize_nginx_line(line)
    if normalized is not None:
        # Match against canonical KNOWN_PREFIXES on the normalized form
        for prefix in known_prefixes:
            if normalized.startswith(prefix):
                return prefix
        # No known prefix matched — return full normalized form (capped)
        return normalized[:120]
    
    # Step 2: try KNOWN_PREFIXES on the raw line (dbapps format)
    for prefix in known_prefixes:
        if line.startswith(prefix):
            return prefix

    # Step 3: regex suffix stripping fallback
    result = line
    for pattern in DYNAMIC_SUFFIX_PATTERNS:
        result = re.sub(pattern, "", result).strip()

    # Hard cap: static prefix is at most 120 chars
    return result[:120]


def parse_log_file(filepath: str, known_prefixes: list, deployment_map: dict) -> dict:
    """
    Parse the aggregated log file.
    Returns a dict: {(namespace, deployment, static_prefix): [sample_lines]}
    """
    groups: dict = defaultdict(list)
    current_ns = "unknown"
    current_dep = "unknown"

    with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")

            # Section header written by get_namespacelogs.sh
            if line.startswith("## [") and line.endswith("]"):
                key_raw = line[4:-1]  # e.g. "dbapps/backendapp"
                info = deployment_map.get(key_raw)
                if info:
                    current_ns = info["namespace"]
                    current_dep = info["deployment"]
                    print (f"\nprocessing... current_ns: {current_ns}, current_dep: {current_dep}\n")
                continue

            # Skip comment / metadata lines
            if line.startswith("#") or line.strip() == "":
                continue
            
            # print (f"debug: {line}")

            # Skip DEBUG lines (belt-and-suspenders — already filtered by bash)
            if re.search(r"\bDEBUG\b", line, re.IGNORECASE):
                continue
            # Gate: line must match a known error-level pattern
            if not ALLOWED_LINE_PATTERNS.search(line):
                continue

            # Gate: reject known noise that passed the bash filter
            if NOISE_PATTERNS.search(line):
                continue

            static_prefix = strip_dynamic_suffix(line, known_prefixes)
            if static_prefix:
                key = (current_ns, current_dep, static_prefix)
                groups[key].append(line)

    return groups


def build_ranked_list(groups: dict) -> list:
    """
    Convert the groups dict into a ranked list of dicts, top 5 only.
    """
    records = []
    for (namespace, deployment, error_prefix), samples in groups.items():
        records.append({
            "namespace": namespace,
            "deployment": deployment,
            "error": error_prefix,
            "sample": samples[0][:200],   # first occurrence, capped at 200 chars
            "count": len(samples),
            "rank": 0,                     # assigned below
            "remediation_status": "pending",
            "remediation_action": None,
            "remediation_result": None,
        })

    # Sort by count descending, then alphabetically for determinism
    records.sort(key=lambda r: (-r["count"], r["error"]))

    # Assign rank and keep top 5
    top5 = records[:5]
    for i, rec in enumerate(top5, start=1):
        rec["rank"] = i

    return top5


def main():
    if len(sys.argv) < 2:
        print("Usage: log_ranker.py <aggregated_log_file>", file=sys.stderr)
        sys.exit(1)

    log_file = sys.argv[1]
    kb_path  = sys.argv[2]

    if not os.path.isfile(log_file):
        print(f"ERROR: Log file not found: {log_file}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(kb_path):
        print(f"ERROR: Knowledge base not found: {kb_path}", file=sys.stderr)
        sys.exit(1)

    # ── Load KB and derive both maps at runtime ───────────────────────────────
    kb_raw     = load_json(kb_path)
    kb_entries = kb_raw.get("list", [])
    known_prefixes  = build_known_prefixes(kb_entries)
    deployment_map  = build_deployment_map(kb_entries)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    groups = parse_log_file(log_file, known_prefixes, deployment_map)
    
    if not groups:
        print("INFO: No relevant error lines found in log file. Writing empty ranked-errors.json.")
        ranked = []
    else:
        ranked = build_ranked_list(groups)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(ranked, fh, indent=2)

    print(f"RANKED_ERRORS_FILE={OUTPUT_FILE}")
    print(f"INFO: {len(ranked)} error type(s) ranked.", file=sys.stderr)
    for r in ranked:
        print(f"  Rank {r['rank']}: [{r['namespace']}/{r['deployment']}] "
              f"{r['error']!r} ({r['count']} occurrences)", file=sys.stderr)


if __name__ == "__main__":
    main()