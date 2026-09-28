#!/usr/bin/env bash
# Posts a commit status to the private repository under test.
# Usage: report-status.sh <state> <context> <description>
# Needs env: RUNNER_PAT, PRIVATE_REPO, SHA, RUN_URL.
set -euo pipefail

state="$1"
context="$2"
description="$(printf '%s' "${3:-}" | head -c 140)"

body="$(python3 -c '
import json, os, sys
print(json.dumps({
    "state": sys.argv[1],
    "context": sys.argv[2],
    "description": sys.argv[3],
    "target_url": os.environ["RUN_URL"],
}))' "$state" "$context" "$description")"

curl -sfS -X POST \
  -H "Authorization: Bearer ${RUNNER_PAT}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${PRIVATE_REPO}/statuses/${SHA}" \
  -d "$body" >/dev/null
echo "status ${state} (${context}): ${description}"
