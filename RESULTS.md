# IRIS and Caché export tuning — synthetic results

Database buffers matter when the export working set can stay cached. Increasing
shared heap or the per-process memory ceiling did not produce the same benefit.
The fastest path depended on row shape in both matched confirmation runs:
narrow rows favored a staged JDBC file plus BCP; wide rows favored the corrected
file-free JDBC bulk path. **All 108 final transfer trials passed full value checks.**

## Final confirmation: Caché

All **54/54 transfer trials** and both typed/text scalar bulk checks passed.
The scalar gate also verified 25 fresh source connections and four isolated
source-worker processes. The following are separate measured configurations,
with three full-value-validated repetitions each:

| Shape / path | Workers | Fetch hint | Bulk batch | Median rows/s | Transfer seconds, min–max | Client peak MiB |
|---|---:|---:|---:|---:|---:|---:|
| Narrow: JDBC file + BCP | 1 | 10,000 | 50,000 | 200,002 | 2.488–2.513 | 92.6 |
| Narrow: JDBC file + BCP | 2 | 10,000 | 50,000 | **223,396** | 2.218–2.265 | 229.8 |
| Wide: file-free JDBC bulk | 1 | 10,000 | 50,000 | **51,975** | 9.403–9.891 | 141.3 |
| Wide: file-free JDBC bulk | 2 | 10,000 | 50,000 | 48,323 | 9.829–10.797 | 331.9 |

These combined-setting trials used a 1 GB database-buffer pool, 64 MiB shared
heap, 64 MB routine pool, 256 MiB per-process ceiling, and 256 MiB JVM heap cap.
The matched buffer pairs showed **no consistent Caché benefit from raising
256 MB to 1 GB**: narrow throughput stayed around 181–185k rows/s at bulk 10k;
wide bulk throughput overlapped at both sizes, with no storage reads in either.
The best combined-setting rate is not evidence that Caché needs 1 GB of buffers.

On this runner, baseline one-worker wide JDBC bulk reached 44,590 rows/s versus
36,889 for JDBC file + BCP, using no intermediate data file. Baseline narrow
JDBC bulk was slower than the staged JDBC file lane (164,350 versus 181,247
rows/s), so the file-free path is not the universal winner.

Full hash verification cost about 3.9 seconds for a narrow trial and 22–25 seconds
for a wide trial, separately from the transfer timer. The downloadable CSV
includes both transfer-only throughput and throughput including full hashing.

## Final confirmation: IRIS

All **54/54 transfer trials** and both typed/text scalar bulk checks passed.
The fresh-connection/isolated-worker source gate passed as well. These are three
repetitions per configuration, measured on the IRIS confirmation runner:

| Shape / path | Workers | Fetch hint | Bulk batch | Median rows/s | Transfer seconds, min–max | Client peak MiB |
|---|---:|---:|---:|---:|---:|---:|
| Narrow: JDBC file + BCP | 1 | 10,000 | 50,000 | 246,636 | 2.027–2.052 | 107.7 |
| Narrow: JDBC file + BCP | 2 | 10,000 | 50,000 | **262,824** | 1.877–2.027 | 235.1 |
| Wide: file-free JDBC bulk | 1 | 10,000 | 50,000 | 53,423 | 9.058–11.191 | 160.9 |
| Wide: file-free JDBC bulk | 2 | 10,000 | 50,000 | **54,672** | 9.042–9.920 | 356.0 |

These combined-setting trials used a 1 GB database-buffer pool, 300 MiB shared
heap, 64 MB routine pool, 256 MiB per-process ceiling, and 256 MiB JVM heap cap.
One wide worker achieved about 98% of two workers' median throughput with less
than half the measured client memory. It is the preferable wide-row starting
point when memory efficiency matters.

The matched 256 MB / 1 GB buffer sequence confirmed the warm-cache effect on
the file-free wide lane (bulk batch 10k, one worker):

| Sequence | Global buffers MB | Wide rows/s | Median source storage reads MiB |
|---|---:|---:|---:|
| Initial baseline | 256 | 20,643 | 613.594 |
| Larger buffers, first pass | 1024 | 45,677 | 0.062 |
| Baseline repeated | 256 | 21,929 | 613.516 |
| Larger buffers repeated | 1024 | 52,282 | 0.000 |
| Baseline repeated again | 256 | 22,702 | 613.125 |

After-profile shared-heap usage snapshots were about 1.6 MiB out of the configured
300 MiB. These are snapshots rather than peak measurements. The first post-restart
source scan still reads the fixture; these rankings concern subsequent warm scans.

