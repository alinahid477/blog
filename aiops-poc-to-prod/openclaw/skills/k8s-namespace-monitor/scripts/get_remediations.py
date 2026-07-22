#!/usr/bin/env python3
"""
get_remediations.py
Reads ranked-errors.json and log-meaning-and-remediation.json.
Matches each error to a knowledge-base entry using:
  1. Exact prefix match
  2. Closest substring match (longest matching prefix wins)
  3. Fallback: unknown / manual review
Writes the result to /tmp/k8s-monitoring-agent/logs/error-meaning.json.

Usage:
    python3 get_remediations.py <ranked_errors_json> <knowledge_base_json>

Token-efficiency note: matching logic runs entirely here. The agent reads
only the compact error-meaning.json — never the full knowledge base.
"""

import json
import os
import sys
import re

OUTPUT_DIR = os.path.join("/tmp", "k8s-monitoring-agent", "logs")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "error-meaning.json")



# ── NEW: nginx datetime-prefixed log normalizer ───────────────────────────────
# Shared normalization logic — identical to the one in log_ranker.py.
# Both sides of the match (ranked error AND knowledge-base entry) are passed
# through this function so they are compared on equal footing.
#
# CHANGE FROM PREVIOUS VERSION:
#   Previously there was no normalization at all. The KB entries for nginx
#   now contain full example lines with timestamps and request ids
#   (e.g. "2026/05/08 02:04:42 [error] 1#1: *4183 no live upstreams...").
#   Without normalizing both sides, LCS was needed to fuzzy-match them,
#   which was slow and produced false positives.
#   Now both sides are normalized first, then simple string matching is used.
NGINX_TS_PREFIX_RE = re.compile(
    r'^\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s+'   # timestamp
    r'(\[(?:error|warn|crit|alert|emerg)\])\s+'       # [severity]
    r'\d+#\d+:\s+'                                     # pid#tid:
    r'(?:\*\d+\s+)?'                                   # optional *requestid
)

NGINX_CONTEXT_FIELDS_RE = re.compile(
    r',\s*client:.*$|'
    r',\s*server:.*$|'
    r',\s*request:.*$|'
    r',\s*upstream:.*$|'
    r',\s*host:.*$|'
    r',\s*referrer:.*$',
    re.IGNORECASE
)

# ── Build script map from the knowledge base ─────────────────────────────────
def build_script_map(kb_entries: list) -> dict:
    """
    Build the script map directly from the 'possible_fix_script' and 'app'
    fields on each KB entry.

    Result structure per entry:
      {
        "script":      "fix_apps.sh" | "fix_pgsqldb.sh" | None,
        "args":        ["<app>"] | [],
        "no_auto_fix": False | True
      }

    Rules:
      - possible_fix_script == "none"  → no_auto_fix = True, script = None
      - possible_fix_script is absent  → no_auto_fix = True, script = None  (safe default)
      - any other value                → script = that value,
                                         args = [entry["app"]] if app is set else []
                                         no_auto_fix = False
    """
    script_map = {}

    for entry in kb_entries:
        entry_id    = entry.get("id", "")
        app         = entry.get("app", "")
        fix_script  = entry.get("possible_fix_script", "none").strip()

        if not entry_id:
            print("WARNING: KB entry missing 'id' field — skipping.", file=sys.stderr)
            continue

        if not fix_script or fix_script.lower() == "none":
            script_map[entry_id] = {
                "script":      None,
                "args":        [],
                "no_auto_fix": True,
            }
        else:
            script_map[entry_id] = {
                "script":      fix_script,
                "args":        [app] if app else [],
                "no_auto_fix": False,
            }

    print(f"  [script_map] Built {len(script_map)} entries from knowledge base.", file=sys.stderr)
    return script_map


def load_json(path: str) -> object:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize_error_text(text: str) -> str:
    """
    Normalize a log line or KB entry to a stable canonical form for matching.

    For new nginx datetime-prefixed format:
        Input:  "2026/05/08 02:28:08 [error] 1#1: *5190 no live upstreams while connecting to upstream"
        Output: "[error] no live upstreams while connecting to upstream"

    For KB entries that also contain a full example timestamp:
        Input:  "2026/05/08 02:04:42 [error] 1#1: *4183 no live upstreams while connecting to upstream"
        Output: "[error] no live upstreams while connecting to upstream"

    For dbapps-style errors (unchanged):
        Input:  "ERROR: Database connection failed"
        Output: "ERROR: Database connection failed"

    CHANGE FROM PREVIOUS VERSION:
        This function is entirely new. Previously no normalization was applied,
        so matching relied on LCS against raw strings that contained different
        timestamps and ids on each side, producing unreliable results.
    """
    text = text.strip()

    m = NGINX_TS_PREFIX_RE.match(text)
    if not m:
        return text

    severity = m.group(1)
    body = text[m.end():].strip()

    # Strip dynamic trailing context fields
    body = NGINX_CONTEXT_FIELDS_RE.sub('', body).strip()
    body = body.rstrip(',').strip()

    return f"{severity} {body}"

