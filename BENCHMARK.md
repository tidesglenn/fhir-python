# Synthetic export and memory experiments

This branch contains generic test infrastructure only. Fixtures are generated,
disposable scalar tables with exact decimals, Unicode strings and NULL payloads.
They contain no application code or customer information. No production system
is contacted or reconfigured.

The `export-memory-bench` workflow runs independent Caché/IRIS jobs for export
settings and server memory. Each server is limited to two CPUs and 3 GiB RAM;
SQL Server's configured memory limit is 1536 MB. The host still schedules both
servers and clients, so results describe this local-container environment.

Every ranked transfer checks its row count and a SHA-256 hash of every ordered
destination value against the fixture. A failed lane or timeout fails the job,
even when another lane passes. Verification has a separate timing field and is
outside throughput timing. Connection setup, JVM startup, source extraction,
file creation/copying when used, target loading, and commit completion are
inside transfer timing. Client interpreter startup and target table creation
are outside it. Each setting has three repetitions in a fixed shuffled order.

Paths include Python ODBC inserts, ODBC/JDBC extraction to a seekable Unicode
file followed by BCP, server-side SQL export followed by file copying and BCP,
IRIS Python extraction to BCP, and a forward-only JDBC ResultSet into SQL
Server's JDBC bulk-copy API. The last path uses no intermediate data file.
Its Java heap is bounded; its full process-tree RSS is sampled every 50 ms.

The original `/dev/stdin` BCP experiment failed every pipe trial, despite the
old workflow reporting success when any transfer passed. Those failures are
excluded from speed rankings. File staging and JDBC bulk copy are separate
approaches. TSV lanes are limited to scalars without field/record delimiters;
they are not an arbitrary CSV, binary or LOB transport contract.

The memory baseline explicitly sets 256 MB of 8 KB global buffers, 64 MB of
routine buffers, 256 MB maximum private memory per server process, and a shared
heap of 64 MB on Caché or 300 MB on IRIS. Candidates independently double the
shared heap, raise global buffers to 1024 MB, raise routine buffers to 128 MB,
or raise private memory to 1024 MB. Every candidate resets all other knobs to
the baseline, restarts the disposable engine, reads back configuration and
actual shared-heap allocation, and verifies a complete source scan. Baseline
is repeated at the end. Original configuration is restored in `finally`.

The first scan after restart starts with a fresh engine cache, while the OS
page cache remains populated. Repeated transfer results are warm-cache results.
These experiments do not establish physical cold-disk performance, remote
network throughput, billion-row behavior or optimal production settings.

The export sweep tests fetch sizes 1000/10000/50000, target bulk batch sizes
1000/10000/50000, one/two/four isolated workers, and 64 MB versus 256 MB JVM
heap on the fastest completely validated baseline lane per row shape. Record
the effective settings and peak RSS before adopting a faster result.

Memory references: [Caché gmheap](https://docs.intersystems.com/latest/csp/docbook/DocBook.UI.Page.cls?KEY=RCPF_gmheap),
[IRIS gmheap](https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=RACS_gmheap),
[IRIS bbsiz](https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=RACS_bbsiz).
Configured `gmheap` and runtime shared-heap allocation can differ because of
engine reservations. `bbsiz` is a per-process ceiling, not preallocated RAM.
