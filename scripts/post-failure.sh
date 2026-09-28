#!/usr/bin/env bash
# Posts the captured failure log back to the PRIVATE repository as an issue.
# The log may contain private source lines, so it is never printed here;
# it goes only to the private repo. Usage: post-failure.sh <logfile> <title>
# Needs env: RUNNER_PAT, PRIVATE_REPO, SHA, RUN_URL.
set -euo pipefail

logfile="$1"
title="$2"

python3 - "$logfile" "$title" <<'PY'
import json, os, sys, urllib.request

log_path, title = sys.argv[1], sys.argv[2]
try:
    log = open(log_path, encoding="utf-8", errors="replace").read()
except OSError:
    log = "(no log was captured)"
token = os.environ.get("RUNNER_PAT", "")
if token:
    log = log.replace(token, "***")
tail = "\n".join(log.splitlines()[-400:])

body = (
    f"Automated report from the public test-runner.\n\n"
    f"Commit: `{os.environ['SHA']}`\n\n"
    f"Runner log (public, summary only): {os.environ['RUN_URL']}\n\n"
    f"<details><summary>Captured log, last 400 lines</summary>\n\n```\n{tail}\n```\n</details>"
)

req = urllib.request.Request(
    f"https://api.github.com/repos/{os.environ['PRIVATE_REPO']}/issues",
    data=json.dumps({"title": title, "body": body, "labels": ["public-runner"]}).encode(),
    headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    },
    method="POST",
)
with urllib.request.urlopen(req) as resp:
    print("filed issue", json.load(resp)["number"])
PY