def find_match(error_text: str, kb_entries: list) -> dict | None:
    """
    Match error_text against knowledge-base entries.

    Both the incoming error_text and each KB log entry are normalized before
    comparison so dynamic values (timestamps, pids, request ids, client IPs)
    do not interfere with matching.

    Match priority:
      Pass 1 — exact equality on normalized forms
      Pass 2 — prefix match: normalized error starts with normalized KB log
      Pass 3 — prefix match reversed: normalized KB log starts with normalized error
               (handles cases where ranked error is a truncated form of the KB entry)
      Pass 4 — substring match: normalized KB log appears inside normalized error,
               or normalized error appears inside normalized KB log.
               Longest matching KB log wins (most specific match).
    """
    normalized_error = normalize_error_text(error_text)

    # Pass 1 — exact match on normalized forms
    for entry in kb_entries:
        kb_log = entry.get("log", "")
        if normalize_error_text(kb_log) == normalized_error:
            return entry

    # Pass 2 — normalized error starts with normalized KB log (prefix match)
    best_match = None
    best_len = 0
    for entry in kb_entries:
        kb_log = entry.get("log", "")
        normalized_kb = normalize_error_text(kb_log)
        if normalized_kb and normalized_error.startswith(normalized_kb):
            if len(normalized_kb) > best_len:
                best_match = entry
                best_len = len(normalized_kb)
    if best_match:
        return best_match

    # Pass 3 — normalized KB log starts with normalized error (reversed prefix)
    best_match = None
    best_len = 0
    for entry in kb_entries:
        kb_log = entry.get("log", "")
        normalized_kb = normalize_error_text(kb_log)
        if normalized_kb and normalized_kb.startswith(normalized_error):
            if len(normalized_kb) > best_len:
                best_match = entry
                best_len = len(normalized_kb)
    if best_match:
        return best_match

    # Pass 4 — substring containment, longest matching KB entry wins
    best_match = None
    best_len = 0
    for entry in kb_entries:
        kb_log = entry.get("log", "")
        normalized_kb = normalize_error_text(kb_log)
        if not normalized_kb:
            continue
        if normalized_kb in normalized_error or normalized_error in normalized_kb:
            if len(normalized_kb) > best_len:
                best_match = entry
                best_len = len(normalized_kb)
    if best_match:
        return best_match

    return None


def main():
    if len(sys.argv) < 3:
        print("Usage: get_remediations.py <ranked_errors_json> <knowledge_base_json>",
              file=sys.stderr)
        sys.exit(1)

    ranked_path = sys.argv[1]
    kb_path = sys.argv[2]

    if not os.path.isfile(ranked_path):
        print(f"ERROR: ranked-errors.json not found: {ranked_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(kb_path):
        print(f"ERROR: knowledge base not found: {kb_path}", file=sys.stderr)
        sys.exit(1)

    ranked_errors = load_json(ranked_path)
    kb_raw = load_json(kb_path)
    kb_entries = kb_raw.get("list", [])

    # Build script map directly from KB 'possible_fix_script' fields
    script_map = build_script_map(kb_entries)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    results = []
    for record in ranked_errors:
        error_text = record.get("error", "")
        match = find_match(error_text, kb_entries)
        


        # Debug: show what normalization produced and which pass matched
        # normalized = normalize_error_text(error_text)
        # print(f"  [match] rank={record.get('rank')} raw='{error_text[:60]}' "
        #       f"normalized='{normalized[:60]}' matched={bool(match)} "
        #       f"kb_id={match.get('id') if match else None}",
        #       file=sys.stderr)

        if match:
            entry_id = match.get("id", "")
            script_info = script_map.get(entry_id, {
                "script": None, "args": [], "no_auto_fix": False
            })
            results.append({
                "rank":                   record.get("rank"),
                "namespace":              record.get("namespace"),
                "deployment":             record.get("deployment"),
                "error":                  error_text,
                "count":                  record.get("count"),
                "meaning":                match.get("meaning", ""),
                "remediation_instruction": match.get("remediation", ""),
                "remediation_script":     script_info["script"],
                "remediation_args":       script_info["args"],
                "no_auto_fix":            script_info["no_auto_fix"],
            })
        else:
            results.append({
                "rank":                   record.get("rank"),
                "namespace":              record.get("namespace"),
                "deployment":             record.get("deployment"),
                "error":                  error_text,
                "count":                  record.get("count"),
                "meaning":                "Unknown error — no entry in knowledge base",
                "remediation_instruction": "Manual review required",
                "remediation_script":     None,
                "remediation_args":       [],
                "no_auto_fix":            True,
            })
        print(f"  Rank {record.get('rank')}: matched={bool(match)} "
              f"script={results[-1]['remediation_script']}", file=sys.stderr)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    print(f"ERROR_MEANING_FILE={OUTPUT_FILE}")
    print(f"INFO: {len(results)} error(s) matched against knowledge base.", file=sys.stderr)


if __name__ == "__main__":
    main()