## Settings to carry forward

For this bounded scalar fixture, use a fetch hint of **10,000** and bulk batches
of **50,000** as the next pilot preset. Two isolated workers gave the fastest
narrow staged-file transfers. One worker gave the best wide speed/memory tradeoff
with the file-free JDBC bulk path; two workers improved IRIS wide throughput by
only about 2.3% and slowed the Caché wide case.

The shared heap is a different pool from database buffers. Its tested increase
did not remove the source reads or deliver a consistent throughput gain. Keeping
adequate database buffers for a reusable working set was useful on the IRIS
runner. Caché showed no consistent benefit from the same buffer increase.
The measured 1 GB pool fits this fixture; it does not predict an ideal pool for
a first, one-pass export of tables much larger than RAM.

This is a benchmark prototype, with scalar mappings and explicit decimal
projections. File lanes require data without TSV delimiters; the corrected
file-free lane passed the separate delimiter/NULL/precision fixture. It is not
a completed arbitrary-type or LOB migration implementation.

## Scope and integrity

These are disposable scalar fixtures: 500,000 rows per shape, five columns,
Unicode labels, exact decimals, and NULL payloads. Narrow payloads are 32
characters; wide payloads are 1024. Every ranked transfer has three successful
repetitions and a full ordered destination SHA-256 matching the fixture. Source
reads are also checked. Verification is outside transfer timing and separately
recorded. JVM startup, connection setup, file creation/copying, loading and
commit completion are inside the timer. Target table creation and index
building are outside it; targets are heaps without secondary indexes.

The jobs use separate runners. Compare settings **within one job**; differing
storage and CPU performance make cross-runner rates unsuitable for ranking the
engines against each other. Each source and SQL Server container is limited to
two CPUs and 3 GiB RAM; SQL Server's memory setting is 1536 MB. Versions recovered
from the prior run are Caché 2018.1.5 Build 659U, IRIS 2026.2 Build 221U, SQL Server
2022 CU27-GDR 16.0.4305.5, Python 3.11.17 and pyodbc 5.3.0. This does not establish
Caché 2018.1.7/Windows or remote-network performance.

## Memory sweep

All rows below use the same JDBC-to-Unicode-file-to-BCP lane, fetch hint 10,000,
bulk commit 10,000 and one worker. Only the indicated memory parameter changes;
every profile restarts the engine and verifies configuration readback. Values
are medians of three fully validated full transfers, in thousands of rows/s.

| Profile | Caché narrow | Caché wide | IRIS narrow | IRIS wide |
|---|---:|---:|---:|---:|
| Baseline | 371.9 | 50.8 | 207.3 | 19.3 |
| Shared heap doubled | 372.3 | 55.0 | 160.9 | 20.7 |
| Database buffers 256 → 1024 MB | 366.1 | 46.6 | 221.4 | 40.8 |
| Routine buffers 64 → 128 MB | 382.0 | 54.8 | 160.5 | 21.0 |
| Per-process ceiling 256 → 1024 MB | 371.5 | 56.2 | 161.9 | 21.1 |
| Baseline repeated at end | 389.0 | 63.8 | 161.3 | 21.1 |

The shared-heap baseline is 64 MiB on Caché and 300 MiB on IRIS; doubled profiles
use 128/600 MiB. No configuration is inferred to be active merely because it was
requested: all four settings and actual shared-heap allocation were read back.
Original settings were restored after each job.

IRIS wide extraction plus file creation fell from a median 14.47 seconds to
2.49 seconds with the larger database cache. Median source storage reads fell
from 613.55 MiB per transfer to 0.062 MiB, while target load time stayed around
10 seconds. Returning to 256 MB restored about 613 MiB of source reads and
23.69 seconds total. This supports a cache-capacity effect. Shared heap,
routine buffers and process ceilings did not remove those reads.

Caché wide baseline speed drifted upward by about 26% by the end of its job.
Candidate gains smaller than this drift do not justify increasing memory.
No source or target OOM events were recorded in the memory trials.

**This is a warm-cache gain.** The first complete scan after restarting IRIS
read wide rows at about 36–39k rows/s with either 256 MB or 1 GB. Raising cache
size did not make that first scan twice as fast. A one-pass export much larger
than RAM may receive little benefit from a larger cache; the 1 GB setting is a
measured fit for this fixture, not a production sizing formula.

## Export settings sweep

