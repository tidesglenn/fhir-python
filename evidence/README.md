# Synthetic numerical results

`validated-settings.csv` contains aggregate settings, timings and resource
measurements from the synthetic benchmark runs. Only configurations with three
successful distinct repeats and an applicable experimental setting are ranked.
`trial-counts.json` includes rejected prototype attempts, which are excluded from
throughput recommendations. No application data or production information is used.

Detailed per-trial measurements and diagnostics remain in the existing GitHub
Actions artifacts and logs linked from `RESULTS.md`. For run 38995544620, the
memory jobs are 117287179119 (Caché) and 117287179380 (IRIS); the export jobs are
117287179178 and 117287179490. Those files preserve checksums and failed attempts.

To reproduce aggregate metrics after downloading an artifact, place its
`measurements.jsonl` in this directory as `tuning-ENGINE-MODE.jsonl`, then run:

```sh
python scripts/analyze_tuning.py evidence --output evidence/tuning-summary
```

Each engine/mode is a separate dataset. Compare settings within one runner.
Java heap changes on an ODBC-only lane are inapplicable and not ranked.
The raw artifacts also include the counters needed to reproduce CPU and I/O
summaries; this CSV contains no hostnames, credentials or diagnostic log text.

The final paired confirmations are run 39036673323, jobs 117407827567 (IRIS) and
117407827733 (Caché). All 54 transfers per engine passed, as did both scalar
correctness gates. Effective memory settings and fixture hashes are in the CSV.
Transfer-only and transfer-plus-full-hash throughput are separate columns.
