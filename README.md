# fhir-python — disposable test-runner infrastructure

This public repository is **test infrastructure only**. It exists so heavy
continuous-integration jobs can run on free public-repo GitHub Actions minutes.

It contains nothing proprietary: no application source, no customer data, no
credentials for real systems, no internal hostnames or paths. Only:

* `.github/workflows/live-test-runner.yml` — the runner workflow
* `scripts/report-status.sh` / `scripts/post-failure.sh` — generic helpers
  that report results back to the private repository under test

## How it works

1. A private repository (or a human, via **Actions → live-test-runner → Run
   workflow**) sends a commit SHA to this repo as a `repository_dispatch`
   event of type `run-private-tests`.
2. Two jobs start:
   * **linux** — throwaway Oracle Free 23 + SQL Server 2022 service containers
   * **windows** — a Windows Server 2025 runner with its built-in LocalDB
     (SQL Server Express fallback) and real `sqlcmd.exe` / `bcp.exe`
3. Each job checks out the private repository at that SHA **at runtime**
   (PAT secret, never persisted), runs that repo's `ci/test-runner/*-live`
   contract script with all output captured to a private log file, prints
   **only the numeric pass/fail summary** in the public log, posts a commit
   status (`public-runner / linux`, `public-runner / windows`) back to the
   private commit, and — on failure — files an issue **in the private repo**
   containing the captured log tail.

Private sources are never printed in public logs, never uploaded as artifacts,
and never committed to this repository. The database passwords in the workflow
protect nothing: the databases exist only for the duration of one run.

## One-time setup

Repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
| --- | --- |
| `RUNNER_PAT` | Fine-grained PAT with access to the private repo: **Contents: read**, **Commit statuses: read/write**, **Issues: read/write** |
| `PRIVATE_REPO` | The private repository, `owner/name` |

The private repo needs the same `RUNNER_PAT` secret so its
`public-test-runner-dispatch` workflow can send `repository_dispatch` events
here (requires **Actions: read/write** on this repo if you want it fully
scoped; a classic `repo` PAT also works).

## Manual use

Actions → live-test-runner → Run workflow → paste a commit SHA from the
private repo. That is all.