These rates come from the separate export-tuning job for each engine and must
not be compared numerically with the memory job above.

| Setting / lane | Caché narrow rows/s | Caché wide rows/s | Client peak memory, narrow / wide |
|---|---:|---:|---:|
| JDBC file + BCP, 1 worker, fetch/bulk 10k | 183,285 | 39,256 | 93 / 104 MiB |
| Same, bulk commit 50k | 192,255 | 42,224 | 93 / 104 MiB |
| Same, 2 isolated workers, bulk 10k | 219,598 | 47,319 | 224 / 261 MiB |
| Same, 4 isolated workers, bulk 10k | 188,694 | 47,940 | 412 / 458 MiB |

Two workers gave nearly the same wide throughput as four with substantially
less client memory. Four workers did not help narrow Caché rows. Fetch hints
1k/10k/50k changed little here; bulk commits of only 1k reduced throughput.
The 64 MiB Java heap cap did not improve the JDBC lane; Java process RSS is
larger than its heap cap.

On the IRIS export runner, ODBC-to-file-to-BCP won baseline selection. With
50k bulk commits it reached 384,531 narrow and 29,788 wide rows/s. Two/four
isolated workers at bulk 10k reached 433,245/446,808 narrow and 35,258/40,277
wide rows/s; peak client memory was 182/263 MiB narrow and 142/248 MiB wide.
These are independent setting changes. Their gains cannot be multiplied to
predict an unmeasured combined configuration. Java heap settings are inapplicable
to an ODBC-only lane and are excluded from recommendation rankings.

## Driver and transport findings

- The previous `/dev/stdin` BCP trials all failed with EOF/cast errors. That
  workflow was green because some file trials passed. They are excluded from
  throughput rankings; the new harness fails if any attempted timed trial fails.
- The initial new sweeps each had six rejected JDBC bulk prototype trials due
  to incorrect transaction ownership in the harness. Other fully validated
  trials remain usable; the overall red jobs are not reported as all passing.
- Direct ResultSet-to-bulk-copy encountered a NULL character-stream exception
  in both source JDBC drivers. The scalar adapter supplies NULL-safe readers;
  it does not replace NULLs with empty strings or skip rows.
- The raw numeric source metadata caused a decimal-range rejection on the
  18-digit edge fixture. The exact decimal-to-text projection passed full
  destination checks on both engines. An explicit DECIMAL(18,4) projection
  passed both corrected scalar gates; raw typed bulk is not recommended
  from the earlier unprojected fixture evidence.
- Both ODBC character decoders set to UTF-8 passed the Caché Linux exact-numeric
  probe, including negatives, NULLs, precision/scale boundaries, Unicode and
  fresh isolated workers. This does not demonstrate the same defect on Windows.
- Plain TSV lanes cover scalars without tabs/newlines/record delimiters and do
  not prove arbitrary text/LOB transport. The separate bulk scalar gate includes
  those delimiters, 4000-character values, NULL versus empty strings, integer
  boundaries and decimal(18,4) boundaries.

## Reproduction and evidence

Generic code and methods: [BENCHMARK.md](BENCHMARK.md). The settings are standalone
benchmark settings; they have not been applied to a production instance. No private application source is in this repository.

- [Original 100k sweep](https://github.com/tidesglenn/fhir-python/actions/runs/38994023814)
- [Original 500k direct/path confirmation](https://github.com/tidesglenn/fhir-python/actions/runs/38994265621)
- [Original pipe comparison — contains failed lanes](https://github.com/tidesglenn/fhir-python/actions/runs/38994467331)
- [Memory and export sweeps — includes rejected bulk prototypes](https://github.com/tidesglenn/fhir-python/actions/runs/38995544620)
- [NULL-safe bulk edge check — text passed, raw typed rejected](https://github.com/tidesglenn/fhir-python/actions/runs/39036441846)

- [Final matched confirmations — all 108 transfers passed](https://github.com/tidesglenn/fhir-python/actions/runs/39036673323)

[Validated settings CSV](evidence/validated-settings.csv) records transfer timing,
full-hash verification cost, effective memory settings, client RSS, I/O counters
and fixture checksums. [Trial counts](evidence/trial-counts.json) retain the number
of rejected prototypes. [Evidence notes](evidence/README.md) link the original
per-trial artifacts and reproduction procedure.

Numerical summaries are generated by `scripts/analyze_tuning.py`, with per-job
separation and eligibility requiring three distinct successful repetitions.
